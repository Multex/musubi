package io.github.multex.musubi;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.View;

/** The controller drawing. Input arrives on the UI thread, so onDraw stays cheap and allocates nothing. */
final class PadView extends View {
    // The drawing is 560 wide; the view box is 20 wider on each side so the rumble waves fit.
    private static final float PAD = 560, WIDTH = 600, HEIGHT = 348, STICK_TRAVEL = 14;
    private static final long FADE_IN_MS = 120, FADE_OUT_MS = 400, TICK_MS = 40;
    // Inner and outer wave axes of the left grip: x0, y0, x1, y1, then the buzz shift.
    private static final float[][] AXES = {{172, 280, 130, 328, 1.1f, 1.0f}, {17.5f, 201, 1.5f, 281, 1.4f, 0.3f}};

    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint wave = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path spikes = new Path();
    private final RectF rect = new RectF();
    private final Path body = new Path(), leftBumper = new Path(), rightBumper = new Path(), dpad = new Path();

    private int held;
    private int target = -1;
    private float lx, ly, rx, ry, l2, r2;
    // Rumble per grip, 0 = left (strong motor), 1 = right (weak motor): last non-zero level, fade state.
    private final float[] level = new float[2], fadeFrom = new float[2];
    private final long[] fadeAt = new long[2];
    private final boolean[] motorOn = new boolean[2];
    private boolean buzz = true;

    PadView(Context context) {
        super(context);
        stroke.setStyle(Paint.Style.STROKE);
        stroke.setStrokeWidth(2);
        stroke.setStrokeJoin(Paint.Join.ROUND);
        text.setTextAlign(Paint.Align.CENTER);
        wave.setStyle(Paint.Style.STROKE);
        wave.setStrokeWidth(3);
        wave.setStrokeJoin(Paint.Join.MITER);
        wave.setStrokeCap(Paint.Cap.ROUND);
        wave.setColor(MainActivity.ACTIVE);

        body.moveTo(280, 62);
        body.cubicTo(320, 62, 360, 58, 400, 56);
        body.cubicTo(440, 54, 478, 70, 492, 104);
        body.cubicTo(512, 152, 532, 222, 540, 272);
        body.cubicTo(546, 312, 528, 340, 498, 340);
        body.cubicTo(470, 340, 452, 320, 436, 296);
        body.cubicTo(420, 272, 402, 256, 376, 256);
        body.lineTo(184, 256);
        body.cubicTo(158, 256, 140, 272, 124, 296);
        body.cubicTo(108, 320, 90, 340, 62, 340);
        body.cubicTo(32, 340, 14, 312, 20, 272);
        body.cubicTo(28, 222, 48, 152, 68, 104);
        body.cubicTo(82, 70, 120, 54, 160, 56);
        body.cubicTo(200, 58, 240, 62, 280, 62);
        body.close();

        bumper(leftBumper, 0, 1);
        bumper(rightBumper, PAD, -1);

        dpad.moveTo(199, 172);
        dpad.rLineTo(26, 0);
        dpad.rLineTo(0, 23);
        dpad.rLineTo(23, 0);
        dpad.rLineTo(0, 26);
        dpad.rLineTo(-23, 0);
        dpad.rLineTo(0, 23);
        dpad.rLineTo(-26, 0);
        dpad.rLineTo(0, -23);
        dpad.rLineTo(-23, 0);
        dpad.rLineTo(0, -26);
        dpad.rLineTo(23, 0);
        dpad.close();
    }

    private static void bumper(Path path, float origin, float side) {
        path.moveTo(origin + side * 70, 100);
        path.cubicTo(origin + side * 80, 62, origin + side * 118, 36, origin + side * 170, 36);
        path.cubicTo(origin + side * 190, 36, origin + side * 208, 38, origin + side * 222, 42);
        path.lineTo(origin + side * 222, 70);
        path.close();
    }

    void setTarget(int control) {
        target = control;
        invalidate();
    }

    /** Motor levels 0..1 straight from the rumble event; 0 fades the waves out. */
    void setRumble(float strong, float weak) {
        long now = SystemClock.uptimeMillis();
        buzz = Settings.Global.getFloat(getContext().getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1) != 0;
        for (int side = 0; side < 2; side++) {
            float next = side == 0 ? strong : weak;
            boolean wasOn = motorOn[side];
            if (next > 0) level[side] = next;
            if ((next > 0) == wasOn) continue;
            fadeFrom[side] = fade(side, now);
            fadeAt[side] = now;
            motorOn[side] = next > 0;
        }
        invalidate();
    }

