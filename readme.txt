fi-8170 Network Scanner Tool
============================

A Linux command-line tool for driving a Ricoh/Fujitsu fi-8170 high-speed
document scanner directly over the network, without any vendor driver or
Windows software.

The fi-8170 does not speak any standard network scanning protocol (no
eSCL/AirScan, no WSD). It uses a proprietary JSON-over-HTTP protocol derived
from Google's old "Privet" local-device protocol, reverse-engineered from a
packet capture of the vendor's PaperStream Capture software. This tool
(scan.py / privet_client.py) reimplements that protocol directly.


Requirements
------------
- Python 3.10+
- pip install -r requirements.txt   (requests, Pillow, numpy, opencv-python-headless)
- The scanner reachable on your network over plain HTTP (port 80)


Basic usage
-----------
1. Load sheets/cards into the scanner's automatic document feeder.
2. Run:

       python scan.py --host <scanner-ip> --output ./scans

3. Scanned pages are saved as JPEGs in the output directory, one file per
   side, named:

       sheet001_front.jpg
       sheet001_back.jpg
       sheet002_front.jpg
       sheet002_back.jpg
       ...

   Sheets are numbered in feed order starting at 1. The scanner always
   returns both sides per sheet (duplex), even for single-sided originals.


Command-line flags
-------------------
--host HOST
    IP address (or hostname) of the scanner.
    Default: 192.168.1.134

--port PORT
    TCP port the scanner's HTTP interface listens on. There should be no
    reason to change this.
    Default: 80

--output DIR
    Directory to save scanned JPEGs into. Created automatically if it
    doesn't exist.
    Default: ./scans

--resolution DPI
    Scan resolution in dots per inch.
    Default: 300

--width UNITS
--height UNITS
    Scan area size, in 1/1200 inch units (i.e. multiply inches by 1200).
    The defaults (5760 x 4576 = 4.8" x 3.81") match the scanner's built-in
    "Horizontal Trading Cards" driver profile, sized for scanning
    trading cards fed sideways through the ADF.
    Defaults: --width 5760 --height 4576

--jpeg-quality N
    JPEG quality the scanner itself encodes images at (device-side
    setting, 0-100 scale).
    Default: 80

--no-rotate
    The scanner feeds cards sideways (landscape) with no rotation setting
    of its own, so by default this tool rotates every image 90 degrees
    to produce upright, correctly-oriented output (matching what PaperStream
    Capture produces on Windows). The duplex back sensor is mounted opposite
    the front one along the feed path, so back-side images are rotated the
    other way (90 degrees the opposite direction) to compensate -- otherwise
    they come out upside down. Pass --no-rotate to skip this and save the
    raw sideways images instead.

--no-crop
    The scanner returns a full, overscanned raw frame with no crop of its
    own (this is confirmed at the protocol level: automaticDeskew and
    cropMargin are both disabled/zero in the scan task, and the image
    metadata's requestDriverCropDeskew field reports "failure"). By default
    this tool crops each image down to the card using content-based edge
    detection (finding the card's dark border against the background),
    tuned to match PaperStream Capture's real output as closely as possible
    (dimensions vary slightly card-to-card, same as PaperStream's own
    output does, since it's genuine per-card detection rather than a fixed
    rectangle). Pass --no-crop to skip this and save the full raw frame.

--crop-threshold N
    Grayscale darkness threshold (0-255) used to detect the card's border
    against the background. Lower = only very dark (e.g. black-bordered)
    edges count; raise this if a card's border isn't being detected.
    Default: 80

--crop-margin-x N
--crop-margin-y N
    Extra pixels of margin kept around the detected card edges, in each
    direction. Tuned against real finished PaperStream scans; the vertical
    margin is larger because the raw dark-pixel edge detection consistently
    undershoots more on that axis (likely anti-aliased/lighter pixels right
    at the card's top and bottom edges).
    Defaults: --crop-margin-x 11 --crop-margin-y 22

--no-deskew
    Cards feed slightly crooked mechanically (typically well under a couple
    of degrees, but visible). No deskew-related setting was found in the
    driver profile, so this is likely a side effect of PaperStream's own
    adaptive edge detection rather than a separate step. By default this
    tool detects the card's rotation angle from its border (via OpenCV's
    minAreaRect) and straightens it before cropping. Pass --no-deskew to
    skip this.

--no-color-correct
    The scanner's raw JPEGs come back noticeably dark compared to
    PaperStream's real output. No brightness/contrast/gamma setting was
    found to differ between driver profiles, so this is likely a fixed step
    in the vendor's own JPEG development pipeline rather than a per-profile
    setting (a PaperStream settings screenshot showed a "Tone Adjustment:
    Bright" / "Color Adjustment: Prioritize Contrast" preset pair and "sRGB
    Output: Off", consistent with this, but PaperStream doesn't expose
    numeric values behind those presets).

    By default this tool corrects for it with a per-channel soft-clip
    brightness curve (out = 1 - exp(-k * in), not gamma or a plain linear
    gain), calibrated by scanning the exact same physical batch of cards
    through both this tool and PaperStream Capture directly and comparing
    the results pixel-for-pixel. Two other approaches were tried and
    rejected first: gamma correction matched brightness well but amplified
    fine real texture in the card border (confirmed not a compression
    artifact, since much higher device JPEG quality barely changed it) into
    visible speckled pixelation, because gamma has a very steep slope near
    black; a plain linear gain fixed that but blew out rules-text-box
    highlights to flat white with a hard clip. The soft-clip curve behaves
    gently near black (avoiding the pixelation) while rolling off smoothly
    near white (avoiding the blown-out clipping) -- see colorcorrect.py for
    the full story. Pass --no-color-correct to save the raw dark colors
    instead.

--color-strength R G B
    Per-channel soft-clip curve steepness (three numbers). Higher values
    brighten that channel further; the curve naturally avoids hard clipping
    regardless of value.
    Default: 3.08 3.14 3.14

--color-blur RADIUS
    Gaussian blur radius applied to the whole image before brightening, to
    soften fine compression/texture detail before the brightness gain
    amplifies it into visible pixelation. Small enough to keep text and real
    edges crisp. Set to 0 to disable.
    Default: 0.8


Examples
--------
Scan with defaults into ./scans:

    python scan.py

Scan a specific scanner IP into a named folder:

    python scan.py --host 192.168.1.134 --output ./mtg_batch_1

Scan full-size letter documents instead of trading cards (8.5" x 11" at
300 dpi = 10200 x 13200 in 1/1200" units), without rotation or cropping:

    python scan.py --width 10200 --height 13200 --no-rotate --no-crop --output ./docs


How it works (for reference)
-----------------------------
Files:
    privet_client.py   - the Privet protocol client (device communication)
    scan.py             - CLI, orchestrates a scan and post-processing
    autocrop.py         - content-based crop and deskew (OpenCV/numpy)
    colorcorrect.py     - white-balance/brightness correction (numpy)

privet_client.py implements the scanner's HTTP API directly:

    GET  /api/privet/info            - device identity/status
    POST /api/privet/session         - all commands (JSON body, keyed by
                                        "method"): createSession, sendTask,
                                        startCapturing, readImageBlock,
                                        releaseImageBlocks, stopCapturing,
                                        closeSession
    GET  /image/image_N.jpeg         - raw JPEG bytes for a scanned page

A single persistent HTTP connection is used for the whole session, matching
the behavior of the vendor software. There is no discovery protocol
involved (no mDNS/SSDP/WS-Discovery) - you must know the scanner's IP
address.
