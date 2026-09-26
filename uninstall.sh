#!/usr/bin/env bash
# Omascan uninstaller. Also available as `omascan uninstall`.
#
#   --purge   also delete saved settings and presets (~/.config/omascan)
#
# Scanned images are never touched.

set -euo pipefail

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="${OMASCAN_HOME:-$DATA_HOME/omascan}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omascan"
PATH_MARKER="# Added by the Omascan installer"

PURGE=false
[[ ${1:-} == --purge ]] && PURGE=true

# Remove the Omarchy menu row first, while the helper script still exists.
HELPER="$PREFIX/app/scripts/omarchy_menu_entry.py"
[[ -f $HELPER ]] || HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/omarchy_menu_entry.py"
if [[ -f $HELPER ]] && command -v python3 >/dev/null; then
  python3 "$HELPER" remove && { command -v omarchy-menu >/dev/null && omarchy-menu refresh &>/dev/null || true; }
fi

rm -f "$HOME/.local/bin/omascan"
rm -f "$DATA_HOME/applications/omascan.desktop"
rm -f "$DATA_HOME/icons/hicolor/scalable/apps/omascan.svg"
update-desktop-database -q "$DATA_HOME/applications" &>/dev/null || true
gtk-update-icon-cache -q "$DATA_HOME/icons/hicolor" &>/dev/null || true

# Undo the PATH line the installer may have added (the marker plus the line after it).
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  if [[ -f $rc ]] && grep -qF "$PATH_MARKER" "$rc"; then
    sed -i "/^$PATH_MARKER\$/,+1d" "$rc"
  fi
done

rm -rf "$PREFIX"

if $PURGE; then
  rm -rf "$CONFIG_DIR"
  echo "Omascan and its settings have been removed."
else
  echo "Omascan has been removed. Your settings are still in $CONFIG_DIR (use --purge to delete them)."
fi
