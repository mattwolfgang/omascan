<p align="center"><img src="assets/omascan.png" width="96" alt=""></p>

```
                ▄▄▄
 ▄█████▄   ▄███████████▄    ▄███████  ▄████████  ▄███████    ▄███████ ███▄▄▄▄
███   ███ ███   ███   ███  ███   ███ ███▄▄▄▄    ███   █▀    ███   ███ ███▀▀▀██▄
███   ███ ███   ███   ███ ▄███▄▄▄███  ▀▀▀▀▀███▄ ███        ▄███▄▄▄███ ███   ███
███   ███ ███   ███   ███ ▀███▀▀▀███ ▄█    ███  ███   █▄   ▀███▀▀▀███ ███   ███
 ▀█████▀   ▀█   ███   █▀   ███   █▀  ▀██████▀    ▀██████▀   ███   █▀   ▀█   █▀
```

# Omascan

A Linux terminal app for scanning with a **Ricoh/Fujitsu fi-8170** document
scanner over the network. No vendor driver, no Windows, no PaperStream.

- A full-screen TUI: pick the scanner, save folder, batch name and settings,
  then press **F5** to scan.
- **Finds the scanner on your network** for you.
- **Presets** for Letter, Legal, A4 and trading cards (e.g. Magic: The
  Gathering, fed sideways and auto-cropped/straightened), plus your own.
- Remembers your scanner, save folder and last preset between sessions.
- Built for [Omarchy](https://omarchy.org): matches your Omarchy theme (and
  follows along live when you switch themes), and adds itself to the app
  launcher and the Omarchy menu. Runs on any Linux with Python 3.10+.
- A command-line mode (`omascan scan`) for scripting.

The fi-8170 doesn't speak any standard network scanning protocol (no
eSCL/AirScan, no WSD, no SANE backend over the network). Omascan talks to it
directly using a proprietary JSON-over-HTTP protocol, reverse-engineered from
a packet capture of the vendor's software. See [How it works](#how-it-works).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/mattwolfgang/omascan/main/install.sh | bash
```

That's it. Then run `omascan`, or open it from the app launcher (**Super +
Space** on Omarchy) or the Omarchy menu.

**Update:** `omascan update`. Omascan also tells you when a new version is
out.

What the installer does:

- **On Omarchy / Arch Linux** it installs the latest release's pacman package
  (after checking its checksum), so the dependencies come from the official Arch
  repos. It asks for your password for that step. On Omarchy it also adds an
  **Omascan** row to the Omarchy menu.
- **On other Linux distributions** it installs Omascan into
  `~/.local/share/omascan` with a private Python environment (nothing is
  installed system-wide), and adds the `omascan` command to `~/.local/bin`
  (adding that folder to your `PATH` if needed) and an app-launcher entry.
  This needs Python 3.10 or newer with the `venv` module (on Debian/Ubuntu,
  `sudo apt install python3-venv`).

Omascan needs the scanner reachable on your network over plain HTTP (port 80).

**Uninstall:**

- Arch package: `omascan menu remove` (if the Omarchy menu row was added),
  then `sudo pacman -R omascan`.
- Otherwise: `omascan uninstall` (add `--purge` to also delete your settings
  and presets).

Your settings are kept unless you purge them, and scanned images are never
touched.

<details>
<summary><b>Other ways to install</b></summary>

**From a clone** (e.g. to try changes): `./install.sh` installs that copy of
the code into `~/.local/share/omascan`, on any distribution.

**The Arch package by hand:** every release has `omascan-any.pkg.tar.zst`
attached (with `SHA256SUMS`):

```bash
curl -fLO https://github.com/mattwolfgang/omascan/releases/latest/download/omascan-any.pkg.tar.zst
sudo pacman -U omascan-any.pkg.tar.zst
omascan menu add    # optional: add Omascan to the Omarchy menu
```

The package isn't signed, which is why it's downloaded first rather than
installed straight from the URL.

**Choosing the method:** set `OMASCAN_METHOD=package` or `OMASCAN_METHOD=script`
before running the install command to override the automatic choice, and
`OMASCAN_REF=v0.3.0` to install a specific release.

</details>

## Using Omascan

Launch it from the app launcher, the Omarchy menu, or a terminal:

```bash
omascan
```

1. **Scanner.** On first launch Omascan searches your local network for the
   fi-8170 and fills in its IP address. Press **Find** (F6) to search again,
   or type an address.
2. **Output.** Choose a **save path** (type it or **Browse**) and a **batch
   name**. The batch name becomes a subfolder of the save path. The full
   destination is shown underneath.
3. **Preset.** Pick a preset, adjust any settings, and optionally **Save as…**
   a new preset.
4. Load the feeder and press **Scan** (F5). Press **Stop** (Esc) to stop after
   the current sheet.

Pages are saved as JPEGs, one per side:

```
sheet001_front.jpg
sheet001_back.jpg
sheet002_front.jpg
...
```

The scanner always scans both sides (duplex). Scanning into a batch folder that
already has sheets continues the numbering instead of overwriting, so you can
feed a big batch in several loads.

Omascan remembers the scanner address, save path, last-used preset and your
presets in `~/.config/omascan/config.json`. At startup it also checks GitHub for
a newer release (set `"check_for_updates": false` in that file to turn this
off).

### Settings

| Setting | What it does |
|---|---|
| Resolution | Scan resolution in dpi. |
| Width / Height | Scan area in inches. The fi-8170's feeder is at most 8.5" wide. |
| JPEG quality | Quality the scanner itself encodes images at (1-100). |
| Rotate | None, 90° clockwise, 90° counter-clockwise or 180°. Applies to the front of each sheet; backs turn the same amount the opposite way, since they're seen from the other side of the sheet. |
| Deskew | Straightens slightly crooked cards, detected from the card's dark border. |
| Auto-crop | Crops to the card's detected edges. |
| Crop threshold | How dark (0-255) a pixel must be to count as card border. Raise it if a card's border isn't detected. |
| Margin X / Y (mm) | Extra space kept around the detected card edges, in millimeters. |
| Brightness + R G B | Brightness correction; the scanner's raw output is dark. Higher numbers are brighter, per color channel. |
| Pre-blur | Slight blur before brightening, to stop fine texture turning into speckles. 0 turns it off. |
| Gamma / Contrast | Optional extra tone adjustments. 1.0 is off. Gamma above 1 brightens midtones. |

### Built-in presets

| Preset | Size | Notes |
|---|---|---|
| **Letter** (default) | 8.5 × 11 in | Full-page documents fed portrait: no rotation, deskew or crop, no pre-blur, JPEG quality 85, brightness correction on. |
| **Legal** | 8.5 × 14 in | Same as Letter. |
| **A4** | 210 × 297 mm | Same as Letter. |
| **Trading cards (TCG)** | 4.8 × 3.81 in | Cards fed sideways through the feeder: rotated 90° clockwise, deskewed, cropped to the card, brightness tuned to match PaperStream Capture's output. |

Built-in presets can't be changed or deleted, but you can start from one and
**Save as…** your own.

## Command line

`omascan scan` runs a scan without the TUI. It starts from a preset (Letter
unless you pass `--preset`) and applies any options you give on top.

```bash
# Letter-size documents into ./scans, scanner found automatically
omascan scan --output ./scans

