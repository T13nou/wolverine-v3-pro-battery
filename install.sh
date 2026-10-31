#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Install, update or remove Wolverine V3 Pro Battery (udev rule, Plasma widget, CLI).
#
#   curl -fsSL https://raw.githubusercontent.com/T13nou/wolverine-v3-pro-battery/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/T13nou/wolverine-v3-pro-battery/main/install.sh | bash -s -- uninstall
#
# Running the install again updates to the latest release. sudo is only used for the udev rule.

set -euo pipefail

REPO="T13nou/wolverine-v3-pro-battery"
PLASMOID_ID="net.liberix.wolverinebattery"
RULE_NAME="70-wolverine-v3-pro-battery.rules"
RULE_PATH="/etc/udev/rules.d/$RULE_NAME"
CLI_LINK="$HOME/.local/bin/wolverine-battery"
CLI_MARKER="Installed by wolverine-v3-pro-battery/install.sh"
PLASMOID_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/plasma/plasmoids/$PLASMOID_ID"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

# Questions go to the terminal, since stdin is the script itself when piped from curl.
ask() {
    local answer
    if (: </dev/tty) 2>/dev/null; then
        read -r -p "$1 [y/N] " answer </dev/tty || answer=""
    else
        answer=""
    fi
    [[ $answer =~ ^[YyOo] ]]
}

need() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is required ($2)."
}

reload_udev() {
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=hidraw
}

restart_plasma() {
    if ask "Restart Plasma now, so the system tray picks up the change?"; then
        systemctl --user restart plasma-plasmashell
    else
        say "Restart Plasma later (log out, or: systemctl --user restart plasma-plasmashell)."
    fi
}

install() {
    need curl "to download the release"
    need tar "to unpack it"
    need python3 "the tool is a Python script"
    need kpackagetool6 "the widget needs KDE Plasma 6"
    need sudo "to install the udev rule"

    local tag
    tag=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])') \
        || die "cannot find the latest release of $REPO."

    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    say "Downloading $tag"
    curl -fsSL "https://github.com/$REPO/archive/refs/tags/$tag.tar.gz" | tar -xz -C "$tmp" --strip-components=1

    if cmp -s "$tmp/udev/$RULE_NAME" "$RULE_PATH"; then
        say "udev rule already up to date"
    else
        say "Installing the udev rule (sudo): $RULE_PATH"
        sudo install -m 644 "$tmp/udev/$RULE_NAME" "$RULE_PATH"
        reload_udev
    fi

    if [ -d "$PLASMOID_DIR" ]; then
        say "Updating the Plasma widget"
        kpackagetool6 -t Plasma/Applet -u "$tmp/plasmoid/$PLASMOID_ID" >/dev/null
    else
        say "Installing the Plasma widget"
        kpackagetool6 -t Plasma/Applet -i "$tmp/plasmoid/$PLASMOID_ID" >/dev/null
    fi

    # The command-line tool runs the copy shipped inside the widget.
    mkdir -p "$(dirname "$CLI_LINK")"
    printf '#!/bin/sh\n# %s\nexec python3 "%s" "$@"\n' "$CLI_MARKER" \
        "$PLASMOID_DIR/contents/code/wolverine_battery.py" >"$CLI_LINK"
    chmod 755 "$CLI_LINK"
    say "Command-line tool: $CLI_LINK (try: wolverine-battery --firmware)"
    case ":$PATH:" in
        *":$(dirname "$CLI_LINK"):"*) ;;
        *) say "Note: $(dirname "$CLI_LINK") is not in your PATH." ;;
    esac

    restart_plasma
    say "Done: Wolverine V3 Pro Battery $tag. The controller must be in PC mode (lsusb shows 1532:0a4c)."
}

uninstall() {
    if [ -d "$PLASMOID_DIR" ]; then
        say "Removing the Plasma widget"
        kpackagetool6 -t Plasma/Applet -r "$PLASMOID_ID" >/dev/null
    fi
    if grep -qs "$CLI_MARKER" "$CLI_LINK"; then
        say "Removing $CLI_LINK"
        rm -f "$CLI_LINK"
    fi
    if [ -e "$RULE_PATH" ]; then
        say "Removing the udev rule (sudo): $RULE_PATH"
        sudo rm -f "$RULE_PATH"
        reload_udev
    fi
    restart_plasma
    say "Uninstalled. The controller keeps its last sleep timer setting."
}

case "${1:-install}" in
    install | update) install ;;
    uninstall | remove) uninstall ;;
    *) die "usage: install.sh [install|update|uninstall]" ;;
esac
