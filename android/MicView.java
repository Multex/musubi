package io.github.multex.musubi;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.view.View;

/** The mic drawing: the capsule fills with the voice level, polled at 30 fps only while the mic records. */
final class MicView extends View implements Runnable {
    static final int ON = 0, OFF = 1, MUTED = 2, PAUSED = 3;
    private static final float WIDTH = 200, HEIGHT = 180;
    private static final int FRAME_MS = 33;

    /** The voice peak (0 to 1) since the last call, or -1 once the mic isn't recording. */
    interface Source {
        float take();
    }

    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint line = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path holder = new Path();
    private final RectF rect = new RectF();
    private final Path capsule = new Path();
    private final Source source;
    private int mode = OFF;
    private float level;
    private boolean animating;

    MicView(Context context, Source source) {
        super(context);
        this.source = source;
        line.setStyle(Paint.Style.STROKE);
        line.setStrokeCap(Paint.Cap.ROUND);
        capsule.addRoundRect(new RectF(72, 10, 128, 114), 28, 28, Path.Direction.CW);
        holder.moveTo(56, 70);
        holder.lineTo(56, 80);
        rect.set(56, 36, 144, 124);
        holder.arcTo(rect, 180, -180);
        holder.lineTo(144, 70);
    }

    void setMode(int mode) {
        if (mode == this.mode) return;
        this.mode = mode;
        setAlpha(mode == PAUSED ? 0.4f : 1);
        if (mode != ON) level = 0;
        invalidate();
    }

    /** Starts the level if it isn't running; it stops by itself once the mic stops and the level has fallen. */
    void start() {
        if (animating || mode != ON) return;
        animating = true;
        postDelayed(this, FRAME_MS);
    }

    @Override
    public void run() {
        float peak = mode == ON ? source.take() : -1;
        // Rise at once, fall smoothly, on the same curve as the headphones.
        level = Math.max(peak > 0 ? (float) Math.pow(peak, HeadphonesView.CURVE) : 0, level * HeadphonesView.FALL);
        if (level < 0.01f) level = 0;
        invalidate();
        animating = peak >= 0 || level > 0;
        if (animating) postDelayed(this, FRAME_MS);
    }

    @Override
    protected void onDetachedFromWindow() {
        super.onDetachedFromWindow();
        removeCallbacks(this);
        animating = false;
    }

    private void arcs(Canvas canvas, float radius) {
        rect.set(100 - radius, 65 - radius, 100 + radius, 65 + radius);
        canvas.drawArc(rect, 150, 60, false, line);
        canvas.drawArc(rect, -30, 60, false, line);
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float scale = Math.min(getWidth() / WIDTH, getHeight() / HEIGHT);
        canvas.save();
        canvas.translate((getWidth() - WIDTH * scale) / 2, (getHeight() - HEIGHT * scale) / 2);
        canvas.scale(scale, scale);
        int grey = mode == ON ? MainActivity.IDLE : MainActivity.LATER;
        line.setColor(MainActivity.ACTIVE);
        line.setStrokeWidth(4);
        if (level > 0.15f) arcs(canvas, 62);
        if (level > 0.55f) arcs(canvas, 78);
        line.setColor(grey);
        line.setStrokeWidth(9);
        canvas.drawPath(holder, line);
        canvas.drawLine(100, 124, 100, 154, line);
        canvas.drawLine(74, 156, 126, 156, line);
        fill.setColor(grey);
        canvas.drawPath(capsule, fill);
        if (level > 0) {
            // Clipped to the capsule, so the amber rises inside the mic's round bottom like a liquid, not as its own pill.
            canvas.save();
            canvas.clipPath(capsule);
            fill.setColor(MainActivity.ACTIVE);
            canvas.drawRect(72, 114 - 104 * level, 128, 114, fill);
            canvas.restore();
        }
        if (mode == MUTED) {
            // A dark gap around the slash keeps it readable over the grey mic.
            line.setColor(MainActivity.BACKGROUND);
            line.setStrokeWidth(16);
            canvas.drawLine(40, 20, 160, 160, line);
            line.setColor(MainActivity.MUTED);
            line.setStrokeWidth(7);
            canvas.drawLine(40, 20, 160, 160, line);
        }
        canvas.restore();
    }
}
