package io.github.multex.musubi;

/**
 * Jitter buffer for the PC's 48 kHz stereo PCM. The level it aims for grows on underruns and shrinks
 * to the margin playback actually used, and drift is absorbed by dropping or repeating single frames.
 * In the background it holds a fixed generous target instead, since latency barely matters there.
 * Not thread-safe: the caller holds one lock around every call.
 */
final class AudioBuffer {
    static final int FRAME = 4;
    static final int BYTES_PER_MS = 48 * FRAME;
    // One packet from the PC, and what the player hands the AudioTrack per write.
    static final int CHUNK = 5 * BYTES_PER_MS;

    // 20 ms is four packets: enough for the cable from the start, and Wi-Fi climbs from there.
    static final int START_MS = 20;
    // Three packets: at two the cable still cut out every 20 to 25 s.
    static final int MIN_MS = 15;
    // Screen-off Wi-Fi can batch packets for close to 100 ms; past that a glitch beats the delay.
    static final int MAX_MS = 100;
    // Screen-off Wi-Fi bursts are rare, so any decay shrank the target between them; about 100 ms had no cuts.
    static final int BACKGROUND_MS = 100;
    // Rise fast so one bad stretch costs one glitch.
    static final int RAISE_MS = 10;
    // After a stable period the target gives back the margin the level never dipped into, less this cushion.
    // Foreground only, starting a step above the floor, so a slow decay costs little and lowers the risk of a cut mid-game.
    static final int CUSHION_MS = 5;
    static final int STABLE_MS = 30000;
    // Steering uses the lowest level over a window, the real margin before running dry, since the level itself
    // swings with packet bursts and AudioTrack wakeups. The window outlasts a 100 ms screen-off Wi-Fi burst.
    static final int WINDOW_MS = 500;
    static final int TOLERANCE_MS = 5;
    // A whole window this far over the target is a stall, not jitter, since bursts only lift the level briefly.
    static final int JUMP_MS = 20;
    // One frame per 480 is a 0.2% speed change, too small to hear.
    static final int CORRECT_EVERY_MS = 10;
    // Room above the highest target before the oldest audio is thrown away.
    static final int CAPACITY_MS = MAX_MS + 50;
    static final int STATS_EVERY_MS = 5000;

    private final byte[] ring = new byte[CAPACITY_MS * BYTES_PER_MS];
    private int start;
    private int size;
    private int target = START_MS * BYTES_PER_MS;
    private boolean filling = true;
    private boolean background;
    private boolean lastSilent;
    private int expected = -1;
    private int stable;
    // Lowest window low since the stable period began: the margin that was really used.
    private int need = Integer.MAX_VALUE;
    private int sinceCorrection;
    private int windowMin = Integer.MAX_VALUE;
    private int sinceWindow;
    // Lowest level of the last full window, or -1 before one has been measured.
    private int low = -1;
    private int sinceStats;
    int underruns, lost, late, dropped, duplicated;

    /** Queues one packet, or returns false for a malformed, late or repeated one. */
    boolean push(int seq, byte[] pcm, int offset, int length) {
        if (length == 0 || length % FRAME != 0 || length > MAX_MS * BYTES_PER_MS) return false;
        if (expected >= 0) {
            int ahead = (seq - expected) & 0xffff;
            if (ahead < 0x8000) {
                lost += ahead;
            } else if (0x10000 - ahead <= ring.length / CHUNK) {
                late++;
                return false;
            } // Further back than the ring holds is a restarted PC, not a late packet, so its numbers are followed.
        }
        expected = (seq + 1) & 0xffff;
        if (size + length > ring.length) {
            int drop = Math.min(size, size + length - target);
            skip(drop);
            dropped += drop / FRAME;
        }
        int end = (start + size) % ring.length;
        int first = Math.min(length, ring.length - end);
        System.arraycopy(pcm, offset, ring, end, first);
        System.arraycopy(pcm, offset + first, ring, 0, length - first);
        size += length;
        // The PC sends 200 ms of zeros before going quiet, so running dry after zeros is the end of a sound.
        int last = offset + length - FRAME;
        lastSilent = (pcm[last] | pcm[last + 1] | pcm[last + 2] | pcm[last + 3]) == 0;
        return true;
    }

    /** After running dry it waits for the target level again, rather than crackling packet by packet. */
    boolean ready() {
        return filling ? size >= target : size > 0;
    }

