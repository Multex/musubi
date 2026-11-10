#!/bin/sh
# Adds Musubi to your app launcher, pointing at this folder. Run it again if you move the folder.
set -eu
dir=$(cd "$(dirname "$0")" && pwd)
apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
icons=${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps
mkdir -p "$apps" "$icons"
cp "$dir/docs/assets/icon.svg" "$icons/io.github.multex.musubi.svg"
# Named after the app ID, so desktops can match the window to this icon.
cat > "$apps/io.github.multex.musubi.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Musubi
Comment=Use a Bluetooth gamepad paired with your phone on this PC
Exec=python3 "$dir/desktop/linux/gui.py"
Icon=$icons/io.github.multex.musubi.svg
Categories=Game;Utility;
EOF
echo "Added Musubi to your app launcher."

# MTP, PTP and ADB are how Android phones show up; 18d1:2d00/2d01 is a phone already in accessory mode.
rules=/etc/udev/rules.d/70-musubi.rules
rule='SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ENV{ID_USB_INTERFACES}=="*:ffff00:*|*:060101:*|*:ff4201:*", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="18d1", ATTR{idProduct}=="2d0[01]", TAG+="uaccess"'
if [ "$(cat "$rules" 2>/dev/null)" != "$rule" ]; then
    echo "To use phones over a USB cable, Musubi needs a udev rule. sudo will ask for your password."
    echo "$rule" | sudo tee "$rules" >/dev/null
    sudo udevadm control --reload
    echo "Added the USB rule. If your phone is plugged in, unplug it and plug it back in."
fi
