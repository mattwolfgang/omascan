"""Color/brightness correction for scanned trading cards.

The raw JPEGs from the scanner come back noticeably dark compared to
PaperStream Capture's real output. Calibrated using the best possible ground
truth: the exact same physical batch of cards was run through PaperStream
Capture directly, so both front and back of several cards could be compared
pixel-mean-for-pixel-mean against our own raw scan of the literal same card. An earlier pass used a weaker proxy (a different
PaperStream session's output of the universal Magic card back art, which is
identical across nearly every card) before this direct same-batch data was
available -- that gave a noticeably brighter target than PaperStream
actually produces, since apparently its output brightness isn't perfectly
fixed run to run.

Went through two wrong turns before landing here:
1. Per-channel gamma (out = in^(1/gamma)) reproduced PaperStream's brightness
   almost exactly, but visibly amplified fine detail already present in the
   card border (real texture in the print, confirmed by testing much higher
   device JPEG quality with little change -- not a compression artifact)
   into jarring speckled pixelation. Gamma has a very steep slope near black
   (its derivative approaches infinity as the pixel value approaches 0), so
   it disproportionately stretches small dark-pixel variations.
2. Switching to a pure linear gain (out = in * gain) fixed the pixelation
   (constant slope everywhere, no disproportionate amplification near black)
   but blew out highlights: card fronts have real near-white content (rules
   text box backgrounds) that a linear gain pushes past 255 with a hard
   clip, losing all texture/gradient there (confirmed: ~78% of one such
   region's red channel was fully clipped to 255).

Fixed with a soft-clip exponential curve: out = 255*(1 - exp(-k*in/255)).
Its derivative at in=0 is a finite, moderate `k` (avoiding gamma's near-black
blowup, so no pixelation), but it saturates smoothly toward 255 rather than
clipping hard (avoiding the linear gain's blown-out flat whites) -- the best
of both. A small pre-brightening blur is still applied first to soften fine
compression/texture detail before the curve amplifies it.

No brightness/contrast/gamma TWAIN capability was found to differ between
driver profiles ("006: Horizontal Trading Cards" vs "002: Color Default" vs
"004: Trading Cards"), so the darkness itself is a fixed step in the
vendor's own iCube JPEG development pipeline, not a per-profile setting.
A PaperStream settings screenshot showed a "Tone Adjustment: Bright" /
"Color Adjustment: Prioritize Contrast" preset pair and "sRGB Output: Off"
-- consistent with the raw data not being in a display-ready color space --
but PaperStream doesn't expose numeric values for those presets, so this
remains an empirical fit rather than an exact reproduction of its curve.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

DEFAULT_STRENGTHS = (3.08, 3.14, 3.14)  # R, G, B -- soft-clip curve steepness
DEFAULT_BLUR_RADIUS = 0.8


def white_balance(
    image: Image.Image,
    strengths: tuple[float, float, float] = DEFAULT_STRENGTHS,
    blur_radius: float = DEFAULT_BLUR_RADIUS,
) -> Image.Image:
    if blur_radius > 0:
        image = image.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    arr = np.asarray(image.convert("RGB")).astype(np.float64) / 255.0
    out = np.empty_like(arr)
    for c in range(3):
        out[:, :, c] = 1.0 - np.exp(-strengths[c] * arr[:, :, c])
    return Image.fromarray(np.clip(out * 255.0, 0, 255).astype(np.uint8), mode="RGB")


def adjust_tone(image: Image.Image, gamma: float = 1.0, contrast: float = 1.0) -> Image.Image:
    """Optional user-facing tone tweaks applied after the brightness curve.

    gamma > 1 brightens midtones (out = in^(1/gamma)); contrast scales around
    mid-gray (out = (in - 0.5) * contrast + 0.5). Both default to 1.0, which is
    a no-op -- the trading-card preset relies on white_balance() alone, since
    gamma near black amplifies border texture into pixelation (see above).
    """
    if gamma == 1.0 and contrast == 1.0:
        return image
    x = np.arange(256, dtype=np.float64) / 255.0
    if gamma != 1.0:
        x = np.power(x, 1.0 / gamma)
    if contrast != 1.0:
        x = (x - 0.5) * contrast + 0.5
    lut = np.clip(np.round(x * 255.0), 0, 255).astype(np.uint8).tolist()
    image = image.convert("RGB")
    return image.point(lut * 3)
