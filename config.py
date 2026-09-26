"""Persistent Omascan state: remembered scanner IP, save path, last-used
preset, and the user's own scan presets.

Stored as JSON at $XDG_CONFIG_HOME/omascan/config.json
(~/.config/omascan/config.json by default).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from processing import UNITS_PER_INCH, ScanSettings

TCG_PRESET_NAME = "Trading cards (TCG)"
LETTER_PRESET_NAME = "Letter (8.5 x 11 in)"
DEFAULT_PRESET_NAME = LETTER_PRESET_NAME


def _document_preset(width_in: float, height_in: float) -> ScanSettings:
    """Plain full-page document settings: fed portrait (no rotation), no
    card-border crop/deskew (those look for a dark card edge, which documents
    don't have), and no pre-blur so text stays sharp. The brightness curve stays
    on because the scanner's raw output is dark regardless of what's scanned."""
    return ScanSettings(
        width=round(width_in * UNITS_PER_INCH),
        height=round(height_in * UNITS_PER_INCH),
        jpeg_quality=85,
        rotation="none",
        deskew=False,
        crop=False,
        blur=0.0,
    )


# Built-in presets can't be overwritten or deleted; they're always rebuilt from code.
BUILTIN_PRESETS: dict[str, ScanSettings] = {
    TCG_PRESET_NAME: ScanSettings(),
    LETTER_PRESET_NAME: _document_preset(8.5, 11),
    "Legal (8.5 x 14 in)": _document_preset(8.5, 14),
    "A4 (210 x 297 mm)": _document_preset(210 / 25.4, 297 / 25.4),
}

DEFAULT_SAVE_PATH = str(Path.home() / "Pictures" / "scans")


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "omascan" / "config.json"


@dataclass
class AppConfig:
    host: str = ""  # empty until found or entered; there's no sensible default address
    save_path: str = DEFAULT_SAVE_PATH
    last_preset: str = DEFAULT_PRESET_NAME
    user_presets: dict[str, ScanSettings] = field(default_factory=dict)

    @property
    def presets(self) -> dict[str, ScanSettings]:
        return {**BUILTIN_PRESETS, **self.user_presets}

    @classmethod
    def load(cls) -> "AppConfig":
        path = config_path()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        presets = {}
        for name, values in data.get("presets", {}).items():
            if name in BUILTIN_PRESETS:
                continue
            try:
                presets[name] = ScanSettings.from_dict(values)
            except (TypeError, ValueError):
                continue
        return cls(
            host=data.get("host", ""),
            save_path=data.get("save_path", DEFAULT_SAVE_PATH),
            last_preset=data.get("last_preset", DEFAULT_PRESET_NAME),
            user_presets=presets,
        )

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "host": self.host,
            "save_path": self.save_path,
            "last_preset": self.last_preset,
            "presets": {name: s.to_dict() for name, s in self.user_presets.items()},
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(path)
