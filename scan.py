#!/usr/bin/env python3
"""Command-line scanner for the Ricoh/Fujitsu fi-8170 over USB or the network.

Starts from a preset (Letter by default; built-ins plus any saved in the
Omascan TUI) and applies any flags given on top of it.

Usage:
    omascan scan --output ./scans
    omascan scan --preset "Trading cards (TCG)" --output ./mtg_batch_1
    python scan.py --transport usb --output ./scans
    python scan.py --host 192.0.2.10 --output ./scans
"""

import argparse
import signal
import sys
from dataclasses import replace
from pathlib import Path

from config import DEFAULT_PRESET_NAME, AppConfig
from discovery import find_scanners, find_usb_scanner
from privet_client import PrivetClient
from processing import ScanSettings, save_image
from sane_client import SaneClient, SaneError, usb_scanner_present

ROTATE_ARGS = {"cw": "cw90", "ccw": "ccw90", "180": "180", "none": "none"}


def build_parser(presets: list[str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", help="Scanner address: an IP (network) or usb:<device name> "
                                      "(USB). Wins over --transport and the remembered address.")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--output", default="./scans", help="Directory to save scanned JPEGs into")
    parser.add_argument("--transport", choices=["auto", "usb", "network"], default="auto",
                        help="How to reach the scanner. 'usb' uses SANE, 'network' the "
                             "scanner's HTTP protocol. 'auto' (default) prefers a "
                             "USB-attached scanner and falls back to the network.")
    parser.add_argument("--preset", default=DEFAULT_PRESET_NAME, metavar="NAME",
                        help=f"Preset to start from. Available: {', '.join(presets)}. "
                             f"Default: {DEFAULT_PRESET_NAME}")
    parser.add_argument("--list-presets", action="store_true", help="Print the available presets and exit")

    # Everything below defaults to None = "use the preset's value".
    parser.add_argument("--resolution", type=int, help="DPI")
    parser.add_argument("--width", type=int, help="Scan width in 1/1200 inch units")
    parser.add_argument("--height", type=int, help="Scan height in 1/1200 inch units")
    parser.add_argument("--jpeg-quality", type=int)
    parser.add_argument("--rotate", choices=list(ROTATE_ARGS),
                        help="Rotation applied to each front image (backs turn the opposite way)")
    parser.add_argument("--no-rotate", action="store_true", help="Same as --rotate none")
    parser.add_argument("--crop", action=argparse.BooleanOptionalAction,
                        help="Crop to the detected card edges (--no-crop saves the full raw frame)")
    parser.add_argument("--deskew", action=argparse.BooleanOptionalAction,
                        help="Straighten minor mechanical feed skew")
    parser.add_argument("--crop-threshold", type=int,
                        help="Grayscale darkness threshold (0-255) used to detect the card's border "
                             "against the background. Lower = stricter/darker required.")
    parser.add_argument("--crop-margin-x", type=float, metavar="MM",
                        help="Horizontal margin (mm) to keep around the detected card edges.")
    parser.add_argument("--crop-margin-y", type=float, metavar="MM",
                        help="Vertical margin (mm) to keep around the detected card edges.")
    parser.add_argument("--color-correct", action=argparse.BooleanOptionalAction,
                        help="Brightness correction (--no-color-correct saves the scanner's raw dark output)")
    parser.add_argument("--color-strength", type=float, nargs=3, metavar=("R", "G", "B"),
                        help="Per-channel soft-clip brightness curve steepness (out = 1-exp(-k*in)). "
                             "Higher = brighter; see colorcorrect.py.")
    parser.add_argument("--color-blur", type=float,
                        help="Gaussian blur radius applied before brightening. 0 disables.")
    parser.add_argument("--gamma", type=float,
                        help="Extra gamma applied after color correction (>1 brightens midtones). 1.0 = off.")
    parser.add_argument("--contrast", type=float,
                        help="Extra contrast applied after color correction, scaled around mid-gray. 1.0 = off.")
    return parser


def settings_from_args(base: ScanSettings, args: argparse.Namespace) -> ScanSettings:
    overrides = {
        "resolution": args.resolution,
        "width": args.width,
        "height": args.height,
        "jpeg_quality": args.jpeg_quality,
        "rotation": "none" if args.no_rotate else ROTATE_ARGS.get(args.rotate),
        "crop": args.crop,
        "deskew": args.deskew,
        "crop_threshold": args.crop_threshold,
        "crop_margin_x_mm": args.crop_margin_x,
        "crop_margin_y_mm": args.crop_margin_y,
        "color_correct": args.color_correct,
        "strengths": tuple(args.color_strength) if args.color_strength else None,
        "blur": args.color_blur,
        "gamma": args.gamma,
        "contrast": args.contrast,
    }
    return replace(base, **{k: v for k, v in overrides.items() if v is not None})


def resolve_host(args: argparse.Namespace, config: AppConfig) -> str | None:
    if args.host:
        return args.host
    # A remembered "usb:" address is not a network host; fall through to the
    # search so --transport auto/network can still find a USB-connected scanner
    # that the TUI was last used on.
    if config.host and not config.host.startswith("usb:"):
        return config.host
    print("No scanner address given or remembered; searching the local network…")
    found = find_scanners(port=args.port)
    if not found:
        return None
    scanner = found[0]
    print(f"Found {scanner.manufacturer} {scanner.model} at {scanner.host}")
    return scanner.host


def resolve_usb(args: argparse.Namespace, config: AppConfig,
               explicit: str | None = None) -> SaneClient | None:
    """The USB scanner: an explicitly requested device name, a name remembered
    from the TUI, or a fresh discovery. Returns None when no usable USB
    scanner is attached."""
    name = explicit
    if name is None:
        name = config.host.removeprefix("usb:") if config.host.startswith("usb:") else None
    if not name:
        if not usb_scanner_present():
            return None
        print("USB fi-series scanner attached; waiting for it to answer SANE…")
        try:
            scanner = find_usb_scanner()
        except SaneError as e:
            print(f"USB scanner unavailable: {e}", file=sys.stderr)
            return None
        if scanner is None:
            return None
        name = scanner.host
    try:
        return SaneClient(name)
    except SaneError as e:
        print(f"USB scanner unavailable: {e}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001 - python-sane's C error type
        print(f"USB scanner unavailable: {e}", file=sys.stderr)
        return None


def resolve_target(args: argparse.Namespace, config: AppConfig) -> tuple[SaneClient | PrivetClient, str] | None:
    if args.host:
        # An explicit --host wins over the transport default and the remembered
        # address; its usb: prefix selects the transport.
        if args.host.startswith("usb:"):
            if args.transport == "network":
                print("--host is a USB device but --transport is network.", file=sys.stderr)
                return None
            client = resolve_usb(args, config, args.host.removeprefix("usb:"))
            return (client, client.device_name) if client else None
        if args.transport == "usb":
            print("--host is a network address but --transport is usb.", file=sys.stderr)
            return None
        return (PrivetClient(args.host, args.port), args.host)
    if args.transport == "network":
        host = resolve_host(args, config)
        return (PrivetClient(host, args.port), host) if host else None
    if args.transport == "usb":
        client = resolve_usb(args, config)
        return (client, client.device_name) if client else None
    # auto: USB first when the scanner is physically attached, then the network.
    if usb_scanner_present():
        client = resolve_usb(args, config)
        if client:
            return client, client.device_name
        print("USB scanner not ready; trying the network.", file=sys.stderr)
    host = resolve_host(args, config)
    return (PrivetClient(host, args.port), host) if host else None


def _shutdown(signum, frame) -> None:
    raise SystemExit(130)


def main() -> int:
    config = AppConfig.load()
    presets = config.presets
    args = build_parser(list(presets)).parse_args()

    if args.list_presets:
        for name in presets:
            print(name)
        return 0
    if args.preset not in presets:
        print(f"Unknown preset {args.preset!r}. Available: {', '.join(presets)}", file=sys.stderr)
        return 2
    settings = settings_from_args(presets[args.preset], args)

    # Raising on a signal unwinds the scan loop, which closes the client's
    # generator and releases the scanner's session instead of leaving it
    # orphaned (a SIGTERM'd process would otherwise leave the scanner busy).
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    target = resolve_target(args, config)
    if not target:
        print("No fi-8170 found (USB or network). Pass --host <scanner-ip> or attach it over USB.",
              file=sys.stderr)
        return 1
    client, _ = target

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        info = client.get_info()
        print(f"Connected to {info.get('manufacturer')} {info.get('model')} "
              f"(serial {info.get('serialNumber')}, firmware {info.get('firmwareVersion')})")
        print(f"Preset: {args.preset}")

        count = 0
        for image in client.scan(
            resolution=settings.resolution, width=settings.width, height=settings.height,
            jpeg_quality=settings.jpeg_quality,
        ):
            filename = out_dir / f"sheet{image.sheet_number:03d}_{image.source}.jpg"
            save_image(image, settings, filename)
            print(f"Saved {filename} ({image.pixel_width}x{image.pixel_height} @ {image.resolution}dpi, "
                  f"{image.size} bytes)")
            count += 1
    except Exception as e:  # noqa: BLE001 - scanner/protocol errors go to the console cleanly
        print(f"Scan failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"Done. {count} image(s) saved to {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
