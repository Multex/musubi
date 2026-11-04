#!/bin/sh
# Adds padbridge to your app launcher, pointing at this folder. Run it again if you move the folder.
set -eu
dir=$(cd "$(dirname "$0")" && pwd)
apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
mkdir -p "$apps"
cat > "$apps/padbridge.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=padbridge
Comment=Use a Bluetooth gamepad paired with your phone on this PC
Exec=python3 "$dir/desktop/gui.py"
Icon=input-gaming
Categories=Game;Utility;
StartupWMClass=io.github.multex.padbridge
EOF
echo "Added padbridge to your app launcher."
