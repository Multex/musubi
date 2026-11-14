package io.github.multex.musubi;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.bluetooth.BluetoothClass;
import android.bluetooth.BluetoothDevice;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONException;

import java.util.HashMap;

/** Offers to open Musubi when a paired controller turns on while Musubi isn't on screen. */
public class ControllerReceiver extends BroadcastReceiver {
    private static final String CHANNEL = "controllers";
    private static final int ID = 2;
    private static final int MAJOR_MINOR = 0x1F00, PERIPHERAL = 0x0500, KIND = 0x3C, JOYSTICK = 0x04, GAMEPAD = 0x08;

    // shortcut: lost when Android kills the process, so a later pad shows alone instead of "2 controllers"; fine until multi-pad.
    private static final HashMap<String, String> connected = new HashMap<>();

    @Override
    @SuppressWarnings("deprecation")
    public void onReceive(Context context, Intent intent) {
        BluetoothDevice device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
        if (device == null || !isController(device.getBluetoothClass())) return;
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (BluetoothDevice.ACTION_ACL_DISCONNECTED.equals(intent.getAction())) {
            connected.remove(device.getAddress());
            if (connected.isEmpty()) manager.cancel(ID);
            return;
        }
        String name = device.getName();
        connected.put(device.getAddress(), name == null ? "A controller" : name);
        SharedPreferences prefs = context.getSharedPreferences("MainActivity", Context.MODE_PRIVATE);
        if (MainActivity.onScreen || !prefs.getBoolean("ask_open", true)) return;

        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            createChannel(context);
            builder = new Notification.Builder(context, CHANNEL);
        } else {
            builder = new Notification.Builder(context);
        }
        String pc = pcName(prefs);
        PendingIntent open = PendingIntent.getActivity(context, 0,
            context.getPackageManager().getLaunchIntentForPackage(context.getPackageName()),
            Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0);
        builder.setSmallIcon(context.getResources().getIdentifier("ic_stat_musubi", "drawable", context.getPackageName()))
            .setColor(MainActivity.ACTIVE)
            .setContentTitle(connected.size() > 1 ? connected.size() + " controllers are on" : connected.get(device.getAddress()) + " is on")
            .setContentText(pc == null ? "Tap to play." : "Tap to play on " + pc + ".")
            .setContentIntent(open)
            .setAutoCancel(true);
        manager.notify(ID, builder.build());
    }

    /** Also called when Musubi opens: on Android 13+ a new channel is what makes Android ask to allow notifications. */
    static void createChannel(Context context) {
        if (Build.VERSION.SDK_INT < 26) return;
        ((NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE)).createNotificationChannel(
            new NotificationChannel(CHANNEL, "Controllers", NotificationManager.IMPORTANCE_DEFAULT));
    }

    static void cancel(Context context) {
        ((NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(ID);
    }

    private static boolean isController(BluetoothClass type) {
        if (type == null) return false;
        int bits = type.getDeviceClass();
        return (bits & MAJOR_MINOR) == PERIPHERAL && ((bits & KIND) == JOYSTICK || (bits & KIND) == GAMEPAD);
    }

    /** Same storage as MainActivity.savePcs. */
    private static String pcName(SharedPreferences prefs) {
        try {
            JSONArray pcs = new JSONArray(prefs.getString("pcs", "[]"));
            if (pcs.length() == 0) return null;
            return pcs.getJSONObject(Math.max(0, Math.min(prefs.getInt("selectedPc", 0), pcs.length() - 1))).getString("name");
        } catch (JSONException corrupt) {
            return null;
        }
    }
}
