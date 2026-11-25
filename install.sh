#!/usr/bin/env bash
# Install aerox9ctl for the current user: on Linux (host or distrobox) or macOS.
#
#   ./install.sh               install (or upgrade) CLI + GUI + app menu entry / app bundle
#   ./install.sh --editable    same, but run straight from this source tree
#   ./install.sh --daemon      also run the lighting daemon (systemd user service / launchd agent)
#   ./install.sh --tray        also start the battery tray icon at login
#   ./install.sh --uninstall   remove everything this script installed
#
# Inside distrobox, the launchers it creates in ~/.local/bin run the tool through
# `distrobox-enter`, so `aerox9ctl` and the menu entry work from the host too.
# Set PYTHON=/path/to/python3 to choose the interpreter (3.11 or newer).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN_DIR="$HOME/.local/bin"
MACOS=0
if [ "$(uname -s)" = Darwin ]; then MACOS=1; fi

# Linux
DESKTOP_FILE="$DATA_HOME/applications/aerox9ctl.desktop"
UNIT_FILE="$CONFIG_HOME/systemd/user/aerox9ctl-daemon.service"
AUTOSTART_FILE="$CONFIG_HOME/autostart/aerox9ctl-tray.desktop"
# macOS
APP_BUNDLE="$HOME/Applications/Aerox 9 Wireless.app"
AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
AGENT_PREFIX="io.github.justfaked.aerox9ctl"

CONTAINER=""
if [ -f /run/.containerenv ]; then
    CONTAINER="$(sed -n 's/^name="\(.*\)"$/\1/p' /run/.containerenv)"
fi
# One venv per environment: host and containers can have different Python versions.
VENV="$DATA_HOME/aerox9ctl/venv${CONTAINER:+-$CONTAINER}"

editable=0 daemon=0 tray=0 uninstall=0
for arg in "$@"; do
    case "$arg" in
        --editable) editable=1 ;;
        --daemon) daemon=1 ;;
        --tray) tray=1 ;;
        --uninstall) uninstall=1 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

# systemctl --user must run on the host
host_systemctl() {
    if [ -n "$CONTAINER" ]; then
        distrobox-host-exec systemctl --user "$@"
    else
        systemctl --user "$@"
    fi
}

# --- launchd (macOS) ---------------------------------------------------------

agent_plist() { echo "$AGENT_DIR/$AGENT_PREFIX.$1.plist"; }

stop_agent() {
    launchctl bootout "gui/$(id -u)/$AGENT_PREFIX.$1" 2>/dev/null || true
}

# install_agent daemon|tray: write the agent from its template and (re)start it now.
install_agent() {
    local plist
    plist="$(agent_plist "$1")"
    mkdir -p "$AGENT_DIR" "$LOG_DIR"
    stop_agent "$1"
    sed -e "s|@BIN_DIR@|$BIN_DIR|g" -e "s|@LOG_DIR@|$LOG_DIR|g" \
        "$SRC/packaging/macos/$AGENT_PREFIX.$1.plist" > "$plist"
    # bootout finishes asynchronously; bootstrap fails until it has.
    for _ in 1 2 3 4 5; do
        if launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then return 0; fi
        sleep 1
    done
    launchctl bootstrap "gui/$(id -u)" "$plist"
}

# --- uninstall ---------------------------------------------------------------

if [ "$uninstall" = 1 ]; then
    if [ "$MACOS" = 1 ]; then
        for agent in daemon tray; do
            stop_agent "$agent"
            rm -f "$(agent_plist "$agent")"
        done
        rm -rf "$APP_BUNDLE"
    elif [ -f "$UNIT_FILE" ]; then
        host_systemctl disable --now aerox9ctl-daemon.service 2>/dev/null || true
        rm -f "$UNIT_FILE"
        host_systemctl daemon-reload || true
    fi
    rm -f "$BIN_DIR/aerox9ctl" "$BIN_DIR/aerox9ctl-gui" "$BIN_DIR/aerox9ctl-tray" "$DESKTOP_FILE" "$AUTOSTART_FILE"
    rm -rf "$VENV"
    echo "Uninstalled. Settings and profiles are kept in $CONFIG_HOME/aerox9ctl."
    exit 0
fi

# --- virtualenv --------------------------------------------------------------

