# Musubi

Bridge between your phone and your PC for wireless gamepads.

Pair a Bluetooth gamepad with an Android phone, and your Linux PC gets a virtual Xbox 360 pad that games treat as a normal controller. I built it because my PC has no Bluetooth and I had an old phone lying around.

```
gamepad  --Bluetooth-->  Android app  --UDP over Wi-Fi-->  Musubi on the PC  --uinput-->  game
```

<p align="center">
  <img src="docs/assets/main.png" alt="Musubi on a phone: a drawn controller with the right stick lit in amber, the connected controller and PC, and an 11 ms ping." width="700">
</p>

**Early stage.** It has run on two Arch-based PCs and three Android phones, including an LG K40S and a Samsung A12. I've played Persona 5 with it on one PC using a BSP G6 pad. On the second PC I only checked the controller on a gamepad tester page. Please open an issue if something fails on your setup.

## What you need

- A Linux PC with Python 3, `python-evdev` and access to `/dev/uinput`. Windows and macOS won't work, because the virtual pad depends on `uinput`. The desktop app also needs GTK 4 and PyGObject.
- An Android phone with the app open while you play. The app declares Android 5.0 or newer, but it has only run on those three phones.
- A Bluetooth gamepad that Android recognizes as a gamepad.
- The phone and the PC on the same network, or a USB cable between them.

## Setup

### 1. Prepare the PC

On Arch-based systems:

```sh
sudo pacman -S --needed git python python-evdev python-pyusb gtk4 python-gobject python-cairo
```

Use the equivalent packages on other distributions. Then get the code and check that you can use `uinput`:

```sh
git clone https://github.com/Multex/musubi.git
cd musubi
test -r /dev/uinput && test -w /dev/uinput && echo 'uinput access OK' || echo 'Check uinput access'
```

