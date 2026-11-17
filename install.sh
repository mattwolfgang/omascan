#!/usr/bin/env bash
# Omascan installer.
#
#   curl -fsSL https://raw.githubusercontent.com/mattwolfgang/omascan/main/install.sh | bash
#
# On Omarchy / Arch Linux this installs the latest release's pacman package
# (dependencies come from the official repos) and adds Omascan to the Omarchy
# menu. Elsewhere, or when run from a clone (./install.sh), it installs into
# ~/.local/share/omascan with its own Python venv and puts an `omascan` command
# in ~/.local/bin. Either way, update later with `omascan update`.
#
# Environment overrides:
#   OMASCAN_METHOD  "package" or "script" (default: package on Arch, unless run from a clone)
#   OMASCAN_REF     release tag (or branch, script method only) to install (default: latest release)
#   OMASCAN_HOME    script-method install location (default: ~/.local/share/omascan)

set -euo pipefail

REPO="mattwolfgang/omascan"
REF="${OMASCAN_REF:-}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="${OMASCAN_HOME:-$DATA_HOME/omascan}"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
PATH_MARKER="# Added by the Omascan installer"
PACKAGE_ASSET="omascan-any.pkg.tar.zst"

bold() { printf '\e[1m%s\e[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die() { printf '\e[31mError:\e[0m %s\n' "$*" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

SCRIPT_DIR=""
if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
FROM_CLONE=false
[[ -n $SCRIPT_DIR && -f $SCRIPT_DIR/omascan.py ]] && FROM_CLONE=true

METHOD="${OMASCAN_METHOD:-}"
if [[ -z $METHOD ]]; then
  if ! $FROM_CLONE && command -v pacman >/dev/null && [[ -f /etc/arch-release ]]; then
    METHOD=package
  else
    METHOD=script
  fi
fi
[[ $METHOD == package || $METHOD == script ]] || die "OMASCAN_METHOD must be 'package' or 'script'."

latest_tag() {
  curl -fsSI "https://github.com/$REPO/releases/latest" | tr -d '\r' \
    | sed -n 's#^[Ll]ocation: .*/releases/tag/##p'
}

omarchy_menu_supported() {
  command -v omarchy-menu >/dev/null && grep -q refresh "$(command -v omarchy-menu)" 2>/dev/null
}

# ---- package install (Omarchy / Arch) -----------------------------------------

if [[ $METHOD == package ]]; then
  command -v curl >/dev/null || die "curl is required to download Omascan."
  command -v sudo >/dev/null || die "sudo is required to install the package."
  [[ -n $REF ]] || REF="$(latest_tag)"
  [[ $REF == v* ]] || die "Couldn't find the latest Omascan release."
  BASE="https://github.com/$REPO/releases/download/$REF"

  bold "Downloading Omascan $REF…"
  curl -fsSL -o "$TMP/$PACKAGE_ASSET" "$BASE/$PACKAGE_ASSET" || die "Couldn't download $BASE/$PACKAGE_ASSET"
  curl -fsSL -o "$TMP/SHA256SUMS" "$BASE/SHA256SUMS" || die "Couldn't download $BASE/SHA256SUMS"
  (cd "$TMP" && grep " $PACKAGE_ASSET\$" SHA256SUMS | sha256sum -c --quiet -) || die "Checksum mismatch; not installing."

  bold "Installing with pacman (you may be asked for your password)…"
  sudo pacman -U --noconfirm "$TMP/$PACKAGE_ASSET"

  # A script install's ~/.local/bin/omascan would take priority over the package.
  # Removed only now, so a failed or cancelled pacman run leaves it working.
  if [[ -d $PREFIX/app ]]; then
    info "Removed the previous script-based install (your settings are kept)"
    bash "$PREFIX/app/uninstall.sh" >/dev/null
  fi

  if omarchy_menu_supported; then
    /usr/bin/omascan menu add >/dev/null && info "Added Omascan to the Omarchy menu"
  fi
  echo
  bold "Omascan is installed. Run 'omascan' or open it from the app launcher. Update later with 'omascan update'."
  exit 0
fi

# ---- script install: find the source files ------------------------------------

if $FROM_CLONE; then
  SRC="$SCRIPT_DIR"
else
  command -v curl >/dev/null || die "curl is required to download Omascan."
  [[ -n $REF ]] || REF="$(latest_tag || true)"
  [[ -n $REF ]] || REF=main
  bold "Downloading Omascan ($REF)…"
  curl -fsSL "https://github.com/$REPO/archive/$REF.tar.gz" | tar -xz -C "$TMP" --strip-components=1 \
    || die "Couldn't download https://github.com/$REPO (ref $REF)."
  SRC="$TMP"
fi

# ---- check Python -------------------------------------------------------------

PYTHON="$(command -v python3 || true)"
[[ -n $PYTHON ]] || die "Python 3.10 or newer is required (install the 'python' package)."
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || die "Python 3.10 or newer is required; found $("$PYTHON" --version 2>&1)."
"$PYTHON" -c 'import venv, ensurepip' 2>/dev/null \
  || die "Python's venv module is missing (on Debian/Ubuntu: sudo apt install python3-venv)."

# ---- install the app ----------------------------------------------------------

bold "Installing Omascan to $PREFIX"
mkdir -p "$PREFIX"
rm -rf "$PREFIX/app"
mkdir -p "$PREFIX/app"
cp "$SRC"/*.py "$SRC/requirements.txt" "$PREFIX/app/"
cp -r "$SRC/assets" "$SRC/scripts" "$PREFIX/app/"
cp "$SRC/uninstall.sh" "$PREFIX/app/"
[[ -f $SRC/VERSION ]] && cp "$SRC/VERSION" "$PREFIX/app/"

info "Setting up the Python environment (this can take a minute the first time)…"
if [[ ! -x $PREFIX/venv/bin/python ]]; then
  "$PYTHON" -m venv "$PREFIX/venv"
fi
"$PREFIX/venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade -r "$PREFIX/app/requirements.txt" \
  || die "Installing Python packages failed (see the output above)."

# ---- USB support (SANE) -------------------------------------------------------

# python-sane builds against the system SANE library, so it is only installed
# when that library is present; without it Omascan still works over the network.
if ! "$PREFIX/venv/bin/python" -c 'import sane' >/dev/null 2>&1; then
  if ldconfig -p 2>/dev/null | grep -q 'libsane\.so'; then
    info "Installing USB scanning support (python-sane)…"
    "$PREFIX/venv/bin/python" -m pip install --quiet --disable-pip-version-check python-sane \
      || info "Note: python-sane failed to install; USB scanning is disabled. The app still works over the network."
  else
    info "Note: the system 'sane' package is missing, so USB scanning is disabled. Install it (e.g. 'sudo pacman -S sane') and re-run the installer."
  fi
fi

# The fi-8170 uses a vendor-specific USB class (0xff) that SANE's default udev
# rule doesn't cover, so grant the user access to the device explicitly. A
# dedicated group is used instead of the shared 'lp' group so the user isn't
# given access to every other device 'lp' owns.
install_udev_rule() {
  # The dedicated group must exist before the rule is applied, or udev drops
  # the group assignment on a fresh install.
  if command -v sudo >/dev/null; then
    if ! id -nG 2>/dev/null | grep -qw omascan; then
      sudo groupadd -f omascan >/dev/null 2>&1 || true
      if sudo usermod -aG omascan "$(id -un)" >/dev/null 2>&1; then
        info "Added the current user to the 'omascan' group (effective at the next login)"
      fi
    fi
  fi
  local rule='SUBSYSTEM=="usb", ATTR{idVendor}=="04c5", ATTR{idProduct}=="15ff", MODE="0660", GROUP="omascan"'
  if [[ -f $SRC/packaging/50-omascan-ricoh.rules ]]; then
    if [[ -w /etc/udev/rules.d ]]; then
      cp "$SRC/packaging/50-omascan-ricoh.rules" /etc/udev/rules.d/50-omascan-ricoh.rules
    elif command -v sudo >/dev/null; then
      sudo install -Dm644 "$SRC/packaging/50-omascan-ricoh.rules" /etc/udev/rules.d/50-omascan-ricoh.rules
    else
      return 0
    fi
  elif command -v sudo >/dev/null; then
    sudo tee /etc/udev/rules.d/50-omascan-ricoh.rules >/dev/null <<EOF
# Omascan: access to the Ricoh/Fujitsu fi-8170 USB scanner
$rule
EOF
  else
    return 0
  fi
  # Reload with privilege so it works when the user can't write rules.d, and
  # re-trigger the USB subsystem so an already-connected scanner gets the new
  # permissions without being replugged.
  if command -v sudo >/dev/null; then
    sudo udevadm control --reload-rules >/dev/null 2>&1 || true
    sudo udevadm trigger --subsystem-match=usb --action=change >/dev/null 2>&1 || true
  else
    udevadm control --reload >/dev/null 2>&1 || true
  fi
  info "Installed the udev rule for USB scanning"
}
install_udev_rule

# ---- the omascan command ------------------------------------------------------

mkdir -p "$BIN_DIR"
cat >"$BIN_DIR/omascan" <<EOF
#!/usr/bin/env bash
# Omascan launcher (created by install.sh).
APP="$PREFIX/app"
PY="$PREFIX/venv/bin/python"
case "\${1:-}" in
  scan) shift; exec "\$PY" "\$APP/scan.py" "\$@" ;;
  update) shift; exec "\$PY" "\$APP/updater.py" "\$@" ;;
  uninstall) shift; exec bash "\$APP/uninstall.sh" "\$@" ;;
  --version|version) cat "\$APP/VERSION" 2>/dev/null || echo unknown; exit 0 ;;
  -h|--help|help)
    echo "Usage: omascan               launch the Omascan TUI"
    echo "       omascan scan [...]    command-line scan (omascan scan --help)"
    echo "       omascan update        install the latest release (--check to only check)"
    echo "       omascan uninstall     remove Omascan (--purge also removes settings)"
    echo "       omascan --version"
    exit 0 ;;
esac
exec "\$PY" "\$APP/omascan.py" "\$@"
EOF
chmod +x "$BIN_DIR/omascan"
info "Installed the omascan command to $BIN_DIR/omascan"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [[ -f $rc ]] && ! grep -qF "$PATH_MARKER" "$rc"; then
      printf '\n%s\nexport PATH="$HOME/.local/bin:$PATH"\n' "$PATH_MARKER" >>"$rc"
      info "Added ~/.local/bin to PATH in $rc (open a new terminal to pick it up)"
    fi
  done
fi

# ---- desktop integration ------------------------------------------------------

mkdir -p "$ICON_DIR" "$APPS_DIR"
cp "$PREFIX/app/assets/omascan.svg" "$ICON_DIR/omascan.svg"
gtk-update-icon-cache -q "$DATA_HOME/icons/hicolor" &>/dev/null || true

if command -v omarchy-launch-or-focus-tui >/dev/null; then
  # Opens in a styled terminal window, or focuses it if Omascan is already open.
  LAUNCH="omarchy-launch-or-focus-tui $BIN_DIR/omascan"
  TERMINAL=false
else
  LAUNCH="$BIN_DIR/omascan"
  TERMINAL=true
fi

cat >"$APPS_DIR/omascan.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Omascan
Comment=Scan documents and cards with the Ricoh/Fujitsu fi-8170
Exec=$LAUNCH
Terminal=$TERMINAL
Icon=omascan
Categories=Graphics;Scanning;
Keywords=scanner;scan;fi-8170;fujitsu;ricoh;
StartupNotify=true
EOF
update-desktop-database -q "$APPS_DIR" &>/dev/null || true
info "Added Omascan to the app launcher"

# The Omarchy menu (Omarchy 4+) reads extra rows from a JSONC extension file.
if omarchy_menu_supported; then
  if "$PYTHON" "$PREFIX/app/scripts/omarchy_menu_entry.py" add "$LAUNCH"; then
    omarchy-menu refresh &>/dev/null || true
    info "Added Omascan to the Omarchy menu"
  fi
fi

echo
bold "Omascan is installed. Run 'omascan' in a terminal or open it from the app launcher. Update later with 'omascan update'."
