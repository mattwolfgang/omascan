#!/usr/bin/env python3
"""CLI for scanning a batch of sheets on the Ricoh/Fujitsu fi-8170 over the network.

Usage:
    python scan.py --host 192.168.1.134 --output ./scans
"""

import argparse
import io
import sys
from pathlib import Path

from PIL import Image

from autocrop import auto_crop, deskew
from colorcorrect import DEFAULT_BLUR_RADIUS, DEFAULT_STRENGTHS, white_balance
from privet_client import PrivetClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.1.134", help="Scanner IP address")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--output", default="./scans", help="Directory to save scanned JPEGs into")
    parser.add_argument("--resolution", type=int, default=300, help="DPI")
    parser.add_argument("--width", type=int, default=5760, help="Scan width in 1/1200 inch units")
    parser.add_argument("--height", type=int, default=4576, help="Scan height in 1/1200 inch units")
    parser.add_argument("--jpeg-quality", type=int, default=80)
    parser.add_argument("--no-rotate", action="store_true",
                         help="Skip the 90-degree correction for the feeder's landscape orientation")
    parser.add_argument("--no-crop", action="store_true",
                         help="Skip cropping to card size; save the full raw (overscanned) image")
    parser.add_argument("--no-deskew", action="store_true",
                         help="Skip straightening minor mechanical feed skew")
    parser.add_argument("--crop-threshold", type=int, default=80,
                         help="Grayscale darkness threshold (0-255) used to detect the card's "
                              "border against the background. Lower = stricter/darker required.")
    parser.add_argument("--crop-margin-x", type=int, default=11,
                         help="Horizontal pixels of margin to keep around the detected card edges.")
    parser.add_argument("--crop-margin-y", type=int, default=22,
                         help="Vertical pixels of margin to keep around the detected card edges.")
    parser.add_argument("--no-color-correct", action="store_true",
                         help="Skip brightness correction; save the scanner's raw dark output")
    parser.add_argument("--color-strength", type=float, nargs=3, metavar=("R", "G", "B"), default=DEFAULT_STRENGTHS,
                         help="Per-channel soft-clip brightness curve steepness (out = 1-exp(-k*in)). "
                              "Tuned against a direct 1:1 comparison of the classic Magic card back "
                              "(identical across nearly every card) between a raw scan and a real "
                              "PaperStream scan. Behaves close to linear (gentle) near black, avoiding "
                              "gamma's amplification of fine border detail into pixelation, but rolls "
                              "off smoothly near white instead of clipping hard like a plain linear gain "
                              "would (which blew out rules-text-box highlights).")
    parser.add_argument("--color-blur", type=float, default=DEFAULT_BLUR_RADIUS,
                         help="Gaussian blur radius applied before brightening, to soften fine "
                              "compression/texture detail in the scan before it gets amplified. "
                              "Set to 0 to disable.")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = PrivetClient(args.host, args.port)

    info = client.get_info()
    print(f"Connected to {info.get('manufacturer')} {info.get('model')} "
          f"(serial {info.get('serialNumber')}, firmware {info.get('firmwareVersion')})")

    count = 0
    for image in client.scan(
        resolution=args.resolution, width=args.width, height=args.height, jpeg_quality=args.jpeg_quality
    ):
        filename = out_dir / f"sheet{image.sheet_number:03d}_{image.source}.jpg"
        if args.no_rotate and args.no_crop and args.no_deskew and args.no_color_correct:
            filename.write_bytes(image.data)
        else:
            picture = Image.open(io.BytesIO(image.data))
            if not args.no_rotate:
                # The card feeds through the ADF sideways ("Horizontal Trading Cards"
                # profile); the scanner has no rotation setting of its own, so correct
                # for it client-side the same way PaperStream does downstream. The
                # duplex back sensor is mounted opposite the front one along the feed
                # path, so its raw image is 180 degrees off from the front's -- rotate
                # the other way to compensate, instead of upside down.
                angle = 90 if image.source == "back" else -90
                picture = picture.rotate(angle, expand=True)
            if not args.no_deskew:
                # Cards feed slightly crooked mechanically (typically well under a
                # couple of degrees); straighten using the same border-detection
                # approach as the crop, before cropping so there's still margin
                # around the card to detect against.
                picture = deskew(picture, dark_threshold=args.crop_threshold)
            if not args.no_crop:
                # The scanner returns a full, overscanned raw frame with no crop of
                # its own (automaticDeskew/cropMargin are disabled at the protocol
                # level, and requestDriverCropDeskew reports "failure"). PaperStream's
                # real output dimensions vary card-to-card (confirmed against actual
                # finished scans), meaning it runs genuine per-card edge detection,
                # not a fixed-size/fixed-position crop -- replicate that here instead.
                picture = auto_crop(
                    picture,
                    dark_threshold=args.crop_threshold,
                    margin_x=args.crop_margin_x,
                    margin_y=args.crop_margin_y,
                )
            if not args.no_color_correct:
                # No brightness/contrast/gamma TWAIN capability was found to differ
                # between driver profiles, so this is likely a fixed step in iCube's
                # own JPEG pipeline rather than a per-profile setting; replicated
                # empirically instead as a soft-clip brightness curve (see colorcorrect.py).
                picture = white_balance(picture, strengths=tuple(args.color_strength), blur_radius=args.color_blur)
            picture.save(filename, quality=95)
        print(f"Saved {filename} ({image.pixel_width}x{image.pixel_height} @ {image.resolution}dpi, "
              f"{image.size} bytes)")
        count += 1

    print(f"Done. {count} image(s) saved to {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
