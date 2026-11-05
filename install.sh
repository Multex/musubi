#!/bin/sh
# Adds Musubi to your app launcher, pointing at this folder. Run it again if you move the folder.
set -eu
dir=$(cd "$(dirname "$0")" && pwd)
apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
mkdir -p "$apps"
# Named after the app ID, so desktops can match the window to this icon.
cat > "$apps/io.github.multex.musubi.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Musubi
Comment=Use a Bluetooth gamepad paired with your phone on this PC
Exec=python3 "$dir/desktop/gui.py"
Icon=input-gaming
Categories=Game;Utility;
EOF
echo "Added Musubi to your app launcher."