# Trading cards
omascan scan --preset "Trading cards (TCG)" --output ./mtg_batch_1

# A preset you saved in the TUI, at a specific scanner address
omascan scan --preset "My receipts" --host 192.0.2.10 --output ./receipts

# List presets
omascan scan --list-presets
```

If you don't pass `--host`, the address remembered by the TUI is used; if
there isn't one, the local network is searched. The command-line scanner
overwrites existing files in the output folder (the TUI continues numbering).

From a clone without installing, run `python scan.py` / `python omascan.py`
after `pip install -r requirements.txt` in a virtualenv.

<details>
<summary><b>All command-line options</b></summary>

Options left out take their value from the preset.

- **`--host HOST`**<br>
  IP address (or hostname) of the scanner.
- **`--port PORT`**<br>
  The scanner's HTTP port. There should be no reason to change this. Default: 80.
- **`--output DIR`**<br>
  Directory to save scanned JPEGs into. Created if it doesn't exist. Default: `./scans`.
- **`--preset NAME`, `--list-presets`**<br>
  Preset to start from, or list them.
- **`--resolution DPI`**<br>
  Scan resolution in dots per inch.
- **`--width UNITS`, `--height UNITS`**<br>
  Scan area in 1/1200 inch units (inches × 1200). Letter is 10200 × 13200; the trading-card area is 5760 × 4576 (4.8" × 3.81"), sized for cards fed sideways through the feeder.
- **`--jpeg-quality N`**<br>
  JPEG quality the scanner encodes images at (0-100).
- **`--rotate {cw,ccw,180,none}`, `--no-rotate`**<br>
  Rotation for front images; backs turn the opposite way. The trading-card preset uses `cw`: cards feed sideways and the scanner has no rotation setting of its own, so images are rotated to come out upright, matching what PaperStream Capture produces on Windows.
- **`--crop` / `--no-crop`**<br>
  Crop each image to the card using content-based edge detection (finding the card's dark border against the background). The scanner itself returns a full, overscanned frame with no crop of its own (confirmed at the protocol level: `automaticDeskew` and `cropMargin` are disabled in the scan task, and the image metadata's `requestDriverCropDeskew` field reports "failure"). The crop is tuned to match PaperStream's real output, whose dimensions vary slightly card-to-card because it's genuine per-card detection rather than a fixed rectangle.
- **`--crop-threshold N`**<br>
  Grayscale darkness threshold (0-255) used to detect the card's border. Lower = only very dark (e.g. black-bordered) edges count. Trading cards: 80.
- **`--crop-margin-x MM`, `--crop-margin-y MM`**<br>
  Extra margin, in millimeters, kept around the detected card edges (converted to pixels at the scan resolution). The vertical margin is larger because the dark-pixel edge detection consistently undershoots more on that axis (likely anti-aliased/lighter pixels right at the card's top and bottom edges). Trading cards: 0.93 and 1.86 mm (11 and 22 pixels at 300 dpi).
- **`--deskew` / `--no-deskew`**<br>
  Cards feed slightly crooked (typically well under a couple of degrees, but visible). Deskew detects the card's rotation angle from its border (OpenCV `minAreaRect`) and straightens it before cropping.
- **`--color-correct` / `--no-color-correct`**<br>
  Brightness correction; see [Brightness correction](#brightness-correction).
- **`--color-strength R G B`**<br>
  Per-channel soft-clip curve steepness. Higher values brighten that channel further; the curve avoids hard clipping regardless. Default: 3.08 3.14 3.14.
- **`--color-blur RADIUS`**<br>
  Gaussian blur radius applied before brightening, to soften fine compression/texture detail before the brightness gain amplifies it into pixelation. Small enough to keep text and edges crisp. 0 disables. Trading cards: 0.8; documents: 0.
- **`--gamma G`, `--contrast C`**<br>
  Extra tone adjustments applied after the brightness curve. Gamma above 1 brightens midtones; contrast scales around mid-gray. 1.0 = off.

</details>

## How it works

| File | Purpose |
|---|---|
| `omascan.py` | The TUI (Textual) |
| `scan.py` | Command-line scanner |
| `privet_client.py` | Client for the scanner's network protocol |
| `processing.py` | Scan settings and the post-processing pipeline shared by the TUI and CLI |
| `autocrop.py` | Content-based crop and deskew (OpenCV/numpy) |
| `colorcorrect.py` | Brightness correction and gamma/contrast (numpy) |
| `config.py` | Settings and preset storage, built-in presets |
| `discovery.py` | Finds fi-series scanners on the local network |
| `logo.py` | The title banner |
| `omarchy_theme.py` | Builds the TUI's color theme from the current Omarchy theme |
| `install.sh`, `uninstall.sh`, `scripts/` | Installer and Omarchy menu integration |
| `packaging/` | Arch package files (launcher, desktop entry, PKGBUILD) |
| `updater.py` | `omascan update` and the update check |
| `.github/workflows/package.yml` | Builds the Arch package for each release |

### The protocol

The fi-8170 uses a JSON-over-HTTP protocol derived from Google's old "Privet"
local-device protocol. `privet_client.py` implements it directly:

```
GET  /api/privet/info            device identity/status
POST /api/privet/session         all commands (JSON body, keyed by "method"):
                                 createSession, sendTask, startCapturing,
                                 readImageBlock, releaseImageBlocks,
                                 stopCapturing, closeSession
