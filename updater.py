"""Check GitHub for a newer Omascan release and install it.

Used by `omascan update` and by the TUI's update notice. The update is
installed the same way Omascan was installed:

- package install (/usr/share/omascan): download the release's Arch package,
  verify it against the release's SHA256SUMS, and install it with pacman;
- script install (~/.local/share/omascan): re-run that release's install.sh.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

REPO = "mattwolfgang/omascan"
APP_DIR = Path(__file__).resolve().parent
PACKAGE_ASSET = "omascan-any.pkg.tar.zst"


def current_version() -> str:
    try:
        return (APP_DIR / "VERSION").read_text().strip()
    except OSError:
        return "0"


def parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.strip().lstrip("v").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def latest_release(timeout: float = 5.0) -> str:
    """Tag of the newest GitHub release, e.g. "v0.3.0". Uses the releases/latest
    redirect rather than the API, so it isn't subject to API rate limits."""
    resp = requests.head(f"https://github.com/{REPO}/releases/latest", allow_redirects=False, timeout=timeout)
    location = resp.headers.get("Location", "")
    if "/releases/tag/" not in location:
        raise RuntimeError(f"Couldn't determine the latest release (HTTP {resp.status_code})")
    return location.rsplit("/", 1)[-1]


def available_update(timeout: float = 5.0) -> str | None:
    """The newer release's tag, or None if this install is up to date."""
    tag = latest_release(timeout)
    return tag if parse_version(tag) > parse_version(current_version()) else None


def install_method() -> str:
    return "package" if str(APP_DIR).startswith("/usr/") else "script"


def _download(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(1 << 16):
                f.write(chunk)


def _update_package(tag: str) -> int:
    base = f"https://github.com/{REPO}/releases/download/{tag}"
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / PACKAGE_ASSET
        print(f"Downloading {PACKAGE_ASSET} ({tag})…", flush=True)
        _download(f"{base}/{PACKAGE_ASSET}", pkg)
        sums = requests.get(f"{base}/SHA256SUMS", timeout=30)
        sums.raise_for_status()
        expected = next((line.split()[0] for line in sums.text.splitlines()
                         if line.strip().endswith(PACKAGE_ASSET)), None)
        actual = hashlib.sha256(pkg.read_bytes()).hexdigest()
        if expected != actual:
            print("Checksum mismatch; not installing.", file=sys.stderr)
            return 1
        print("Installing with pacman (you may be asked for your password)…", flush=True)
        return subprocess.call(["sudo", "pacman", "-U", "--noconfirm", str(pkg)])


def _update_script(tag: str) -> int:
    resp = requests.get(f"https://raw.githubusercontent.com/{REPO}/{tag}/install.sh", timeout=30)
    resp.raise_for_status()
    env = {**os.environ, "OMASCAN_REF": tag, "OMASCAN_METHOD": "script"}
    return subprocess.run(["bash", "-s"], input=resp.text, text=True, env=env).returncode


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    try:
        tag = available_update()
    except (requests.RequestException, RuntimeError) as e:
        print(f"Couldn't check for updates: {e}", file=sys.stderr)
        return 1
    if tag is None:
        print(f"Omascan {current_version()} is up to date.")
        return 0
    print(f"Omascan {tag.lstrip('v')} is available (installed: {current_version()}).", flush=True)
    if check_only:
        return 0
    try:
        code = _update_package(tag) if install_method() == "package" else _update_script(tag)
    except (requests.RequestException, OSError) as e:
        print(f"Update failed: {e}", file=sys.stderr)
        return 1
    if code == 0:
        print(f"Updated to Omascan {tag.lstrip('v')}.")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
