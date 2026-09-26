#!/usr/bin/env python3
"""Add or remove Omascan's row in the Omarchy menu extension file
(~/.config/omarchy/extensions/omarchy-menu.jsonc).

The file is JSONC (comments allowed), so it's edited line-by-line instead of
being parsed and rewritten -- that keeps the user's comments and other entries
exactly as they were. Omascan's entry always lives on a single line.

Usage: omarchy_menu_entry.py add <launch-command> | remove
"""

import json
import os
import re
import sys
from pathlib import Path

KEY = "omascan"
ENTRY_RE = re.compile(rf'^\s*"{KEY}"\s*:')


def menu_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "omarchy" / "extensions" / "omarchy-menu.jsonc"


def significant(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("//")


def closing_brace(lines: list[str]) -> int:
    """Index of the top-level object's closing brace: the last non-comment line."""
    last = max(i for i, l in enumerate(lines) if significant(l))
    if lines[last].strip() != "}":
        raise ValueError("couldn't find the closing brace")
    return last


def remove_entry(lines: list[str]) -> list[str]:
    lines = [l for l in lines if not ENTRY_RE.match(l)]
    # If our entry was last, the entry before it now has a dangling comma.
    close = closing_brace(lines)
    prev = next((i for i in range(close - 1, -1, -1) if significant(lines[i])), None)
    if prev is not None and lines[prev].rstrip().endswith(","):
        lines[prev] = lines[prev].rstrip()[:-1] + "\n"
    return lines


def add_entry(lines: list[str], command: str) -> list[str]:
    lines = remove_entry(lines)
    entry = {
        "icon": "\U000f06ab",  # nf-md-scanner
        "label": "Omascan",
        "aliases": ["scanner"],
        "description": "Scan documents and cards with the Ricoh/Fujitsu fi-8170",
        "action": command,
    }
    new_line = f'  "{KEY}": {json.dumps(entry, ensure_ascii=False, separators=(",", ":"))}\n'
    close = closing_brace(lines)
    prev = next((i for i in range(close - 1, -1, -1) if significant(lines[i])), None)
    if prev is not None and not lines[prev].rstrip().endswith((",", "{")):
        lines[prev] = lines[prev].rstrip() + ",\n"
    return lines[:close] + [new_line] + lines[close:]


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("add", "remove") or (sys.argv[1] == "add" and len(sys.argv) != 3):
        print(__doc__, file=sys.stderr)
        return 2
    path = menu_file()
    if path.exists():
        lines = path.read_text().splitlines(keepends=True)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
    elif sys.argv[1] == "add":
        lines = ["{\n", "}\n"]
    else:
        return 0
    try:
        lines = add_entry(lines, sys.argv[2]) if sys.argv[1] == "add" else remove_entry(lines)
    except ValueError as e:
        print(f"{path}: {e}; leaving it alone", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