GET  /image/image_N.jpeg         raw JPEG bytes for a scanned page
```

A single persistent HTTP connection is used for the whole session, matching
the vendor software. The scan settings are a fixed set of parameters sent
directly over this protocol; nothing depends on profiles configured in
PaperStream Capture or on the scanner itself.

The scanner doesn't announce itself (no mDNS/SSDP/WS-Discovery), so
`discovery.py` probes every address in each local /24 network for
`/api/privet/info` and keeps the devices that identify as Fujitsu/Ricoh
fi-series. It takes a few seconds.

### Brightness correction

The scanner's raw JPEGs come back noticeably dark compared to PaperStream's
output. No brightness/contrast/gamma setting was found to differ between
driver profiles, so this is likely a fixed step in the vendor's own JPEG
pipeline rather than a per-profile setting. (A PaperStream settings screen
shows a "Tone Adjustment: Bright" / "Color Adjustment: Prioritize Contrast"
pair and "sRGB Output: Off", consistent with this, but doesn't expose the
numbers behind them.)

Omascan corrects for it with a per-channel soft-clip curve,
`out = 1 - exp(-k * in)`, calibrated by scanning the same physical batch of
cards through both this tool and PaperStream Capture and comparing the results
pixel-for-pixel. Two other approaches were tried and rejected first:

- **Gamma** matched brightness well but amplified fine real texture in card
  borders into visible speckled pixelation, because gamma has a very steep
  slope near black. (Confirmed not a compression artifact: much higher device
  JPEG quality barely changed it.)
- **A plain linear gain** fixed that but blew out near-white areas (like
  rules-text boxes) to flat white with a hard clip.

The soft-clip curve is gentle near black and rolls off smoothly near white,
avoiding both. See `colorcorrect.py` for the full story. The curve was tuned
on trading cards; the document presets use the same curve without the
pre-blur.

## Status

Tested with the fi-8170. Other fi-series models that use the same network
protocol may work but haven't been tried. The trading-card preset was
calibrated against real PaperStream output; the document presets are new and
may need brightness tweaks for your paper.

## Making a release

1. Update `VERSION` (e.g. `0.3.0`) and commit.
2. Publish a GitHub release tagged to match (`v0.3.0`), e.g.
   `gh release create v0.3.0 --title "Omascan v0.3.0" --notes "…"`.
3. The **Arch package** workflow builds the package and attaches it to the
   release, with `SHA256SUMS`. It fails if `VERSION` doesn't match the tag.

`omascan update` and the installer pick up the new release from there.

## License

[MIT](LICENSE)
