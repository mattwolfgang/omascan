"""USB scanner client for the Ricoh/Fujitsu fi-8170 via SANE.

The scanner's Linux driver is SANE's `fujitsu` backend (sane-backends),
driven through the `python-sane` bindings in a dedicated worker process
(sane_worker.py). The SANE C library is a per-process singleton that is not
thread-safe and can block indefinitely on a scanner that never answers, so
the parent never touches it directly: every SANE call is a request/response
pair over the worker's stdio, which can be bounded by a deadline and killed
without leaving C state or an orphaned scanner session behind.

The scanner exposes only a vendor-specific USB interface (class 0xff), so a
udev rule is needed to give the user access to /dev/bus/usb/<bus>/<dev>
(see install.sh / the README):

    SUBSYSTEM=="usb", ATTR{idVendor}=="04c5", ATTR{idProduct}=="15ff", MODE="0660", GROUP="omascan"

The device goes to sleep when idle and wakes on USB access, so SANE
discovery may need a few passes; a raw descriptor read of the USB device is
done first to wake it.
"""
from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import NoReturn

from privet_client import ScannedImage

# USB ids of the fi-8170 (the vendor string is still "Fujitsu" in the firmware).
USB_IDS = {"04c5:15ff"}

FEEDER_EMPTY = "document feeder out of documents"


class SaneError(RuntimeError):
    """Raised when the SANE/USB stack can't reach the scanner."""