    private float fade(int side, long now) {
        float t = Math.min(1f, (now - fadeAt[side]) / (float) (motorOn[side] ? FADE_IN_MS : FADE_OUT_MS));
        if (motorOn[side]) return fadeFrom[side] + (1 - fadeFrom[side]) * (1 - (1 - t) * (1 - t));
        return fadeFrom[side] * (1 - t * t);
    }

    private void waves(Canvas canvas, int side, long now) {
        float alpha = fade(side, now) * Math.min(1f, level[side] * 5);
        if (alpha <= 0) return;
        float amp = 9 * level[side];
        float phase = !buzz ? 0 : (now / (side == 0 ? 70 : 40)) % 2 == 0 ? 1 : -1;
        wave.setAlpha((int) (alpha * 255));
        for (float[] a : AXES) {
            float dx = a[2] - a[0], dy = a[3] - a[1], len = (float) Math.hypot(dx, dy);
            float nx = dy / len, ny = -dx / len;
            spikes.rewind();
            for (int i = 0; i < 10; i++) {
                float off = i == 0 || i == 9 ? 0 : (i % 2 == 1 ? amp : -amp);
                float x = a[0] + dx * i / 9 + nx * off + a[4] * phase;
                float y = a[1] + dy * i / 9 + ny * off + a[5] * phase;
                if (i == 0) spikes.moveTo(side == 0 ? x : PAD - x, y);
                else spikes.lineTo(side == 0 ? x : PAD - x, y);
            }
            canvas.drawPath(spikes, wave);
        }
    }

    void setState(int held, float lx, float ly, float rx, float ry, float l2, float r2) {
        if (held == this.held && lx == this.lx && ly == this.ly && rx == this.rx && ry == this.ry
                && l2 == this.l2 && r2 == this.r2) {
            return;
        }
        this.held = held;
        this.lx = lx;
        this.ly = ly;
        this.rx = rx;
        this.ry = ry;
        this.l2 = l2;
        this.r2 = r2;
        invalidate();
    }

    private boolean on(int control) {
        return (held & 1 << control) != 0;
    }

    private int line(int control) {
        return on(control) ? MainActivity.ACTIVE : control == target ? MainActivity.TEXT : MainActivity.IDLE;
    }

    private int ground(int control, int idle) {
        return on(control) ? MainActivity.ACTIVE : control == target ? MainActivity.TEXT : idle;
    }

    private int ink(int control) {
        return on(control) || control == target ? MainActivity.BACKGROUND : MainActivity.TEXT;
    }

    private void label(Canvas canvas, String label, float x, float y, float size, int color, boolean bold) {
        text.setColor(color);
        text.setTextSize(size);
        text.setFakeBoldText(bold);
        canvas.drawText(label, x, y - (text.ascent() + text.descent()) / 2, text);
    }

    private void shape(Canvas canvas, Path path, int ground, int outline) {
        fill.setColor(ground);
        canvas.drawPath(path, fill);
        stroke.setColor(outline);
        canvas.drawPath(path, stroke);
    }

    private void circle(Canvas canvas, float x, float y, float radius, int ground, int outline) {
        fill.setColor(ground);
        canvas.drawCircle(x, y, radius, fill);
        stroke.setColor(outline);
        canvas.drawCircle(x, y, radius, stroke);
    }

    private void pill(Canvas canvas, float x, float y, float w, float h, float radius, int ground, int outline) {
        rect.set(x, y, x + w, y + h);
        fill.setColor(ground);
        canvas.drawRoundRect(rect, radius, radius, fill);
        stroke.setColor(outline);
        canvas.drawRoundRect(rect, radius, radius, stroke);
    }

    private void trigger(Canvas canvas, int control, float x, float amount, float labelX, String name) {
        label(canvas, name, labelX, 17, 12, MainActivity.MUTED, false);
        int outline = amount > 0 ? MainActivity.ACTIVE : control == target ? MainActivity.TEXT : MainActivity.IDLE;
        pill(canvas, x, 4, 84, 26, 8, MainActivity.SURFACE, outline);
        if (amount > 0) {
            rect.set(x + 4, 8, x + 4 + 76 * amount, 26);
            fill.setColor(MainActivity.ACTIVE);
            canvas.drawRoundRect(rect, 5, 5, fill);
        }
    }

