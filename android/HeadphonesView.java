package io.github.multex.musubi;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.view.View;

/** The headphones drawing. Levels animate at 30 fps only while sound arrives, and nothing is allocated per frame. */
final class HeadphonesView extends View implements Runnable {
    static final int ON = 0, OFF = 1, NONE = 2;
    private static final float WIDTH = 300, HEIGHT = 266, FALL = 0.85f;
    private static final int FRAME_MS = 33;
    // Below 1, quiet sound shows taller while full scale still maps to 1: 0.1 shows as 0.45, 0.2 as 0.57, 0.5 as 0.78.
    private static final double CURVE = 0.35;

    /** Fills out with the left and right peaks (0 to 1) since the last call; false once the sound has stopped. */
    interface Source {
        boolean take(float[] out);
    }

    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint wave = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path band = new Path(), wavePath = new Path();
    private final RectF rect = new RectF();
    private final float[] next = new float[2];
    private final Source source;
    private int mode = ON;
    private float left, right;
    private boolean animating;

    HeadphonesView(Context context, Source source) {
        super(context);
        this.source = source;
        wave.setStyle(Paint.Style.STROKE);
        wave.setStrokeWidth(3);
        wave.setStrokeCap(Paint.Cap.ROUND);
        wave.setStrokeJoin(Paint.Join.ROUND);
        wave.setColor(MainActivity.ACTIVE);
        text.setTextAlign(Paint.Align.CENTER);
        text.setFakeBoldText(true);
        text.setTextSize(28);

        band.moveTo(32, 156);
        band.lineTo(32, 140);
        rect.set(32, 22, 268, 258);
        band.arcTo(rect, 180, 180);
        band.lineTo(268, 156);
        band.lineTo(246, 156);
        band.lineTo(246, 140);
        rect.set(54, 44, 246, 236);
        band.arcTo(rect, 0, -180);
        band.lineTo(54, 156);
        band.close();
    }

    void setMode(int mode) {
        this.mode = mode;
        setAlpha(mode == NONE ? 0.4f : 1);
        if (mode != ON) left = right = 0;
        invalidate();
    }

    /** Starts the levels if they aren't running; they stop by themselves once the sound has faded out. */
    void start() {
        if (animating || mode != ON) return;
        animating = true;
        postDelayed(this, FRAME_MS);
    }

    @Override
    public void run() {
        boolean live = mode == ON && source.take(next);
        // Rise at once, fall smoothly.
        left = Math.max(live ? (float) Math.pow(next[0], CURVE) : 0, left * FALL);
        right = Math.max(live ? (float) Math.pow(next[1], CURVE) : 0, right * FALL);
        if (left < 0.01f) left = 0;
        if (right < 0.01f) right = 0;
        invalidate();
        animating = live || left > 0 || right > 0;
        if (animating) postDelayed(this, FRAME_MS);
    }

    @Override
    protected void onDetachedFromWindow() {
        super.onDetachedFromWindow();
        removeCallbacks(this);
        animating = false;
    }

    private void side(Canvas canvas, float shellX, float padX, float level, String label, int shell, int pad) {
        rect.set(shellX, 146, shellX + 38, 238);
        fill.setColor(shell);
        canvas.drawRoundRect(rect, 15, 15, fill);
        if (level > 0) {
            float h = 92 * level, r = Math.min(15, h / 2);
            rect.set(shellX, 238 - h, shellX + 38, 238);
            fill.setColor(MainActivity.ACTIVE);
            canvas.drawRoundRect(rect, r, r, fill);
        }
        rect.set(padX, 132, padX + 26, 252);
        fill.setColor(pad);
        canvas.drawRoundRect(rect, 12, 12, fill);
        text.setColor(level > 0 ? MainActivity.TEXT : MainActivity.MUTED);
        canvas.drawText(label, shellX + 19, 254 - (text.ascent() + text.descent()) / 2, text);
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float scale = Math.min(getWidth() / WIDTH, getHeight() / HEIGHT);
        canvas.save();
        canvas.translate((getWidth() - WIDTH * scale) / 2, (getHeight() - HEIGHT * scale) / 2);
        canvas.scale(scale, scale);
        int shell = mode == OFF ? MainActivity.LATER : MainActivity.IDLE;
        int pad = mode == OFF ? MainActivity.THUMB : MainActivity.LATER;
        fill.setColor(shell);
        canvas.drawPath(band, fill);
        side(canvas, 22, 56, left, "L", shell, pad);
        side(canvas, 240, 218, right, "R", shell, pad);
        if (left > 0 || right > 0) {
            wavePath.rewind();
            for (int x = 96; x <= 204; x += 2) {
                float t = (x - 96) / 108f;
                float amp = 20 * (left + (right - left) * t) * (float) Math.sin(Math.PI * t);
                float y = 192 - amp * (float) Math.sin(2 * Math.PI * (x - 96) / 18);
                if (x == 96) wavePath.moveTo(x, y);
                else wavePath.lineTo(x, y);
            }
            canvas.drawPath(wavePath, wave);
        }
        canvas.restore();
    }
}
