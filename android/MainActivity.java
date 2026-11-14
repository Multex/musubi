package io.github.multex.musubi;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.LayerDrawable;
import android.graphics.drawable.RippleDrawable;
import android.hardware.input.InputManager;
import android.hardware.usb.UsbAccessory;
import android.hardware.usb.UsbManager;
import android.media.AudioAttributes;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTimestamp;
import android.media.AudioTrack;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelFileDescriptor;
import android.os.SystemClock;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.text.Editable;
import android.text.InputType;
import android.text.SpannableString;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.text.format.DateFormat;
import android.text.style.ForegroundColorSpan;
import android.util.Log;
import android.util.Patterns;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewTreeObserver;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Button;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.lang.reflect.Method;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity {
    static final int BACKGROUND = 0xFF0D0C0A, SURFACE = 0xFF171512, BORDER = 0xFF332E28, IDLE = 0xFF6B6256;
    static final int TEXT = 0xFFE6DEC8, MUTED = 0xFF8F8573, ACTIVE = 0xFFFF9E1B, ALERT = 0xFFE04F3E;
    static final int LATER = 0xFF4A443B, THUMB = 0xFF2A2621, PRIMARY_HOVER = 0xFFF4EDDA, GAME_BG = 0xFF000000;

    // The first 11 bits match BUTTONS in musubi.py; the rest become trigger and hat axes.
    static final int A_BIT = 0, B_BIT = 1, X_BIT = 2, Y_BIT = 3, L1_BIT = 4, R1_BIT = 5, SELECT_BIT = 6, START_BIT = 7;
    static final int L3_BIT = 9, R3_BIT = 10, L2_BIT = 11, R2_BIT = 12;
    static final int LEFT_BIT = 13, RIGHT_BIT = 14, UP_BIT = 15, DOWN_BIT = 16;
    private static final int[] BUTTON_KEYS = {
        KeyEvent.KEYCODE_BUTTON_A, KeyEvent.KEYCODE_BUTTON_B,
        KeyEvent.KEYCODE_BUTTON_X, KeyEvent.KEYCODE_BUTTON_Y,
        KeyEvent.KEYCODE_BUTTON_L1, KeyEvent.KEYCODE_BUTTON_R1,
        KeyEvent.KEYCODE_BUTTON_SELECT, KeyEvent.KEYCODE_BUTTON_START,
        KeyEvent.KEYCODE_BUTTON_MODE,
        KeyEvent.KEYCODE_BUTTON_THUMBL, KeyEvent.KEYCODE_BUTTON_THUMBR,
        KeyEvent.KEYCODE_BUTTON_L2, KeyEvent.KEYCODE_BUTTON_R2,
        KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT,
        KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN,
    };
    private static final String[] BUTTON_NAMES = {
        "A", "B", "X", "Y", "L1", "R1", "Select", "Start", "Guide", "L3", "R3", "L2", "R2",
        "Left", "Right", "Up", "Down",
    };
    private static final int WIRE_BUTTONS = (1 << 11) - 1;

    private static final int[] STEP_CONTROLS = {
        A_BIT, B_BIT, X_BIT, Y_BIT, L1_BIT, R1_BIT, L2_BIT, R2_BIT, SELECT_BIT, START_BIT,
        L3_BIT, R3_BIT, UP_BIT, DOWN_BIT, LEFT_BIT, RIGHT_BIT,
    };
    private static final String[] STEP_TITLES = {
        "Press A", "Press B", "Press X", "Press Y", "Press L1", "Press R1", "Press L2", "Press R2",
        "Press Select", "Press Start", "Press down the left stick", "Press down the right stick",
        "Press D-pad up", "Press D-pad down", "Press D-pad left", "Press D-pad right",
    };
    private static final String[] STEP_NAMES = {
        "A", "B", "X", "Y", "L1", "R1", "L2", "R2", "Select", "Start", "L3", "R3",
        "D-pad up", "D-pad down", "D-pad left", "D-pad right",
    };

    // Added in Android 10, so the android-28 jar doesn't have the constant.
    private static final int WIFI_MODE_FULL_LOW_LATENCY = 4;
    private static final int PORT = 9999;
    private static final int HEARTBEAT_MS = 50;
    private static final int PING_INTERVAL_MS = 1000;
    private static final int PING_SIZE = 8;
    private static final byte[] HELLO_MAGIC = {'P', 'B', 'H', 'I'};
    // Below 0x20, so 0.5.x desktops drop it from the controller name as a control character.
    private static final byte FEATURE_AUDIO = 1;
    private static final byte FEATURE_RUMBLE = 2;
    // Most stable: asks the PC for 7.5 ms packets instead of 5.
    private static final byte FEATURE_STABLE = 4;
    // Magic, sequence number, then PCM, matching AUDIO in musubi.py.
    private static final byte[] AUDIO_MAGIC = {'P', 'B', 'A', 'U'};
    private static final int AUDIO_HEADER = AUDIO_MAGIC.length + 2;
    // Magic, strong and weak motor 0..65535, then ms (0 = until the next message), matching RUMBLE in musubi.py.
    private static final byte[] RUMBLE_MAGIC = {'P', 'B', 'R', 'M'};
    private static final int RUMBLE_SIZE = RUMBLE_MAGIC.length + 6;
    // Magic, then the PC name in UTF-8, matching NAME_MAGIC in core.py. Only sent over the cable.
    private static final byte[] NAME_MAGIC = {'P', 'B', 'N', 'M'};
    private static final String USB_PC_NAME = "PC on USB";
    // The PC repeats an endless rumble every second, so a lost stop can't leave the pad buzzing past this.
    private static final int RUMBLE_CAP_MS = 2000;
    // 5 ms of 48 kHz stereo PCM is under 1 KB.
    private static final int UDP_RECEIVE_SIZE = 2048;
    private static final int AUDIO_RATE = 48000;
    // Pause the track after a short silence, and only give it up after a long one, since rebuilding it delays the next sound.
    private static final int AUDIO_IDLE_MS = 500;
    private static final int AUDIO_RELEASE_MS = 30000;
    // Android warns a moment before the earbuds' sound moves to the speaker, while they're still listed as connected.
    private static final int NOISY_HOLD_MS = 2000;
    // About 150 stats lines per file, and the previous file is kept, so a long session is still there after unplugging.
    private static final int STATS_FILE_BYTES = 20000;
    private static final int MAX_NAME_LENGTH = 64;
    // The accessory driver drops data when a read asks for less than this.
    private static final int USB_READ_SIZE = 16384;
    // Long enough that one lost ping doesn't flash an error at someone mid-game.
    private static final int PC_TIMEOUT_MS = 3000;
    private static final int STICK_MAX = 32767;
    private static final int TRIGGER_MAX = 255;
    private static final int UI_INTERVAL_MS = 100;
    private static final int TICK_MS = 500;
    private static final int AUTO_GAME_MODE_MS = 60000;
    // A single press can fire a key and an axis a moment apart; both belong to the same step.
    private static final int CAPTURE_WINDOW_MS = 150;
    private static final int STEP_COOLDOWN_MS = 350;
    // Sticks rarely rest at exactly zero, and this only affects what the drawing shows.
    private static final float DISPLAY_DEAD_ZONE = 0.05f;
    private static final String REPO = "https://github.com/Multex/musubi";

    // CONTROLLERS is the controllers page, opened from HOME.
    private static final int CONTROLLERS = 0, GAME = 1, REMAP = 2, REMAP_DONE = 3, SETTINGS = 4, EDIT_PC = 5, HOME = 6, HEADPHONES = 7;
    private static final int CONNECTING = 0, RESPONDING = 1, LOST = 2;
    private static final int PRIMARY = 0, SECONDARY = 1, PLAIN = 2;
    private static final int ICON_DOWN = 0, ICON_RIGHT = 1, ICON_LEFT = 2, ICON_ARROW_UP = 3, ICON_ARROW_DOWN = 4;
    private static final int ICON_SQUARE = 5, ICON_HALF = 6, ICON_INFO = 7, ICON_SLIDERS = 8;
    private static final int NODE_LIVE = 0, NODE_IDLE = 1, NODE_LATER = 2;
    // How often the player thread re-measures the delay, in 5 ms chunks.
    private static final int DELAY_EVERY = 50;

    private static final class Pc {
        String name;
        String ip;

        Pc(String name, String ip) {
            this.name = name;
            this.ip = ip;
        }
    }

    private final Object lock = new Object();
    private final Object audioLock = new Object();
    private final AudioBuffer audio = new AudioBuffer();
    private long lastAudioAt;
    private Thread player;
    private boolean audioBroken;
    // No sound may play: PC sound is off, or it's set to headphones and none are connected. Guarded by audioLock.
    private boolean muted = true;
    // PC sound is opt-in; while off, the hello doesn't offer audio, so the PC never creates its output.
    private volatile boolean soundOn;
    // Force the phone's own speaker instead of following the system route (Android 6+).
    private volatile boolean speaker;
    // The "Most stable" sound buffer, pref sound_buffer = "stable"; else lowest delay.
    private volatile boolean stableSound;
    // Settings shows "measuring the new delay" from a buffer switch until the next reading.
    private boolean measuringNewDelay;
    private boolean settingsSoundLive;
    // The connected earbuds or wired headphones, or null. UI thread only.
    private String earbuds;
    private boolean earbudsWired;
    private long quietUntil;
    // What the AudioTrack queues ahead of the speaker, on top of the jitter buffer. Player thread only.
    private AudioBuffer.Track trackSize;
    private int held;
    // Peaks of the PCM just played, for the headphones drawing; the UI thread takes and clears them.
    private volatile float peakLeft, peakRight;
    // Queued in Musubi plus what the track, mixer and output add, without the network; -1 until first measured.
    private volatile int delayMs = -1;
    private final HeadphonesView.Source levels = out -> {
        if (this.backgrounded || !receivingAudio()) return false;
        out[0] = peakLeft;
        out[1] = peakRight;
        peakLeft = peakRight = 0;
        return true;
    };
    private final short[] axes = new short[6];
    // Sticks as the pad reports them, before the deadzone, for the Settings preview. UI thread only.
    private float rawLx, rawLy, rawRx, rawRy;
    private boolean dirty;
    private InetAddress target;

    private volatile boolean running;
    private volatile long targetSince;
    private volatile long lastPongAt;
    private volatile long rttMs;
    private volatile String networkError;
    private volatile long nextPing;
    private volatile Thread sender;
    private UsbManager usbManager;
    private ParcelFileDescriptor usbFile;
    private volatile FileOutputStream usbOut;
    private final Pc usbPc = new Pc(USB_PC_NAME, "Cable");
    private WifiManager.WifiLock wifiLock;
    private InputManager inputManager;
    private AudioManager audioManager;
    private SharedPreferences prefs;
    /** Read by ControllerReceiver, which skips its notification while Musubi is on screen. */
    static volatile boolean onScreen;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private final List<Pc> pcs = new ArrayList<>();
    private int selectedPc;
    private boolean autoGameMode;
    private volatile boolean vibration = true;
    private volatile int vibrationStrength = 100;
    // Stick deadzone radius, 0..1; read once per motion event.
    private volatile float deadzone;

    private volatile int deviceId = -1;
    private volatile String deviceName;
    private String descriptor;
    private int stickXAxis = MotionEvent.AXIS_Z, stickYAxis = MotionEvent.AXIS_RZ;
    private ButtonMap map = defaultMap();
    private int axisButtons;
    private String unmapped;

    private int screen = HOME;
    private int renderedScreen = -1;
    private ScrollView scroller;
    private int gameFrom = HOME;
    private int selectedPlayer = 1;
    private int shownPcState = -1;
    private long lastTouch;
    private long lastUiUpdate;
    private String draftName = "", draftIp = "", formError;
    private int editIndex = -1;

    private PadView padView;
    private StickPreview stickPreview;
    private float rumbleStrong, rumbleWeak;
    private TextView readout, pcDetail, settingsPing, settingsSoundNow, soundName, soundDetail;
    private TextView earbudsName, earbudsDetail, soundReadout, soundStatus;
    private HeadphonesView headphones;

    private int step;
    private ButtonMap pending;
    private final List<ButtonMap> history = new ArrayList<>();
    private final HashMap<Integer, Integer> claimed = new HashMap<>();
    private final List<Integer> capture = new ArrayList<>();
    private boolean[] stepMapped;
    private long ignoreInputUntil;
    private String stepNote;

    private volatile boolean backgrounded;
    // Once the PC stops answering, end the background session like leaving the app unconnected.
    private final Runnable watchPc = new Runnable() {
        @Override
        public void run() {
            if (pcState() == LOST) {
                stopAll();
            } else {
                refreshOutput();
                handler.postDelayed(this, TICK_MS);
            }
        }
    };
    private final BroadcastReceiver stopReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            stopAll();
            finish();
        }
    };
    private final BroadcastReceiver noisyReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            quietUntil = SystemClock.uptimeMillis() + NOISY_HOLD_MS;
            refreshOutput();
        }
    };
    private final Runnable finishCapture = this::finishCapture;
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            refreshStatus();
            handler.postDelayed(this, TICK_MS);
        }
    };
    private final InputManager.InputDeviceListener deviceListener = new InputManager.InputDeviceListener() {
        @Override public void onInputDeviceAdded(int id) { updateController(); }
        @Override public void onInputDeviceRemoved(int id) { updateController(); }
        @Override public void onInputDeviceChanged(int id) { updateController(); }
    };

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        // Without this, Wi-Fi power saving batches packets and adds tens of milliseconds.
        wifiLock = wifi.createWifiLock(
            Build.VERSION.SDK_INT >= 29 ? WIFI_MODE_FULL_LOW_LATENCY : WifiManager.WIFI_MODE_FULL_HIGH_PERF, "musubi");
        inputManager = (InputManager) getSystemService(Context.INPUT_SERVICE);
        usbManager = (UsbManager) getSystemService(Context.USB_SERVICE);
        audioManager = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
        prefs = getPreferences(MODE_PRIVATE);
        ControllerReceiver.createChannel(this);
        autoGameMode = prefs.getBoolean("autoGameMode", true);
        vibration = prefs.getBoolean("vibration", true);
        vibrationStrength = prefs.getInt("vibration_strength", 100);
        deadzone = prefs.getInt("deadzone", 0) / 100f;
        soundOn = prefs.getBoolean("sound", false);
        speaker = prefs.getBoolean("speaker", false);
        stableSound = "stable".equals(prefs.getString("sound_buffer", "low"));
        synchronized (audioLock) {
            audio.setSteady(stableSound);
        }
        // Registered for the app's whole life, since the sound also plays in the background.
        registerReceiver(noisyReceiver, new IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY));
        refreshOutput();
        loadPcs();
        applySelectedPc();
        findController();
        render();
    }

    @Override
    protected void onResume() {
        super.onResume();
        onScreen = true;
        ControllerReceiver.cancel(this);
        // Notifications may have been allowed in Android's settings meanwhile.
        if (screen == SETTINGS) render();
        lastTouch = SystemClock.uptimeMillis();
        // Coming back from the background keeps the same socket, threads and AudioTrack.
        leaveBackground();
        if (!running) {
            wifiLock.acquire();
            running = true;
            sender = new Thread(this::sendLoop, "musubi-sender");
            sender.start();
            targetSince = lastTouch;
        }
        openAccessory();
        inputManager.registerInputDeviceListener(deviceListener, handler);
        updateController();
        handler.post(tick);
    }

    @Override
    protected void onPause() {
        super.onPause();
        onScreen = false;
        handler.removeCallbacks(tick);
        handler.removeCallbacks(finishCapture);
        capture.clear();
        inputManager.unregisterInputDeviceListener(deviceListener);
        rumble(deviceId, 0, 0, 0);
        showRumble(0, 0, 0);
        // The app gets no key-up events while in the background.
        axisButtons = 0;
        clearInput();
        // Connected, so keep the PC's sound playing; the sender keeps the neutral pad alive on the PC.
        if (prefs.getBoolean("background", true) && pcState() == RESPONDING) enterBackground();
        else stopAll();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        unregisterReceiver(noisyReceiver);
        stopAll();
        closeAccessory();
    }

    private void stopAll() {
        if (!running) return;
        leaveBackground();
        running = false;
        sender.interrupt();
        wifiLock.release();
        stopPlayer();
    }

    /** Ends the player thread, which releases its AudioTrack; the next packet that may play starts a new one. */
    private void stopPlayer() {
        synchronized (audioLock) {
            player = null;
            audio.reset();
            audioLock.notifyAll();
        }
        delayMs = -1;
    }

    private void enterBackground() {
        backgrounded = true;
        registerReceiver(stopReceiver, new IntentFilter(KeepAliveService.ACTION_STOP));
        handler.postDelayed(watchPc, TICK_MS);
        showKeepAlive();
        synchronized (audioLock) {
            audio.setBackground(true);
        }
    }

    private void leaveBackground() {
        if (!backgrounded) return;
        backgrounded = false;
        synchronized (audioLock) {
            audio.setBackground(false);
        }
        unregisterReceiver(stopReceiver);
        handler.removeCallbacks(watchPc);
        stopService(new Intent(this, KeepAliveService.class));
    }

    /** Starts the service, or updates its notification when the cable comes or goes. */
    private void showKeepAlive() {
        startService(new Intent(this, KeepAliveService.class).putExtra(KeepAliveService.EXTRA_PC, currentPc().name)
            .putExtra(KeepAliveService.EXTRA_SOUND, soundOn));
    }

    /** Android only lists the accessory once Musubi on the PC has switched the phone over. */
    private void openAccessory() {
        UsbAccessory[] accessories = usbManager.getAccessoryList();
        if (usbFile != null || accessories == null) return;
        UsbAccessory accessory = accessories[0];
        if (!"Musubi".equals(accessory.getManufacturer()) || !usbManager.hasPermission(accessory)) return;
        ParcelFileDescriptor file = usbManager.openAccessory(accessory);
        if (file == null) return;
        usbFile = file;
        usbOut = new FileOutputStream(file.getFileDescriptor());
        FileInputStream in = new FileInputStream(file.getFileDescriptor());
        new Thread(() -> usbReadLoop(file, in), "musubi-usb").start();
        transportChanged();
    }

    private void closeAccessory() {
        if (usbFile == null) return;
        usbOut = null;
        // The next PC on the cable sends its own name; older ones never do.
        usbPc.name = USB_PC_NAME;
        try {
            usbFile.close();
        } catch (IOException ignored) {
        }
        usbFile = null;
        transportChanged();
    }

    private void transportChanged() {
        nextPing = 0;
        lastPongAt = 0;
        targetSince = SystemClock.uptimeMillis();
        if (backgrounded) {
            if (currentPc() == null) stopAll();
            else showKeepAlive();
        }
        if (screen == CONTROLLERS || screen == HOME || screen == GAME) render();
    }

    /** Messages from the PC are length-prefixed like ours, and one read can hold several or part of one. */
    private void usbReadLoop(ParcelFileDescriptor file, FileInputStream in) {
        byte[] data = new byte[USB_READ_SIZE];
        // Room for the longest unfinished message plus one more read.
        ByteBuffer pending = ByteBuffer.allocate(2 + 65535 + USB_READ_SIZE);
        try {
            for (int length; (length = in.read(data)) >= 0; ) {
                pending.put(data, 0, length);
                pending.flip();
                while (pending.remaining() >= 2) {
                    int size = pending.getShort(pending.position()) & 0xffff;
                    if (pending.remaining() < 2 + size) break;
                    int start = pending.position() + 2;
                    onPcMessage(pending.array(), start, size);
                    pending.position(start + size);
                }
                pending.compact();
            }
        } catch (IOException unplugged) {
        }
        handler.post(() -> {
            if (usbFile == file) closeAccessory();
        });
    }

    @Override
    public void onBackPressed() {
        if (screen == HOME) {
            // Leaves the app without finishing it, so the sound keeps playing in the background.
            moveTaskToBack(true);
        } else if (screen == EDIT_PC) {
            show(SETTINGS);
        } else if (screen == GAME) {
            show(gameFrom);
        } else if (screen == REMAP || screen == REMAP_DONE) {
            if (screen == REMAP_DONE) loadMap();
            show(CONTROLLERS);
        } else {
            show(HOME);
        }
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent event) {
        lastTouch = SystemClock.uptimeMillis();
        return super.dispatchTouchEvent(event);
    }

    private void sendLoop() {
        byte[] data = new byte[20];
        byte[] ping = new byte[PING_SIZE];
        DatagramPacket packet = new DatagramPacket(data, data.length);
        packet.setPort(PORT);
        DatagramPacket pingPacket = new DatagramPacket(ping, ping.length);
        pingPacket.setPort(PORT);
        nextPing = 0;
        try (DatagramSocket socket = new DatagramSocket()) {
            new Thread(() -> receiveLoop(socket), "musubi-receive").start();
            // A USB write blocks while the PC isn't reading, so an old sender can outlive onPause.
            while (running && sender == Thread.currentThread()) {
                InetAddress address;
                synchronized (lock) {
                    if (!dirty) {
                        lock.wait(HEARTBEAT_MS);
                    }
                    dirty = false;
                    fill(ByteBuffer.wrap(data));
                    address = target;
                }
                FileOutputStream usb = usbOut;
                if (address == null && usb == null) {
                    continue;
                }
                try {
                    long now = SystemClock.uptimeMillis();
                    if (now >= nextPing) {
                        nextPing = now + PING_INTERVAL_MS;
                        // Before the state, so the PC knows about the controller before any input.
                        byte[] hello = helloPacket();
                        ByteBuffer.wrap(ping).putLong(System.nanoTime());
                        if (usb != null) {
                            sendUsb(usb, hello);
                            sendUsb(usb, ping);
                        } else {
                            socket.send(new DatagramPacket(hello, hello.length, address, PORT));
                            pingPacket.setAddress(address);
                            socket.send(pingPacket);
                        }
                    }
                    if (usb != null) {
                        sendUsb(usb, data);
                    } else {
                        packet.setAddress(address);
                        socket.send(packet);
                    }
                    networkError = null;
                } catch (IOException ex) {
                    networkError = ex.getMessage();
                    Thread.sleep(TICK_MS);
                }
            }
        } catch (IOException ex) {
            networkError = ex.getMessage();
        } catch (InterruptedException ignored) {
        }
    }

    /** USB doesn't keep message boundaries, so each message starts with its length, matching cable.py. */
    private void sendUsb(FileOutputStream usb, byte[] message) throws IOException {
        byte[] frame = new byte[2 + message.length];
        frame[0] = (byte) (message.length >> 8);
        frame[1] = (byte) message.length;
        System.arraycopy(message, 0, frame, 2, message.length);
        usb.write(frame);
    }

    /** Tells the PC who this phone is, matching HELLO in musubi.py. */
    private byte[] helloPacket() {
        String controller = deviceName;
        byte[] names = (shorten(Build.MODEL) + "\0" + shorten(controller != null ? controller : ""))
            .getBytes(StandardCharsets.UTF_8);
        ByteBuffer out = ByteBuffer.allocate(HELLO_MAGIC.length + 3 + names.length + 4);
        out.put(HELLO_MAGIC);
        out.put((byte) (controller != null ? 1 : 0));
        out.putShort((short) Math.min(rttMs, 65535));
        out.put(names);
        out.put((byte) 0);
        out.put((byte) ((soundOn ? FEATURE_AUDIO : 0) | FEATURE_RUMBLE | (stableSound ? FEATURE_STABLE : 0)));
        // Same figure as the Headphones page; 0 means no reading.
        int delay = soundOn ? Math.max(soundDelay(), 0) : 0;
        out.putShort((short) Math.min(delay, 65535));
        return out.array();
    }

    /** The network's half of the ping plus delayMs, to 10 ms, or -1 before the first reading. */
    private int soundDelay() {
        return delayMs < 0 ? -1 : Math.round((rttMs / 2 + delayMs) / 10f) * 10;
    }

    private static String shorten(String name) {
        return name.length() > MAX_NAME_LENGTH ? name.substring(0, MAX_NAME_LENGTH) : name;
    }

    private void receiveLoop(DatagramSocket socket) {
        byte[] data = new byte[UDP_RECEIVE_SIZE];
        DatagramPacket packet = new DatagramPacket(data, data.length);
        try {
            while (true) {
                packet.setLength(data.length);
                socket.receive(packet);
                InetAddress expected;
                synchronized (lock) {
                    expected = target;
                }
                if (packet.getAddress().equals(expected)) onPcMessage(data, 0, packet.getLength());
            }
        } catch (IOException closedWithTheSender) {
        }
    }

    /** One message from the PC, over Wi-Fi or the cable. Anything unknown is from a newer PC and skipped. */
    private void onPcMessage(byte[] data, int offset, int length) {
        ByteBuffer in = ByteBuffer.wrap(data, offset, length);
        if (length == PING_SIZE) {
            rttMs = (System.nanoTime() - in.getLong()) / 1000000;
            lastPongAt = SystemClock.uptimeMillis();
        } else if (length >= 4) {
            int magic = in.getInt();
            if (length >= AUDIO_HEADER && magic == ByteBuffer.wrap(AUDIO_MAGIC).getInt()) {
                onAudio(in.getShort() & 0xffff, data, in.position(), in.remaining());
            } else if (length >= RUMBLE_SIZE && magic == ByteBuffer.wrap(RUMBLE_MAGIC).getInt()) {
                // The pad's input doesn't reach the PC from the background, so neither does its rumble.
                if (running && !backgrounded && vibration) {
                    int scale = vibrationStrength;
                    int strong = (in.getShort() & 0xffff) * scale / 100, weak = (in.getShort() & 0xffff) * scale / 100, ms = in.getShort() & 0xffff;
                    rumble(deviceId, strong, weak, ms);
                    handler.post(() -> showRumble(strong, weak, ms));
                }
            } else if (magic == ByteBuffer.wrap(NAME_MAGIC).getInt()) {
                String name = shorten(new String(data, in.position(), in.remaining(), StandardCharsets.UTF_8)
                    .replaceAll("\\p{Cntrl}", "").trim());
                if (!name.isEmpty()) handler.post(() -> {
                    if (name.equals(usbPc.name)) return;
                    usbPc.name = name;
                    if (backgrounded && currentPc() == usbPc) showKeepAlive();
                    render();
                });
            }
        }
    }

    /** Feeds the motor levels to the pad drawing, and zeroes them when the rumble expires. */
    private void showRumble(int strong, int weak, int ms) {
        handler.removeCallbacks(endRumble);
        rumbleStrong = strong / 65535f;
        rumbleWeak = weak / 65535f;
        if (strong > 0 || weak > 0) handler.postDelayed(endRumble, ms > 0 ? ms : RUMBLE_CAP_MS);
        if (padView != null) padView.setRumble(rumbleStrong, rumbleWeak);
    }

    private final Runnable endRumble = () -> showRumble(0, 0, 0);

    /** Drives the controller's motors; levels are 0..65535, and 0, 0 stops them. */
    private static void rumble(int id, int strong, int weak, int ms) {
        InputDevice device = InputDevice.getDevice(id);
        if (device == null) return;
        int duration = ms > 0 ? ms : RUMBLE_CAP_MS;
        try {
            if (Build.VERSION.SDK_INT >= 31 && rumbleMotors(device, strong, weak, duration)) return;
            Vibrator vibrator = device.getVibrator();
            int level = Math.max(strong, weak);
            if (!vibrator.hasVibrator()) return;
            if (level == 0) vibrator.cancel();
            else if (Build.VERSION.SDK_INT >= 26) vibrator.vibrate(VibrationEffect.createOneShot(duration, amplitude(level)));
            else vibrator.vibrate(duration);
        } catch (ReflectiveOperationException | RuntimeException ex) {
            Log.w("musubi", "rumble failed", ex);
        }
    }

    /**
     * Android 12+ drives each motor on its own. The android-28 jar lacks these classes, so they go through reflection.
     * Returns false for a pad with fewer than two motors, which the single vibrator handles.
     */
    private static boolean rumbleMotors(InputDevice device, int strong, int weak, int duration)
            throws ReflectiveOperationException {
        Class<?> managerClass = Class.forName("android.os.VibratorManager");
        Object manager = InputDevice.class.getMethod("getVibratorManager").invoke(device);
        int[] ids = (int[]) managerClass.getMethod("getVibratorIds").invoke(manager);
        if (ids.length < 2) return false;
        if (strong == 0 && weak == 0) {
            managerClass.getMethod("cancel").invoke(manager);
            return true;
        }
        Class<?> combinedClass = Class.forName("android.os.CombinedVibration");
        Class<?> parallelClass = Class.forName("android.os.CombinedVibration$ParallelCombination");
        Object parallel = combinedClass.getMethod("startParallel").invoke(null);
        Method add = parallelClass.getMethod("addVibrator", int.class, VibrationEffect.class);
        // AOSP's EventHub gives a rumble pad vibrator 0 as the strong (low-frequency) motor and 1 as the weak one.
        if (strong > 0) add.invoke(parallel, ids[0], VibrationEffect.createOneShot(duration, amplitude(strong)));
        if (weak > 0) add.invoke(parallel, ids[1], VibrationEffect.createOneShot(duration, amplitude(weak)));
        managerClass.getMethod("vibrate", combinedClass).invoke(manager, parallelClass.getMethod("combine").invoke(parallel));
        return true;
    }

    /** The same vibrator the rumble falls back to; many pads have none over Bluetooth. */
    private static boolean canVibrate(int id) {
        InputDevice device = InputDevice.getDevice(id);
        try {
            if (device == null) return false;
            if (Build.VERSION.SDK_INT >= 31) {
                Object manager = InputDevice.class.getMethod("getVibratorManager").invoke(device);
                if (((int[]) manager.getClass().getMethod("getVibratorIds").invoke(manager)).length > 0) return true;
            }
            return device.getVibrator().hasVibrator();
        } catch (ReflectiveOperationException | RuntimeException ex) {
            return false;
        }
    }

    /** True only when the pad's vibrator(s) can vary amplitude; some Bluetooth pads are on/off. Always false below API 26. */
    private static boolean canVaryStrength(int id) {
        if (Build.VERSION.SDK_INT < 26) return false;
        InputDevice device = InputDevice.getDevice(id);
        try {
            if (device == null) return false;
            if (Build.VERSION.SDK_INT >= 31) {
                Object manager = InputDevice.class.getMethod("getVibratorManager").invoke(device);
                int[] ids = (int[]) manager.getClass().getMethod("getVibratorIds").invoke(manager);
                for (int vid : ids) {
                    Vibrator v = (Vibrator) manager.getClass().getMethod("getVibrator", int.class).invoke(manager, vid);
                    if (v.hasAmplitudeControl()) return true;
                }
                if (ids.length > 0) return false;
            }
            return device.getVibrator().hasAmplitudeControl();
        } catch (ReflectiveOperationException | RuntimeException ex) {
            return false;
        }
    }

    private static int amplitude(int level) {
        return Math.max(1, level * 255 / 65535);
    }

    /** 16-bit stereo PCM at 48 kHz from the PC. A lost packet is simply skipped. */
    private void onAudio(int seq, byte[] pcm, int offset, int length) {
        if (!running) return;
        synchronized (audioLock) {
            if (audioBroken || muted || !audio.push(seq, pcm, offset, length)) return;
            lastAudioAt = SystemClock.uptimeMillis();
            if (player == null) {
                player = new Thread(this::playLoop, "musubi-audio");
                player.start();
            }
            audioLock.notifyAll();
        }
    }

    /** Its own thread, since AudioTrack.write blocks and the receive thread must not. */
    private void playLoop() {
        Thread self = Thread.currentThread();
        AudioTrack track = newTrack();
        if (track == null) {
            synchronized (audioLock) {
                audioBroken = true;
                if (player == self) player = null;
            }
            return;
        }
        byte[] chunk = new byte[AudioBuffer.CHUNK + AudioBuffer.FRAME];
        boolean paused = true;
        int trackUnderruns = 0;
        int written = 0;
        long frames = 0;
        AudioTimestamp stamp = new AudioTimestamp();
        // Starving within 20 ms of waiting for audio is the network's fault, not the track's size.
        int settle = 4;
        try {
            while (true) {
                int length, queued;
                String stats;
                boolean background;
                synchronized (audioLock) {
                    while (player == self && !audio.ready()) {
                        settle = 4;
                        long idle = SystemClock.uptimeMillis() - lastAudioAt;
                        if (idle >= AUDIO_RELEASE_MS) {
                            player = null;
                        } else if (idle >= AUDIO_IDLE_MS && !paused) {
                            track.pause();
                            track.flush();
                            frames = 0;
                            paused = true;
                            // What's left is from before the silence, so the next sound starts clean.
                            audio.reset();
                        } else {
                            audioLock.wait((paused ? AUDIO_RELEASE_MS : AUDIO_IDLE_MS) - idle);
                        }
                    }
                    if (player != self) return;
                    length = audio.pull(chunk);
                    queued = audio.level();
                    stats = audio.stats();
                    background = audio.background();
                }
                if (stats != null) {
                    String line = stats + ", track " + trackSize.ms + " ms";
                    Log.i("musubi-audio", line);
                    handler.post(() -> saveStats(line));
                }
                if (paused) {
                    track.play();
                    paused = false;
                }
                track.write(chunk, 0, length);
                frames += length / AudioBuffer.FRAME;
                peaks(chunk, length);
                if (written++ % DELAY_EVERY == 0) {
                    int out = outputLatency(track, stamp, frames);
                    if (out >= 0) delayMs = queued / AudioBuffer.BYTES_PER_MS + out;
                }
                if (Build.VERSION.SDK_INT >= 24) {
                    int count = track.getUnderrunCount();
                    int ms = trackSize.setBackground(background);
                    if (ms == 0) ms = trackSize.played(length, count > trackUnderruns && settle == 0);
                    if (ms > 0) resizeTrack(track, ms);
                    trackUnderruns = count;
                    if (settle > 0) settle--;
                }
            }
        } catch (InterruptedException | IllegalStateException stopped) {
        } finally {
            track.release();
        }
    }

    /** Little-endian 16-bit stereo, as the PC sends it. */
    private void peaks(byte[] pcm, int length) {
        int left = 0, right = 0;
        for (int i = 0; i + 3 < length; i += AudioBuffer.FRAME) {
            left = Math.max(left, Math.abs((short) (pcm[i] & 0xff | pcm[i + 1] << 8)));
            right = Math.max(right, Math.abs((short) (pcm[i + 2] & 0xff | pcm[i + 3] << 8)));
        }
        peakLeft = Math.max(peakLeft, Math.min(1, left / 32767f));
        peakRight = Math.max(peakRight, Math.min(1, right / 32767f));
    }

    /**
     * Frames written but not yet heard: the track's queue, the mixer and the output, Bluetooth included when its HAL
     * reports it (the Moto's matches AudioFlinger's ~320 ms). The hidden getLatency read 52 ms on one run and 384 on
     * the next with the same earbuds, so it isn't used. Returns -1 while the track has no position yet.
     */
    private static int outputLatency(AudioTrack track, AudioTimestamp stamp, long written) {
        if (!track.getTimestamp(stamp)) return -1;
        long ms = (written - stamp.framePosition) * 1000 / AUDIO_RATE - (System.nanoTime() - stamp.nanoTime) / 1000000;
        return (int) Math.max(0, ms);
    }

    private boolean receivingAudio() {
        synchronized (audioLock) {
            return player != null;
        }
    }

    private AudioTrack newTrack() {
        AudioAttributes attributes = new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_GAME).build();
        AudioFormat format = new AudioFormat.Builder().setSampleRate(AUDIO_RATE)
            .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build();
        // Twice the minimum leaves room for a late wakeup, and every extra byte is more delay.
        int size = 2 * AudioTrack.getMinBufferSize(AUDIO_RATE, AudioFormat.CHANNEL_OUT_STEREO,
            AudioFormat.ENCODING_PCM_16BIT);
        AudioTrack track;
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                track = new AudioTrack.Builder().setAudioAttributes(attributes).setAudioFormat(format)
                    .setBufferSizeInBytes(size).setTransferMode(AudioTrack.MODE_STREAM)
                    .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY).build();
            } else {
                track = new AudioTrack(attributes, format, size, AudioTrack.MODE_STREAM,
                    AudioManager.AUDIO_SESSION_ID_GENERATE);
            }
        } catch (RuntimeException unsupported) {
            return null;
        }
        if (track.getState() == AudioTrack.STATE_INITIALIZED) {
            if (useSpeaker()) preferSpeaker(track);
            // Without the fast mixer (Bluetooth, or the fast path refused) the whole capacity is queued, 170 to 256 ms on a Moto Edge 60.
            if (Build.VERSION.SDK_INT >= 24) {
                track.setBufferSizeInFrames(Math.min(track.getBufferSizeInFrames(), AudioBuffer.Track.STEP_MS * (AUDIO_RATE / 1000)));
            }
            trackSize = new AudioBuffer.Track(Build.VERSION.SDK_INT >= 23 ? track.getBufferSizeInFrames() / (AUDIO_RATE / 1000)
                : size / AudioBuffer.BYTES_PER_MS);
            Log.i("musubi-audio", "track " + trackSize.ms + " ms, asked for " + size / AudioBuffer.BYTES_PER_MS + " ms");
            return track;
        }
        track.release();
        return null;
    }

    // Its own method so Android 5 never has to resolve AudioDeviceInfo.
    private void preferSpeaker(AudioTrack track) {
        for (AudioDeviceInfo device : audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)) {
            if (device.getType() == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER) {
                track.setPreferredDevice(device);
                return;
            }
        }
    }

    private boolean useSpeaker() {
        return speaker && Build.VERSION.SDK_INT >= 23;
    }

    private void resizeTrack(AudioTrack track, int ms) {
        int frames = track.setBufferSizeInFrames(ms * (AUDIO_RATE / 1000));
        if (frames <= 0 || frames / (AUDIO_RATE / 1000) == trackSize.ms) return;
        Log.i("musubi-audio", "track " + (frames / (AUDIO_RATE / 1000) > trackSize.ms ? "starved" : "steady")
            + ", now " + frames / (AUDIO_RATE / 1000) + " ms");
        trackSize.ms = frames / (AUDIO_RATE / 1000);
    }

    /** Appends to a file adb can pull, on the main thread so a slow disk can't stall playback. */
    private void saveStats(String line) {
        File dir = getExternalFilesDir(null);
        if (dir == null) return;
        File file = new File(dir, "audio-stats.txt");
        if (file.length() > STATS_FILE_BYTES) file.renameTo(new File(dir, "audio-stats.old.txt"));
        try (FileWriter out = new FileWriter(file, true)) {
            out.write(DateFormat.format("yyyy-MM-dd HH:mm:ss ", System.currentTimeMillis()) + line + "\n");
        } catch (IOException ignored) {
        }
    }

    private void fill(ByteBuffer out) {
        out.putInt(held & WIRE_BUTTONS);
        for (int i = 0; i < 4; i++) {
            out.putShort(axes[i]);
        }
        out.putShort((held & 1 << L2_BIT) != 0 ? TRIGGER_MAX : axes[4]);
        out.putShort((held & 1 << R2_BIT) != 0 ? TRIGGER_MAX : axes[5]);
        out.putShort(hat(LEFT_BIT, RIGHT_BIT));
        out.putShort(hat(UP_BIT, DOWN_BIT));
    }

    private short hat(int negative, int positive) {
        if ((held & 1 << negative) != 0) return -1;
        if ((held & 1 << positive) != 0) return 1;
        return 0;
    }

    private void flush() {
        synchronized (lock) {
            dirty = true;
            lock.notify();
        }
    }

    private void clearInput() {
        synchronized (lock) {
            held = 0;
            Arrays.fill(axes, (short) 0);
            dirty = true;
            lock.notify();
        }
        rawLx = rawLy = rawRx = rawRy = 0;
        unmapped = null;
        pushPadState();
    }

    private static ButtonMap defaultMap() {
        ButtonMap defaults = new ButtonMap();
        for (int i = 0; i < BUTTON_KEYS.length; i++) {
            defaults.set(BUTTON_KEYS[i], i);
        }
        defaults.set(ButtonMap.HAT_LEFT, LEFT_BIT);
        defaults.set(ButtonMap.HAT_RIGHT, RIGHT_BIT);
        defaults.set(ButtonMap.HAT_UP, UP_BIT);
        defaults.set(ButtonMap.HAT_DOWN, DOWN_BIT);
        defaults.set(ButtonMap.LEFT_TRIGGER, L2_BIT);
        defaults.set(ButtonMap.RIGHT_TRIGGER, R2_BIT);
        return defaults;
    }

    private String mapKey() {
        return "map:" + descriptor;
    }

    private boolean hasCustomMap() {
        return descriptor != null && prefs.contains(mapKey());
    }

    private void loadMap() {
        ButtonMap saved = hasCustomMap() ? ButtonMap.decode(prefs.getString(mapKey(), "")) : null;
        map = saved != null ? saved : defaultMap();
        clearInput();
    }

    private void updateController() {
        int before = deviceId;
        findController();
        if (deviceId != before) {
            rumble(before, 0, 0, 0);
            showRumble(0, 0, 0);
            // A controller joining or dropping is always player 1 until multi-pad, so show that tab.
            selectedPlayer = 1;
        }
        if (deviceId != before && (screen == CONTROLLERS || screen == HOME || screen == SETTINGS)) {
            render();
        }
    }

    private void findController() {
        for (int id : InputDevice.getDeviceIds()) {
            InputDevice device = InputDevice.getDevice(id);
            if (isGamepad(device)) {
                adopt(device);
                return;
            }
        }
        deviceId = -1;
        deviceName = null;
    }

    private void adopt(InputDevice device) {
        if (device.getId() == deviceId) return;
        deviceId = device.getId();
        deviceName = device.getName();
        descriptor = device.getDescriptor();
        // Most pads put the right stick on Z/RZ, but some only report RX/RY.
        boolean onlyRx = device.getMotionRange(MotionEvent.AXIS_Z) == null
            && device.getMotionRange(MotionEvent.AXIS_RX) != null;
        stickXAxis = onlyRx ? MotionEvent.AXIS_RX : MotionEvent.AXIS_Z;
        stickYAxis = onlyRx ? MotionEvent.AXIS_RY : MotionEvent.AXIS_RZ;
        loadMap();
    }

    // SOURCE_GAMEPAD shares its class bit with keyboards, so test the whole mask.
    // Some Bluetooth earbuds expose a joystick with no sticks, so a joystick also needs X and Y.
    private static boolean isGamepad(InputDevice device) {
        if (device == null) return false;
        int sources = device.getSources();
        return (sources & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD
            || (sources & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK
                && device.getMotionRange(MotionEvent.AXIS_X, InputDevice.SOURCE_JOYSTICK) != null
                && device.getMotionRange(MotionEvent.AXIS_Y, InputDevice.SOURCE_JOYSTICK) != null;
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (!isGamepad(event.getDevice())) {
            return super.dispatchKeyEvent(event);
        }
        adopt(event.getDevice());
        if (event.getRepeatCount() == 0) {
            if (event.getAction() == KeyEvent.ACTION_DOWN) {
                onInput(event.getKeyCode(), true);
            } else if (event.getAction() == KeyEvent.ACTION_UP) {
                onInput(event.getKeyCode(), false);
            }
        }
        return true;
    }

    @Override
    public boolean dispatchGenericMotionEvent(MotionEvent event) {
        if (!isGamepad(event.getDevice())) {
            return super.dispatchGenericMotionEvent(event);
        }
        adopt(event.getDevice());
        float hatX = event.getAxisValue(MotionEvent.AXIS_HAT_X);
        float hatY = event.getAxisValue(MotionEvent.AXIS_HAT_Y);
        float left = Math.max(event.getAxisValue(MotionEvent.AXIS_LTRIGGER), event.getAxisValue(MotionEvent.AXIS_BRAKE));
        float right = Math.max(event.getAxisValue(MotionEvent.AXIS_RTRIGGER), event.getAxisValue(MotionEvent.AXIS_GAS));
        setAxisButton(ButtonMap.HAT_LEFT, hatX < -0.5f);
        setAxisButton(ButtonMap.HAT_RIGHT, hatX > 0.5f);
        setAxisButton(ButtonMap.HAT_UP, hatY < -0.5f);
        setAxisButton(ButtonMap.HAT_DOWN, hatY > 0.5f);
        setAxisButton(ButtonMap.LEFT_TRIGGER, left > 0.5f);
        setAxisButton(ButtonMap.RIGHT_TRIGGER, right > 0.5f);
        if (screen == REMAP) {
            return true;
        }
        int leftControl = map.control(ButtonMap.LEFT_TRIGGER), rightControl = map.control(ButtonMap.RIGHT_TRIGGER);
        float l2 = leftControl == L2_BIT ? left : rightControl == L2_BIT ? right : 0;
        float r2 = leftControl == R2_BIT ? left : rightControl == R2_BIT ? right : 0;
        rawLx = event.getAxisValue(MotionEvent.AXIS_X);
        rawLy = event.getAxisValue(MotionEvent.AXIS_Y);
        rawRx = event.getAxisValue(stickXAxis);
        rawRy = event.getAxisValue(stickYAxis);
        if (stickPreview != null) stickPreview.setSticks(rawLx, rawLy, rawRx, rawRy);
        float d = deadzone;
        synchronized (lock) {
            Deadzone.apply(axes, 0, rawLx, rawLy, d);
            Deadzone.apply(axes, 2, rawRx, rawRy, d);
            axes[4] = (short) (l2 * TRIGGER_MAX);
            axes[5] = (short) (r2 * TRIGGER_MAX);
            dirty = true;
            lock.notify();
        }
        pushPadState();
        return true;
    }

    private void setAxisButton(int input, boolean down) {
        int bit = 1 << (-input - 1);
        if (((axisButtons & bit) != 0) == down) return;
        axisButtons ^= bit;
        onInput(input, down);
    }

    private void onInput(int input, boolean down) {
        if (screen == REMAP) {
            if (down) remapInput(input);
            return;
        }
        int control = map.control(input);
        if (control < 0) {
            // Unmapped keys stay visible here so the pad can be calibrated by hand.
            if (down && input > 0) {
                unmapped = KeyEvent.keyCodeToString(input) + " (not mapped)";
                updateReadout(true);
            }
            return;
        }
        // These keep their analog travel through the axes instead of snapping to fully pressed.
        boolean analog = (input == ButtonMap.LEFT_TRIGGER || input == ButtonMap.RIGHT_TRIGGER)
            && (control == L2_BIT || control == R2_BIT);
        if (!analog) {
            synchronized (lock) {
                held = down ? held | 1 << control : held & ~(1 << control);
                dirty = true;
                lock.notify();
            }
        }
        unmapped = null;
        pushPadState();
    }

    private void pushPadState() {
        if (padView != null) {
            padView.setState(held, shown(axes[0]), shown(axes[1]), shown(axes[2]), shown(axes[3]),
                triggerShown(L2_BIT, axes[4]), triggerShown(R2_BIT, axes[5]));
        }
        updateReadout(false);
    }

    private static float shown(short axis) {
        float value = axis / (float) STICK_MAX;
        return Math.abs(value) < DISPLAY_DEAD_ZONE ? 0 : value;
    }

    private float triggerShown(int bit, short axis) {
        return (held & 1 << bit) != 0 ? 1 : axis / (float) TRIGGER_MAX;
    }

    private void updateReadout(boolean force) {
        if (readout == null) return;
        // setText on every motion event would stall the input thread.
        long now = SystemClock.uptimeMillis();
        if (!force && now - lastUiUpdate < UI_INTERVAL_MS) return;
        lastUiUpdate = now;
        String text = describeInput();
        readout.setText(text.isEmpty() ? "Press a button to test" : text);
        readout.setTextColor(text.isEmpty() ? MUTED : ACTIVE);
    }

    private String describeInput() {
        if (unmapped != null) return unmapped;
        List<String> parts = new ArrayList<>();
        for (int i = 0; i < BUTTON_NAMES.length; i++) {
            if ((held & 1 << i) != 0) parts.add(BUTTON_NAMES[i]);
        }
        if (axes[4] > 0 && (held & 1 << L2_BIT) == 0) parts.add("L2 " + axes[4] * 100 / TRIGGER_MAX + "%");
        if (axes[5] > 0 && (held & 1 << R2_BIT) == 0) parts.add("R2 " + axes[5] * 100 / TRIGGER_MAX + "%");
        addAxis(parts, "LX", axes[0]);
        addAxis(parts, "LY", axes[1]);
        addAxis(parts, "RX", axes[2]);
        addAxis(parts, "RY", axes[3]);
        return TextUtils.join(" · ", parts);
    }

    private static void addAxis(List<String> parts, String name, short axis) {
        float value = shown(axis);
        if (value != 0) parts.add(String.format(Locale.US, "%s %.2f", name, value));
    }

    private void loadPcs() {
        pcs.clear();
        String stored = prefs.getString("pcs", null);
        if (stored != null) {
            try {
                JSONArray list = new JSONArray(stored);
                for (int i = 0; i < list.length(); i++) {
                    JSONObject pc = list.getJSONObject(i);
                    pcs.add(new Pc(pc.getString("name"), pc.getString("ip")));
                }
            } catch (JSONException corrupt) {
                pcs.clear();
            }
        } else if (!prefs.getString("host", "").isEmpty()) {
            // Keeps the address saved by version 0.1.0.
            pcs.add(new Pc("My PC", prefs.getString("host", "")));
            savePcs();
        }
        selectedPc = Math.max(0, Math.min(prefs.getInt("selectedPc", 0), pcs.size() - 1));
    }

    private void savePcs() {
        JSONArray list = new JSONArray();
        try {
            for (Pc pc : pcs) {
                list.put(new JSONObject().put("name", pc.name).put("ip", pc.ip));
            }
        } catch (JSONException impossibleWithStrings) {
            throw new IllegalStateException(impossibleWithStrings);
        }
        prefs.edit().putString("pcs", list.toString()).putInt("selectedPc", selectedPc).apply();
    }

    private Pc savedPc() {
        return pcs.isEmpty() ? null : pcs.get(selectedPc);
    }

    /** The cable wins over Wi-Fi while it's plugged in. */
    private Pc currentPc() {
        return usbOut != null ? usbPc : savedPc();
    }

    private void applySelectedPc() {
        InetAddress address = null;
        Pc pc = savedPc();
        // The pattern guarantees a literal, so getByName never does a DNS lookup here.
        if (pc != null && Patterns.IP_ADDRESS.matcher(pc.ip).matches()) {
            try {
                address = InetAddress.getByName(pc.ip);
            } catch (IOException ignored) {
            }
        }
        synchronized (lock) {
            target = address;
        }
        targetSince = SystemClock.uptimeMillis();
        lastPongAt = 0;
        rttMs = 0;
        shownPcState = -1;
    }

    private void selectPc(int index) {
        selectedPc = index;
        savePcs();
        applySelectedPc();
    }

    private int pcState() {
        long now = SystemClock.uptimeMillis();
        if (lastPongAt != 0 && now - lastPongAt < PC_TIMEOUT_MS) return RESPONDING;
        return now - targetSince < PC_TIMEOUT_MS ? CONNECTING : LOST;
    }

    private void refreshStatus() {
        int state = pcState();
        refreshOutput();
        if (state != shownPcState) {
            shownPcState = state;
            if (((screen == CONTROLLERS || screen == HOME || screen == HEADPHONES) && currentPc() != null) || screen == GAME || screen == SETTINGS) render();
        }
        if (pcDetail != null) pcDetail.setText(pcDetailText(state));
        if (settingsPing != null) {
            CharSequence ping = pcUseText(savedPc());
            if (!ping.toString().contentEquals(settingsPing.getText())) settingsPing.setText(ping);
        }
        if (settingsSoundNow != null) updateSoundNow();
        updateSound();
        updateReadout(true);
        if ((screen == CONTROLLERS || screen == HOME) && autoGameMode && deviceId != -1 && currentPc() != null
                && SystemClock.uptimeMillis() - lastTouch >= AUTO_GAME_MODE_MS) {
            openGame();
        }
    }

    private String pcDetailText(int state) {
        Pc pc = currentPc();
        if (pc == null) return "";
        return (pc == usbPc ? "USB cable" : "Wi-Fi · " + pc.ip) + " · "
            + (state == RESPONDING ? rttMs + " ms" : state == LOST ? "no answer" : "connecting");
    }

    /** Works out whether the phone may play, and stops the sound the moment it may not. */
    private void refreshOutput() {
        findEarbuds();
        boolean mute = !soundOn
            || !useSpeaker() && (earbuds == null || SystemClock.uptimeMillis() < quietUntil);
        boolean stop;
        synchronized (audioLock) {
            stop = mute && !muted;
            muted = mute;
        }
        if (stop) stopPlayer();
    }

    private void setSound(boolean on) {
        soundOn = on;
        prefs.edit().putBoolean("sound", on).apply();
        // The PC hears about it with the next hello instead of up to a second later.
        nextPing = 0;
        refreshOutput();
        render();
    }

    private void setSpeaker(boolean on) {
        if (speaker == on) return;
        speaker = on;
        prefs.edit().putBoolean("speaker", on).apply();
        // The route is chosen when the track is built, so the next packet builds one for the new output.
        stopPlayer();
        refreshOutput();
        render();
    }

    private boolean soundLive() {
        return soundOn && shownPcState == RESPONDING;
    }

    /** Packets are arriving now, not just a player kept around after the sound stopped. */
    private boolean soundArriving() {
        synchronized (audioLock) {
            return player != null && SystemClock.uptimeMillis() - lastAudioAt < AUDIO_IDLE_MS;
        }
    }

    /** Fills the Home row or the Headphones page, whichever is on screen, twice a second. */
    private void updateSound() {
        if (headphones == null) return;
        boolean lost = shownPcState == LOST, waiting = soundOn && !useSpeaker() && earbuds == null;
        boolean arriving = soundOn && !lost && soundArriving();
        String delay = delayMs < 0 ? null : "Delay ≈ " + soundDelay() + " ms";
        headphones.setMode(!soundOn ? HeadphonesView.OFF : waiting ? HeadphonesView.NONE : HeadphonesView.ON);
        headphones.setContentDescription(!soundOn ? "Headphones, sound off" : waiting ? "Headphones, no earbuds connected"
            : arriving ? "Headphones, sound arriving" : "Headphones, nothing playing");
        if (arriving) headphones.start();
        if (soundName != null) {
            soundName.setText(!soundOn ? "PC sound is off" : lost ? "Paused" : waiting ? "No earbuds connected"
                : useSpeaker() ? "Phone speaker" : earbuds);
            soundDetail.setText(!soundOn ? (useSpeaker() ? "Plays on the phone speaker" : "Plays on your paired earbuds")
                : lost ? "Waiting for the PC" : waiting ? "Waiting for earbuds" : !arriving ? "Nothing playing on the PC"
                : delay != null ? delay : "Measuring the delay");
        }
        if (earbudsName != null) {
            earbudsName.setText(earbuds != null ? earbuds : "No earbuds connected");
            earbudsName.setTextColor(earbuds != null ? TEXT : waiting ? ALERT : MUTED);
            earbudsDetail.setText(earbuds != null
                ? (useSpeaker() ? "Connected, but PC sound plays on the phone speaker"
                    : earbudsWired ? "Plugged into this phone" : "Paired with this phone")
                : useSpeaker() ? "PC sound plays on the phone speaker."
                : waiting ? (Build.VERSION.SDK_INT >= 23 ? "Connect them, or switch to the phone speaker."
                    : "Connect them in the phone's Bluetooth settings.")
                : "Connect them in the phone's Bluetooth settings.");
            soundReadout.setText(!soundOn ? "Turn on PC sound to test" : lost ? "Waiting for the PC"
                : waiting ? "Waiting for earbuds" : arriving ? "Sound arriving" : "Nothing playing on the PC");
            soundReadout.setTextColor(!soundOn || lost ? MUTED : waiting ? ALERT : arriving ? ACTIVE : MUTED);
            soundStatus.setText(!soundOn ? "Off"
                : (delay != null ? delay + " · " : "") + (currentPc() == usbPc ? "USB cable" : "Wi-Fi"));
        }
    }

    /** From the real audio route, so earbuds without buttons count too. Bluetooth first, since it takes the sound. */
    private void findEarbuds() {
        if (Build.VERSION.SDK_INT >= 23) {
            findOutputDevice();
        } else {
            earbudsWired = !audioManager.isBluetoothA2dpOn() && audioManager.isWiredHeadsetOn();
            earbuds = audioManager.isBluetoothA2dpOn() ? "Bluetooth earbuds" : earbudsWired ? "Wired headphones" : null;
        }
    }

    // Its own method so Android 5 never has to resolve AudioDeviceInfo.
    private void findOutputDevice() {
        earbuds = null;
        earbudsWired = false;
        for (AudioDeviceInfo device : audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)) {
            int type = device.getType();
            if (type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP) {
                CharSequence name = device.getProductName();
                earbuds = name != null && name.length() > 0 ? name.toString() : "Bluetooth earbuds";
                earbudsWired = false;
                return;
            }
            if (type == AudioDeviceInfo.TYPE_WIRED_HEADSET || type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES
                    || type == AudioDeviceInfo.TYPE_USB_HEADSET) {
                earbuds = "Wired headphones";
                earbudsWired = true;
            }
        }
    }

    private void openGame() {
        gameFrom = screen;
        show(GAME);
    }

    private void show(int next) {
        screen = next;
        render();
    }

    private void render() {
        int keepScroll = renderedScreen == screen && scroller != null ? scroller.getScrollY() : 0;
        scroller = null;
        padView = null;
        stickPreview = null;
        readout = null;
        pcDetail = null;
        settingsPing = null;
        settingsSoundNow = null;
        soundName = null;
        soundDetail = null;
        earbudsName = null;
        earbudsDetail = null;
        soundReadout = null;
        soundStatus = null;
        headphones = null;
        if (screen != EDIT_PC && screen != HOME) formError = null;
        applyGameWindow(screen == GAME);
        shownPcState = pcState();
        switch (screen) {
            case HOME: setContentView(currentPc() != null ? buildHome(currentPc()) : buildMain()); break;
            case GAME: setContentView(buildGame()); break;
            case REMAP: setContentView(buildRemap()); break;
            case REMAP_DONE: setContentView(buildRemapDone()); break;
            case SETTINGS: setContentView(buildSettings()); break;
            case EDIT_PC: setContentView(buildEditPc()); break;
            case HEADPHONES: setContentView(buildHeadphones()); break;
            default: setContentView(currentPc() != null ? buildControllers(currentPc()) : buildMain()); break;
        }
        renderedScreen = screen;
        if (keepScroll > 0 && scroller != null) {
            ScrollView fresh = scroller;
            fresh.getViewTreeObserver().addOnPreDrawListener(new ViewTreeObserver.OnPreDrawListener() {
                @Override public boolean onPreDraw() {
                    fresh.getViewTreeObserver().removeOnPreDrawListener(this);
                    fresh.scrollTo(0, keepScroll);
                    return true;
                }
            });
        }
        pushPadState();
        updateSound();
        updateReadout(true);
    }

    private void applyGameWindow(boolean game) {
        if (game || prefs.getBoolean("keep_screen", true)) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        WindowManager.LayoutParams attributes = getWindow().getAttributes();
        attributes.screenBrightness = game ? prefs.getInt("game_brightness", 1) / 100f : WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE;
        getWindow().setAttributes(attributes);
        getWindow().getDecorView().setSystemUiVisibility(game
            ? View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            : 0);
    }

    /** Controller drawing between the parts of a screen. Middle may be null. */
    private View withPad(int control, View top, View middle, View bottom) {
        padView = new PadView(this);
        padView.setRumble(rumbleStrong, rumbleWeak);
        padView.setContentDescription("Controller");
        padView.setTarget(control);
        int padding = dp(8);
        padView.setPadding(padding, padding, padding, padding);
        LinearLayout root = column();
        root.setPadding(dp(16), dp(16), dp(16), dp(16));
        root.addView(top);
        root.addView(padView, weighted(ViewGroup.LayoutParams.MATCH_PARENT, 0));
        if (middle != null) root.addView(middle);
        root.addView(bottom);
        return root;
    }

    /** First run, before any PC is saved. */
    private View buildMain() {
        LinearLayout top = column();
        top.addView(controllerBlock());
        top.addView(firstRunForm());
        LinearLayout actions = column();
        actions.addView(button("Save PC", PRIMARY, this::saveFirstPc), buttonParams(48));
        return withPad(-1, top, null, actions);
    }

    /** The cord: the PC on top, then one node per feature, live in amber while data flows. */
    private View buildHome(Pc pc) {
        boolean lost = shownPcState == LOST, answering = shownPcState == RESPONDING;
        boolean soundLive = soundLive();
        int players = deviceId != -1 ? 1 : 0;
        // Below the last live feature the cord is always off.
        int toControllers = answering ? ACTIVE : BORDER, toSound = soundLive ? ACTIVE : BORDER;

        LinearLayout pcBlock = column();
        pcBlock.setPadding(0, 0, 0, dp(16));
        LinearLayout pcLine = row();
        LinearLayout labels = column();
        labels.addView(text("PC", 12, MUTED));
        TextView name = text(pc.name, 22, TEXT);
        name.setTypeface(Typeface.DEFAULT_BOLD);
        labels.addView(name);
        pcDetail = mono(pcDetailText(shownPcState));
        labels.addView(pcDetail);
        pcLine.addView(labels, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        Button switchButton = button("Switch", SECONDARY, this::switchPc);
        switchButton.setContentDescription("Switch PC");
        pcLine.addView(switchButton, new LinearLayout.LayoutParams(dp(84), dp(44)));
        pcBlock.addView(pcLine);
        if (lost) {
            TextView status = text("PC not responding", 14, ALERT);
            status.setTypeface(Typeface.DEFAULT_BOLD);
            status.setPadding(0, dp(8), 0, dp(4));
            pcBlock.addView(status);
            pcBlock.addView(detail(lostMessage(pc)));
        }
        View square = new View(this);
        GradientDrawable squareShape = new GradientDrawable();
        squareShape.setCornerRadius(dp(3));
        squareShape.setColor(lost ? ALERT : answering ? ACTIVE : IDLE);
        square.setBackground(squareShape);

        LinearLayout controllers = clickableRow(() -> {
                selectedPlayer = 1;
                show(CONTROLLERS);
            },
            "Controllers, " + players + " of 4 players connected, open");
        controllers.setMinimumHeight(dp(64));
        controllers.setPadding(dp(4), dp(6), dp(4), dp(6));
        LinearLayout controllerText = featureText("Controllers · to PC", players + " of 4 players", TEXT);
        LinearLayout chips = row();
        chips.setPadding(0, dp(2), 0, 0);
        for (int i = 1; i <= 4; i++) chips.addView(chip(i, i <= players));
        controllerText.addView(chips);
        controllers.addView(controllerText, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        padView = new PadView(this);
        padView.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        padView.setRumble(rumbleStrong, rumbleWeak);
        padView.setAlpha(deviceId != -1 ? 1 : 0.35f);
        controllers.addView(padView, gapped(84, 49));
        controllers.addView(new IconView(this, ICON_RIGHT), gapped(20, 20));

        LinearLayout sound = clickableRow(() -> show(HEADPHONES), "Headphones, open");
        sound.setMinimumHeight(dp(64));
        sound.setPadding(dp(4), dp(6), dp(4), dp(6));
        LinearLayout soundText = featureText("Headphones · from PC", "", TEXT);
        soundName = (TextView) soundText.getChildAt(1);
        soundDetail = mono("");
        soundText.addView(soundDetail);
        sound.addView(soundText, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        headphones = new HeadphonesView(this, levels);
        sound.addView(headphones, gapped(60, 60));
        sound.addView(new IconView(this, ICON_RIGHT), gapped(20, 20));

        LinearLayout mic = row();
        mic.setMinimumHeight(dp(64));
        mic.setPadding(dp(4), dp(6), dp(4), dp(6));
        LinearLayout micText = featureText("Mic · to PC", "Coming later", MUTED);
        micText.addView(detail("A spot for the next feature. Not built yet."));
        mic.addView(micText, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout features = column();
        features.setAlpha(lost ? 0.45f : 1);
        features.addView(strand(toControllers, 26, node(answering && players > 0 ? NODE_LIVE : NODE_IDLE, ICON_ARROW_UP), 24, toSound, controllers, true));
        features.addView(strand(toSound, 26, node(soundLive ? NODE_LIVE : NODE_IDLE, ICON_ARROW_DOWN), 24, BORDER, sound, true));
        features.addView(strand(BORDER, 26, node(NODE_LATER, ICON_ARROW_UP), 24, 0, mic, true));

        LinearLayout cord = column();
        cord.setPadding(dp(16), dp(16), dp(16), 0);
        cord.addView(strand(0, 22, square, 14, toControllers, pcBlock, false));
        cord.addView(features);

        LinearLayout actions = column();
        actions.addView(new View(this), weighted(ViewGroup.LayoutParams.MATCH_PARENT, 0));
        if (!lost) {
            Button game = button("Game mode", PRIMARY, this::openGame);
            LinearLayout.LayoutParams gameParams = buttonParams(48);
            gameParams.topMargin = dp(16);
            actions.addView(game, gameParams);
        }
        actions.addView(spaced(button("Settings", SECONDARY, () -> show(SETTINGS)), 44));

        actions.setPadding(dp(16), 0, dp(16), dp(16));
        LinearLayout root = column();
        root.addView(cord);
        root.addView(actions, weighted(ViewGroup.LayoutParams.MATCH_PARENT, 0));
        return scrolling(root);
    }

    /** One stretch of the cord beside its content: a line in, the node, then a line out that stretches with the row. */
    private LinearLayout strand(int inColor, int inDp, View node, int nodeDp, int outColor, View content, boolean feature) {
        LinearLayout cord = column();
        cord.setGravity(Gravity.CENTER_HORIZONTAL);
        cord.addView(cordLine(inColor), new LinearLayout.LayoutParams(dp(2), dp(inDp)));
        cord.addView(node, new LinearLayout.LayoutParams(dp(nodeDp), dp(nodeDp)));
        cord.addView(cordLine(outColor), weighted(dp(2), 0));
        LinearLayout strand = new LinearLayout(this);
        strand.addView(cord, new LinearLayout.LayoutParams(dp(24), ViewGroup.LayoutParams.MATCH_PARENT));
        LinearLayout.LayoutParams params = weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT);
        // A feature row's middle lines up with its node: 6 above, 14 below.
        params.setMargins(dp(12), feature ? dp(6) : 0, 0, feature ? dp(14) : 0);
        strand.addView(content, params);
        return strand;
    }

    private View cordLine(int color) {
        View line = new View(this);
        line.setBackgroundColor(color);
        return line;
    }

    private View node(int kind, int icon) {
        FrameLayout node = new FrameLayout(this);
        GradientDrawable shape = new GradientDrawable();
        shape.setShape(GradientDrawable.OVAL);
        if (kind == NODE_LIVE) {
            shape.setColor(ACTIVE);
        } else {
            shape.setColor(BACKGROUND);
            if (kind == NODE_IDLE) shape.setStroke(dp(2), IDLE);
            else shape.setStroke(dp(2), LATER, dp(3), dp(2));
        }
        node.setBackground(shape);
        int arrow = kind == NODE_LIVE ? BACKGROUND : kind == NODE_IDLE ? MUTED : IDLE;
        node.addView(new IconView(this, icon, arrow),
            new FrameLayout.LayoutParams(dp(12), dp(12), Gravity.CENTER));
        return node;
    }

    private TextView chip(int number, boolean on) {
        TextView chip = text(String.valueOf(number), 10, on ? BACKGROUND : MUTED);
        chip.setTypeface(Typeface.DEFAULT_BOLD);
        chip.setGravity(Gravity.CENTER);
        chip.setIncludeFontPadding(false);
        GradientDrawable shape = new GradientDrawable();
        shape.setShape(GradientDrawable.OVAL);
        if (on) shape.setColor(ACTIVE);
        else shape.setStroke(dp(1.5f), IDLE, dp(2), dp(2));
        chip.setBackground(shape);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(dp(18), dp(18));
        params.rightMargin = dp(4);
        chip.setLayoutParams(params);
        return chip;
    }

    /** Caption, then the value line. */
    private LinearLayout featureText(String caption, String value, int valueColor) {
        LinearLayout block = column();
        block.addView(text(caption, 12, MUTED));
        TextView line = text(value, 16, valueColor);
        line.setTypeface(medium());
        block.addView(line);
        return block;
    }

    private TextView mono(String value) {
        TextView view = text(value, 12, MUTED);
        view.setTypeface(Typeface.MONOSPACE);
        return view;
    }

    private TextView detail(String value) {
        TextView view = text(value, 13, MUTED);
        view.setLineSpacing(0, 1.4f);
        return view;
    }

    /** Settings-only detail line: the design's tighter 1.35 line spacing. */
    private TextView settingsNote(String value) {
        TextView view = detail(value);
        view.setLineSpacing(0, 1.35f);
        return view;
    }

    private LinearLayout.LayoutParams gapped(int widthDp, int heightDp) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(dp(widthDp), dp(heightDp));
        params.leftMargin = dp(10);
        return params;
    }

    private String lostMessage(Pc pc) {
        String error = networkError;
        if (pc == usbPc) return "Check that Musubi is running on the PC this phone is plugged into.";
        return error != null
            ? "The phone can't send on this network: " + error
            : "Check that Musubi is running on " + pc.ip + ", that you allowed this phone there, and that the firewall lets it through.";
    }

    private View controllerBlock() {
        LinearLayout block = column();
        block.addView(text("Controller", 12, MUTED));
        TextView name = text(deviceName != null ? deviceName : "Not connected", 16, TEXT);
        name.setTypeface(medium());
        block.addView(name);
        TextView status = text(deviceName != null ? "Connected" : "Pair your controller in the phone's Bluetooth settings.", 13, MUTED);
        block.addView(status);
        return block;
    }

    private View firstRunForm() {
        LinearLayout form = column();
        form.addView(field("PC IP address", draftIp, "192.168.1.12", true, value -> draftIp = value));
        TextView hint = text(formError != null ? formError : "To find it, run on the PC: ip -4 addr", 12, formError != null ? ALERT : MUTED);
        hint.setPadding(0, dp(6), 0, 0);
        form.addView(hint);
        return divided(form);
    }

    private void saveFirstPc() {
        String ip = draftIp.trim();
        if (!Patterns.IP_ADDRESS.matcher(ip).matches()) {
            formError = "That IP address isn't valid.";
            render();
            return;
        }
        pcs.add(new Pc("My PC", ip));
        draftIp = "";
        formError = null;
        selectPc(pcs.size() - 1);
        render();
    }

    /** Always the dialog, even with one PC: its "Manage PCs" button is the way to add another. */
    private void switchPc() {
        AlertDialog dialog = new AlertDialog.Builder(this).create();
        LinearLayout panel = column();
        panel.setPadding(dp(16), dp(16), dp(16), dp(16));
        GradientDrawable background = new GradientDrawable();
        background.setColor(SURFACE);
        background.setStroke(dp(1), BORDER);
        background.setCornerRadius(dp(6));
        panel.setBackground(background);
        TextView title = text("Switch PC", 18, TEXT);
        title.setTypeface(medium());
        title.setPadding(0, 0, 0, dp(8));
        panel.addView(title);
        boolean onCable = currentPc() == usbPc;
        if (onCable) {
            // The cable always wins while it's plugged in, so Wi-Fi rows here would look like they do nothing.
            panel.addView(pcRow(usbPc.name, "USB cable", true, dialog::dismiss));
            TextView note = settingsNote("Unplug the cable to use Wi-Fi.");
            note.setPadding(dp(34), dp(4), dp(8), dp(4));  // Lines up with the PC name, past the dot.
            panel.addView(note);
        }
        for (int i = 0; !onCable && i < pcs.size(); i++) {
            int index = i;
            Pc pc = pcs.get(i);
            panel.addView(pcRow(pc.name, pc.ip, !onCable && i == selectedPc, () -> {
                selectPc(index);
                dialog.dismiss();
                render();
            }));
        }
        panel.addView(spaced(button("Manage PCs", SECONDARY, () -> {
            dialog.dismiss();
            show(SETTINGS);
        }), 44));
        dialog.setView(panel);
        dialog.show();
        if (dialog.getWindow() != null) {
            dialog.getWindow().setBackgroundDrawable(new ColorDrawable(0));
        }
    }

    private LinearLayout pcRow(String title, String detail, boolean active, Runnable pick) {
        LinearLayout row = clickableRow(pick, title + ", " + detail + (active ? ", in use" : ""));
        row.setPadding(dp(8), dp(6), dp(8), dp(6));
        View dot = new View(this);
        GradientDrawable circle = new GradientDrawable();
        circle.setShape(GradientDrawable.OVAL);
        if (active) circle.setColor(ACTIVE);
        else circle.setStroke(dp(2), IDLE);
        dot.setBackground(circle);
        LinearLayout.LayoutParams dotParams = new LinearLayout.LayoutParams(dp(14), dp(14));
        dotParams.rightMargin = dp(12);
        row.addView(dot, dotParams);
        LinearLayout labels = column();
        TextView name = text(title, 15, active ? ACTIVE : TEXT);
        name.setTypeface(medium());
        labels.addView(name);
        labels.addView(mono(detail + (active ? " · In use" : "")));
        row.addView(labels, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        return row;
    }

    private View buildGame() {
        LinearLayout root = column();
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(GAME_BG);
        root.setClickable(true);
        root.setContentDescription("Game mode. Tap to go back");
        root.setOnClickListener(view -> show(gameFrom));
        Pc pc = currentPc();
        boolean lost = shownPcState == LOST;
        String status = lost ? "PC not responding"
            : (deviceName != null ? deviceName : "No controller") + " to " + (pc != null ? pc.name : "no PC");
        TextView line = text(status, 16, lost ? ALERT : MUTED);
        line.setGravity(Gravity.CENTER);
        root.addView(line);
        TextView back = text("Tap to go back", 13, MUTED);
        back.setGravity(Gravity.CENTER);
        back.setPadding(0, dp(8), 0, 0);
        root.addView(back);
        return root;
    }

    /** Spec 4.5, plus the output choice between the sound switch and the note about the delay. */
    private View buildHeadphones() {
        LinearLayout body = column();
        body.setPadding(dp(16), dp(16), dp(16), dp(16));
        body.addView(text("Earbuds", 12, MUTED));
        earbudsName = text("", 16, TEXT);
        earbudsName.setTypeface(medium());
        body.addView(earbudsName);
        earbudsDetail = detail("");
        body.addView(earbudsDetail);

        FrameLayout stage = new FrameLayout(this);
        stage.setPadding(0, dp(16), 0, dp(8));
        headphones = new HeadphonesView(this, levels);
        stage.addView(headphones, new FrameLayout.LayoutParams(dp(250), dp(222), Gravity.CENTER));
        body.addView(stage, weighted(ViewGroup.LayoutParams.MATCH_PARENT, 0));
        soundReadout = mono("");
        soundReadout.setGravity(Gravity.CENTER);
        soundReadout.setPadding(0, 0, 0, dp(8));
        body.addView(soundReadout);

        LinearLayout play = row();
        LinearLayout playText = column();
        TextView playTitle = text("Play PC sound", 16, TEXT);
        playTitle.setTypeface(medium());
        playText.addView(playTitle);
        soundStatus = mono("");
        playText.addView(soundStatus);
        play.addView(playText, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        play.addView(soundSwitch(), gapped(56, 44));
        body.addView(divided(play));

        LinearLayout output = column();
        output.addView(text("Plays on", 12, MUTED));
        LinearLayout tabs = row();
        tabs.setPadding(0, dp(6), 0, 0);
        tabs.addView(outputTab("Headphones", false), weighted(0, dp(44)));
        View gap = new View(this);
        tabs.addView(gap, new LinearLayout.LayoutParams(dp(4), 0));
        tabs.addView(outputTab("Phone speaker", true), weighted(0, dp(44)));
        output.addView(tabs);
        output.addView(cordLine(BORDER), buttonParams(1));
        TextView outputNote = detail(useSpeaker() ? "The phone's own speaker, even while earbuds are connected."
            : "Earbuds or wired headphones, when connected. Without them, nothing plays."
                + (Build.VERSION.SDK_INT >= 23 ? "" : " The phone speaker needs Android 6 or newer."));
        outputNote.setPadding(0, dp(8), 0, 0);
        output.addView(outputNote);
        body.addView(divided(output));

        LinearLayout about = column();
        about.addView(text("About the delay", 12, MUTED));
        TextView aboutNote = detail("Bluetooth adds its own delay, which some phones can't measure. "
            + "For less delay, use the USB cable and your earbuds' game mode.");
        aboutNote.setPadding(0, dp(8), 0, 0);
        about.addView(aboutNote);
        body.addView(divided(about));
        return withHeader("Headphones", node(soundLive() ? NODE_LIVE : NODE_IDLE, ICON_ARROW_DOWN), () -> show(HOME), body);
    }

    /** Spec 4.3. Until multi-pad ships only player 1 is real; players 2 to 4 are placeholders. */
    private View buildControllers(Pc pc) {
        boolean connected = deviceId != -1;
        int n = selectedPlayer;
        LinearLayout strip = row();
        strip.setPadding(dp(12), dp(8), dp(12), 0);
        for (int i = 1; i <= 4; i++) {
            if (i > 1) strip.addView(new View(this), new LinearLayout.LayoutParams(dp(4), 0));
            strip.addView(playerTab(i, connected), weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        }

        LinearLayout body = column();
        body.setPadding(dp(16), dp(16), dp(16), dp(16));
        TextView value = text(n > 1 ? "Player " + n : connected ? deviceName : "Player 1 is free", 16, TEXT);
        value.setTypeface(medium());
        body.addView(value);
        body.addView(detail(n > 1 ? "More controllers at once are coming in a later version."
            : connected ? "Player 1 · games see Xbox 360 controller 1"
            : "Turn on a controller paired with this phone. Games will see it as Xbox 360 controller 1."));
        if (n == 1 && connected && !canVibrate(deviceId)) body.addView(detail("Can't vibrate over Bluetooth"));
        if (n > 1) {
            body.addView(new View(this), weighted(ViewGroup.LayoutParams.MATCH_PARENT, 0));
        } else {
            PadView pad = new PadView(this);
            pad.setContentDescription("Controller");
            int padding = dp(8);
            pad.setPadding(padding, padding, padding, padding);
            body.addView(pad, weighted(ViewGroup.LayoutParams.MATCH_PARENT, 0));
            TextView status = mono(connected ? "" : "Waiting for a controller");
            status.setGravity(Gravity.CENTER);
            status.setPadding(0, 0, 0, dp(8));
            body.addView(status);
            if (connected) {
                padView = pad;
                pad.setRumble(rumbleStrong, rumbleWeak);
                readout = status;
                body.addView(button("Remap", SECONDARY, this::startRemap), buttonParams(44));
            } else {
                pad.setAlpha(0.35f);
                body.addView(button("Pair a new controller", SECONDARY, () -> {
                    try {
                        startActivity(new Intent(android.provider.Settings.ACTION_BLUETOOTH_SETTINGS));
                    } catch (ActivityNotFoundException missing) {
                        Toast.makeText(this, "This phone has no Bluetooth settings to open", Toast.LENGTH_SHORT).show();
                    }
                }), buttonParams(44));
                TextView caption = text("Opens the phone's Bluetooth settings", 12, MUTED);
                caption.setGravity(Gravity.CENTER);
                caption.setPadding(0, dp(6), 0, 0);
                body.addView(caption);
            }
        }
        LinearLayout footer = column();
        footer.addView(detail("Controllers only work while Musubi is open. Game mode keeps it open with the screen dimmed."));
        LinearLayout.LayoutParams gameParams = buttonParams(48);
        gameParams.topMargin = dp(12);
        footer.addView(button("Game mode", PRIMARY, this::openGame), gameParams);
        body.addView(divided(footer));

        LinearLayout page = column();
        page.addView(strip);
        page.addView(cordLine(BORDER), buttonParams(1));
        page.addView(body, weighted(ViewGroup.LayoutParams.MATCH_PARENT, 0));
        LinearLayout root = (LinearLayout) withHeader("Controllers", node(shownPcState == RESPONDING && deviceId != -1 ? NODE_LIVE : NODE_IDLE, ICON_ARROW_UP), () -> show(HOME), page);
        LinearLayout header = (LinearLayout) root.getChildAt(0);
        header.getChildAt(0).setContentDescription("Back to " + pc.name);
        header.addView(new View(this), weighted(0, 0));
        header.addView(mono((connected ? 1 : 0) + " of 4"));
        return root;
    }

    /** Number over label; the selected tab gets the surface fill and a cream bottom edge, like the output tabs. */
    private View playerTab(int n, boolean connected) {
        boolean real = n == 1, on = real && connected, selected = n == selectedPlayer;
        LinearLayout tab = column();
        tab.setGravity(Gravity.CENTER_HORIZONTAL);
        tab.setPadding(0, dp(10), 0, 0);
        tab.setClickable(true);
        tab.setFocusable(true);
        tab.setSelected(selected);
        tab.setContentDescription("Player " + n + (on ? ", " + deviceName : real ? ", free" : ", coming later"));
        tab.setOnClickListener(view -> {
            selectedPlayer = n;
            render();
        });
        GradientDrawable fill = new GradientDrawable();
        int r = dp(6);
        fill.setCornerRadii(new float[] {r, r, r, r, 0, 0, 0, 0});
        fill.setColor(selected ? SURFACE : 0);
        tab.setBackground(ripple(fill));
        TextView number = text(String.valueOf(n), 13, on ? BACKGROUND : MUTED);
        number.setTypeface(Typeface.DEFAULT_BOLD);
        number.setGravity(Gravity.CENTER);
        number.setIncludeFontPadding(false);
        GradientDrawable shape = new GradientDrawable();
        shape.setShape(GradientDrawable.OVAL);
        if (on) shape.setColor(ACTIVE);
        else shape.setStroke(dp(2), IDLE, dp(3), dp(2));
        number.setBackground(shape);
        tab.addView(number, new LinearLayout.LayoutParams(dp(28), dp(28)));
        TextView label = text(on ? "Connected" : real ? "Free" : "Later", 11, selected ? TEXT : MUTED);
        label.setGravity(Gravity.CENTER);
        label.setSingleLine(true);
        label.setEllipsize(TextUtils.TruncateAt.END);
        label.setPadding(dp(2), dp(6), dp(2), dp(8));
        tab.addView(label, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        // A child view, not a layer: API 21 can't pin a layer to the bottom of a wrap-content height.
        View edge = new View(this);
        edge.setBackgroundColor(selected ? TEXT : 0);
        tab.addView(edge, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(2)));
        return tab;
    }

    private View soundSwitch() {
        SwitchView view = new SwitchView(this);
        view.setChecked(soundOn);
        view.setContentDescription("Play PC sound");
        view.setBackground(ripple(new ColorDrawable(0)));
        view.setOnCheckedChangeListener((button, on) -> setSound(on));
        return view;
    }

    /** Styled like the player strip: the chosen output gets the surface fill and a cream bottom edge. */
    private Button outputTab(String label, boolean forSpeaker) {
        Button tab = button(label, PLAIN, () -> setSpeaker(forSpeaker));
        styleTab(tab, forSpeaker == useSpeaker());
        if (forSpeaker && Build.VERSION.SDK_INT < 23) {
            tab.setEnabled(false);
            tab.setAlpha(0.5f);
        }
        return tab;
    }

    private void styleTab(Button tab, boolean chosen) {
        tab.setSelected(chosen);
        tab.setTextColor(chosen ? TEXT : MUTED);
        if (chosen) {
            ColorDrawable edge = new ColorDrawable(TEXT);
            LayerDrawable fill = new LayerDrawable(new Drawable[] {new ColorDrawable(SURFACE), edge});
            fill.setLayerInset(1, 0, dp(42), 0, 0);
            tab.setBackground(ripple(fill));
        }
    }

    private void startRemap() {
        pending = map.copy();
        history.clear();
        claimed.clear();
        capture.clear();
        stepMapped = new boolean[STEP_CONTROLS.length];
        step = 0;
        stepNote = null;
        ignoreInputUntil = SystemClock.uptimeMillis() + STEP_COOLDOWN_MS;
        screen = REMAP;
        // The game shouldn't react to presses meant for the setup.
        clearInput();
        render();
    }

    private void remapInput(int input) {
        long now = SystemClock.uptimeMillis();
        if (now < ignoreInputUntil) return;
        Integer usedBy = claimed.get(input);
        if (usedBy != null) {
            if (capture.isEmpty()) {
                stepNote = "Already used for " + STEP_NAMES[usedBy] + ". Press another button or skip.";
                render();
            }
            return;
        }
        if (capture.isEmpty()) {
            handler.postDelayed(finishCapture, CAPTURE_WINDOW_MS);
        }
        if (!capture.contains(input)) capture.add(input);
    }

    private void finishCapture() {
        if (capture.isEmpty() || screen != REMAP) return;
        history.add(pending.copy());
        pending.assign(STEP_CONTROLS[step], capture);
        for (int input : capture) claimed.put(input, step);
        stepMapped[step] = true;
        capture.clear();
        nextStep();
    }

    private void skipStep() {
        handler.removeCallbacks(finishCapture);
        capture.clear();
        history.add(pending.copy());
        stepMapped[step] = false;
        nextStep();
    }

    private void nextStep() {
        step++;
        stepNote = null;
        ignoreInputUntil = SystemClock.uptimeMillis() + STEP_COOLDOWN_MS;
        if (step < STEP_CONTROLS.length) {
            render();
            return;
        }
        map = pending;
        clearInput();
        show(REMAP_DONE);
    }

    private void previousStep() {
        handler.removeCallbacks(finishCapture);
        capture.clear();
        step--;
        pending = history.remove(history.size() - 1);
        stepMapped[step] = false;
        Iterator<Integer> owners = claimed.values().iterator();
        while (owners.hasNext()) {
            if (owners.next() >= step) owners.remove();
        }
        stepNote = null;
        render();
    }

    private View buildRemap() {
        LinearLayout top = column();
        TextView progress = text("Step " + (step + 1) + " of " + STEP_CONTROLS.length, 12, MUTED);
        progress.setTypeface(Typeface.MONOSPACE);
        top.addView(progress);
        String title = STEP_TITLES[step];
        TextView heading = text(title, title.length() > 14 ? 24 : 32, TEXT);
        heading.setTypeface(Typeface.DEFAULT_BOLD);
        heading.setPadding(0, dp(12), 0, 0);
        top.addView(heading);
        TextView help = text("Press the button on your controller that should act as " + STEP_NAMES[step] + ".", 14, TEXT);
        help.setLineSpacing(0, 1.3f);
        help.setPadding(0, dp(8), 0, 0);
        top.addView(help);

        TextView status = text(stepNote != null ? stepNote : "Waiting for your controller", 13, stepNote != null ? ALERT : MUTED);
        status.setGravity(Gravity.CENTER_VERTICAL);
        status.setPadding(0, dp(8), 0, dp(8));

        LinearLayout actions = column();
        Button back = button("Back", SECONDARY, this::previousStep);
        back.setEnabled(step > 0);
        back.setAlpha(step > 0 ? 1 : 0.5f);
        LinearLayout pair = pair(back, button("Skip", SECONDARY, this::skipStep));
        ((LinearLayout.LayoutParams) pair.getLayoutParams()).topMargin = 0;
        actions.addView(pair);
        actions.addView(spaced(button("Cancel remap", PLAIN, () -> show(CONTROLLERS)), 44));
        return withPad(STEP_CONTROLS[step], top, status, actions);
    }

    private View buildRemapDone() {
        int mapped = 0;
        for (boolean done : stepMapped) {
            if (done) mapped++;
        }
        LinearLayout top = column();
        TextView heading = text("Try your new layout", 24, TEXT);
        heading.setTypeface(Typeface.DEFAULT_BOLD);
        top.addView(heading);
        TextView help = text("Each button should light up on the drawing when you press it.", 14, TEXT);
        help.setLineSpacing(0, 1.3f);
        help.setPadding(0, dp(8), 0, 0);
        top.addView(help);
        TextView counts = text(mapped + " mapped · " + (stepMapped.length - mapped) + " skipped", 12, MUTED);
        counts.setTypeface(Typeface.MONOSPACE);
        counts.setPadding(0, dp(12), 0, 0);
        top.addView(counts);
        TextView forDevice = text("For " + deviceName, 13, MUTED);
        forDevice.setPadding(0, dp(4), 0, 0);
        top.addView(forDevice);

        LinearLayout actions = column();
        actions.addView(button("Save", PRIMARY, () -> {
            prefs.edit().putString(mapKey(), map.encode()).apply();
            show(CONTROLLERS);
        }), buttonParams(48));
        actions.addView(pair(
            button("Start over", SECONDARY, () -> {
                loadMap();
                startRemap();
            }),
            button("Reset", SECONDARY, () -> {
                resetLayout();
                show(CONTROLLERS);
            })));
        return withPad(-1, top, null, actions);
    }

    private void resetLayout() {
        prefs.edit().remove(mapKey()).apply();
        loadMap();
    }

    private View buildSettings() {
        LinearLayout pcSection = column();
        pcSection.addView(sectionHeading("PCs", shownPcState == RESPONDING, ICON_SQUARE, null));
        for (int i = 0; i < pcs.size(); i++) {
            pcSection.addView(pcSettingsRow(i));
        }
        View addPc = spaced(button("Add PC", SECONDARY, () -> editPc(-1)), 44);
        ((LinearLayout.LayoutParams) addPc.getLayoutParams()).topMargin = dp(4);
        pcSection.addView(addPc);
        TextView cableNote = settingsNote("A USB cable always wins over Wi-Fi while it's plugged in.");
        cableNote.setPadding(0, dp(10), 0, 0);
        pcSection.addView(cableNote);

        // Until multi-pad ships at most one controller is connected.
        boolean controller = deviceId != -1;
        LinearLayout generalSection = column();
        generalSection.addView(sectionHeading("General", false, ICON_SLIDERS, null));
        generalSection.addView(prefRow("Keep the screen on", "While Musubi is open. Game mode always keeps it on.", "keep_screen",
            () -> applyGameWindow(screen == GAME)));
        generalSection.addView(prefRow("Play in the background", "PC sound keeps playing after you leave Musubi. Controllers wait until you're back.", "background", null));
        boolean blocked = Build.VERSION.SDK_INT >= 24
            && !((android.app.NotificationManager) getSystemService(NOTIFICATION_SERVICE)).areNotificationsEnabled();
        LinearLayout notificationsOff = column();
        generalSection.addView(prefRow("Ask to open Musubi", "Shows a notification when a paired controller turns on.", "ask_open",
            () -> notificationsOff.setVisibility(blocked && prefs.getBoolean("ask_open", true) ? View.VISIBLE : View.GONE)));
        if (blocked) {
            TextView warning = settingsNote("Android has notifications off for Musubi, so this can't show.");
            warning.setTextColor(ALERT);
            notificationsOff.addView(warning);
            notificationsOff.addView(spaced(button("Allow notifications", SECONDARY, this::openNotificationSettings), 44));
            notificationsOff.setPadding(0, 0, 0, dp(4));
            notificationsOff.setVisibility(prefs.getBoolean("ask_open", true) ? View.VISIBLE : View.GONE);
            generalSection.addView(notificationsOff);
        }

        LinearLayout soundSection = column();
        settingsSoundLive = soundOn && soundArriving();
        soundSection.addView(sectionHeading("Sound", settingsSoundLive, ICON_ARROW_DOWN, null));
        soundSection.addView(bufferRow("Lowest delay", "Best on good Wi-Fi or a USB cable.", false));
        soundSection.addView(bufferRow("Most stable", "For choppy Wi-Fi. Waits a bit longer and sends fewer, bigger packets.", true));
        settingsSoundNow = mono("");
        settingsSoundNow.setTextColor(ACTIVE);
        settingsSoundNow.setPadding(dp(36), dp(4), 0, dp(2));
        settingsSoundNow.setVisibility(View.GONE);
        soundSection.addView(settingsSoundNow);
        updateSoundNow();

        LinearLayout controllerSection = column();
        controllerSection.addView(sectionHeading("Controllers", controller, ICON_ARROW_UP, controller ? "1 connected" : null));
        TextView layouts = text("Button layouts", 15, TEXT);
        layouts.setTypeface(medium());
        layouts.setPadding(0, dp(4), 0, 0);
        controllerSection.addView(layouts);
        controllerSection.addView(settingsNote("Each controller keeps its own layout. Change one from Controllers, on its player, with Remap."));
        boolean canReset = controller && hasCustomMap();
        Button reset = button("Reset layout", SECONDARY, () -> {
            resetLayout();
            render();
        });
        reset.setEnabled(canReset);
        reset.setAlpha(canReset ? 1 : 0.5f);
        controllerSection.addView(spaced(reset, 44));
        boolean stuck = controller && !canVibrate(deviceId);
        LinearLayout vibrationRow = prefRow("Vibration", stuck ? deviceName + " can't vibrate over Bluetooth" : "Rumble from games", "vibration",
            () -> {
                vibration = prefs.getBoolean("vibration", true);
                render();
            });
        if (stuck) {
            View toggle = vibrationRow.getChildAt(1);
            toggle.setEnabled(false);
            toggle.setAlpha(0.5f);
        }
        vibrationRow.setPadding(0, dp(8), 0, 0);
        controllerSection.addView(vibrationRow);
        if (vibration && !stuck) {
            if (!controller || canVaryStrength(deviceId)) {
                controllerSection.addView(strengthRow(controller));
            } else {
                LinearLayout testRow = row();
                testRow.addView(testButton());
                controllerSection.addView(testRow);
            }
        }
        controllerSection.addView(deadzoneRow(controller));

        LinearLayout gameSection = column();
        gameSection.addView(sectionHeading("Game mode", false, ICON_HALF, null));
        LinearLayout autoRow = row();
        autoRow.setMinimumHeight(dp(52));
        LinearLayout autoText = column();
        TextView autoTitle = text("Turn on by itself", 15, TEXT);
        autoTitle.setTypeface(medium());
        autoText.addView(autoTitle);
        autoText.addView(settingsNote("After 1 minute without touching the screen, while a controller is connected"));
        autoRow.addView(autoText, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        SwitchView auto = new SwitchView(this);
        auto.setChecked(autoGameMode);
        auto.setContentDescription("Turn on game mode by itself");
        auto.setBackground(ripple(new ColorDrawable(0)));
        auto.setOnCheckedChangeListener((view, checked) -> {
            autoGameMode = checked;
            prefs.edit().putBoolean("autoGameMode", checked).apply();
        });
        autoRow.addView(auto, gapped(56, 44));
        gameSection.addView(autoRow);

        LinearLayout brightness = column();
        brightness.setPadding(0, dp(8), 0, 0);
        TextView brightnessTitle = text("Screen brightness", 15, TEXT);
        brightnessTitle.setTypeface(medium());
        brightness.addView(brightnessTitle);
        brightness.addView(settingsNote("Lowest saves the most battery."));
        LinearLayout levels = row();
        levels.setPadding(0, dp(6), 0, 0);
        int[] percents = {1, 5, 15, 30};
        for (int i = 0; i < percents.length; i++) {
            int percent = percents[i];
            Button level = button(percent + "%", PLAIN, () -> {
                prefs.edit().putInt("game_brightness", percent).apply();
                render();
            });
            boolean chosen = percent == prefs.getInt("game_brightness", 1);
            level.setSelected(chosen);
            level.setTypeface(Typeface.MONOSPACE);
            level.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
            level.setTextColor(chosen ? BACKGROUND : TEXT);
            GradientDrawable outline = new GradientDrawable();
            outline.setCornerRadius(dp(6));
            outline.setColor(chosen ? TEXT : 0);
            outline.setStroke(dp(1), chosen ? TEXT : IDLE);
            level.setBackground(ripple(outline));
            level.setContentDescription("Screen brightness " + percent + "%");
            if (i > 0) levels.addView(new View(this), new LinearLayout.LayoutParams(dp(6), 0));
            levels.addView(level, weighted(0, dp(44)));
        }
        brightness.addView(levels);
        gameSection.addView(brightness);

        LinearLayout aboutSection = column();
        aboutSection.addView(sectionHeading("About", false, ICON_INFO, null));
        aboutSection.addView(link("PC setup guide", REPO + "#setup"));
        aboutSection.addView(link("Source code", REPO));
        TextView version = mono("Musubi " + versionName() + " · MIT license");
        version.setPadding(0, dp(8), 0, 0);
        aboutSection.addView(version);

        LinearLayout body = column();
        View[] sections = {pcSection, generalSection, soundSection, controllerSection, gameSection, aboutSection};
        for (int i = 0; i < sections.length; i++) {
            if (i > 0) body.addView(cordLine(BORDER), buttonParams(1));
            sections[i].setPadding(dp(16), dp(16), dp(16), dp(12));
            body.addView(sections[i]);
        }
        // The 44dp deadzone slider already carries its own air below the track.
        controllerSection.setPadding(dp(16), dp(16), dp(16), dp(4));
        return withHeader("Settings", null, () -> show(HOME), body);
    }

    /** One sound buffer choice: a 20dp ring, bone with a dot when chosen, like the canvas. */
    private View bufferRow(String title, String note, boolean stable) {
        boolean chosen = stable == stableSound;
        LinearLayout row = clickableRow(() -> setStableSound(stable), title + ", " + note);
        row.setMinimumHeight(dp(56));
        FrameLayout dot = new FrameLayout(this);
        GradientDrawable ring = new GradientDrawable();
        ring.setShape(GradientDrawable.OVAL);
        ring.setStroke(dp(2), chosen ? TEXT : IDLE);
        dot.setBackground(ring);
        if (chosen) {
            View fill = new View(this);
            GradientDrawable inner = new GradientDrawable();
            inner.setShape(GradientDrawable.OVAL);
            inner.setColor(TEXT);
            fill.setBackground(inner);
            dot.addView(fill, new FrameLayout.LayoutParams(dp(10), dp(10), Gravity.CENTER));
        }
        LinearLayout.LayoutParams dotParams = new LinearLayout.LayoutParams(dp(20), dp(20));
        dotParams.leftMargin = dotParams.rightMargin = dp(4);
        row.addView(dot, dotParams);
        LinearLayout text = column();
        text.setPadding(dp(12), dp(4), 0, dp(4));
        TextView name = text(title, 15, TEXT);
        name.setTypeface(medium());
        text.addView(name);
        text.addView(settingsNote(note));
        row.addView(text, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        row.setAccessibilityDelegate(new View.AccessibilityDelegate() {
            @Override
            public void onInitializeAccessibilityNodeInfo(View host, AccessibilityNodeInfo info) {
                super.onInitializeAccessibilityNodeInfo(host, info);
                info.setClassName(RadioButton.class.getName());
                info.setCheckable(true);
                info.setChecked(chosen);
            }
        });
        return row;
    }

    private void setStableSound(boolean on) {
        if (on == stableSound) return;
        stableSound = on;
        prefs.edit().putString("sound_buffer", on ? "stable" : "low").apply();
        // Refills to the new target at once instead of drifting there a frame at a time; the next hello switches the PC's packets.
        synchronized (audioLock) {
            audio.setSteady(on);
            audio.reset();
        }
        delayMs = -1;
        measuringNewDelay = true;
        render();
    }

    /** The amber "Now:" line under the choices, only while sound plays; touches the view only when its text changes. */
    private void updateSoundNow() {
        boolean playing = soundOn && soundArriving();
        if (playing != settingsSoundLive) {
            render();
            return;
        }
        int delay = soundDelay();
        if (delay >= 0) measuringNewDelay = false;
        String now = !playing ? "" : delay >= 0 ? "Now: delay ≈ " + delay + " ms" : measuringNewDelay ? "Now: measuring the new delay…" : "";
        if (now.contentEquals(settingsSoundNow.getText())) return;
        settingsSoundNow.setText(now);
        settingsSoundNow.setVisibility(now.isEmpty() ? View.GONE : View.VISIBLE);
    }

    private Button testButton() {
        Button test = button("Test", SECONDARY, () -> {
            int level = 65535 * vibrationStrength / 100;
            rumble(deviceId, level, level, 500);
        });
        test.setContentDescription("Test vibration");
        test.setPadding(dp(14), 0, dp(14), 0);
        test.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(44)));
        return test;
    }

    private LinearLayout strengthRow(boolean withTest) {
        LinearLayout box = column();
        box.setPadding(0, dp(8), 0, 0);
        LinearLayout labels = row();
        TextView name = text("Strength", 14, TEXT);
        name.setTypeface(medium());
        labels.addView(name, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        TextView value = mono(vibrationStrength + "%");
        value.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        labels.addView(value);
        if (withTest) {
            Button test = testButton();
            ((LinearLayout.LayoutParams) test.getLayoutParams()).leftMargin = dp(12);
            labels.addView(test);
        }
        box.addView(labels);

        StrengthView bar = new StrengthView(this, "Vibration strength", 10, 100, 10, vibrationStrength, true);
        bar.setListener(new StrengthView.Listener() {
            @Override public void onChange(int strength) {
                vibrationStrength = strength;
                value.setText(strength + "%");
                prefs.edit().putInt("vibration_strength", strength).apply();
            }
            @Override public void onHold(boolean held) {
                value.setTextColor(held ? ACTIVE : MUTED);
            }
        });
        box.addView(bar, buttonParams(44));
        return box;
    }

    private LinearLayout deadzoneRow(boolean controller) {
        LinearLayout box = column();
        box.setPadding(0, dp(8), 0, 0);
        LinearLayout labels = row();
        TextView name = text("Stick deadzone", 14, TEXT);
        name.setTypeface(medium());
        labels.addView(name, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        int percent = Math.round(deadzone * 100);
        TextView value = mono(percent == 0 ? "Off" : percent + "%");
        value.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        labels.addView(value);
        box.addView(labels);
        box.addView(settingsNote("Ignores small stick moves, for a stick that drifts."));

        StickPreview preview = new StickPreview(this, controller);
        preview.setDeadzone(deadzone);
        preview.setSticks(rawLx, rawLy, rawRx, rawRy);
        preview.setVisibility(View.GONE);
        StrengthView bar = new StrengthView(this, "Stick deadzone", 0, 30, 1, percent, false);
        bar.setListener(new StrengthView.Listener() {
            @Override public void onChange(int next) {
                deadzone = next / 100f;
                value.setText(next == 0 ? "Off" : next + "%");
                preview.setDeadzone(deadzone);
                prefs.edit().putInt("deadzone", next).apply();
            }
            @Override public void onHold(boolean held) {
                value.setTextColor(held ? ACTIVE : MUTED);
                preview.setVisibility(held ? View.VISIBLE : View.GONE);
                stickPreview = held ? preview : null;
                if (held) preview.setSticks(rawLx, rawLy, rawRx, rawRy);
            }
        });
        box.addView(bar, buttonParams(44));
        LinearLayout.LayoutParams previewParams = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        previewParams.topMargin = dp(4);
        previewParams.bottomMargin = dp(8);
        box.addView(preview, previewParams);
        return box;
    }

    private void openNotificationSettings() {
        Intent intent = Build.VERSION.SDK_INT >= 26
            ? new Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, getPackageName())
            : new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", getPackageName(), null));
        try {
            startActivity(intent);
        } catch (ActivityNotFoundException noScreen) {
            Toast.makeText(this, "Couldn't open Android's settings for Musubi", Toast.LENGTH_SHORT).show();
        }
    }

    private LinearLayout prefRow(String title, String note, String key, Runnable changed) {
        LinearLayout row = row();
        row.setMinimumHeight(dp(52));
        LinearLayout text = column();
        TextView name = text(title, 15, TEXT);
        name.setTypeface(medium());
        text.addView(name);
        text.addView(settingsNote(note));
        row.addView(text, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        SwitchView toggle = new SwitchView(this);
        toggle.setChecked(prefs.getBoolean(key, true));
        toggle.setContentDescription(title);
        toggle.setBackground(ripple(new ColorDrawable(0)));
        toggle.setOnCheckedChangeListener((view, checked) -> {
            prefs.edit().putBoolean(key, checked).apply();
            if (changed != null) changed.run();
        });
        row.addView(toggle, gapped(56, 44));
        return row;
    }

    /** "In use · 8 ms" in amber while this PC answers, plain "In use" otherwise. */
    private CharSequence pcUseText(Pc pc) {
        String ip = pc.ip + " · ";
        if (pc != currentPc() || shownPcState != RESPONDING) return ip + "In use";
        SpannableString line = new SpannableString(ip + "In use · " + rttMs + " ms");
        line.setSpan(new ForegroundColorSpan(ACTIVE), ip.length(), line.length(), 0);
        return line;
    }

    private View pcSettingsRow(int index) {
        Pc pc = pcs.get(index);
        LinearLayout row = clickableRow(() -> editPc(index), "Edit " + pc.name);
        LinearLayout labels = column();
        TextView name = text(pc.name, 15, TEXT);
        name.setTypeface(medium());
        labels.addView(name);
        TextView detail = mono(pc.ip);
        if (index == selectedPc) {
            detail.setText(pcUseText(pc));
            settingsPing = detail;
        }
        labels.addView(detail);
        row.addView(labels, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        row.addView(new IconView(this, ICON_RIGHT), new LinearLayout.LayoutParams(dp(20), dp(20)));
        return row;
    }

    private void editPc(int index) {
        editIndex = index;
        Pc pc = index >= 0 ? pcs.get(index) : null;
        draftName = pc != null ? pc.name : "";
        draftIp = pc != null ? pc.ip : "";
        formError = null;
        show(EDIT_PC);
    }

    private View buildEditPc() {
        LinearLayout form = column();
        form.addView(field("Name", draftName, "My PC", false, value -> draftName = value));
        View ip = field("PC IP address", draftIp, "192.168.1.12", true, value -> draftIp = value);
        ip.setPadding(0, dp(12), 0, 0);
        form.addView(ip);
        TextView hint = text(formError != null ? formError : "To find it, run on the PC: ip -4 addr", 12, formError != null ? ALERT : MUTED);
        hint.setPadding(0, dp(6), 0, dp(12));
        form.addView(hint);
        form.addView(button("Save PC", PRIMARY, this::savePc), buttonParams(48));
        if (editIndex >= 0) {
            List<View> extras = new ArrayList<>();
            if (editIndex != selectedPc) {
                extras.add(button("Use this PC", SECONDARY, () -> {
                    selectPc(editIndex);
                    show(SETTINGS);
                }));
            }
            extras.add(button("Delete", SECONDARY, this::deletePc));
            form.addView(extras.size() == 2 ? pair(extras.get(0), extras.get(1)) : spaced(extras.get(0), 44));
        }
        return withHeader(editIndex >= 0 ? "Edit PC" : "Add PC", null, () -> show(SETTINGS), padded(form));
    }

    private void savePc() {
        String ip = draftIp.trim();
        if (!Patterns.IP_ADDRESS.matcher(ip).matches()) {
            formError = "That IP address isn't valid.";
            render();
            return;
        }
        String name = draftName.trim();
        if (name.isEmpty()) name = pcs.isEmpty() ? "My PC" : "PC " + (pcs.size() + 1);
        if (editIndex >= 0) {
            pcs.get(editIndex).name = name;
            pcs.get(editIndex).ip = ip;
            savePcs();
            if (editIndex == selectedPc) applySelectedPc();
        } else {
            pcs.add(new Pc(name, ip));
            selectPc(pcs.size() - 1);
        }
        show(SETTINGS);
    }

    private void deletePc() {
        new AlertDialog.Builder(this)
            .setTitle("Delete " + pcs.get(editIndex).name + "?")
            .setPositiveButton("Delete", (dialog, which) -> {
                pcs.remove(editIndex);
                if (selectedPc >= editIndex && selectedPc > 0) selectedPc--;
                savePcs();
                applySelectedPc();
                draftIp = "";
                show(pcs.isEmpty() ? HOME : SETTINGS);
            })
            .setNegativeButton("Keep", null)
            .show();
    }

    private String versionName() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (PackageManager.NameNotFoundException impossibleForOurselves) {
            return "";
        }
    }

    private int dp(float value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private static Typeface medium() {
        // Some vendor fonts map "sans-serif-medium" to regular; the weight API is exact.
        if (Build.VERSION.SDK_INT >= 28) return Typeface.create(Typeface.DEFAULT, 500, false);
        return Typeface.create("sans-serif-medium", Typeface.NORMAL);
    }

    private TextView text(String value, int sp, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        view.setTextColor(color);
        return view;
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        return layout;
    }

    private LinearLayout row() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.HORIZONTAL);
        layout.setGravity(Gravity.CENTER_VERTICAL);
        return layout;
    }

    private static LinearLayout.LayoutParams weighted(int width, int height) {
        return new LinearLayout.LayoutParams(width, height, 1);
    }

    private LinearLayout.LayoutParams buttonParams(int heightDp) {
        return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(heightDp));
    }

    private ScrollView scrolling(View content) {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setVerticalScrollBarEnabled(false);
        scroll.addView(content);
        scroller = scroll;
        return scroll;
    }

    private View padded(View content) {
        content.setPadding(dp(16), dp(12), dp(16), dp(12));
        return content;
    }

    private View spaced(View content, int heightDp) {
        LinearLayout.LayoutParams params = buttonParams(heightDp);
        params.topMargin = dp(8);
        content.setLayoutParams(params);
        return content;
    }

    private LinearLayout pair(View first, View second) {
        LinearLayout pair = row();
        LinearLayout.LayoutParams firstParams = weighted(0, dp(44));
        firstParams.rightMargin = dp(4);
        LinearLayout.LayoutParams secondParams = weighted(0, dp(44));
        secondParams.leftMargin = dp(4);
        pair.addView(first, firstParams);
        pair.addView(second, secondParams);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(8);
        pair.setLayoutParams(params);
        return pair;
    }

    /** A hairline above the block, separating it from the one before. */
    private View divided(View content) {
        LinearLayout wrapper = column();
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(12);
        wrapper.setLayoutParams(params);
        View line = new View(this);
        line.setBackgroundColor(BORDER);
        wrapper.addView(line, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1)));
        content.setPadding(0, dp(8), 0, dp(8));
        wrapper.addView(content);
        return wrapper;
    }

    /** A Settings section heading: a small cord node, the title, then an optional amber note on the right. */
    private LinearLayout sectionHeading(String title, boolean live, int icon, String note) {
        LinearLayout heading = row();
        heading.setPadding(0, 0, 0, dp(4));
        heading.addView(node(live ? NODE_LIVE : NODE_IDLE, icon), new LinearLayout.LayoutParams(dp(24), dp(24)));
        TextView label = text(title, 14, TEXT);
        label.setTypeface(medium());
        label.setPadding(dp(10), 0, 0, 0);
        heading.addView(label, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        if (note != null) {
            TextView noteView = mono(note);
            noteView.setTextColor(ACTIVE);
            heading.addView(noteView);
        }
        return heading;
    }

    private Drawable ripple(Drawable content) {
        GradientDrawable mask = new GradientDrawable();
        mask.setColor(0xFFFFFFFF);
        mask.setCornerRadius(dp(6));
        return new RippleDrawable(ColorStateList.valueOf(0x40808080), content, mask);
    }

    private Button button(String label, int kind, Runnable action) {
        Button button = new Button(this);
        button.setAllCaps(false);
        button.setText(label);
        button.setTextSize(TypedValue.COMPLEX_UNIT_SP, kind == PRIMARY ? 15 : 14);
        button.setTextColor(kind == PRIMARY ? BACKGROUND : TEXT);
        button.setTypeface(kind == PRIMARY ? Typeface.DEFAULT_BOLD : Typeface.DEFAULT);
        button.setStateListAnimator(null);
        button.setPadding(0, 0, 0, 0);
        button.setMinimumHeight(0);
        button.setMinHeight(0);
        button.setMinimumWidth(0);
        button.setMinWidth(0);
        GradientDrawable shape = new GradientDrawable();
        shape.setCornerRadius(dp(6));
        if (kind == PRIMARY) {
            shape.setColor(TEXT);
        } else if (kind == SECONDARY) {
            shape.setStroke(dp(1), IDLE);
        }
        button.setBackground(kind == PRIMARY ? new RippleDrawable(ColorStateList.valueOf(PRIMARY_HOVER), shape, null)
            : ripple(kind == PLAIN ? new ColorDrawable(0) : shape));
        button.setOnClickListener(view -> action.run());
        return button;
    }

    private LinearLayout clickableRow(Runnable action, String description) {
        LinearLayout row = row();
        row.setMinimumHeight(dp(52));
        row.setClickable(true);
        row.setFocusable(true);
        row.setContentDescription(description);
        row.setBackground(ripple(new ColorDrawable(0)));
        row.setOnClickListener(view -> action.run());
        return row;
    }

    private View link(String label, String url) {
        LinearLayout row = clickableRow(() -> {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            } catch (ActivityNotFoundException noBrowser) {
                Toast.makeText(this, "No browser found to open this link", Toast.LENGTH_SHORT).show();
            }
        }, label);
        row.setMinimumHeight(dp(44));
        TextView view = text(label, 15, TEXT);
        view.setPaintFlags(view.getPaintFlags() | Paint.UNDERLINE_TEXT_FLAG);
        row.addView(view);
        return row;
    }

    private interface OnText {
        void changed(String value);
    }

    private View field(String label, String value, String hint, boolean ip, OnText onText) {
        LinearLayout block = column();
        TextView title = text(label, 14, TEXT);
        title.setTypeface(medium());
        title.setPadding(0, 0, 0, dp(6));
        block.addView(title);
        EditText input = new EditText(this);
        input.setId(View.generateViewId());
        title.setLabelFor(input.getId());
        input.setSingleLine();
        // Numeric input types accept only one dot, which makes an IP impossible to type.
        input.setInputType(ip
            ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI
            : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_WORDS);
        input.setHint(hint);
        input.setHintTextColor(MUTED);
        input.setTextColor(TEXT);
        input.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        if (ip) input.setTypeface(Typeface.MONOSPACE);
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(SURFACE);
        shape.setStroke(dp(1), IDLE);
        shape.setCornerRadius(dp(6));
        input.setBackground(shape);
        input.setPadding(dp(10), 0, dp(10), 0);
        input.setText(value);
        input.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void onTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void afterTextChanged(Editable s) {
                onText.changed(s.toString());
            }
        });
        block.addView(input, buttonParams(44));
        return block;
    }

    /** The page header: back, an optional cord node, then the title. */
    private View withHeader(String title, View node, Runnable onBack, View body) {
        LinearLayout root = column();
        LinearLayout header = row();
        header.setPadding(dp(4), 0, dp(16), 0);
        FrameLayout back = new FrameLayout(this);
        back.setClickable(true);
        back.setFocusable(true);
        back.setContentDescription("Back");
        back.setBackground(ripple(new ColorDrawable(0)));
        back.setOnClickListener(view -> onBack.run());
        FrameLayout.LayoutParams iconParams = new FrameLayout.LayoutParams(dp(20), dp(20), Gravity.CENTER);
        back.addView(new IconView(this, ICON_LEFT), iconParams);
        header.addView(back, new LinearLayout.LayoutParams(dp(44), dp(44)));
        if (node != null) {
            LinearLayout.LayoutParams nodeParams = new LinearLayout.LayoutParams(dp(24), dp(24));
            nodeParams.setMargins(dp(4), 0, dp(10), 0);
            header.addView(node, nodeParams);
        }
        TextView heading = text(title, 18, TEXT);
        heading.setTypeface(medium());
        header.addView(heading);
        root.addView(header, buttonParams(52));
        View line = new View(this);
        line.setBackgroundColor(BORDER);
        root.addView(line, buttonParams(1));
        root.addView(scrolling(body), weighted(ViewGroup.LayoutParams.MATCH_PARENT, 0));
        return root;
    }

    /** The spec's switch: a 44×24 track and an 18 knob, the same on every Android version. */
    private static final class SwitchView extends CompoundButton {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final RectF track = new RectF();

        SwitchView(Context context) {
            super(context, null);
            setClickable(true);
            setFocusable(true);
        }

        @Override
        public void setChecked(boolean checked) {
            super.setChecked(checked);
            invalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            float d = getResources().getDisplayMetrics().density, x = getWidth() / 2f, y = getHeight() / 2f;
            boolean on = isChecked();
            track.set(x - 22 * d, y - 12 * d, x + 22 * d, y + 12 * d);
            if (!on) track.inset(d / 2, d / 2);
            paint.setStyle(on ? Paint.Style.FILL : Paint.Style.STROKE);
            paint.setStrokeWidth(d);
            paint.setColor(on ? TEXT : IDLE);
            canvas.drawRoundRect(track, 12 * d, 12 * d, paint);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(on ? BACKGROUND : MUTED);
            canvas.drawCircle(x + (on ? 10 : -10) * d, y, 9 * d, paint);
        }

        @Override
        public void onInitializeAccessibilityNodeInfo(AccessibilityNodeInfo info) {
            super.onInitializeAccessibilityNodeInfo(info);
            info.setClassName(Switch.class.getName());
        }
    }

    private static final class IconView extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Path path = new Path(), fill = new Path();

        IconView(Context context, int kind) {
            this(context, kind, kind == ICON_LEFT ? TEXT : MUTED);
        }

        IconView(Context context, int kind, int color) {
            super(context);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(2);
            paint.setColor(color);
            if (kind == ICON_ARROW_UP || kind == ICON_ARROW_DOWN) {
                // The spec's 12-unit arrow with its 2-unit stroke, in this view's 20-unit space.
                paint.setStrokeWidth(2 * 20 / 12f);
                float tip = kind == ICON_ARROW_UP ? 3.3f : 16.7f, wing = kind == ICON_ARROW_UP ? 9.2f : 10.8f;
                path.moveTo(10, 20 - tip);
                path.lineTo(10, tip);
                path.moveTo(4.2f, wing);
                path.lineTo(10, tip);
                path.lineTo(15.8f, wing);
            } else if (kind == ICON_SQUARE) {
                fill.addRoundRect(new RectF(5, 5, 15, 15), 1.7f, 1.7f, Path.Direction.CW);
            } else if (kind == ICON_HALF) {
                paint.setStrokeWidth(2.5f);
                path.addCircle(10, 10, 6.7f, Path.Direction.CW);
                fill.addArc(new RectF(3.3f, 3.3f, 16.7f, 16.7f), 90, 180);
                fill.close();
            } else if (kind == ICON_INFO) {
                paint.setStrokeWidth(3.3f);
                path.moveTo(10, 9.2f);
                path.lineTo(10, 16.7f);
                fill.addCircle(10, 4.7f, 2, Path.Direction.CW);
            } else if (kind == ICON_SLIDERS) {
                paint.setStrokeWidth(2 * 20 / 12f);
                path.moveTo(3.3f, 6);
                path.lineTo(16.7f, 6);
                path.moveTo(3.3f, 14);
                path.lineTo(16.7f, 14);
                fill.addCircle(7, 6, 3.3f, Path.Direction.CW);
                fill.addCircle(13, 14, 3.3f, Path.Direction.CW);
            } else if (kind == ICON_DOWN) {
                path.moveTo(5, 8);
                path.lineTo(10, 13);
                path.lineTo(15, 8);
            } else if (kind == ICON_RIGHT) {
                path.moveTo(8, 5);
                path.lineTo(13, 10);
                path.lineTo(8, 15);
            } else {
                path.moveTo(12, 4);
                path.lineTo(6, 10);
                path.lineTo(12, 16);
            }
        }

        @Override
        protected void onDraw(Canvas canvas) {
            canvas.save();
            canvas.scale(getWidth() / 20f, getHeight() / 20f);
            canvas.drawPath(path, paint);
            paint.setStyle(Paint.Style.FILL);
            canvas.drawPath(fill, paint);
            paint.setStyle(Paint.Style.STROKE);
            canvas.restore();
        }
    }

    /** Percent slider. With zigzag on, a zigzag line replaces the fill while the finger is down. */
    private static final class StrengthView extends View {
        interface Listener {
            void onChange(int strength);
            void onHold(boolean held);
        }

        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Path zigzag = new Path();
        private final float d = getResources().getDisplayMetrics().density;
        private final String label;
        private final int min, max, step;
        private final boolean zigzagOn;
        private Listener listener;
        private int strength;
        private boolean held, jitterUp, still;
        private final Runnable jitter = new Runnable() {
            @Override public void run() {
                jitterUp = !jitterUp;
                invalidate();
                // Half period: 140ms per cycle at 10%, 80ms at 100%.
                postDelayed(this, (long) (70 - (strength - 10) / 90f * 30));
            }
        };

        StrengthView(Context context, String label, int min, int max, int step, int strength, boolean zigzagOn) {
            super(context);
            this.label = label;
            this.min = min;
            this.max = max;
            this.step = step;
            this.zigzagOn = zigzagOn;
            this.strength = strength;
            setFocusable(true);
            setContentDescription(description());
            paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setStrokeJoin(Paint.Join.MITER);
        }

        void setListener(Listener listener) {
            this.listener = listener;
        }

        private String description() {
            return label + ", " + (strength == 0 ? "off" : strength + "%");
        }

        private void setStrength(int next) {
            next = Math.max(min, Math.min(max, next));
            if (next == strength) return;
            strength = next;
            setContentDescription(description());
            if (listener != null) listener.onChange(strength);
            invalidate();
        }

        private void setHeld(boolean down) {
            if (held == down) return;
            held = down;
            removeCallbacks(jitter);
            if (down) {
                still = android.provider.Settings.Global.getFloat(getContext().getContentResolver(),
                    android.provider.Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f;
                if (!still && zigzagOn) postDelayed(jitter, 70);
            }
            if (listener != null) listener.onHold(down);
            invalidate();
        }

        @Override
        public boolean onTouchEvent(MotionEvent event) {
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    if (getParent() != null) getParent().requestDisallowInterceptTouchEvent(true);
                    setHeld(true);
                    // fall through
                case MotionEvent.ACTION_MOVE:
                    float r = 11 * d, span = Math.max(1, getWidth() - 2 * r);
                    setStrength(min + step * Math.round((event.getX() - r) / span * (max - min) / step));
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    setHeld(false);
                    return true;
                default:
                    return super.onTouchEvent(event);
            }
        }

        @Override
        public boolean onKeyDown(int keyCode, KeyEvent event) {
            if (keyCode == KeyEvent.KEYCODE_DPAD_LEFT || keyCode == KeyEvent.KEYCODE_DPAD_RIGHT) {
                setStrength(strength + (keyCode == KeyEvent.KEYCODE_DPAD_LEFT ? -step : step));
                return true;
            }
            return super.onKeyDown(keyCode, event);
        }

        @Override
        public boolean performAccessibilityAction(int action, Bundle arguments) {
            if (action == AccessibilityNodeInfo.ACTION_SCROLL_FORWARD || action == AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD) {
                setStrength(strength + (action == AccessibilityNodeInfo.ACTION_SCROLL_FORWARD ? step : -step));
                return true;
            }
            return super.performAccessibilityAction(action, arguments);
        }

        @Override
        public void onInitializeAccessibilityNodeInfo(AccessibilityNodeInfo info) {
            super.onInitializeAccessibilityNodeInfo(info);
            info.setClassName("android.widget.SeekBar");
            info.setRangeInfo(AccessibilityNodeInfo.RangeInfo.obtain(AccessibilityNodeInfo.RangeInfo.RANGE_TYPE_INT, min, max, strength));
            info.addAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD);
            info.addAction(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD);
        }

        @Override
        protected void onDetachedFromWindow() {
            removeCallbacks(jitter);
            held = false;
            super.onDetachedFromWindow();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            float r = 11 * d, cy = getHeight() / 2f, start = r, end = getWidth() - r;
            float x = start + (strength - min) / (float) (max - min) * (end - start);
            boolean zigzagging = held && zigzagOn;
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(4 * d);
            paint.setColor(BORDER);
            canvas.drawLine(zigzagging ? x : start, cy, end, cy, paint);
            if (!zigzagging) {
                paint.setColor(TEXT);
                canvas.drawLine(start, cy, x, cy, paint);
            } else {
                float filled = x - start, spacing = 11 * d;
                int n = Math.max(2, Math.round(filled / spacing));
                float h = Math.max(d, 5.4f * d * strength / 100f);
                float shift = still ? 0 : (jitterUp ? -0.6f : 0.6f) * d;
                zigzag.rewind();
                for (int i = 0; i <= n; i++) {
                    float taper = Math.min(1f, Math.min(i, n - i) / 2f);
                    float px = start + filled * i / n, py = cy + shift + (i % 2 == 0 ? h : -h) * taper;
                    if (i == 0) zigzag.moveTo(px, py); else zigzag.lineTo(px, py);
                }
                paint.setStrokeWidth(2 * d);
                paint.setColor(ACTIVE);
                paint.setAlpha((int) (Math.min(1f, strength * 5 / 100f) * 255));
                canvas.drawPath(zigzag, paint);
            }
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(held ? ACTIVE : TEXT);
            canvas.drawCircle(x, cy, r, paint);
            if (isFocused() && !held) {
                paint.setStyle(Paint.Style.STROKE);
                paint.setStrokeWidth(2 * d);
                paint.setColor(BACKGROUND);
                canvas.drawCircle(x, cy, r - 3 * d, paint);
            }
        }
    }

    /** Both sticks at their raw position, with the deadzone drawn as a dashed disc. Shown while the deadzone slider is held. */
    private static final class StickPreview extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint dashed = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint caption = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint line = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final float d = getResources().getDisplayMetrics().density;
        private final boolean connected;
        private float zone, lx, ly, rx, ry;
        private String text = "";
        private int shownPercent = -1;
        private boolean shownIgnored;

        StickPreview(Context context, boolean connected) {
            super(context);
            this.connected = connected;
            GradientDrawable box = new GradientDrawable();
            box.setColor(SURFACE);
            box.setStroke((int) d, BORDER);
            box.setCornerRadius(6 * d);
            setBackground(box);
            dashed.setStyle(Paint.Style.STROKE);
            dashed.setStrokeWidth(1.5f * d);
            dashed.setColor(MUTED);
            dashed.setPathEffect(new DashPathEffect(new float[] {4 * d, 3 * d}, 0));
            float sp = getResources().getDisplayMetrics().scaledDensity;
            caption.setTextSize(12 * sp);
            caption.setColor(MUTED);
            caption.setTextAlign(Paint.Align.CENTER);
            line.setTextSize(12 * sp);
            line.setColor(ACTIVE);
            line.setTypeface(Typeface.MONOSPACE);
            line.setTextAlign(Paint.Align.CENTER);
        }

        void setDeadzone(float zone) {
            this.zone = zone;
            invalidate();
        }

        void setSticks(float lx, float ly, float rx, float ry) {
            this.lx = lx;
            this.ly = ly;
            this.rx = rx;
            this.ry = ry;
            invalidate();
        }

        @Override
        protected void onMeasure(int widthSpec, int heightSpec) {
            float height = 24 * d + 120 * d + 6 * d + caption.getFontSpacing();
            if (connected) height += 10 * d + line.getFontSpacing();
            setMeasuredDimension(MeasureSpec.getSize(widthSpec), (int) Math.ceil(height));
        }

        @Override
        protected void onDraw(Canvas canvas) {
            float inner = getWidth() - 24 * d, top = 12 * d, radius = 60 * d, cy = top + radius;
            float captionY = cy + radius + 6 * d - caption.ascent();
            drawStick(canvas, 12 * d + inner / 4, cy, lx, ly, "Left stick", captionY);
            drawStick(canvas, 12 * d + inner * 3 / 4, cy, rx, ry, "Right stick", captionY);
            if (!connected) return;
            float m = (float) Math.sqrt(lx * lx + ly * ly);
            int percent = Math.round(m * 100);
            boolean ignored = m <= zone;
            // Rebuilt only when the words change, not on every motion event.
            if (percent != shownPercent || ignored != shownIgnored) {
                shownPercent = percent;
                shownIgnored = ignored;
                text = "Left stick at rest: " + percent + "%. " + (ignored ? "Ignored." : "Sent as a small move.");
                setContentDescription(text);
            }
            canvas.drawText(text, getWidth() / 2f, captionY + caption.descent() + 10 * d - line.ascent(), line);
        }

        private void drawStick(Canvas canvas, float cx, float cy, float x, float y, String name, float captionY) {
            // The dot's centre travels 6dp short of the rim so it stays inside the circle.
            float travel = 54 * d, m = (float) Math.sqrt(x * x + y * y);
            if (m > 1) {
                x /= m;
                y /= m;
            }
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(1.5f * d);
            paint.setColor(IDLE);
            canvas.drawCircle(cx, cy, 60 * d - 0.75f * d, paint);
            if (zone > 0) {
                paint.setStyle(Paint.Style.FILL);
                paint.setColor(BORDER);
                canvas.drawCircle(cx, cy, zone * travel, paint);
                canvas.drawCircle(cx, cy, zone * travel, dashed);
            }
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(ACTIVE);
            canvas.drawCircle(cx + x * travel, cy + y * travel, 6 * d, paint);
            canvas.drawText(name, cx, captionY, caption);
        }
    }
}
