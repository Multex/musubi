# padbridge

Bridge between your phone and your PC for wireless gamepads.

Pair a Bluetooth gamepad with an Android phone, and your Linux PC gets a virtual Xbox 360 pad that games treat as a normal controller. I built it because my PC has no Bluetooth and I had an old phone lying around.

```
gamepad  --Bluetooth-->  Android app  --UDP over Wi-Fi-->  padbridge.py  --uinput-->  game
```

**Early stage.** Tested on one setup: an LG K40S, a BSP G6 pad and a CachyOS PC, playing Persona 5. Nobody has followed these steps on another machine yet, so please open an issue if something fails.

## What you need

- A Linux PC with Python 3, `python-evdev` and access to `/dev/uinput`. Windows and macOS won't work, because the virtual pad depends on `uinput`.
- An Android phone with the app open while you play. The app declares Android 5.0 or newer, but it has only run on the K40S.
- A Bluetooth gamepad that Android recognizes as a gamepad.
- The phone and the PC on the same network.

## Setup

### 1. Prepare the PC

On Arch-based systems:

```sh
sudo pacman -S --needed git python python-evdev
```

Use the equivalent packages on other distributions. Then get the code and check that you can use `uinput`:

```sh
git clone https://github.com/Multex/padbridge.git
cd padbridge
test -r /dev/uinput && test -w /dev/uinput && echo 'uinput access OK' || echo 'Check uinput access'
```

If that prints `Check uinput access`, see [uinput permissions](#uinput-permissions).

### 2. Open the firewall

The phone sends to UDP port 9999. With `ufw`, this lets only your phone through (replace the address with your phone's IP):

```sh
sudo ufw allow from 192.168.1.10 to any port 9999 proto udp
```

The phone's IP is usually in **Settings > About phone > Status**. Reserve it in your router if you can: when it changes, you have to update both this rule and the command in step 3.

### 3. Start the receiver

```sh
python3 padbridge.py 192.168.1.10
```

Use your phone's IP here too. Packets from any other address are ignored. To find your PC's IP, run `ip -4 addr`.

### 4. Set up the phone

1. Download `padbridge.apk` from [Releases](https://github.com/Multex/padbridge/releases/latest) on the phone and install it. Android will ask you to allow installs from that source, and you can turn that off again afterwards.
2. Disconnect the pad from the PC if it is plugged in, because some pads stay in USB mode.
3. Put the pad in pairing mode and pair it in the phone's Bluetooth settings. The button combination depends on the pad, so check its manual.
4. Open padbridge, type your PC's IP in **PC IP address** and tap **Save PC**. After a moment, the app should show the PC with its ping in milliseconds.
5. Press a button. It lights up on the drawing, and the virtual pad reacts on the PC.

Keep the app open and in front while you play. **Game mode** dims the screen and stops drawing the controller, and it turns on by itself after 1 minute without touching the screen. You can turn that off in **Settings**.

If you update from 0.1.0, restart `padbridge.py` with the new version too. Otherwise the app shows **PC not responding**, because the old receiver doesn't answer its pings.

## Remap

Tap **Remap** and press each button as the app asks, or tap **Skip** for the ones your pad doesn't have. You can try the layout before saving it. Each controller keeps its own layout, and **Reset** goes back to the default. Sticks can't be remapped.

## Limits

- **The app must stay in the foreground.** If you leave it, the phone stops sending and the PC releases every button after half a second. The app keeps the screen on while it is open.
- **One pad at a time.** Two pads paired with the phone would be merged into one.
- **No rumble.** The PC only answers the app's pings, and nothing travels back to the pad.
- **The guide button can't be remapped,** and it may not arrive at all, because Android can keep it for itself.
- **Use it only on a network you trust.** Packets are not encrypted or authenticated. Someone on your network who fakes the phone's address could press buttons in your game.

## Latency

Total latency, from pressing a button to the game reacting, has not been measured.

The only number so far is the network ping between the PC and the K40S with the app open: 9 ms median and 71 ms maximum over 300 pings, with none lost. That is a round trip with the PC on cable and the phone on 2.4 GHz Wi-Fi, and it leaves out Bluetooth and Android's own input handling.

Persona 5 plays fine, but it is turn-based. I can't vouch for rhythm or fighting games yet.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| The game doesn't see the pad | Start `padbridge.py` first, then restart the game. |
| The game sees two pads | Your real pad is still connected to the PC. Disconnect it. |
| The app says **PC not responding** | Check that `padbridge.py` is running, then check the firewall rule and the phone's IP. Some routers also block Wi-Fi devices from reaching wired ones. |
| A button shows `(not mapped)` in the app | Your pad sends a key padbridge doesn't know yet. Use **Remap** to assign it. |
| The receiver prints `link lost` | The phone stopped sending. Check that the app is still in front and that the phone is on Wi-Fi. |

### uinput permissions

If `/dev/uinput` doesn't exist, load the module:

```sh
sudo modprobe uinput
```

If it exists but you can't write to it, this rule gives access to the user logged in at the PC:

```sh
echo 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/60-padbridge-uinput.rules
```

```sh
sudo udevadm control --reload && sudo udevadm trigger --name-match=uinput
```

Any program you run can then create virtual input devices, so remove the rule if you stop using padbridge.

## How it works

Each packet is 20 bytes and holds the whole pad state: 11 button bits, then 8 axes (`!I8h` in `padbridge.py`). The app sends one on every change and at least every 50 ms, so a lost packet is repaired by the next one and a release can't get stuck. After 0.5 seconds of silence, the receiver releases everything.

Once a second the app also sends an 8-byte ping, and the receiver echoes it back. That is how the app knows the PC is answering and what the round trip takes.

The virtual pad uses the same IDs and layout as the kernel's `xpad` driver, so games map it as an Xbox 360 pad.

## Build the app

`build.sh` calls the SDK tools directly, without Gradle. You need `platforms;android-28` and `build-tools;34.0.0` in `~/Android/Sdk`, or in `ANDROID_HOME`. It needs Java 17, because `d8` crashes on Java 27. The script looks in `/usr/lib/jvm/java-17-temurin`, so set `JAVA_HOME` if yours is somewhere else.

```sh
./build.sh
```

The result is `android/build/padbridge.apk`. The first build creates `android/debug.keystore`, which stays out of git. Keep that file: Android refuses updates signed with a different key.

`build.sh` also runs the button map test. Run the receiver tests with:

```sh
python3 test_padbridge.py
```

## License

[MIT](LICENSE)
