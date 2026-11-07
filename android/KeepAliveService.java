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
    static final String ACTION_STOP = "io.github.multex.musubi.STOP";
    static final String EXTRA_PC = "pc";
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
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).createNotificationChannel(
                new NotificationChannel(CHANNEL, "Running in the background", NotificationManager.IMPORTANCE_LOW));
            builder = new Notification.Builder(this, CHANNEL);
        } else {
            builder = new Notification.Builder(this);
        }
        PendingIntent open = PendingIntent.getActivity(this, 0,
            getPackageManager().getLaunchIntentForPackage(getPackageName()), 0);
        PendingIntent stop = PendingIntent.getBroadcast(this, 0, stopIntent(), 0);
        builder.setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Connected to " + intent.getStringExtra(EXTRA_PC))
            .setContentText("Sound keeps playing. Open Musubi to use the controller.")
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(0, "Stop", stop);
        startForeground(1, builder.build());
        return START_NOT_STICKY;
    }

    private Intent stopIntent() {
        return new Intent(ACTION_STOP).setPackage(getPackageName());
    }

    /** Swiping Musubi away from recents ends the background session too. */
    @Override
    public void onTaskRemoved(Intent rootIntent) {
        sendBroadcast(stopIntent());
        stopSelf();
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
