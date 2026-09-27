"""Match the TUI's colors to the current Omarchy theme.

Omarchy keeps the active theme's palette in
~/.local/state/omarchy/current/theme/colors.toml. `omarchy-theme-color --all`
resolves it with the same aliases/fallbacks Omarchy's own templates use, so
that's preferred; the file is parsed directly if the command isn't available.
Older Omarchy versions kept the theme under ~/.config/omarchy/current/theme/.

On systems without Omarchy none of these exist and the TUI keeps Textual's
default theme.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

from textual.theme import Theme

_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
_CANDIDATES = [
    _STATE_HOME / "omarchy" / "current" / "theme" / "colors.toml",
    _CONFIG_HOME / "omarchy" / "current" / "theme" / "colors.toml",
]
_NAME_FILES = [
    _STATE_HOME / "omarchy" / "current" / "theme.name",
    _CONFIG_HOME / "omarchy" / "current" / "theme.name",
]


def colors_file() -> Path | None:
    return next((p for p in _CANDIDATES if p.is_file()), None)


def theme_stamp() -> tuple:
    """Changes whenever the Omarchy theme is switched; cheap enough to poll."""
    stamp = []
    for path in [*_CANDIDATES, *_NAME_FILES]:
        try:
            st = path.stat()
            stamp.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    return tuple(stamp)


def theme_name() -> str:
    for path in _NAME_FILES:
        try:
            return path.read_text().strip() or "omarchy"
        except OSError:
            continue
    return "omarchy"


def _resolved_with_omarchy(path: Path) -> dict[str, str] | None:
    exe = shutil.which("omarchy-theme-color")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--file", str(path), "--all"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    palette = dict(line.split("\t", 1) for line in out.stdout.splitlines() if "\t" in line)
    return palette or None


def _parsed_directly(path: Path) -> dict[str, str] | None:
    """Minimal version of omarchy-theme-color's fallbacks, for the keys used here."""
    try:
        raw = {k: str(v) for k, v in tomllib.loads(path.read_text()).items()}
    except (OSError, ValueError):
        return None
    get = lambda *keys: next((raw[k] for k in keys if raw.get(k)), None)  # noqa: E731
    palette = {
        "background": get("background", "bg", "color0"),
        "foreground": get("foreground", "fg", "color7"),
        "accent": get("accent", "blue", "color4"),
        "red": get("red", "color1"),
        "green": get("green", "color2"),
        "yellow": get("yellow", "color3"),
        "blue": get("blue", "color4"),
        "lighter_background": get("lighter_background", "lighter_bg"),
        "selection": get("selection", "selection_background", "color8"),
        "mode": get("mode", "theme_type"),
    }
    if not palette["background"] or not palette["foreground"]:
        return None
    if not palette["mode"]:
        if (path.parent / "light.mode").exists():
            palette["mode"] = "light"
        else:
            hex_bg = palette["background"].lstrip("#")
            lum = sum(int(hex_bg[i:i + 2], 16) for i in (0, 2, 4)) if len(hex_bg) == 6 else 0
            palette["mode"] = "light" if lum > 382 else "dark"
    return {k: v for k, v in palette.items() if v}


def load_palette() -> dict[str, str] | None:
    path = colors_file()
    if path is None:
        return None
    return _resolved_with_omarchy(path) or _parsed_directly(path)


def build_theme(name: str) -> Theme | None:
    """A Textual theme built from the current Omarchy palette, or None if there
    isn't one."""
    palette = load_palette()
    if not palette or "background" not in palette or "foreground" not in palette:
        return None
    accent = palette.get("accent") or palette.get("blue") or palette["foreground"]
    return Theme(
        name=name,
        primary=accent,
        secondary=palette.get("blue", accent),
        accent=accent,
        warning=palette.get("yellow"),
        error=palette.get("red"),
        success=palette.get("green"),
        foreground=palette["foreground"],
        background=palette["background"],
        surface=palette.get("lighter_background"),
        panel=palette.get("selection"),
        dark=palette.get("mode", "dark") != "light",
    )