If that prints `Check uinput access`, see [uinput permissions](#uinput-permissions).

### 2. Start the desktop app

This adds Musubi to your app launcher, so you only need the terminal once:

```sh
./install.sh
```

Open **Musubi** from your launcher. It shows the address to type on your phone. If your `ufw` firewall blocks the phone, the app offers to open UDP port 9999 for your local network. If you use another firewall, allow that port yourself.

### 3. Set up the phone

1. Download `musubi.apk` from [Releases](https://github.com/Multex/musubi/releases/latest) on the phone and install it. Android will ask you to allow installs from that source, and you can turn that off again afterwards.
2. Disconnect the pad from the PC if it is plugged in, because some pads stay in USB mode.
3. Put the pad in pairing mode and pair it in the phone's Bluetooth settings. The button combination depends on the pad, so check its manual.
4. Open Musubi, type your PC's IP in **PC IP address** and tap **Save PC**.
5. On the PC, tap **Allow** when the desktop app asks about the phone. The phone should then show the PC with its ping in milliseconds.
6. Press a button. It lights up on both drawings, and the virtual pad reacts on the PC.

Keep the app open and in front while you play. **Game mode** dims the screen and stops drawing the controller, and it turns on by itself after 1 minute without touching the screen. You can turn that off in **Settings**.

Update the PC and the phone together. The desktop app still works with phone app 0.2.0, but it can't show that phone's name or controller.

### Over a USB cable

The cable is still being tested, and so far it has worked on one phone. Wi-Fi stays the main way to connect.

Plug the phone into the PC while the desktop app is running, and set its USB mode to **File transfer** from the USB notification. Musubi then switches the phone into accessory mode, and Android asks whether to open Musubi when this accessory connects. Tick **Always** and tap **OK**. From then on, plugging in opens the app and the pad works over the cable, with no IP to type and no firewall to open. The phone charges while you play.

This needs `python-pyusb` and the USB rule that `./install.sh` adds. The cable only works with the desktop app, not with `musubi.py` in a terminal.

### Without the desktop app

You can still run the receiver in a terminal, with your phone's IP. You need to open the firewall yourself:

```sh
sudo ufw allow from 192.168.1.10 to any port 9999 proto udp
```

```sh
python3 desktop/musubi.py 192.168.1.10
```

## Desktop app

- **Allowed phones:** a new phone asks for your approval once. Manage them in **Settings > Manage phones**. Phones are recognized by IP, so reserve the address in your router if you can, or the phone will ask again when it changes. If you ignore or remove a phone, the waiting screen offers **Allow** for as long as that phone keeps trying to connect.
- **Virtual controller:** it only exists while a controller is connected to the phone, so games don't see a pad you can't use.
- **Start with my PC:** this adds a systemd user service in **Settings**. It needs a desktop session that starts `graphical-session.target`. If yours doesn't, the app shows the command to add to your desktop's startup instead.
- **Tray:** if your desktop has a tray, closing the window keeps Musubi running there. Without one, closing the window quits.

## Remap

<p align="center">
  <img src="docs/assets/remap.png" alt="The remap setup on step 1 of 16: the A button is highlighted on the drawing, with Back, Skip and Cancel buttons beside it." width="700">
</p>

Tap **Remap** and press each button as the app asks, or tap **Skip** for the ones your pad doesn't have. You can try the layout before saving it. Each controller keeps its own layout, and **Reset** goes back to the default. Sticks can't be remapped.

## Limits

- **The app must stay in the foreground.** If you leave it, the phone stops sending and the PC releases every button after half a second. The app keeps the screen on while it is open.
- **One pad at a time.** Two pads paired with the phone would be merged into one.
- **No rumble.** The PC only answers the app's pings, and nothing travels back to the pad.
- **The guide button can't be remapped,** and it may not arrive at all, because Android can keep it for itself.
- **One phone at a time.** If two allowed phones are on, the first one keeps the pad until it has been silent for 5 seconds.
- **Use it only on a network you trust.** Packets are not encrypted or authenticated, and approval only checks the IP. Someone on your network who fakes an allowed phone's address could press buttons in your game.

## Latency

Total latency, from pressing a button to the game reacting, has not been measured.

The only number so far is the network ping between the PC and the K40S with the app open: 9 ms median and 71 ms maximum over 300 pings, with none lost. That is a round trip with the PC on cable and the phone on 2.4 GHz Wi-Fi, and it leaves out Bluetooth and Android's own input handling.

Persona 5 plays fine, but it is turn-based. I can't vouch for rhythm or fighting games yet.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| The game doesn't see the pad | Connect the controller to the phone first, then restart the game. |
| The game sees two pads | Your real pad is still connected to the PC. Disconnect it. |
| The desktop app says it **can't use** the USB cable | Install `python-pyusb`, or run `./install.sh` again so the USB rule is added, then plug the phone back in. |
| The phone says **PC not responding** | Check that Musubi is running on the PC and that you allowed the phone, then check the firewall and the phone's IP. Some routers also block Wi-Fi devices from reaching wired ones. |
| A button shows `(not mapped)` on the phone | Your pad sends a key Musubi doesn't know yet. Use **Remap** to assign it. |
| The desktop app says it **can't run** | Another program is using UDP port 9999. Check that `musubi.py` isn't still running in a terminal. |
| **Open the port** fails | Your desktop may have no password prompt for system changes. The app shows a command you can run in a terminal instead. |

### uinput permissions

If `/dev/uinput` doesn't exist, load the module:

```sh
sudo modprobe uinput
```

If it exists but you can't write to it, this rule gives access to the user logged in at the PC:

```sh
echo 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/60-musubi-uinput.rules
```

```sh
sudo udevadm control --reload && sudo udevadm trigger --name-match=uinput
```

Any program you run can then create virtual input devices, so remove the rule if you stop using Musubi.

## How it works

Each packet is 20 bytes and holds the whole pad state: 11 button bits, then 8 axes (`!I8h` in `desktop/musubi.py`). The app sends one on every change and at least every 50 ms, so a lost packet is repaired by the next one and a release can't get stuck. After 0.5 seconds of silence, the receiver releases everything.

Once a second the app also sends an 8-byte ping, and the receiver echoes it back to allowed phones. That is how the app knows the PC is answering and what the round trip takes. It also sends a hello packet with the phone's model, the controller's name and that ping time, which the desktop app shows.

Over a USB cable the phone uses Android Open Accessory instead of Wi-Fi. The PC asks the phone to switch modes (`desktop/cable.py`), and then the same packets travel over USB, each one starting with its length in 2 bytes.

The desktop app runs the receiver as a separate process, so drawing the window can't delay the pad.

The virtual pad uses the same IDs and layout as the kernel's `xpad` driver, so games map it as an Xbox 360 pad.

## Build the app

`build.sh` calls the SDK tools directly, without Gradle. You need `platforms;android-28` and `build-tools;34.0.0` in `~/Android/Sdk`, or in `ANDROID_HOME`. It needs Java 17, because `d8` crashes on Java 27. The script looks in `/usr/lib/jvm/java-17-temurin`, so set `JAVA_HOME` if yours is somewhere else.

```sh
./build.sh
```

The result is `android/build/musubi.apk`. The first build creates `android/debug.keystore`, which stays out of git. Keep that file: Android refuses updates signed with a different key.

`build.sh` also runs the button map test. Run the PC tests with:

```sh
python3 desktop/test_musubi.py
```

```sh
python3 desktop/test_system.py
```

## License

[MIT](LICENSE)
