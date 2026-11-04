package padbridge.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.content.res.Configuration;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.hardware.input.InputManager;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.Editable;
import android.text.InputType;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.util.Patterns;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity {
    static final int BACKGROUND = 0xFF0D0C0A, SURFACE = 0xFF171512, BORDER = 0xFF332E28, IDLE = 0xFF6B6256;
    static final int TEXT = 0xFFE6DEC8, MUTED = 0xFF8F8573, ACTIVE = 0xFFFF9E1B, ALERT = 0xFFE04F3E;

    // The first 11 bits match BUTTONS in padbridge.py; the rest become trigger and hat axes.
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

    private static final int PORT = 9999;
    private static final int HEARTBEAT_MS = 50;
    private static final int PING_INTERVAL_MS = 1000;
    private static final int PING_SIZE = 8;
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
    private static final String REPO = "https://github.com/Multex/padbridge";

    private static final int MAIN = 0, GAME = 1, REMAP = 2, REMAP_DONE = 3, SETTINGS = 4, EDIT_PC = 5;
    private static final int CONNECTING = 0, RESPONDING = 1, LOST = 2;
    private static final int PRIMARY = 0, SECONDARY = 1, PLAIN = 2;
    private static final int ICON_DOWN = 0, ICON_RIGHT = 1, ICON_LEFT = 2;

    private static final class Pc {
        String name;
        String ip;

        Pc(String name, String ip) {
            this.name = name;
            this.ip = ip;
        }
    }

    private final Object lock = new Object();
    private int held;
    private final short[] axes = new short[6];
    private boolean dirty;
    private InetAddress target;

    private volatile boolean running;
    private volatile long targetSince;
    private volatile long lastPongAt;
    private volatile long rttMs;
    private volatile String networkError;
    private Thread sender;
    private WifiManager.WifiLock wifiLock;
    private InputManager inputManager;
    private SharedPreferences prefs;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private final List<Pc> pcs = new ArrayList<>();
    private int selectedPc;
    private boolean autoGameMode;

    private int deviceId = -1;
    private String deviceName;
    private String descriptor;
    private int stickXAxis = MotionEvent.AXIS_Z, stickYAxis = MotionEvent.AXIS_RZ;
    private ButtonMap map = defaultMap();
    private int axisButtons;
    private String unmapped;

    private int screen = MAIN;
    private int orientation;
    private int shownPcState = -1;
    private long lastTouch;
    private long lastUiUpdate;
    private String draftName = "", draftIp = "", formError;
    private int editIndex = -1;

    private PadView padView;
    private TextView readout, pcDetail;

    private int step;
    private ButtonMap pending;
    private final List<ButtonMap> history = new ArrayList<>();
    private final HashMap<Integer, Integer> claimed = new HashMap<>();
    private final List<Integer> capture = new ArrayList<>();
    private boolean[] stepMapped;
    private long ignoreInputUntil;
    private String stepNote;

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
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        // Without this, Wi-Fi power saving batches packets and adds tens of milliseconds.
        wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "padbridge");
        inputManager = (InputManager) getSystemService(Context.INPUT_SERVICE);
        prefs = getPreferences(MODE_PRIVATE);
        orientation = getResources().getConfiguration().orientation;
        autoGameMode = prefs.getBoolean("autoGameMode", true);
        loadPcs();
        applySelectedPc();
        findController();
        render();
    }

    @Override
    protected void onResume() {
        super.onResume();
        wifiLock.acquire();
        running = true;
        sender = new Thread(this::sendLoop, "padbridge-sender");
        sender.start();
        lastTouch = SystemClock.uptimeMillis();
        targetSince = lastTouch;
        inputManager.registerInputDeviceListener(deviceListener, handler);
        updateController();
        handler.post(tick);
    }

    @Override
    protected void onPause() {
        super.onPause();
        running = false;
        sender.interrupt();
        wifiLock.release();
        handler.removeCallbacks(tick);
        handler.removeCallbacks(finishCapture);
        capture.clear();
        inputManager.unregisterInputDeviceListener(deviceListener);
        // The app gets no key-up events while in the background.
        axisButtons = 0;
        clearInput();
    }

    @Override
    public void onConfigurationChanged(Configuration config) {
        super.onConfigurationChanged(config);
        // Pairing a pad also lands here, and rebuilding then would drop what's being typed.
        if (config.orientation != orientation) {
            orientation = config.orientation;
            render();
        }
    }

    @Override
    public void onBackPressed() {
        if (screen == MAIN) {
            super.onBackPressed();
        } else if (screen == EDIT_PC) {
            show(SETTINGS);
        } else {
            if (screen == REMAP_DONE) loadMap();
            show(MAIN);
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
        long nextPing = 0;
        try (DatagramSocket socket = new DatagramSocket()) {
            new Thread(() -> pongLoop(socket), "padbridge-pong").start();
            while (running) {
                InetAddress address;
                synchronized (lock) {
                    if (!dirty) {
                        lock.wait(HEARTBEAT_MS);
                    }
                    dirty = false;
                    fill(ByteBuffer.wrap(data));
                    address = target;
                }
                if (address == null) {
                    continue;
                }
                packet.setAddress(address);
                try {
                    socket.send(packet);
                    long now = SystemClock.uptimeMillis();
                    if (now >= nextPing) {
                        nextPing = now + PING_INTERVAL_MS;
                        ByteBuffer.wrap(ping).putLong(System.nanoTime());
                        pingPacket.setAddress(address);
                        socket.send(pingPacket);
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

    private void pongLoop(DatagramSocket socket) {
        byte[] data = new byte[PING_SIZE];
        DatagramPacket packet = new DatagramPacket(data, data.length);
        try {
            while (true) {
                packet.setLength(data.length);
                socket.receive(packet);
                InetAddress expected;
                synchronized (lock) {
                    expected = target;
                }
                if (packet.getLength() == PING_SIZE && packet.getAddress().equals(expected)) {
                    rttMs = (System.nanoTime() - ByteBuffer.wrap(data).getLong()) / 1000000;
                    lastPongAt = SystemClock.uptimeMillis();
                }
            }
        } catch (IOException closedWithTheSender) {
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
        if (deviceId != before && (screen == MAIN || screen == SETTINGS)) {
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
    private static boolean isGamepad(InputDevice device) {
        if (device == null) return false;
        int sources = device.getSources();
        return (sources & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD
            || (sources & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK;
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
        synchronized (lock) {
            axes[0] = stick(event, MotionEvent.AXIS_X);
            axes[1] = stick(event, MotionEvent.AXIS_Y);
            axes[2] = stick(event, stickXAxis);
            axes[3] = stick(event, stickYAxis);
            axes[4] = (short) (l2 * TRIGGER_MAX);
            axes[5] = (short) (r2 * TRIGGER_MAX);
            dirty = true;
            lock.notify();
        }
        pushPadState();
        return true;
    }

    private static short stick(MotionEvent event, int axis) {
        return (short) (event.getAxisValue(axis) * STICK_MAX);
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

    private Pc currentPc() {
        return pcs.isEmpty() ? null : pcs.get(selectedPc);
    }

    private void applySelectedPc() {
        InetAddress address = null;
        Pc pc = currentPc();
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
        if (state != shownPcState) {
            shownPcState = state;
            if ((screen == MAIN && currentPc() != null) || screen == GAME) render();
        }
        if (pcDetail != null) pcDetail.setText(pcDetailText(state));
        updateReadout(true);
        if (screen == MAIN && autoGameMode && deviceId != -1 && currentPc() != null
                && SystemClock.uptimeMillis() - lastTouch >= AUTO_GAME_MODE_MS) {
            show(GAME);
        }
    }

    private String pcDetailText(int state) {
        Pc pc = currentPc();
        if (pc == null) return "";
        return pc.ip + " · " + (state == RESPONDING ? rttMs + " ms" : "Connecting");
    }

    private void show(int next) {
        screen = next;
        render();
    }

    private void render() {
        padView = null;
        readout = null;
        pcDetail = null;
        if (screen != EDIT_PC && screen != MAIN) formError = null;
        applyGameWindow(screen == GAME);
        shownPcState = pcState();
        switch (screen) {
            case GAME: setContentView(buildGame()); break;
            case REMAP: setContentView(buildRemap()); break;
            case REMAP_DONE: setContentView(buildRemapDone()); break;
            case SETTINGS: setContentView(buildSettings()); break;
            case EDIT_PC: setContentView(buildEditPc()); break;
            default: setContentView(buildMain()); break;
        }
        pushPadState();
        updateReadout(true);
    }

    private void applyGameWindow(boolean game) {
        WindowManager.LayoutParams attributes = getWindow().getAttributes();
        attributes.screenBrightness = game ? 0.01f : WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE;
        getWindow().setAttributes(attributes);
        getWindow().getDecorView().setSystemUiVisibility(game
            ? View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            : 0);
    }

    private boolean landscape() {
        return orientation == Configuration.ORIENTATION_LANDSCAPE;
    }

    /** Controller drawing beside a side panel, or between its parts in portrait. Middle may be null. */
    private View withPad(int control, View top, View middle, View bottom) {
        padView = new PadView(this);
        padView.setContentDescription("Controller");
        padView.setTarget(control);
        int padding = dp(8);
        padView.setPadding(padding, padding, padding, padding);
        if (landscape()) {
            LinearLayout root = column();
            root.setOrientation(LinearLayout.HORIZONTAL);
            root.addView(padView, weighted(0, ViewGroup.LayoutParams.MATCH_PARENT));
            View divider = new View(this);
            divider.setBackgroundColor(BORDER);
            root.addView(divider, new LinearLayout.LayoutParams(dp(1), ViewGroup.LayoutParams.MATCH_PARENT));
            LinearLayout panel = column();
            panel.setPadding(dp(16), dp(16), dp(16), dp(16));
            panel.addView(top);
            panel.addView(middle != null ? middle : new View(this), weighted(ViewGroup.LayoutParams.MATCH_PARENT, 0));
            panel.addView(bottom);
            root.addView(scrolling(panel), new LinearLayout.LayoutParams(dp(250), ViewGroup.LayoutParams.MATCH_PARENT));
            return root;
        }
        LinearLayout root = column();
        root.setPadding(dp(16), dp(16), dp(16), dp(16));
        root.addView(top);
        root.addView(padView, weighted(ViewGroup.LayoutParams.MATCH_PARENT, 0));
        if (middle != null) root.addView(middle);
        root.addView(bottom);
        return root;
    }

    private View buildMain() {
        LinearLayout top = column();
        top.addView(controllerBlock());
        LinearLayout actions = column();
        View middle = null;
        Pc pc = currentPc();
        if (pc == null) {
            top.addView(firstRunForm());
            actions.addView(button("Save PC", PRIMARY, this::saveFirstPc), buttonParams(48));
        } else if (shownPcState == LOST) {
            top.addView(lostBlock(pc));
            TextView message = text(lostMessage(pc), 13, TEXT);
            message.setLineSpacing(0, 1.3f);
            message.setPadding(0, dp(8), 0, dp(8));
            middle = message;
            actions.addView(button("Switch PC", PRIMARY, this::switchPc), buttonParams(48));
            actions.addView(spaced(button("Settings", SECONDARY, () -> show(SETTINGS)), 44));
        } else {
            top.addView(pcRow(pc));
            readout = text("", 12, MUTED);
            readout.setTypeface(Typeface.MONOSPACE);
            readout.setGravity(Gravity.CENTER);
            readout.setPadding(0, dp(8), 0, dp(8));
            middle = readout;
            actions.addView(button("Game mode", PRIMARY, () -> show(GAME)), buttonParams(48));
            Button remap = button("Remap", SECONDARY, this::startRemap);
            remap.setEnabled(deviceId != -1);
            remap.setAlpha(deviceId != -1 ? 1 : 0.5f);
            actions.addView(pair(remap, button("Settings", SECONDARY, () -> show(SETTINGS))));
        }
        return withPad(-1, top, middle, actions);
    }

    private String lostMessage(Pc pc) {
        String error = networkError;
        return error != null
            ? "The phone can't send on this network: " + error
            : "Check that padbridge.py is running on " + pc.ip + " and that the firewall lets the phone through.";
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

    private View pcRow(Pc pc) {
        LinearLayout row = clickableRow(this::switchPc, "Switch PC");
        LinearLayout labels = column();
        labels.addView(text("PC", 12, MUTED));
        TextView name = text(pc.name, 16, TEXT);
        name.setTypeface(medium());
        labels.addView(name);
        pcDetail = text(pcDetailText(shownPcState), 12, MUTED);
        pcDetail.setTypeface(Typeface.MONOSPACE);
        labels.addView(pcDetail);
        row.addView(labels, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        row.addView(new IconView(this, ICON_DOWN), new LinearLayout.LayoutParams(dp(20), dp(20)));
        return divided(row);
    }

    private View lostBlock(Pc pc) {
        LinearLayout block = column();
        block.addView(text("PC", 12, MUTED));
        TextView name = text(pc.name, 16, TEXT);
        name.setTypeface(medium());
        block.addView(name);
        TextView status = text("PC not responding", 14, ALERT);
        status.setTypeface(Typeface.DEFAULT_BOLD);
        block.addView(status);
        return divided(block);
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

    private void switchPc() {
        if (pcs.size() < 2) {
            show(SETTINGS);
            return;
        }
        CharSequence[] names = new CharSequence[pcs.size()];
        for (int i = 0; i < names.length; i++) {
            names[i] = pcs.get(i).name + " (" + pcs.get(i).ip + ")";
        }
        new AlertDialog.Builder(this)
            .setTitle("Switch PC")
            .setSingleChoiceItems(names, selectedPc, (dialog, which) -> {
                selectPc(which);
                dialog.dismiss();
                render();
            })
            .setPositiveButton("Manage PCs", (dialog, which) -> show(SETTINGS))
            .show();
    }

    private View buildGame() {
        LinearLayout root = column();
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(0xFF000000);
        root.setClickable(true);
        root.setContentDescription("Game mode. Tap to go back");
        root.setOnClickListener(view -> show(MAIN));
        Pc pc = currentPc();
        boolean lost = shownPcState == LOST;
        String status = lost ? "PC not responding"
            : (deviceName != null ? deviceName : "No controller") + " to " + (pc != null ? pc.name : "no PC");
        TextView line = text(status, 16, lost ? ALERT : MUTED);
        line.setGravity(Gravity.CENTER);
        root.addView(line);
        TextView back = text("Tap to go back", 13, MUTED);
        back.setPadding(0, dp(8), 0, 0);
        root.addView(back);
        return root;
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
        actions.addView(spaced(button("Cancel remap", PLAIN, () -> show(MAIN)), 44));
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
            show(MAIN);
        }), buttonParams(48));
        actions.addView(pair(
            button("Start over", SECONDARY, () -> {
                loadMap();
                startRemap();
            }),
            button("Reset", SECONDARY, () -> {
                resetLayout();
                show(MAIN);
            })));
        return withPad(-1, top, null, actions);
    }

    private void resetLayout() {
        prefs.edit().remove(mapKey()).apply();
        loadMap();
    }

    private View buildSettings() {
        LinearLayout pcSection = column();
        pcSection.addView(sectionTitle("PCs", false));
        for (int i = 0; i < pcs.size(); i++) {
            pcSection.addView(pcSettingsRow(i));
        }
        pcSection.addView(spaced(button("Add PC", SECONDARY, () -> editPc(-1)), 44));

        pcSection.addView(sectionTitle("Button layout", true));
        LinearLayout layoutRow = row();
        layoutRow.setMinimumHeight(dp(52));
        LinearLayout layoutText = column();
        TextView layoutName = text(hasCustomMap() ? "Custom" : "Default", 15, TEXT);
        layoutName.setTypeface(medium());
        layoutText.addView(layoutName);
        layoutText.addView(text(deviceName != null ? "For " + deviceName : "Connect a controller to change it", 12, MUTED));
        layoutRow.addView(layoutText, weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        Button reset = button("Reset", SECONDARY, () -> {
            resetLayout();
            render();
        });
        reset.setEnabled(hasCustomMap());
        reset.setAlpha(hasCustomMap() ? 1 : 0.5f);
        layoutRow.addView(reset, new LinearLayout.LayoutParams(dp(84), dp(44)));
        pcSection.addView(layoutRow);

        LinearLayout otherSection = column();
        otherSection.addView(sectionTitle("Game mode", false));
        Switch auto = new Switch(this);
        auto.setChecked(autoGameMode);
        auto.setText("Turn on by itself after 1 minute without touching the screen");
        auto.setTextColor(TEXT);
        auto.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        auto.setMinimumHeight(dp(52));
        auto.setOnCheckedChangeListener((view, checked) -> {
            autoGameMode = checked;
            prefs.edit().putBoolean("autoGameMode", checked).apply();
        });
        otherSection.addView(auto);
        otherSection.addView(sectionTitle("About", true));
        otherSection.addView(link("PC setup guide", REPO + "#setup"));
        otherSection.addView(link("Source code", REPO));
        TextView version = text("padbridge " + versionName() + " · MIT license", 12, MUTED);
        version.setTypeface(Typeface.MONOSPACE);
        version.setPadding(0, dp(8), 0, 0);
        otherSection.addView(version);

        LinearLayout body = column();
        if (landscape()) {
            body.setOrientation(LinearLayout.HORIZONTAL);
            body.addView(padded(pcSection), weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
            body.addView(padded(otherSection), weighted(0, ViewGroup.LayoutParams.WRAP_CONTENT));
        } else {
            body.addView(padded(pcSection));
            body.addView(padded(otherSection));
        }
        return withHeader("Settings", () -> show(MAIN), body);
    }

    private View pcSettingsRow(int index) {
        Pc pc = pcs.get(index);
        LinearLayout row = clickableRow(() -> editPc(index), "Edit " + pc.name);
        LinearLayout labels = column();
        TextView name = text(pc.name, 15, TEXT);
        name.setTypeface(medium());
        labels.addView(name);
        TextView detail = text(pc.ip + (index == selectedPc ? " · In use" : ""), 12, MUTED);
        detail.setTypeface(Typeface.MONOSPACE);
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
        return withHeader(editIndex >= 0 ? "Edit PC" : "Add PC", () -> show(SETTINGS), padded(form));
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
                show(pcs.isEmpty() ? MAIN : SETTINGS);
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
        scroll.addView(content);
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

    private View sectionTitle(String title, boolean separated) {
        TextView view = text(title, 12, MUTED);
        view.setPadding(0, separated ? dp(20) : 0, 0, dp(4));
        return view;
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
        button.setBackground(ripple(kind == PLAIN ? new ColorDrawable(0) : shape));
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

    private View withHeader(String title, Runnable onBack, View body) {
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

    private static final class IconView extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Path path = new Path();

        IconView(Context context, int kind) {
            super(context);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(2);
            paint.setColor(kind == ICON_LEFT ? TEXT : MUTED);
            if (kind == ICON_DOWN) {
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
            canvas.restore();
        }
    }
}
