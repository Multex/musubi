package io.github.multex.musubi;

/** Radial stick deadzone, rescaled so the stick still reaches full travel. Runs on every motion event. */
final class Deadzone {
    static final int STICK_MAX = 32767;

    private Deadzone() {
    }

    /** Writes the stick (x, y in -1..1) to out[i] and out[i + 1]; d is the deadzone radius, 0..1. */
    static void apply(short[] out, int i, float x, float y, float d) {
        if (d > 0) {
            float m = (float) Math.sqrt(x * x + y * y);
            if (m <= d) {
                x = y = 0;
            } else {
                float scale = (m - d) / (1 - d) / m;
                x *= scale;
                y *= scale;
            }
        }
        // Per axis, so a square-gate pad still reaches full diagonal as it does with the deadzone off.
        out[i] = (short) (Math.max(-1f, Math.min(1f, x)) * STICK_MAX);
        out[i + 1] = (short) (Math.max(-1f, Math.min(1f, y)) * STICK_MAX);
    }
}