    /** Fills out, which needs room for CHUNK plus one repeated frame, and returns the byte count. */
    int pull(byte[] out) {
        if (!ready()) return 0;
        int length = Math.min(CHUNK, size);
        int first = Math.min(length, ring.length - start);
        System.arraycopy(ring, start, out, 0, first);
        System.arraycopy(ring, 0, out, first, length - first);
        skip(length);
        filling = false;
        windowMin = Math.min(windowMin, size);
        sinceWindow += length;
        if (sinceWindow >= WINDOW_MS * BYTES_PER_MS) {
            low = windowMin;
            windowMin = Integer.MAX_VALUE;
            sinceWindow = 0;
            // A track's start can stall and pile up 40 ms, which frame drops would take 20 s to trim, so cut it at once.
            if (low > target + JUMP_MS * BYTES_PER_MS) {
                dropped += (low - target) / FRAME;
                skip(low - target);
                low = target;
            }
            need = Math.min(need, low);
        }
        sinceCorrection += length;
        if (low >= 0 && sinceCorrection >= CORRECT_EVERY_MS * BYTES_PER_MS) {
            if (low > target + TOLERANCE_MS * BYTES_PER_MS && size > 0) {
                skip(FRAME);
                dropped++;
                sinceCorrection = 0;
            } else if (low < target - TOLERANCE_MS * BYTES_PER_MS && size < target - TOLERANCE_MS * BYTES_PER_MS) {
                System.arraycopy(out, length - FRAME, out, length, FRAME);
                length += FRAME;
                duplicated++;
                sinceCorrection = 0;
            }
        }
        sinceStats += length;
        if (size == 0) {
            filling = true;
            forgetWindow();
            if (!lastSilent) {
                underruns++;
                target = Math.min(target + RAISE_MS * BYTES_PER_MS, MAX_MS * BYTES_PER_MS);
                stable = 0;
                need = Integer.MAX_VALUE;
                return length;
            }
        }
        if (background) return length;
        stable += length;
        if (stable >= STABLE_MS * BYTES_PER_MS) {
            // Steering then trims the level to the new target, or the jump cuts it if far over.
            if (need != Integer.MAX_VALUE && need > CUSHION_MS * BYTES_PER_MS) {
                target = Math.max(target - need + CUSHION_MS * BYTES_PER_MS, MIN_MS * BYTES_PER_MS);
            }
            need = Integer.MAX_VALUE;
            stable = 0;
        }
        return length;
    }

    /**
     * Background holds BACKGROUND_MS; foreground restarts adaptive from START_MS, and the jump cut
     * trims the excess level at the next window.
     */
    void setBackground(boolean on) {
        if (on == background) return;
        background = on;
        target = (on ? BACKGROUND_MS : START_MS) * BYTES_PER_MS;
        stable = 0;
        need = Integer.MAX_VALUE;
    }

    boolean background() {
        return background;
    }

    /** Forgets queued audio and the sequence, for a new sound after a silence. Keeps the target. */
    void reset() {
        start = 0;
        size = 0;
        filling = true;
        expected = -1;
        sinceCorrection = 0;
        forgetWindow();
    }

    /** Running dry ends the window, since refilling to the target restarts the margin. */
    private void forgetWindow() {
        windowMin = Integer.MAX_VALUE;
        sinceWindow = 0;
        low = -1;
    }

    int target() {
        return target;
    }

    int level() {
        return size;
    }

    /** One log line every STATS_EVERY_MS of played audio, counting since the previous line, else null. */
    String stats() {
        if (sinceStats < STATS_EVERY_MS * BYTES_PER_MS) return null;
        String line = (background ? "bg" : "fg") + ", target " + target / BYTES_PER_MS + " ms, level " + size / BYTES_PER_MS
            + " ms, low " + low / BYTES_PER_MS + " ms, underruns " + underruns + ", lost " + lost + ", late " + late
            + ", dropped " + dropped + ", duplicated " + duplicated;
        sinceStats = 0;
        underruns = lost = late = dropped = duplicated = 0;
        return line;
    }

    /**
     * The AudioTrack's own queue: a step more each time it starves, and in the foreground half the excess back
     * after a stable period. Back from the background it drops to the minimum at once. Player thread only.
     */
    static final class Track {
        static final int STEP_MS = 10;
        int ms;
        private int stable;
        private boolean background;

        Track(int ms) {
            this.ms = ms;
        }

        /** Before each write with the buffer's mode; returns STEP_MS on the way back to the foreground, else 0. */
        int setBackground(boolean on) {
            if (on == background) return 0;
            background = on;
            stable = 0;
            return on || ms <= STEP_MS ? 0 : STEP_MS;
        }

        /** After each write; returns the size in ms to ask the track for, or 0 to leave it. */
        int played(int bytes, boolean starved) {
            if (starved) {
                stable = 0;
                return ms + STEP_MS;
            }
            if (background || ms <= STEP_MS || (stable += bytes) < STABLE_MS * BYTES_PER_MS) return 0;
            stable = 0;
            return Math.max(STEP_MS, ms - Math.max(STEP_MS, (ms - STEP_MS) / 2));
        }
    }

    private void skip(int bytes) {
        start = (start + bytes) % ring.length;
        size -= bytes;
    }
}
