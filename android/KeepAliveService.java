package io.github.multex.musubi;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

/** Keeps the process and CPU awake while MainActivity is in the background; the work itself stays there. */
public class KeepAliveService extends Service {
    private static final String ACTION_STOP = "io.github.multex.musubi.STOP";
    static final String EXTRA_PC = "pc";
    static final String EXTRA_SOUND = "sound";
    static final String EXTRA_MIC = "mic";
    static Runnable onStop;
    private static final String CHANNEL = "background";

    private PowerManager.WakeLock wakeLock;

    @Override
    public void onCreate() {
        super.onCreate();
        wakeLock = ((PowerManager) getSystemService(POWER_SERVICE))
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "musubi:background");
        wakeLock.acquire();
    }

    @Override
    @SuppressWarnings("deprecation")
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null || ACTION_STOP.equals(intent.getAction())) {
            stopSession();
            return START_NOT_STICKY;
        }
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).createNotificationChannel(
                new NotificationChannel(CHANNEL, "Running in the background", NotificationManager.IMPORTANCE_LOW));
            builder = new Notification.Builder(this, CHANNEL);
        } else {
            builder = new Notification.Builder(this);
        }
        int pendingFlags = Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0;
        PendingIntent open = PendingIntent.getActivity(this, 0,
            getPackageManager().getLaunchIntentForPackage(getPackageName()), pendingFlags);
        PendingIntent stop = PendingIntent.getService(this, 0,
            new Intent(this, KeepAliveService.class).setAction(ACTION_STOP), pendingFlags);
        // The build makes no R class, so the icon is looked up by name.
        builder.setSmallIcon(getResources().getIdentifier("ic_stat_musubi", "drawable", getPackageName()))
            .setContentTitle("Connected to " + intent.getStringExtra(EXTRA_PC))
            .setContentText(contentText(intent.getBooleanExtra(EXTRA_SOUND, true), intent.getBooleanExtra(EXTRA_MIC, false)))
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(0, "Stop", stop);
        startForeground(1, builder.build());
        return START_NOT_STICKY;
    }

    private static String contentText(boolean sound, boolean mic) {
        if (mic) return (sound ? "Sound and mic keep going." : "Mic keeps going.") + " Open Musubi to use the controller.";
        return sound ? "Sound keeps playing. Open Musubi to use the controller."
            : "PC sound is off. Open Musubi to use the controller.";
    }

    private void stopSession() {
        Runnable action = onStop;
        onStop = null;
        if (action != null) action.run();
        stopSelf();
    }

    /** Swiping Musubi away from recents ends the background session too. */
    @Override
    public void onTaskRemoved(Intent rootIntent) {
        stopSession();
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        wakeLock.release();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
