#!/usr/bin/env python3
"""Omascan's SANE worker: runs the SANE C library in a dedicated process.

The SANE library is a per-process singleton that is not thread-safe and can
block indefinitely on a scanner that never answers, so it lives in this
child process: the parent speaks a line protocol over stdin/stdout, can
bound every blocking call, and can kill the process on timeout without
leaving C state behind. When stdin reaches EOF (the parent is gone), the
device is closed cleanly so the scanner's session is released.

Protocol (one command per line on stdin, responses on stdout):
    init             -> ok | error <msg>
    devices          -> dev <name> <brand> <model>  (repeated) -> end | error <msg>
    open <name>      -> ok | error <msg>
    scan <res> <w> <h> <q>
                      -> frame <sheet> <source> <pw> <ph> <res> <size> followed by
                         exactly <size> bytes  (repeated) -> done | error <msg>
    close            -> ok
"""
import io
import os
import sys
from typing import Any

FEEDER_EMPTY = "document feeder out of documents"
MM_PER_INCH = 25.4
UNITS_PER_INCH = 1200

# Protocol globals: `sane` is set by the "init" command before any other
# command may arrive, and `dev` by "open" before "scan".
sane: Any = None
dev: Any = None


def out(data: bytes) -> None:
    os.write(1, data)


def reply(text: str) -> None:
    out(text.encode() + b"\n")


def close_dev() -> None:
    global dev
    d, dev = dev, None
    if d is not None:
        try:
            d.cancel()
        except Exception:  # noqa: S110 - cleanup: a failed cancel must not block close
            pass
        try:
            d.close()
        except Exception:  # noqa: S110 - cleanup
            pass


def run_scan(res: int, width: int, height: int, quality: int) -> None:
    d = dev
    d.mode = "Color"
    d.resolution = res
    d.source = "ADF Duplex"
    # 1/1200-inch units -> mm
    d.page_width = width / UNITS_PER_INCH * MM_PER_INCH
    d.page_height = height / UNITS_PER_INCH * MM_PER_INCH
    frame = 0
    while True:
        try:
            d.start()
        except Exception as e:  # the C error type; also covers a dead device
            if FEEDER_EMPTY in str(e).lower():
                reply("done")
                return
            raise
        try:
            picture = d.snap(no_cancel=True)
        except Exception as e:  # the C error type; also covers a dead device
            if FEEDER_EMPTY in str(e).lower():
                reply("done")
                return
            raise
        buf = io.BytesIO()
        picture.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        pw, ph = picture.size
        reply(f"frame {frame // 2 + 1} {'front' if frame % 2 == 0 else 'back'} "
              f"{pw} {ph} {res} {len(data)}")
        out(data)
        frame += 1


def main() -> None:
    global sane, dev
    try:
        import sane as _sane
        sane = _sane
    except ImportError:
        pass  # reported by the "init" command below
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        cmd, _, rest = line.partition(" ")
        if sane is None:
            if cmd == "init":
                reply("error the 'sane' Python module is missing (pip install "
                     "python-sane; it needs the system 'sane' package)")
            else:
                reply("error init not called")
            continue
        try:
            if cmd == "init":
                sane.init()
                reply("ok")
            elif cmd == "devices":
                for name, brand, model, _devtype in sane.get_devices():
                    reply(f"dev {name} {brand} {model}")
                reply("end")
            elif cmd == "open":
                dev = sane.open(rest)
                reply("ok")
            elif cmd == "scan":
                res, width, height, quality = (int(x) for x in rest.split())
                run_scan(res, width, height, quality)
            elif cmd == "close":
                close_dev()
                reply("ok")
                return  # the parent waits on the process; exit
            else:
                reply(f"error unknown command {cmd!r}")
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 - delivered to the parent
            reply(f"error {type(e).__name__}: {e}")
    # stdin EOF: the parent is gone; release the scanner's session.
    close_dev()


if __name__ == "__main__":
    main()