find_python() {
    local candidate
    for candidate in ${PYTHON:-} python3 python3.14 python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

if ! PYTHON_BIN="$(find_python)"; then
    echo "error: Python 3.11 or newer is needed." >&2
    if [ "$MACOS" = 1 ]; then
        echo "The python3 that comes with macOS is too old. Install a newer one, e.g." >&2
        echo "  brew install python@3.13      (or the installer from python.org)" >&2
    fi
    exit 1
fi

echo "==> Creating virtualenv in $VENV (with $("$PYTHON_BIN" -c 'import sys; print(sys.executable)'))"
"$PYTHON_BIN" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
if [ "$editable" = 1 ]; then
    "$VENV/bin/python" -m pip install --quiet --editable "${SRC}[gui]"
else
    "$VENV/bin/python" -m pip install --quiet "${SRC}[gui]"
fi

echo "==> Writing launchers to $BIN_DIR"
mkdir -p "$BIN_DIR"
for name in aerox9ctl aerox9ctl-gui aerox9ctl-tray; do
    if [ -n "$CONTAINER" ]; then
        cat > "$BIN_DIR/$name" <<EOF
#!/bin/sh
# Generated by aerox9ctl install.sh: runs $name inside the "$CONTAINER" distrobox.
if [ -f /run/.containerenv ] && grep -qx 'name="$CONTAINER"' /run/.containerenv; then
    exec "$VENV/bin/$name" "\$@"
fi
exec distrobox-enter -n "$CONTAINER" -- "$VENV/bin/$name" "\$@"
EOF
    else
        cat > "$BIN_DIR/$name" <<EOF
#!/bin/sh
# Generated by aerox9ctl install.sh
exec "$VENV/bin/$name" "\$@"
EOF
    fi
    chmod +x "$BIN_DIR/$name"
done

# --- macOS: app bundle, launchd agents ---------------------------------------

if [ "$MACOS" = 1 ]; then
    echo "==> Creating $APP_BUNDLE"
    rm -rf "$APP_BUNDLE"
    mkdir -p "$APP_BUNDLE/Contents/MacOS"
    version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$SRC/pyproject.toml")"
    sed "s|@VERSION@|$version|g" "$SRC/packaging/macos/Info.plist" > "$APP_BUNDLE/Contents/Info.plist"
    cat > "$APP_BUNDLE/Contents/MacOS/aerox9ctl-gui" <<EOF
#!/bin/sh
# Generated by aerox9ctl install.sh
exec "$VENV/bin/aerox9ctl-gui" "\$@"
EOF
    chmod +x "$APP_BUNDLE/Contents/MacOS/aerox9ctl-gui"

    if [ "$tray" = 1 ]; then
        echo "==> Starting the menu bar battery icon (now and at login)"
        install_agent tray
    fi
    if [ "$daemon" = 1 ]; then
        echo "==> Starting the lighting daemon (now and at login)"
        install_agent daemon
    fi

    echo
    echo "Done. Run 'aerox9ctl status' or open 'Aerox 9 Wireless' from Launchpad or Spotlight."
    echo "Quit SteelSeries GG first: it keeps the mouse busy."
    if [ "$daemon" = 0 ] && [ ! -f "$(agent_plist daemon)" ]; then
        echo "To restore static colors automatically after sleep/power-off: ./install.sh --daemon"
    fi
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *)
            echo "Note: $BIN_DIR is not in your PATH. Add it with:"
            echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zprofile"
            ;;
    esac
    exit 0
fi

# --- Linux: app menu entry, autostart, systemd -------------------------------

echo "==> Adding app menu entry"
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Aerox 9 Wireless
GenericName=Mouse Settings
Comment=Configure the SteelSeries Aerox 9 Wireless mouse
Exec=$BIN_DIR/aerox9ctl-gui
Icon=input-mouse
Terminal=false
Categories=Settings;HardwareSettings;
Keywords=steelseries;mouse;dpi;rgb;
StartupWMClass=aerox9ctl
EOF

if [ "$tray" = 1 ]; then
    echo "==> Starting the tray icon at login"
    mkdir -p "$(dirname "$AUTOSTART_FILE")"
    cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Aerox 9 Battery
Comment=Battery level of the SteelSeries Aerox 9 Wireless in the system tray
Exec=$BIN_DIR/aerox9ctl-tray
Icon=input-mouse
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
fi

mkdir -p "$(dirname "$UNIT_FILE")"
sed "s|@BIN_DIR@|$BIN_DIR|" "$SRC/packaging/aerox9ctl-daemon.service" > "$UNIT_FILE"
if [ "$daemon" = 1 ]; then
    echo "==> Enabling the lighting daemon"
    host_systemctl daemon-reload
    host_systemctl enable --now aerox9ctl-daemon.service
fi

echo
echo "Done. Run 'aerox9ctl status' or open 'Aerox 9 Wireless' from the app menu."
if [ "$tray" = 1 ]; then echo "Tray icon: starts at next login, or run 'aerox9ctl-tray &' now."; fi
if [ "$daemon" = 0 ]; then
    echo "To restore static colors automatically after sleep/power-off:"
    echo "  systemctl --user enable --now aerox9ctl-daemon.service   (on the host)"
fi
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "Note: $BIN_DIR is not in your PATH." ;; esac
