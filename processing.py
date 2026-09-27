"""Scan settings and the client-side post-processing pipeline, shared by the CLI
(scan.py) and the TUI (omascan.py)."""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from PIL import Image

from autocrop import auto_crop, deskew
from colorcorrect import DEFAULT_BLUR_RADIUS, DEFAULT_STRENGTHS, adjust_tone, white_balance
from privet_client import ScannedImage

UNITS_PER_INCH = 1200  # the scanner's width/height unit is 1/1200 inch
MM_PER_INCH = 25.4

# Rotation choices -> degrees counter-clockwise (PIL's convention) for the front
# side. The back side is turned the same amount in the opposite direction:
# flipping a sheet over mirrors its rotation, so a card fed sideways reads
# clockwise-turned on the front and counter-clockwise-turned on the back.
ROTATIONS = {"none": 0, "cw90": -90, "ccw90": 90, "180": 180}
ROTATION_LABELS = {"none": "None", "cw90": "90° clockwise", "ccw90": "90° counter-clockwise", "180": "180°"}


@dataclass
class ScanSettings:
    """Everything that makes up a preset. Defaults are the trading-card settings."""

    resolution: int = 300
    width: int = 5760  # 1/1200 inch
    height: int = 4576  # 1/1200 inch
    jpeg_quality: int = 80
    rotation: str = "cw90"  # a key of ROTATIONS
    deskew: bool = True
    crop: bool = True
    crop_threshold: int = 80
    # Margins kept around the detected card edges, in mm (converted to pixels at
    # the scan's resolution). 0.93/1.86 mm are exactly 11/22 px at 300 dpi, the
    # values tuned against real PaperStream output.
    crop_margin_x_mm: float = 0.93
    crop_margin_y_mm: float = 1.86
    color_correct: bool = True
    strengths: tuple[float, float, float] = DEFAULT_STRENGTHS
    blur: float = DEFAULT_BLUR_RADIUS
    gamma: float = 1.0
    contrast: float = 1.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["strengths"] = list(self.strengths)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ScanSettings":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        # Presets saved before rotation had a direction stored a plain on/off flag.
        if "rotation" not in kwargs and "rotate" in data:
            kwargs["rotation"] = "cw90" if data["rotate"] else "none"
        if kwargs.get("rotation", "cw90") not in ROTATIONS:
            raise ValueError(f"Unknown rotation {kwargs['rotation']!r}")
        # Presets saved before margins were in mm stored pixels at the preset's resolution.
        dpi = data.get("resolution") or cls.resolution
        for axis in ("x", "y"):
            if f"crop_margin_{axis}_mm" not in kwargs and f"crop_margin_{axis}" in data:
                kwargs[f"crop_margin_{axis}_mm"] = round(data[f"crop_margin_{axis}"] / dpi * MM_PER_INCH, 2)
        if "strengths" in kwargs:
            kwargs["strengths"] = tuple(float(s) for s in kwargs["strengths"])
        return cls(**kwargs)

    @property
    def is_passthrough(self) -> bool:
        return (self.rotation == "none" and not self.deskew and not self.crop and not self.color_correct
                and self.gamma == 1.0 and self.contrast == 1.0)


def mm_to_px(mm: float, dpi: int) -> int:
    return round(mm / MM_PER_INCH * dpi)


def process_image(image: ScannedImage, settings: ScanSettings) -> Image.Image:
    picture = Image.open(io.BytesIO(image.data))
    angle = ROTATIONS[settings.rotation]
    if angle:
        # The scanner has no rotation setting of its own, so correct for the feed
        # orientation client-side (trading cards feed sideways, needing 90 degrees
        # clockwise). The back side is seen from the other side of the sheet, so
        # it turns the opposite way -- otherwise it comes out upside down. (For
        # 180 degrees the two directions are the same.)
        if image.source == "back":
            angle = -angle
        picture = picture.rotate(angle, expand=True)
    if settings.deskew:
        # Cards feed slightly crooked mechanically (typically well under a
        # couple of degrees); straighten using the same border-detection
        # approach as the crop, before cropping so there's still margin
        # around the card to detect against.
        picture = deskew(picture, dark_threshold=settings.crop_threshold)
    if settings.crop:
        # The scanner returns a full, overscanned raw frame with no crop of
        # its own (automaticDeskew/cropMargin are disabled at the protocol
        # level, and requestDriverCropDeskew reports "failure"). PaperStream's
        # real output dimensions vary card-to-card (confirmed against actual
        # finished scans), meaning it runs genuine per-card edge detection,
        # not a fixed-size/fixed-position crop -- replicate that here instead.
        picture = auto_crop(
            picture,
            dark_threshold=settings.crop_threshold,
            margin_x=mm_to_px(settings.crop_margin_x_mm, image.resolution or settings.resolution),
            margin_y=mm_to_px(settings.crop_margin_y_mm, image.resolution or settings.resolution),
        )
    if settings.color_correct:
        # No brightness/contrast/gamma TWAIN capability was found to differ
        # between driver profiles, so this is likely a fixed step in iCube's
        # own JPEG pipeline rather than a per-profile setting; replicated
        # empirically instead as a soft-clip brightness curve (see colorcorrect.py).
        picture = white_balance(picture, strengths=tuple(settings.strengths), blur_radius=settings.blur)
    picture = adjust_tone(picture, gamma=settings.gamma, contrast=settings.contrast)
    return picture


def save_image(image: ScannedImage, settings: ScanSettings, filename: Path) -> None:
    if settings.is_passthrough:
        filename.write_bytes(image.data)
    else:
        process_image(image, settings).save(filename, quality=95)


_SHEET_RE = re.compile(r"^sheet(\d+)_(?:front|back)\.jpg$")


def next_sheet_offset(out_dir: Path) -> int:
    """Highest sheet number already saved in `out_dir`, so a second feeder load
    into the same batch folder continues numbering instead of overwriting."""
    if not out_dir.is_dir():
        return 0
    numbers = [int(m.group(1)) for p in out_dir.iterdir() if (m := _SHEET_RE.match(p.name))]
    return max(numbers, default=0)
