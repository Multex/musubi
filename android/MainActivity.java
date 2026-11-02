package padbridge.app;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.SystemClock;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.util.Patterns;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.WindowManager;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.ByteBuffer;
import java.util.List;

public class MainActivity extends Activity {
    private static final int PORT = 9999;
    private static final int HEARTBEAT_MS = 50;
    private static final int STICK_MAX = 32767;
    private static final int TRIGGER_MAX = 255;
    private static final int UI_INTERVAL_MS = 100;

    // The first 11 bits match BUTTONS in padbridge.py; the rest become trigger and hat axes.
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
    private static final int WIRE_BUTTONS = (1 << 11) - 1;
    private static final int L2 = 1 << 11, R2 = 1 << 12;
    private static final int LEFT = 1 << 13, RIGHT = 1 << 14, UP = 1 << 15, DOWN = 1 << 16;

    private final Object lock = new Object();
    private int held;
    private final short[] axes = new short[8];
    private boolean dirty;
    private InetAddress target;

    private volatile boolean running;
    private Thread sender;
    private WifiManager.WifiLock wifiLock;
    private TextView status;
    private TextView input;
    private long lastUiUpdate;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        // Without this, Wi-Fi power saving batches packets and adds tens of milliseconds.
        wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "padbridge");

        int padding = (int) (16 * getResources().getDisplayMetrics().density);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(padding, padding, padding, padding);

        TextView label = new TextView(this);
        label.setText("IP de la PC");
        root.addView(label);

        SharedPreferences prefs = getPreferences(MODE_PRIVATE);
        EditText host = new EditText(this);
        // Numeric input types accept only one dot, which makes an IP impossible to type.
        host.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        host.setContentDescription("IP de la PC");
        host.setSingleLine();
        root.addView(host);

        status = new TextView(this);
        status.setPadding(0, padding, 0, padding);
        root.addView(status);

        input = new TextView(this);
        input.setText("Pulsa un botón del mando para ver lo que llega.");
        root.addView(input);
        setContentView(root);

        host.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void onTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void afterTextChanged(Editable s) {
                setTarget(s.toString().trim(), prefs);
            }
        });
        host.setText(prefs.getString("host", ""));
        setTarget(host.getText().toString(), prefs);
    }

    private void setTarget(String text, SharedPreferences prefs) {
        InetAddress address = null;
        // The pattern guarantees a literal, so getByName never does a DNS lookup here.
        if (Patterns.IP_ADDRESS.matcher(text).matches()) {
            try {
                address = InetAddress.getByName(text);
                prefs.edit().putString("host", text).apply();
            } catch (IOException ignored) {
            }
        }
        synchronized (lock) {
            target = address;
        }
        if (address != null) {
            status.setText("Enviando a " + text + ":" + PORT);
        } else {
            status.setText(text.isEmpty() ? "Escribe la IP de la PC." : "Esa IP no es válida.");
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        wifiLock.acquire();
        running = true;
        sender = new Thread(this::sendLoop, "padbridge-sender");
        sender.start();
    }

    @Override
    protected void onPause() {
        super.onPause();
        running = false;
        sender.interrupt();
        wifiLock.release();
        // The app gets no key-up events while in the background.
        synchronized (lock) {
            held = 0;
            java.util.Arrays.fill(axes, (short) 0);
        }
    }

    private void sendLoop() {
        byte[] data = new byte[20];
        DatagramPacket packet = new DatagramPacket(data, data.length);
        packet.setPort(PORT);
        try (DatagramSocket socket = new DatagramSocket()) {
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
                } catch (IOException ex) {
                    showStatus("No se pudo enviar: " + ex.getMessage());
                    Thread.sleep(500);
                }
            }
        } catch (IOException ex) {
            showStatus("No se pudo abrir la red: " + ex.getMessage());
        } catch (InterruptedException ignored) {
        }
    }

    private void showStatus(String text) {
        runOnUiThread(() -> status.setText(text));
    }

    private void fill(ByteBuffer out) {
        out.putInt(held & WIRE_BUTTONS);
        for (int i = 0; i < 4; i++) {
            out.putShort(axes[i]);
        }
        out.putShort((held & L2) != 0 ? TRIGGER_MAX : axes[4]);
        out.putShort((held & R2) != 0 ? TRIGGER_MAX : axes[5]);
        out.putShort(hat(LEFT, RIGHT, axes[6]));
        out.putShort(hat(UP, DOWN, axes[7]));
    }

    private short hat(int negative, int positive, short axis) {
        if ((held & negative) != 0) return -1;
        if ((held & positive) != 0) return 1;
        return axis;
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (!isGamepad(event.getDevice())) {
            return super.dispatchKeyEvent(event);
        }
        int bit = -1;
        for (int i = 0; i < BUTTON_KEYS.length; i++) {
            if (BUTTON_KEYS[i] == event.getKeyCode()) bit = i;
        }
        if (bit >= 0) {
            synchronized (lock) {
                if (event.getAction() == KeyEvent.ACTION_DOWN) {
                    held |= 1 << bit;
                } else if (event.getAction() == KeyEvent.ACTION_UP) {
                    held &= ~(1 << bit);
                }
                dirty = true;
                lock.notify();
            }
        }
        // Unmapped keys stay visible here so the pad can be calibrated by hand.
        input.setText(KeyEvent.keyCodeToString(event.getKeyCode()) + (bit < 0 ? " (sin asignar)" : ""));
        return true;
    }

    @Override
    public boolean dispatchGenericMotionEvent(MotionEvent event) {
        if (!isGamepad(event.getDevice())) {
            return super.dispatchGenericMotionEvent(event);
        }
        synchronized (lock) {
            axes[0] = stick(event, MotionEvent.AXIS_X);
            axes[1] = stick(event, MotionEvent.AXIS_Y);
            axes[2] = stick(event, MotionEvent.AXIS_Z);
            axes[3] = stick(event, MotionEvent.AXIS_RZ);
            axes[4] = trigger(event, MotionEvent.AXIS_LTRIGGER, MotionEvent.AXIS_BRAKE);
            axes[5] = trigger(event, MotionEvent.AXIS_RTRIGGER, MotionEvent.AXIS_GAS);
            axes[6] = (short) Math.round(event.getAxisValue(MotionEvent.AXIS_HAT_X));
            axes[7] = (short) Math.round(event.getAxisValue(MotionEvent.AXIS_HAT_Y));
            dirty = true;
            lock.notify();
        }
        // setText on every motion event would stall the input thread.
        long now = SystemClock.uptimeMillis();
        if (now - lastUiUpdate >= UI_INTERVAL_MS) {
            lastUiUpdate = now;
            input.setText(describeAxes(event));
        }
        return true;
    }

    private static short stick(MotionEvent event, int axis) {
        return (short) (event.getAxisValue(axis) * STICK_MAX);
    }

    private static short trigger(MotionEvent event, int axis, int alternate) {
        return (short) (Math.max(event.getAxisValue(axis), event.getAxisValue(alternate)) * TRIGGER_MAX);
    }

    private static String describeAxes(MotionEvent event) {
        StringBuilder text = new StringBuilder();
        List<InputDevice.MotionRange> ranges = event.getDevice().getMotionRanges();
        for (InputDevice.MotionRange range : ranges) {
            float value = event.getAxisValue(range.getAxis());
            if (Math.abs(value) > 0.02f) {
                text.append(MotionEvent.axisToString(range.getAxis()))
                    .append(' ').append(String.format("%.2f", value)).append('\n');
            }
        }
        return text.toString();
    }

    // SOURCE_GAMEPAD shares its class bit with keyboards, so test the whole mask.
    private static boolean isGamepad(InputDevice device) {
        if (device == null) return false;
        int sources = device.getSources();
        return (sources & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD
            || (sources & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK;
    }
}