    private void stick(Canvas canvas, int control, float x, float y, float dx, float dy) {
        boolean moved = dx != 0 || dy != 0;
        boolean active = moved || on(control);
        int outline = active ? MainActivity.ACTIVE : control == target ? MainActivity.TEXT : MainActivity.IDLE;
        circle(canvas, x, y, 36, MainActivity.BACKGROUND, outline);
        if (on(control)) {
            stroke.setStrokeWidth(6);
            stroke.setColor(MainActivity.ACTIVE);
            canvas.drawCircle(x, y, 36, stroke);
            stroke.setStrokeWidth(2);
        }
        int thumb = active ? MainActivity.ACTIVE : control == target ? MainActivity.TEXT : MainActivity.THUMB;
        circle(canvas, x + dx * STICK_TRAVEL, y + dy * STICK_TRAVEL, 20, thumb, outline);
    }

    private void face(Canvas canvas, int control, float x, float y, String name) {
        circle(canvas, x, y, 18, ground(control, MainActivity.BACKGROUND), line(control));
        label(canvas, name, x, y, 14, ink(control), true);
    }

    private void direction(Canvas canvas, int control, float x, float y) {
        int color = on(control) ? MainActivity.ACTIVE : control == target ? MainActivity.TEXT : 0;
        if (color == 0) return;
        rect.set(x, y, x + 24, y + 24);
        fill.setColor(color);
        canvas.drawRect(rect, fill);
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float width = getWidth() - getPaddingLeft() - getPaddingRight();
        float height = getHeight() - getPaddingTop() - getPaddingBottom();
        float scale = Math.min(width / WIDTH, height / HEIGHT);
        canvas.save();
        canvas.translate(getPaddingLeft() + (width - WIDTH * scale) / 2, getPaddingTop() + (height - HEIGHT * scale) / 2);
        canvas.scale(scale, scale);
        canvas.translate(20, 0);

        trigger(canvas, MainActivity.L2_BIT, 112, l2, 96, "L2");
        trigger(canvas, MainActivity.R2_BIT, 364, r2, 464, "R2");
        shape(canvas, leftBumper, ground(MainActivity.L1_BIT, MainActivity.SURFACE), line(MainActivity.L1_BIT));
        shape(canvas, rightBumper, ground(MainActivity.R1_BIT, MainActivity.SURFACE), line(MainActivity.R1_BIT));
        shape(canvas, body, MainActivity.SURFACE, MainActivity.IDLE);
        label(canvas, "L1", 150, 47, 11, ink(MainActivity.L1_BIT), true);
        label(canvas, "R1", 410, 47, 11, ink(MainActivity.R1_BIT), true);

        stick(canvas, MainActivity.L3_BIT, 150, 128, lx, ly);
        stick(canvas, MainActivity.R3_BIT, 348, 208, rx, ry);

        shape(canvas, dpad, MainActivity.BACKGROUND, MainActivity.IDLE);
        direction(canvas, MainActivity.UP_BIT, 200, 173);
        direction(canvas, MainActivity.DOWN_BIT, 200, 219);
        direction(canvas, MainActivity.LEFT_BIT, 177, 196);
        direction(canvas, MainActivity.RIGHT_BIT, 223, 196);

        pill(canvas, 216, 120, 30, 16, 8, ground(MainActivity.SELECT_BIT, MainActivity.BACKGROUND), line(MainActivity.SELECT_BIT));
        label(canvas, "select", 231, 148, 10, MainActivity.MUTED, false);
        pill(canvas, 314, 120, 30, 16, 8, ground(MainActivity.START_BIT, MainActivity.BACKGROUND), line(MainActivity.START_BIT));
        label(canvas, "start", 329, 148, 10, MainActivity.MUTED, false);
        circle(canvas, 280, 100, 15, MainActivity.BACKGROUND, MainActivity.IDLE);

        face(canvas, MainActivity.Y_BIT, 410, 94, "Y");
        face(canvas, MainActivity.X_BIT, 376, 128, "X");
        face(canvas, MainActivity.B_BIT, 444, 128, "B");
        face(canvas, MainActivity.A_BIT, 410, 162, "A");

        long now = SystemClock.uptimeMillis();
        waves(canvas, 0, now);
        waves(canvas, 1, now);
        canvas.restore();
        // Tick only while a wave is on or still fading; it stops by itself.
        if (motorOn[0] || motorOn[1] || now - fadeAt[0] < FADE_OUT_MS && fadeFrom[0] > 0
                || now - fadeAt[1] < FADE_OUT_MS && fadeFrom[1] > 0) {
            postInvalidateDelayed(TICK_MS);
        }
    }
}
