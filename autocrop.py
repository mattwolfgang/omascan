"""Content-based crop for scanned trading cards.

PaperStream Capture's own output (confirmed by comparing real finished scans
against our raw device output) is NOT a fixed-size
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


def _extend_over_white_border(profile: np.ndarray, probe: int = 4, step: int = 4, min_drop: float = 12.0) -> int:
    """`profile` is the median brightness of each row/column walking outward from
    one side of the dark bounding box. Returns how many pixels of bright card
    border lie past that side before the background starts, or 0 if no card edge
    is found within the profile.

    The card's edge is a sharp step down (white card -> background) within a
    few pixels. The background itself is gradient-lit and varies by 20+ levels
    across the frame, but only gradually, so a black-bordered card (whose dark
    box already ends at the edge, going straight into shadow/background) sees
    no step and isn't extended."""
    if len(profile) <= probe + step:
        return 0
    border = float(np.median(profile[probe:probe + 3]))
    for i in range(probe + step, len(profile)):
        if profile[i - step] - profile[i] >= min_drop and border - profile[i] >= min_drop:
            # Card ends halfway down the step.
            edge = i - step + int(np.argmax(profile[i - step:i + 1] < (border + profile[i]) / 2))
            return edge
    return 0


def detect_card_bbox(
    image: Image.Image, dark_threshold: int = 80, margin_x: int = 11, margin_y: int = 22,
    max_border_x: int = 0, max_border_y: int = 0,
) -> tuple[int, int, int, int]:
    """Returns a (left, top, right, bottom) box tightly bounding the card's dark
    border/content, expanded by `margin_x`/`margin_y` pixels, clamped to the image
    bounds. Falls back to the full image if nothing dark enough is found.

    On a white-bordered card the darkest pixels are the inner frame line, not
    the card's edge, so each side of the dark box is then pushed outward across
    the bright border to where it steps down sharply to background -- up to
    `max_border_x`/`max_border_y` pixels (0 disables this). A side with no such
    step within that distance is left where it was.

    Margins default to asymmetric values (rather than one fixed margin) because
    the raw dark-pixel bounding box consistently undershoots real PaperStream
    output more on the vertical axis than the horizontal one -- likely due to
    anti-aliased/lighter border pixels right at the card's top and bottom edges
    -- tuned empirically against real finished PaperStream scans.
    """
    gray = np.asarray(image.convert("L"))
    dark = gray < dark_threshold
    ys, xs = np.where(dark)
    if len(xs) == 0 or len(ys) == 0:
        return (0, 0, image.width, image.height)

    left, right = int(xs.min()), int(xs.max())
    top, bottom = int(ys.min()), int(ys.max())

    if max_border_x or max_border_y:
        rows, cols = gray[top:bottom + 1], gray[:, left:right + 1]
        reach_x, reach_y = max_border_x + 1, max_border_y + 1
        left_ext = _extend_over_white_border(
            np.median(rows[:, max(left - reach_x, 0):left][:, ::-1], axis=0))
        right_ext = _extend_over_white_border(
            np.median(rows[:, right + 1:right + 1 + reach_x], axis=0))
        top_ext = _extend_over_white_border(
            np.median(cols[max(top - reach_y, 0):top][::-1], axis=1))
        bottom_ext = _extend_over_white_border(
            np.median(cols[bottom + 1:bottom + 1 + reach_y], axis=1))
        left, right = left - left_ext, right + right_ext
        top, bottom = top - top_ext, bottom + bottom_ext

    left = max(left - margin_x, 0)
    top = max(top - margin_y, 0)
    right = min(right + margin_x, image.width)
    bottom = min(bottom + margin_y, image.height)
    return (left, top, right, bottom)


def auto_crop(
    image: Image.Image, dark_threshold: int = 80, margin_x: int = 11, margin_y: int = 22,
    max_border_x: int = 0, max_border_y: int = 0,
) -> Image.Image:
    box = detect_card_bbox(image, dark_threshold=dark_threshold, margin_x=margin_x, margin_y=margin_y,
                           max_border_x=max_border_x, max_border_y=max_border_y)
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
