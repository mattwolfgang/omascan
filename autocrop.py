"""Content-based crop for scanned trading cards.

PaperStream Capture's own output (confirmed by comparing real finished scans
in /var/mnt/Data/scans/TCGP7/ against our raw device output) is NOT a fixed-size
or fixed-position crop -- output dimensions vary card-to-card (~786-795 x
1085-1091px at 300dpi for a nominal 2.5x3.5in card), meaning it's doing real
per-scan edge detection against the background, not applying a static rectangle.

This replicates that by finding the bounding box of the card's dark border
against the (light gray, gradient-lit) background, then adding a small margin
to match the little bit of border PaperStream's own output keeps.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def detect_card_bbox(
    image: Image.Image, dark_threshold: int = 80, margin_x: int = 11, margin_y: int = 22
) -> tuple[int, int, int, int]:
    """Returns a (left, top, right, bottom) box tightly bounding the card's dark
    border/content, expanded by `margin_x`/`margin_y` pixels, clamped to the image
    bounds. Falls back to the full image if nothing dark enough is found (e.g. a
    white-bordered card against a similarly bright background).

    Margins default to asymmetric values (rather than one fixed margin) because
    the raw dark-pixel bounding box consistently undershoots real PaperStream
    output more on the vertical axis than the horizontal one -- likely due to
    anti-aliased/lighter border pixels right at the card's top and bottom edges
    -- tuned empirically against real finished scans in /var/mnt/Data/scans/.
    """
    gray = np.asarray(image.convert("L"))
    dark = gray < dark_threshold
    ys, xs = np.where(dark)
    if len(xs) == 0 or len(ys) == 0:
        return (0, 0, image.width, image.height)

    left = max(int(xs.min()) - margin_x, 0)
    top = max(int(ys.min()) - margin_y, 0)
    right = min(int(xs.max()) + margin_x, image.width)
    bottom = min(int(ys.max()) + margin_y, image.height)
    return (left, top, right, bottom)


def auto_crop(image: Image.Image, dark_threshold: int = 80, margin_x: int = 11, margin_y: int = 22) -> Image.Image:
    box = detect_card_bbox(image, dark_threshold=dark_threshold, margin_x=margin_x, margin_y=margin_y)
    return image.crop(box)


def detect_skew_angle(image: Image.Image, dark_threshold: int = 80) -> float:
    """Returns the angle (degrees) the card's border is rotated off-axis, in the
    range (-45, 45]. Positive means rotating the image by this same angle
    (counter-clockwise, PIL's convention) straightens it."""
    gray = np.asarray(image.convert("L"))
    mask = (gray < dark_threshold).astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < 1000:
        return 0.0
    angle = cv2.minAreaRect(largest)[-1] % 90
    if angle > 45:
        angle -= 90
    return float(angle)


def deskew(image: Image.Image, dark_threshold: int = 80, max_angle: float = 10.0) -> Image.Image:
    """Straightens minor mechanical feed skew (typically well under a couple of
    degrees) detected from the card's border, the same way PaperStream's own
    output is straightened. Angles beyond `max_angle` are assumed to be
    detection noise (e.g. no card present) and left alone."""
    angle = detect_skew_angle(image, dark_threshold=dark_threshold)
    if abs(angle) < 0.05 or abs(angle) > max_angle:
        return image
    fill = (255, 255, 255) if image.mode == "RGB" else 255
    return image.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=fill)