class _SaneWorker:
    """A dedicated SANE subprocess speaking the sane_worker.py line protocol.

    `call`/`devices` enforce a hard deadline (timeout -> kill + SaneError);
    `poll_line`/`poll_bytes` time out softly (None) so a scan loop can keep
    polling while checking for a stop request. Closing the worker sends a
    clean "close" first (releasing the scanner's session) and only kills a
    worker that no longer answers."""

    def __init__(self, extra_env: dict[str, str] | None = None) -> None:
        script = Path(__file__).resolve().parent / "sane_worker.py"
        env = {**os.environ, **(extra_env or {})}
        self._p = subprocess.Popen(
            [sys.executable, str(script)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
        )
        assert self._p.stdin is not None and self._p.stdout is not None
        assert self._p.stderr is not None
        self._in = self._p.stdin
        self._out = self._p.stdout
        self._err = self._p.stderr
        self._buf = b""
        self._closed = False

    # ----- low-level reads -------------------------------------------------

    def _fill(self, deadline: float) -> bool:
        """Block until a byte arrives or `deadline` passes; True when a byte
        arrived."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            # select caps each wait at 0.5 s, so loop back for the rest of
            # the budget.
            r, _, _ = select.select([self._out], [], [], min(remaining, 0.5))
            if r:
                chunk = os.read(self._out.fileno(), 4096)
                if not chunk:  # the worker died
                    self._fail("the SANE worker exited unexpectedly")
                self._buf += chunk
                return True

    def _read_line_or_none(self, deadline: float) -> str | None:
        while True:
            i = self._buf.find(b"\n")
            if i >= 0:
                line, self._buf = self._buf[:i], self._buf[i + 1:]
                return line.decode()
            if not self._fill(deadline):
                return None

    def _read_line(self, timeout: float) -> str:
        line = self._read_line_or_none(time.monotonic() + timeout)
        if line is None:
            self._abort(timeout)
        return line

    def poll_bytes(self, n: int, timeout: float) -> bytes:
        """Up to `n` bytes, blocking up to `timeout` for the first byte.
        Partial data is never lost: the caller accumulates the return value
        and the worker's buffer keeps the remainder. May return fewer than
        `n` bytes (or none) when the window expires mid-stream."""
        deadline = time.monotonic() + timeout
        out = bytearray()
        while True:
            if self._buf:
                take = min(len(self._buf), n - len(out))
                out += self._buf[:take]
                self._buf = self._buf[take:]
                if len(out) == n:
                    return bytes(out)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return bytes(out)
            r, _, _ = select.select([self._out], [], [], min(remaining, 0.5))
            if r:
                chunk = os.read(self._out.fileno(), 1 << 20)
                if not chunk:
                    self._fail("the SANE worker exited unexpectedly")
                self._buf += chunk
            elif len(out) == 0:
                continue  # first byte not here yet; keep waiting to the deadline
            else:
                return bytes(out)  # partial gathered; the stream paused

    def _abort(self, timeout: float) -> NoReturn:
        self.kill()
        raise SaneError(f"the scanner did not answer within {timeout:.0f} s")

    def _fail(self, msg: str) -> NoReturn:
        self.kill()
        raise SaneError(msg + self._stderr_tail())

    # ----- protocol --------------------------------------------------------

    def _send(self, line: str) -> None:
        self._in.write(line.encode() + b"\n")
        self._in.flush()

    def call(self, cmd: str, timeout: float) -> str:
        """Send a command, return its single response line (enforcing the
        deadline)."""
        self._send(cmd)
        line = self._read_line_or_none(time.monotonic() + timeout)
        if line is None:
            self._abort(timeout)
        return line

    def devices(self, timeout: float) -> list[tuple[str, str, str]]:
        """One device enumeration pass (name, brand, model), deadline-bounded."""
        self._send("devices")
        deadline = time.monotonic() + timeout
        out: list[tuple[str, str, str]] = []
        while True:
            line = self._read_line_or_none(deadline)
            if line is None:
                self._abort(timeout)
            if line.startswith("error "):
                raise SaneError(line[6:] + self._stderr_tail())
            if line == "end":
                return out
            if line.startswith("dev "):
                name, brand, model = line[4:].rsplit(" ", 2)
                out.append((name, brand, model))

    def poll_line(self, timeout: float) -> str | None:
        return self._read_line_or_none(time.monotonic() + timeout)

    # ----- lifecycle -------------------------------------------------------

    def _stderr_tail(self) -> str:
        err = b""
        while True:
            r, _, _ = select.select([self._err], [], [], 0.1)
            if not r:
                break
            chunk = os.read(self._err.fileno(), 65536)
            if not chunk:
                break
            err = (err + chunk)[-512:]
        return f": {err.decode(errors='replace').strip()}" if err else ""

    def close(self) -> None:
        """Clean exit (releases the scanner's session); a worker that no
        longer answers is killed."""
        if self._closed:
            return
        self._closed = True
        try:
            self._send("close")
            self._p.wait(timeout=5)
        except Exception:  # noqa: BLE001 - a dead or stuck worker is killed below
            self.kill()

    def kill(self) -> None:
        try:
            self._p.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._p.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass


def _usb_nodes(usb_root: Path = Path("/sys/bus/usb/devices")) -> list[Path]:
    """/dev/bus/usb nodes for every fi-8170 present, read straight from
    sysfs (no external tools like lsusb needed)."""
    found: list[Path] = []
    try:
        entries = list(usb_root.iterdir())
    except OSError:
        return found
    for dev in entries:
        try:
            vendor = (dev / "idVendor").read_text().strip().lower()
            product = (dev / "idProduct").read_text().strip().lower()
        except OSError:
            continue  # a hub or a device mid-attach has no id files
        if f"{vendor}:{product}" in USB_IDS:
            # /dev/bus/usb zero-pads both numbers (the kernel's naming); the
            # sysfs files don't.
            busnum = (dev / "busnum").read_text().strip()
            devnum = (dev / "devnum").read_text().strip()
            found.append(Path(f"/dev/bus/usb/{int(busnum):03d}/{int(devnum):03d}"))
    return found


def usb_scanner_present() -> bool:
    """Fast, SANE-free check for a physically connected fi-8170."""
    return bool(_usb_nodes())


def _wake(node: Path) -> None:
    """Read the USB device descriptors; the kernel's control access wakes a
    sleeping scanner. Best effort."""
    try:
        with open(node, "rb") as f:
            f.read(256)
    except OSError:
        pass


def ensure_sane_config() -> Path:
    """The SANE config dir Omascan scans through: only the backends that can
    talk to an fi-series scanner, so discovery doesn't probe every USB device
    on the system. Created once under $XDG_CACHE_HOME."""
    cache = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    cfg = cache / "omascan" / "sane"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "dll.conf").write_text("epson2\nfujitsu\n")
    fujitsu_conf = cfg / "fujitsu.conf"
    if not fujitsu_conf.exists():
        system = Path("/etc/sane.d/fujitsu.conf")
        if system.is_file():
            shutil.copyfile(system, fujitsu_conf)
        else:
            fujitsu_conf.write_text("# fujitsu backend options (none needed for the fi-8170)\n")
    return cfg


def find_device(retries: int = 4, pause: float = 5.0, budget: float = 90.0) -> tuple[str, str, str] | None:
    """(device name, brand, model) for a ready fi-series scanner, or None.

    Each pass is one SANE device enumeration: ~1 s when the scanner is awake,
    longer while the wake-up is still taking effect. `budget` caps the total
    wait so a scanner that never answers can't hang discovery.
    """
    nodes = _usb_nodes()
    if not nodes:
        return None
    for node in nodes:
        if not os.access(str(node), os.R_OK):
            raise SaneError(
                f"no read permission on {node}; install the Omascan udev rule "
                "(GROUP=omascan, MODE=0660, for idVendor 04c5 / idProduct 15ff) and "
                "log in again."
            )
        _wake(node)
    worker = _SaneWorker({"SANE_CONFIG_DIR": str(ensure_sane_config())})
    try:
        line = worker.call("init", 30.0)
        if line.startswith("error "):
            raise SaneError(line[6:])  # e.g. python-sane missing: not a "not found"
        deadline = time.monotonic() + budget
        for attempt in range(retries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            for name, brand, model in worker.devices(min(30.0, remaining)):
                if "fi-81" in model:
                    return name, brand, model
            if attempt < retries - 1:
                time.sleep(min(pause, max(0.0, deadline - time.monotonic())))
        return None
    finally:
        worker.close()


class SaneClient:
    """USB equivalent of PrivetClient: get_info() and a scan() generator that
    yields ScannedImage objects (front then back for each sheet).

    Runs a dedicated SANE worker process; the worker is closed when the scan
    generator ends or close() is called, which releases the scanner's session.
    """

    def __init__(self, device_name: str, timeout: float = 60.0) -> None:
        """`timeout` bounds how long opening the device may block on a scanner
        that is still waking up. Raises SaneError when the scanner can't be
        reached, so transport selection can fall back to the network."""
        self.device_name = device_name
        self.timeout = timeout
        self._stopped = threading.Event()
        self._worker = _SaneWorker({"SANE_CONFIG_DIR": str(ensure_sane_config())})
        line = self._worker.call("init", timeout)
        if line.startswith("error "):
            self._worker.close()
            raise SaneError(line[6:])
        line = self._worker.call(f"open {device_name}", timeout)
        if line.startswith("error "):
            self._worker.close()
            raise SaneError(line[6:])

    def get_info(self) -> dict:
        parts = self.device_name.split(":")
        return {
            "manufacturer": "Fujitsu/Ricoh",
            "model": parts[1] if len(parts) > 1 else "",
            "serialNumber": parts[-1] if len(parts) > 1 else "",
            "firmwareVersion": "USB",
        }

    def stop(self) -> None:
        """Request a stop (the TUI's Stop button); an in-progress frame read
        ends at the next poll, even if the device is hung."""
        self._stopped.set()

    def scan(self, resolution: int = 300, width: int = 5760, height: int = 4576,
             jpeg_quality: int = 80, frame_deadline: float = 120.0):
        """Yields one ScannedImage per scanned side; a sheet's front is
        followed by its back, like the network protocol.

        `frame_deadline` ends the scan (killing the worker) when no data
        arrives for that long — a normal frame is in 10-30 s, so this only
        fires on a truly stuck device; the stop request always wins over it.
        """
        try:
            # The worker answers a scan job with frame lines (no separate
            # "started" reply).
            self._worker._send(f"scan {resolution} {width} {height} {jpeg_quality}")
            frame_idx = 0
            while True:
                line = self._read_scan_line(time.monotonic() + frame_deadline)
                if line is None:
                    return  # stopped: the worker was killed
                if line.startswith("error "):
                    raise SaneError(line[6:])
                if line == "done":
                    return
                _sheet, source, pw, ph, res, size = line.split(" ")[1:]
                data = self._read_scan_bytes(int(size), time.monotonic() + frame_deadline)
                if data is None:
                    return  # stopped: the worker was killed
                frame_idx += 1
                yield ScannedImage(
                    sheet_number=(frame_idx - 1) // 2 + 1,
                    source=source,
                    uri="",
                    size=len(data),
                    pixel_width=int(pw),
                    pixel_height=int(ph),
                    resolution=int(res),
                    end_of_scan=False,
                    data=data,
                )
        finally:
            self.close()

    def _read_scan_line(self, stall: float) -> str | None:
        """Read the next response line, polling for a stop every second. A
        frame legitimately takes 10-30 s, so the only hard ends are a stop
        request, the stall deadline, or a dead worker."""
        while True:
            line = self._worker.poll_line(1.0)
            if line is None:
                if self._stopped.is_set():
                    self._worker.kill()
                    return None
                if time.monotonic() > stall:
                    self._worker.kill()
                    raise SaneError("the scan stalled (no data for the frame deadline); "
                                   "the device may be stuck")
                continue
            return line

    def _read_scan_bytes(self, n: int, stall: float) -> bytes | None:
        data = b""
        while len(data) < n:
            chunk = self._worker.poll_bytes(n - len(data), 1.0)
            if not chunk and self._stopped.is_set():
                self._worker.kill()
                return None
            if not chunk and time.monotonic() > stall:
                self._worker.kill()
                raise SaneError("the scan stalled (no data for the frame deadline); "
                               "the device may be stuck")
            data += chunk
        return data

    def close(self) -> None:
        self._worker.close()
