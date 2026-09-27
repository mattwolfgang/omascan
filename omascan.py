#!/usr/bin/env python3
"""Omascan: terminal UI for scanning batches on the Ricoh/Fujitsu fi-8170.

Usage:
    omascan              # launch the TUI
    omascan scan [...]   # run the command-line scanner (see scan.py --help)
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from textual import on, work
from textual.content import Content
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.validation import Number
from textual.widgets import (
    Button,
    DirectoryTree,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
    Switch,
)
from textual.worker import get_current_worker

from config import BUILTIN_PRESETS, DEFAULT_PRESET_NAME, TCG_PRESET_NAME, AppConfig, config_path
from logo import BANNER, BANNER_WIDTH
from omarchy_theme import build_theme, theme_name, theme_stamp
from updater import available_update
from discovery import FoundScanner, find_scanners, local_networks
from privet_client import PrivetClient
from processing import ROTATION_LABELS, UNITS_PER_INCH, ScanSettings, next_sheet_offset, save_image

RESOLUTIONS = [150, 200, 240, 300, 400, 600]

# Input id -> (ScanSettings field, type). Width/height are shown in inches and
# converted to the scanner's 1/1200-inch units.
NUMBER_FIELDS = {
    "jpeg_quality": int,
    "crop_threshold": int,
    "crop_margin_x_mm": float,
    "crop_margin_y_mm": float,
    "blur": float,
    "gamma": float,
    "contrast": float,
}
SWITCH_FIELDS = ["deskew", "crop", "color_correct"]


def format_inches(units: int) -> str:
    """4 decimals is enough to round-trip any 1/1200-inch value exactly."""
    return f"{units / UNITS_PER_INCH:.4f}".rstrip("0").rstrip(".")


def batch_folder_name(name: str) -> str:
    """Make a batch name safe to use as a single folder name."""
    name = re.sub(r"[/\\\x00]", "-", name.strip())
    return "" if name in (".", "..") else name


class PresetNameScreen(ModalScreen[str | None]):
    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self.initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Save preset as:")
            yield Input(value=self.initial, placeholder="Preset name", id="preset-name")
            with Horizontal(classes="buttons"):
                yield Button("Save", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def submit(self) -> None:
        name = self.query_one("#preset-name", Input).value.strip()
        if not name:
            self.notify("Enter a preset name", severity="warning")
        elif name in BUILTIN_PRESETS:
            self.notify(f'"{name}" is a built-in preset; choose another name', severity="warning", markup=False)
        else:
            self.dismiss(name)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)


class ScannerPickScreen(ModalScreen[str | None]):
    def __init__(self, scanners: list[FoundScanner]) -> None:
        super().__init__()
        self.scanners = scanners

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Scanners found — pick one:")
            yield ListView(
                *[ListItem(Label(Content(f"{s.host}  {s.manufacturer} {s.model}  (serial {s.serial})"))) for s in self.scanners],
                id="scanner-list",
            )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")

    @on(ListView.Selected)
    def picked(self, event: ListView.Selected) -> None:
        self.dismiss(self.scanners[event.list_view.index].host)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)


class DirectoryPickScreen(ModalScreen[str | None]):
    def __init__(self, start: str) -> None:
        super().__init__()
        path = Path(start).expanduser()
        while not path.is_dir() and path != path.parent:
            path = path.parent
        self.start = path
        self.selected = path

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog tall"):
            yield Label("Choose save folder (Enter/click to select a folder):")
            yield DirectoryTree(str(self.start.parent if self.start.parent != self.start else self.start), id="tree")
            yield Label(str(self.selected), id="picked")
            with Horizontal(classes="buttons"):
                yield Button("Use this folder", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    @on(DirectoryTree.DirectorySelected)
    def dir_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.selected = event.path
        self.query_one("#picked", Label).update(str(event.path))

    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        self.dismiss(str(self.selected))

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)


class ScanApp(App):
    TITLE = "Omascan"
    CSS = """
    #banner { width: 100%; height: auto; content-align: center middle; color: $accent; padding: 1 0 0 0; }
    #banner-subtitle { width: 100%; content-align: center middle; color: $text-muted; margin-bottom: 1; }
    #title-compact { width: 100%; content-align: center middle; color: $accent; text-style: bold; display: none; }
    #main { height: 1fr; }
    #left { width: 1fr; min-width: 40; padding: 0 1; }
    #right { width: 1fr; min-width: 44; padding: 0 1; }
    .section { border: round $primary; padding: 0 1; height: auto; margin-bottom: 1; }
    .row { height: auto; }
    .row > Label { width: 16; padding-top: 1; }
    .row > Input { width: 1fr; }
    .row > Select { width: 1fr; }
    .row > Button { margin-left: 1; min-width: 10; }
    .row > Switch { margin-right: 2; }
    .hint { color: $text-muted; padding-left: 16; }
    #strength-row > Input { width: 1fr; }
    #scanner-status, #resolved-path, #preset-status { color: $text-muted; }
    #log { height: 1fr; min-height: 6; border: round $primary; }
    #actions { height: auto; padding: 0 1; }
    #actions > Button { margin-right: 2; }
    #scan-status { padding-top: 1; }
    .dialog { width: 80; height: auto; max-height: 80%; border: thick $primary; background: $surface; padding: 1 2; }
    .dialog.tall { height: 80%; }
    .dialog #tree { height: 1fr; }
    .dialog ListView { height: auto; max-height: 12; margin: 1 0; }
    .buttons { height: auto; margin-top: 1; }
    .buttons > Button { margin-right: 2; }
    ModalScreen { align: center middle; }
    """
    BINDINGS = [
        ("f5", "scan", "Scan"),
        ("f6", "find_scanner", "Find scanner"),
        ("escape", "stop_scan", "Stop"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = AppConfig.load()
        self.stop_event = threading.Event()
        self.scanning = False
        self._loading = False

    # ----- layout -----

    def compose(self) -> ComposeResult:
        yield Static(BANNER, id="banner")
        yield Static("network scanning for the Ricoh/Fujitsu fi-8170", id="banner-subtitle")
        yield Static("OMASCAN", id="title-compact")
        with Horizontal(id="main"):
            with VerticalScroll(id="left"):
                with Vertical(classes="section") as v:
                    v.border_title = "Scanner"
                    with Horizontal(classes="row"):
                        yield Label("IP address")
                        yield Input(self.config.host, id="host")
                        yield Button("Find", id="find")
                    yield Static("", id="scanner-status")
                with Vertical(classes="section") as v:
                    v.border_title = "Output"
                    with Horizontal(classes="row"):
                        yield Label("Save path")
                        yield Input(self.config.save_path, id="save_path")
                        yield Button("Browse", id="browse")
                    with Horizontal(classes="row"):
                        yield Label("Batch name")
                        yield Input(placeholder="e.g. mtg_box_3 (subfolder)", id="batch")
                    yield Static("", id="resolved-path")
                with Vertical(classes="section") as v:
                    v.border_title = "Preset"
                    with Horizontal(classes="row"):
                        yield Label("Preset")
                        yield Select(self._preset_options(), allow_blank=False, id="preset")
                    with Horizontal(classes="row"):
                        yield Button("Save as…", id="save-preset")
                        yield Button("Overwrite", id="overwrite-preset")
                        yield Button("Delete", id="delete-preset", variant="error")
                    yield Static("", id="preset-status")
                with Horizontal(id="actions"):
                    yield Button("Scan (F5)", variant="success", id="scan")
                    yield Button("Stop (Esc)", variant="error", id="stop", disabled=True)
                    yield Static("", id="scan-status")
                yield RichLog(id="log", wrap=True, markup=True)
            with VerticalScroll(id="right"):
                with Vertical(classes="section") as v:
                    v.border_title = "Scan area"
                    with Horizontal(classes="row"):
                        yield Label("Resolution")
                        yield Select([(f"{r} dpi", r) for r in RESOLUTIONS], allow_blank=False, id="resolution")
                    with Horizontal(classes="row"):
                        yield Label("Width (in)")
                        yield Input(id="width", validators=[Number(minimum=0.1, maximum=8.5)])
                    with Horizontal(classes="row"):
                        yield Label("Height (in)")
                        yield Input(id="height", validators=[Number(minimum=0.1, maximum=240)])
                    yield Static("Cards feed sideways: width is the card's long edge.", classes="hint", id="card-hint")
                    with Horizontal(classes="row"):
                        yield Label("JPEG quality")
                        yield Input(id="jpeg_quality", validators=[Number(minimum=1, maximum=100)])
                with Vertical(classes="section") as v:
                    v.border_title = "Geometry"
                    with Horizontal(classes="row"):
                        yield Label("Rotate")
                        yield Select([(label, key) for key, label in ROTATION_LABELS.items()],
                                     allow_blank=False, id="rotation")
                    yield Static("Applies to fronts; backs turn the opposite way.", classes="hint")
                    with Horizontal(classes="row"):
                        yield Label("Deskew")
                        yield Switch(id="deskew")
                        yield Label("Auto-crop")
                        yield Switch(id="crop")
                    with Horizontal(classes="row"):
                        yield Label("Crop threshold")
                        yield Input(id="crop_threshold", validators=[Number(minimum=0, maximum=255)])
                    with Horizontal(classes="row"):
                        yield Label("Margin X (mm)")
                        yield Input(id="crop_margin_x_mm", validators=[Number(minimum=0, maximum=50)])
                    with Horizontal(classes="row"):
                        yield Label("Margin Y (mm)")
                        yield Input(id="crop_margin_y_mm", validators=[Number(minimum=0, maximum=50)])
                with Vertical(classes="section") as v:
                    v.border_title = "Tone"
                    with Horizontal(classes="row"):
                        yield Label("Brightness")
                        yield Switch(id="color_correct")
                    with Horizontal(classes="row", id="strength-row"):
                        yield Label("Bright R G B")
                        yield Input(id="strength_r", validators=[Number(minimum=0.01, maximum=20)])
                        yield Input(id="strength_g", validators=[Number(minimum=0.01, maximum=20)])
                        yield Input(id="strength_b", validators=[Number(minimum=0.01, maximum=20)])
                    yield Static("Soft-clip curve strength per channel; higher = brighter.", classes="hint")
                    with Horizontal(classes="row"):
                        yield Label("Pre-blur")
                        yield Input(id="blur", validators=[Number(minimum=0, maximum=10)])
                    with Horizontal(classes="row"):
                        yield Label("Gamma")
                        yield Input(id="gamma", validators=[Number(minimum=0.1, maximum=5)])
                    with Horizontal(classes="row"):
                        yield Label("Contrast")
                        yield Input(id="contrast", validators=[Number(minimum=0.1, maximum=5)])
                    yield Static("Gamma/contrast 1.0 = off. Gamma > 1 brightens midtones.", classes="hint")
        yield Footer()

    def on_mount(self) -> None:
        preset = self.config.last_preset if self.config.last_preset in self.config.presets else DEFAULT_PRESET_NAME
        self.query_one("#preset", Select).value = preset
        self.apply_settings(self.config.presets[preset])
        self.update_resolved_path()
        self.log_line("Config: $path", path=str(config_path()))
        self._theme_stamp = theme_stamp()
        self._theme_serial = 0
        self.apply_omarchy_theme()
        self.set_interval(2, self.check_omarchy_theme)
        self.fit_banner()
        if not self.config.host:
            # First run: nothing remembered yet, so go looking for the scanner.
            self.action_find_scanner()
        if self.config.check_for_updates:
            self.check_for_update()

    @work(thread=True, exclusive=True, group="update-check")
    def check_for_update(self) -> None:
        try:
            tag = available_update(timeout=5)
        except Exception:  # noqa: BLE001 - offline etc.; the check is best-effort
            return
        if tag:
            self.call_from_thread(self.show_update_notice, tag.lstrip("v"))

    def show_update_notice(self, version: str) -> None:
        text = f"Omascan {version} is available. Run 'omascan update' in a terminal to install it."
        self.notify(text, title="Update available", timeout=15, markup=False)
        self.log_line("[$accent]$message[/]", message=text)

    # ----- Omarchy theme -----

    def apply_omarchy_theme(self) -> None:
        """Recolor to match the current Omarchy theme (no-op without Omarchy)."""
        # A fresh name each time: re-setting App.theme to the same name wouldn't
        # trigger a refresh after the palette behind it changed.
        self._theme_serial += 1
        theme = build_theme(f"omarchy-{theme_name()}-{self._theme_serial}")
        if theme is None:
            return
        old = self.theme
        self.register_theme(theme)
        self.theme = theme.name
        if old.startswith("omarchy-"):
            self.unregister_theme(old)

    def check_omarchy_theme(self) -> None:
        stamp = theme_stamp()
        if stamp != self._theme_stamp:
            self._theme_stamp = stamp
            self.apply_omarchy_theme()

    def on_resize(self) -> None:
        self.fit_banner()

    def fit_banner(self) -> None:
        """The big banner needs ~81 columns and 8 rows; fall back to a one-line
        title on smaller terminals so the form keeps its room."""
        big = self.size.width >= BANNER_WIDTH + 2 and self.size.height >= 38
        self.query_one("#banner").display = big
        self.query_one("#banner-subtitle").display = big
        self.query_one("#title-compact").display = not big

    def on_unmount(self) -> None:
        self.save_config()

    def _preset_options(self) -> list[tuple[str, str]]:
        return [(Content(name + ("  (built-in)" if name in BUILTIN_PRESETS else "")), name) for name in self.config.presets]

    # ----- settings <-> form -----

    def apply_settings(self, s: ScanSettings) -> None:
        self._loading = True
        self.query_one("#resolution", Select).value = s.resolution if s.resolution in RESOLUTIONS else 300
        self.query_one("#rotation", Select).value = s.rotation
        self.query_one("#width", Input).value = format_inches(s.width)
        self.query_one("#height", Input).value = format_inches(s.height)
        for name in NUMBER_FIELDS:
            self.query_one(f"#{name}", Input).value = str(getattr(s, name))
        for name in SWITCH_FIELDS:
            self.query_one(f"#{name}", Switch).value = getattr(s, name)
        for inp, v in zip(("strength_r", "strength_g", "strength_b"), s.strengths):
            self.query_one(f"#{inp}", Input).value = str(v)
        self._loading = False
        self.update_preset_status()

    def read_settings(self) -> ScanSettings:
        """Raises ValueError with a readable message if any field is invalid."""
        values: dict = {
            "resolution": self.query_one("#resolution", Select).value,
            "rotation": self.query_one("#rotation", Select).value,
        }
        for name, kind in NUMBER_FIELDS.items():
            inp = self.query_one(f"#{name}", Input)
            if not inp.is_valid:
                raise ValueError(f"Invalid value for {name.replace('_', ' ')}: {inp.value!r}")
            values[name] = kind(float(inp.value))
        for name in ("width", "height"):
            inp = self.query_one(f"#{name}", Input)
            if not inp.is_valid:
                raise ValueError(f"Invalid value for {name}: {inp.value!r}")
            values[name] = round(float(inp.value) * UNITS_PER_INCH)
        strengths = []
        for inp_id in ("strength_r", "strength_g", "strength_b"):
            inp = self.query_one(f"#{inp_id}", Input)
            if not inp.is_valid:
                raise ValueError(f"Invalid brightness value: {inp.value!r}")
            strengths.append(float(inp.value))
        values["strengths"] = tuple(strengths)
        for name in SWITCH_FIELDS:
            values[name] = self.query_one(f"#{name}", Switch).value
        return ScanSettings(**values)

    def update_preset_status(self) -> None:
        if self._loading:
            return
        name = self.query_one("#preset", Select).value
        preset = self.config.presets.get(name)
        try:
            modified = preset is None or self.read_settings() != preset
        except ValueError:
            modified = True
        builtin = name in BUILTIN_PRESETS
        self.query_one("#overwrite-preset", Button).disabled = builtin or not modified
        self.query_one("#delete-preset", Button).disabled = builtin
        self.query_one("#preset-status", Static).update(
            "[$warning]● modified — save as a new preset to keep these changes[/]" if modified else "Settings match preset"
        )

    @on(Input.Changed)
    def input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "host":
            self.config.host = event.value.strip() or self.config.host
        elif event.input.id in ("save_path", "batch"):
            if event.input.id == "save_path":
                self.config.save_path = event.value.strip() or self.config.save_path
            self.update_resolved_path()
        else:
            self.update_preset_status()

    @on(Switch.Changed)
    def switch_changed(self) -> None:
        self.update_preset_status()

    @on(Select.Changed, "#resolution")
    @on(Select.Changed, "#rotation")
    def resolution_changed(self) -> None:
        self.update_preset_status()

    @on(Select.Changed, "#preset")
    def preset_changed(self, event: Select.Changed) -> None:
        self.query_one("#card-hint").display = event.value == TCG_PRESET_NAME
        if event.value in self.config.presets:
            self.apply_settings(self.config.presets[event.value])
            self.config.last_preset = event.value
            self.config.save()

    # ----- output path -----

    def output_dir(self) -> Path:
        base = Path(self.query_one("#save_path", Input).value.strip() or ".").expanduser()
        batch = batch_folder_name(self.query_one("#batch", Input).value)
        return base / batch if batch else base

    def update_resolved_path(self) -> None:
        out = self.output_dir()
        existing = next_sheet_offset(out)
        note = f"  [$warning](exists — continues after sheet {existing:03d})[/]" if existing else ""
        self.query_one("#resolved-path", Static).update(Content.from_markup(f"Saves to: $path{note}", path=str(out)))

    @on(Button.Pressed, "#browse")
    def browse(self) -> None:
        def chosen(path: str | None) -> None:
            if path:
                self.query_one("#save_path", Input).value = path
                self.save_config()

        self.push_screen(DirectoryPickScreen(self.query_one("#save_path", Input).value or str(Path.home())), chosen)

    def save_config(self) -> None:
        try:
            self.config.save()
        except OSError as e:
            self.notify(f"Couldn't save config: {e}", severity="error", markup=False)

    # ----- presets -----

    @on(Button.Pressed, "#save-preset")
    def save_preset(self) -> None:
        try:
            settings = self.read_settings()
        except ValueError as e:
            self.notify(str(e), severity="error", markup=False)
            return

        def named(name: str | None) -> None:
            if not name:
                return
            self.config.user_presets[name] = settings
            self.config.last_preset = name
            self.config.save()
            select = self.query_one("#preset", Select)
            select.set_options(self._preset_options())
            select.value = name
            self.notify(f'Saved preset "{name}"', markup=False)

        current = self.query_one("#preset", Select).value
        self.push_screen(PresetNameScreen("" if current in BUILTIN_PRESETS else current), named)

    @on(Button.Pressed, "#overwrite-preset")
    def overwrite_preset(self) -> None:
        name = self.query_one("#preset", Select).value
        if name in BUILTIN_PRESETS:
            return
        try:
            self.config.user_presets[name] = self.read_settings()
        except ValueError as e:
            self.notify(str(e), severity="error", markup=False)
            return
        self.config.save()
        self.update_preset_status()
        self.notify(f'Updated preset "{name}"', markup=False)

    @on(Button.Pressed, "#delete-preset")
    def delete_preset(self) -> None:
        name = self.query_one("#preset", Select).value
        if name in BUILTIN_PRESETS or name not in self.config.user_presets:
            return
        del self.config.user_presets[name]
        self.config.last_preset = DEFAULT_PRESET_NAME
        self.config.save()
        select = self.query_one("#preset", Select)
        select.set_options(self._preset_options())
        select.value = DEFAULT_PRESET_NAME
        self.notify(f'Deleted preset "{name}"', markup=False)

    # ----- scanner discovery -----

    @on(Button.Pressed, "#find")
    def action_find_scanner(self) -> None:
        if self.scanning:
            return
        self.query_one("#find", Button).disabled = True
        nets = ", ".join(str(n) for n in local_networks()) or "no networks found"
        self.query_one("#scanner-status", Static).update(f"Searching {nets}…")
        self.discover()

    @work(thread=True, exclusive=True, group="discover")
    def discover(self) -> None:
        try:
            found = find_scanners()
        except Exception as e:  # noqa: BLE001 - surface anything to the user
            self.call_from_thread(self.discovery_done, [], str(e))
            return
        self.call_from_thread(self.discovery_done, found, None)

    def discovery_done(self, found: list[FoundScanner], error: str | None) -> None:
        self.query_one("#find", Button).disabled = False
        status = self.query_one("#scanner-status", Static)
        if error:
            status.update(Content.from_markup("[$error]Search failed: $error_text[/]", error_text=error))
        elif not found:
            status.update("[$error]No fi-series scanner found on the local network.[/]")
        elif len(found) == 1:
            self.use_scanner(found[0].host, found[0])
        else:
            status.update(f"Found {len(found)} scanners.")
            self.push_screen(ScannerPickScreen(found), self.use_scanner)

    def use_scanner(self, host: str | None, scanner: FoundScanner | None = None) -> None:
        if not host:
            return
        self.query_one("#host", Input).value = host
        desc = f"{scanner.manufacturer} {scanner.model} (serial {scanner.serial})" if scanner else "selected"
        self.query_one("#scanner-status", Static).update(
            Content.from_markup("[$success]Found $desc at $host[/]", desc=desc, host=host))
        self.save_config()

    # ----- scanning -----

    @on(Button.Pressed, "#scan")
    def action_scan(self) -> None:
        if self.scanning:
            return
        try:
            settings = self.read_settings()
        except ValueError as e:
            self.notify(str(e), severity="error", markup=False)
            return
        host = self.query_one("#host", Input).value.strip()
        if not host:
            self.notify("Enter the scanner's IP address (or press Find)", severity="error")
            return
        out_dir = self.output_dir()
        self.save_config()
        self.set_scanning(True)
        self.run_scan(host, out_dir, settings)

    @on(Button.Pressed, "#stop")
    def action_stop_scan(self) -> None:
        if self.scanning:
            self.stop_event.set()
            self.query_one("#scan-status", Static).update("Stopping after the current sheet…")

    def set_scanning(self, scanning: bool) -> None:
        self.scanning = scanning
        self.query_one("#scan", Button).disabled = scanning
        self.query_one("#stop", Button).disabled = not scanning
        self.query_one("#find", Button).disabled = scanning
        if scanning:
            self.stop_event.clear()

    def log_line(self, text: str, **values: object) -> None:
        """Write a line of markup; `values` fill $name placeholders as plain text,
        so paths and device/error messages can't be misread as markup."""
        self.query_one("#log", RichLog).write(Content.from_markup(text, **values))

    @work(thread=True, exclusive=True, group="scan")
    def run_scan(self, host: str, out_dir: Path, settings: ScanSettings) -> None:
        def log(text: str, **values: object) -> None:
            self.call_from_thread(self.log_line, text, **values)

        def status(text: str) -> None:
            self.call_from_thread(self.query_one("#scan-status", Static).update, Content.from_markup(text))

        worker = get_current_worker()
        count = 0
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            offset = next_sheet_offset(out_dir)
            client = PrivetClient(host)
            status("Connecting…")
            info = client.get_info()
            log("Connected to $manufacturer $model (serial $serial, firmware $firmware)",
                manufacturer=str(info.get("manufacturer")), model=str(info.get("model")),
                serial=str(info.get("serialNumber")), firmware=str(info.get("firmwareVersion")))
            if offset:
                log(f"$path already has sheets up to {offset:03d}; continuing numbering from {offset + 1:03d}",
                    path=str(out_dir))
            status("Scanning… (feeding sheets)")
            scan = client.scan(resolution=settings.resolution, width=settings.width, height=settings.height,
                               jpeg_quality=settings.jpeg_quality)
            try:
                for image in scan:
                    filename = out_dir / f"sheet{image.sheet_number + offset:03d}_{image.source}.jpg"
                    save_image(image, settings, filename)
                    count += 1
                    log(f"Saved $name ({image.pixel_width}x{image.pixel_height} @ {image.resolution}dpi)",
                        name=filename.name)
                    status(f"Scanning… {count} image(s) saved")
                    if (self.stop_event.is_set() or worker.is_cancelled) and image.source == "back":
                        log("[$warning]Stopped by user.[/]")
                        break
            finally:
                scan.close()  # runs the client's closeSession cleanup if we broke out early
            log(f"[$success]Done. {count} image(s) saved to $path/[/]", path=str(out_dir))
            status(f"Done — {count} image(s)")
        except Exception as e:  # noqa: BLE001 - network/device errors should land in the log, not crash the UI
            log(f"[$error]Scan failed: {type(e).__name__}: $message[/]", message=str(e))
            status("[$error]Scan failed[/]")
        finally:
            self.call_from_thread(self.set_scanning, False)
            self.call_from_thread(self.update_resolved_path)


def main() -> None:
    ScanApp().run()


if __name__ == "__main__":
    main()
