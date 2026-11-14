package io.github.multex.musubi;

public class AudioBufferTest {
    private static final int MS = AudioBuffer.BYTES_PER_MS;
    private static final byte[] out = new byte[AudioBuffer.CHUNK + AudioBuffer.FRAME];

    private static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    private static byte[] packet(int seq) {
        byte[] pcm = new byte[AudioBuffer.CHUNK];
        for (int i = 0; i < pcm.length; i++) pcm[i] = (byte) (seq + i / AudioBuffer.FRAME + 1 | 1);
        return pcm;
    }

    /** Pushes count packets from seq on and returns the next seq. */
    private static int fill(AudioBuffer buffer, int seq, int count) {
        for (int i = 0; i < count; i++, seq = (seq + 1) & 0xffff) {
            check(buffer.push(seq, packet(seq), 0, AudioBuffer.CHUNK), "packet " + seq + " is queued");
        }
        return seq;
    }

    private static void drain(AudioBuffer buffer) {
        while (buffer.ready()) buffer.pull(out);
    }

    public static void main(String[] args) {
        AudioBuffer buffer = new AudioBuffer();
        check(buffer.target() == AudioBuffer.START_MS * MS, "starts at the start target");
        int seq = fill(buffer, 0, 3);
        check(!buffer.ready(), "waits for the target level before playing");
        seq = fill(buffer, seq, 1);
        check(buffer.ready(), "plays once the target level is in");
        drain(buffer);
        check(buffer.underruns == 1 && buffer.target() == (AudioBuffer.START_MS + AudioBuffer.RAISE_MS) * MS,
            "running dry mid-sound raises the target");

        buffer.push(seq, new byte[AudioBuffer.CHUNK], 0, AudioBuffer.CHUNK);
        seq++;
        seq = fill(buffer, seq, 5);
        buffer.push(seq, new byte[AudioBuffer.CHUNK], 0, AudioBuffer.CHUNK);
        seq++;
        drain(buffer);
        check(buffer.underruns == 1, "running dry after the PC's trailing zeros is the end of a sound, not an underrun");

        for (int i = 0; i < 20; i++) {
            seq = fill(buffer, seq, buffer.target() / AudioBuffer.CHUNK);
            drain(buffer);
        }
        check(buffer.target() == AudioBuffer.MAX_MS * MS, "the target stops at its cap");

        buffer = new AudioBuffer();
        seq = fill(buffer, 0, AudioBuffer.START_MS / 5);
        int pulls = AudioBuffer.STABLE_MS / 5;
        for (int i = 0; i < pulls - 1; i++) {
            buffer.pull(out);
            seq = fill(buffer, seq, 1);
        }
        check(buffer.target() == AudioBuffer.START_MS * MS, "the target holds until the stable period is over");
        buffer.pull(out);
        seq = fill(buffer, seq, 1);
        check(buffer.target() == AudioBuffer.MIN_MS * MS, "a stable period gives back the unused margin, down to the floor");
        for (int i = 0; i < 3 * pulls; i++) {
            buffer.pull(out);
            seq = fill(buffer, seq, 1);
        }
        check(buffer.target() == AudioBuffer.MIN_MS * MS && buffer.underruns == 0, "the target stops at its floor");

        // From the cap: one 55 ms gap in the period leaves 40 ms of margin used least, so 35 ms of target goes.
        for (int dip : new int[] {18, 11}) {
            buffer = new AudioBuffer();
            seq = 0;
            while (buffer.target() < AudioBuffer.MAX_MS * MS) {
                seq = fill(buffer, seq, buffer.target() / AudioBuffer.CHUNK);
                drain(buffer);
            }
            seq = fill(buffer, seq, AudioBuffer.MAX_MS / 5);
            buffer.underruns = 0;
            for (int i = 0; i < pulls; i++) {
                buffer.pull(out);
                if (i == 1000) {
                    for (int j = 0; j < dip; j++) buffer.pull(out);
                    seq = fill(buffer, seq, dip);
                    i += dip;
                }
                seq = fill(buffer, seq, 1);
            }
            int expect = dip == 11 ? AudioBuffer.MAX_MS - 35 : AudioBuffer.MAX_MS;
            check(buffer.target() == expect * MS && buffer.underruns == 0,
                "a " + dip * 5 + " ms dip sets the target to its depth plus the cushion within one period");
        }
        for (int i = 0; i < 2 * AudioBuffer.WINDOW_MS / 5; i++) {
            buffer.pull(out);
            seq = fill(buffer, seq, 1);
        }
        check(buffer.level() - AudioBuffer.CHUNK == (AudioBuffer.MAX_MS - 35) * MS,
            "the level follows the lower target at once through the jump cut");
        drain(buffer);
        check(buffer.underruns == 1 && buffer.target() == (AudioBuffer.MAX_MS - 35 + AudioBuffer.RAISE_MS) * MS,
            "an underrun still raises the target a full step");

        AudioBuffer.Track track = new AudioBuffer.Track(140);
        int bytesPerPeriod = AudioBuffer.STABLE_MS * MS;
        check(track.played(AudioBuffer.CHUNK, true) == 150, "a starved track grows a step");
        track.ms = 150;
        check(track.played(bytesPerPeriod - 1, false) == 0, "the track holds until the stable period is over");
        check(track.played(1, false) == 80, "a stable period gives back half the excess");
        track.played(bytesPerPeriod / 2, false);
        check(track.played(AudioBuffer.CHUNK, true) == 160 && track.played(bytesPerPeriod - 1, false) == 0,
            "starving restarts the stable period");
        int[] steps = {80, 45, 28, 18, 10};
        track = new AudioBuffer.Track(150);
        for (int step : steps) {
            int ms = track.played(bytesPerPeriod, false);
            check(ms == step, "the track steps back to " + step + " ms, got " + ms);
            track.ms = ms;
        }
        check(track.played(10 * bytesPerPeriod, false) == 0, "the track stops at its minimum");

        for (int burst : new int[] {4, 20}) {
            buffer = new AudioBuffer();
            seq = fill(buffer, 0, AudioBuffer.START_MS / 5);
            // Packets in bursts of 20 or 100 ms for just under a stable period, so the target holds.
            for (int i = 0; i < (AudioBuffer.STABLE_MS - 1000) / (5 * burst); i++) {
                seq = fill(buffer, seq, burst);
                for (int j = 0; j < burst; j++) buffer.pull(out);
            }
            check(buffer.dropped == 0 && buffer.duplicated == 0 && buffer.underruns == 0,
                "bursts of " + burst + " packets with a steady average are left alone");
        }

        buffer = new AudioBuffer();
        seq = fill(buffer, 0, 12);
        int played = 0;
        for (; played < AudioBuffer.WINDOW_MS / 5; played++) {
            check(buffer.dropped == 0, "nothing is trimmed before a full window is measured");
            buffer.pull(out);
            seq = fill(buffer, seq, 1);
        }
        check(buffer.level() - AudioBuffer.CHUNK == AudioBuffer.START_MS * MS
            && buffer.dropped == (55 - AudioBuffer.START_MS) * MS / AudioBuffer.FRAME,
            "a whole window far over the target is cut at once");

        // 15 ms over, under the jump, so it goes frame by frame.
        seq = fill(buffer, seq, 3);
        int before = buffer.level();
        int dropped = buffer.dropped;
        int start = played;
        for (; played < AudioBuffer.STABLE_MS / 5 - 1; played++) {
            check(buffer.pull(out) == AudioBuffer.CHUNK, "a drop never shortens what's played");
            seq = fill(buffer, seq, 1);
            check((buffer.dropped - dropped) * AudioBuffer.CORRECT_EVERY_MS <= (played + 1 - start) * 5,
                "later excess is dropped at most one frame per 10 ms");
        }
        check(buffer.dropped > dropped, "a later sustained excess is trimmed");
        check(buffer.level() == before - (buffer.dropped - dropped) * AudioBuffer.FRAME,
            "each drop takes exactly one frame off the level");
        check(buffer.level() - AudioBuffer.CHUNK <= (AudioBuffer.START_MS + AudioBuffer.TOLERANCE_MS) * MS
            && buffer.level() - AudioBuffer.CHUNK >= AudioBuffer.START_MS * MS, "a sustained excess is trimmed to the target");
        check(buffer.duplicated == 0, "nothing is repeated while trimming");

        buffer = new AudioBuffer();
        seq = fill(buffer, 0, 4);
        buffer.pull(out);
        buffer.pull(out);
        int length = 0;
        for (int i = 0; i <= AudioBuffer.WINDOW_MS / 5 && buffer.duplicated == 0; i++) {
            length = buffer.pull(out);
            seq = fill(buffer, seq, 1);
        }
        check(length == AudioBuffer.CHUNK + AudioBuffer.FRAME && buffer.duplicated == 1, "a low margin repeats one frame");
        for (int i = 1; i <= AudioBuffer.FRAME; i++) {
            check(out[length - i] == out[length - i - AudioBuffer.FRAME], "the repeated frame copies the one before");
        }
        check(buffer.dropped == 0, "nothing is dropped while the margin is low");

        buffer = new AudioBuffer();
        seq = fill(buffer, 65534, 3);
        check(seq == 1 && buffer.lost == 0, "the sequence wraps without counting a loss");
        check(!buffer.push(0, packet(0), 0, AudioBuffer.CHUNK), "a repeated packet is dropped");
        check(!buffer.push(65535, packet(0), 0, AudioBuffer.CHUNK), "a packet from before the wrap is late");
        check(buffer.late == 2, "late and repeated packets are counted");
        fill(buffer, 3, 1);
        check(buffer.lost == 2, "a gap counts its missing packets");
        buffer = new AudioBuffer();
        fill(buffer, 65533, 1);
        fill(buffer, 1, 1);
        check(buffer.lost == 3, "a gap across the wrap counts its missing packets");
        buffer.reset();
        check(buffer.push(7, packet(7), 0, AudioBuffer.CHUNK) && buffer.lost == 3, "after a reset any sequence is accepted");
        check(!buffer.push(8, packet(8), 0, 3), "a partial frame is rejected");

        buffer = new AudioBuffer();
        fill(buffer, 30000, 1);
        fill(buffer, 0, 4);
        check(buffer.ready() && buffer.late == 0, "a restarted PC's numbers are followed, not rejected as late");

        buffer = new AudioBuffer();
        fill(buffer, 0, 4);
        for (int i = 0; i < AudioBuffer.CAPACITY_MS / 5; i++) fill(buffer, 4 + i, 1);
        check(buffer.level() <= AudioBuffer.CAPACITY_MS * MS, "a flood is capped");

        buffer = new AudioBuffer();
        buffer.setBackground(true);
        check(buffer.target() == AudioBuffer.BACKGROUND_MS * MS, "the background starts at its fixed target");
        seq = fill(buffer, 0, AudioBuffer.BACKGROUND_MS / 5);
        for (int i = 0; i < 3 * pulls; i++) {
            buffer.pull(out);
            seq = fill(buffer, seq, 1);
        }
        check(buffer.target() == AudioBuffer.BACKGROUND_MS * MS && buffer.underruns == 0 && buffer.dropped == 0,
            "the background target holds through long stable periods");
        buffer.setBackground(false);
        check(buffer.target() == AudioBuffer.START_MS * MS, "the foreground restarts at the start target");
        for (int i = 0; i < AudioBuffer.WINDOW_MS / 5; i++) {
            buffer.pull(out);
            seq = fill(buffer, seq, 1);
        }
        check(buffer.level() - AudioBuffer.CHUNK == AudioBuffer.START_MS * MS && buffer.underruns == 0,
            "the background's excess is cut within a window of returning");
        for (int i = 0; i < pulls; i++) {
            buffer.pull(out);
            seq = fill(buffer, seq, 1);
        }
        check(buffer.target() == AudioBuffer.MIN_MS * MS, "the foreground decays again after a stable period");

        track = new AudioBuffer.Track(80);
        check(track.setBackground(true) == 0 && track.played(10 * bytesPerPeriod, false) == 0,
            "the background never shrinks the track");
        check(track.played(AudioBuffer.CHUNK, true) == 90, "a starved track still grows in the background");
        track.ms = 90;
        check(track.setBackground(true) == 0, "staying in the background leaves the track");
        check(track.setBackground(false) == AudioBuffer.Track.STEP_MS, "the foreground drops the track to its minimum");
        track = new AudioBuffer.Track(AudioBuffer.Track.STEP_MS);
        track.setBackground(true);
        check(track.setBackground(false) == 0, "a track already at its minimum is left alone");

        buffer = new AudioBuffer();
        buffer.setSteady(true);
        check(buffer.target() == AudioBuffer.STEADY_MIN_MS * MS, "most stable starts at its floor");
        byte[] big = new byte[2 * AudioBuffer.CHUNK];
        java.util.Arrays.fill(big, (byte) 1);
        seq = 0;
        for (int i = 0; i < AudioBuffer.STEADY_MIN_MS / 10; i++, seq++) check(buffer.push(seq, big, 0, big.length), "a 10 ms packet is queued");
        for (int i = 0; i < 3 * pulls / 2; i++, seq++) {
            buffer.push(seq, big, 0, big.length);
            check(buffer.pull(out) == AudioBuffer.CHUNK && buffer.pull(out) == AudioBuffer.CHUNK, "10 ms packets play as 5 ms writes");
        }
        check(buffer.target() == AudioBuffer.STEADY_MIN_MS * MS && buffer.underruns == 0 && buffer.dropped == 0
            && buffer.duplicated == 0, "most stable holds its floor through stable periods");
        buffer.setBackground(true);
        buffer.setBackground(false);
        check(buffer.target() == AudioBuffer.STEADY_MIN_MS * MS, "back from the background most stable restarts at its floor");
        buffer.setSteady(false);
        check(buffer.target() == AudioBuffer.START_MS * MS, "lowest delay restarts at the start target");

        buffer = new AudioBuffer();
        seq = fill(buffer, 0, 4);
        String line = null;
        for (int i = 0; i < AudioBuffer.STATS_EVERY_MS / 5 && line == null; i++) {
            buffer.pull(out);
            seq = fill(buffer, seq, 1);
            line = buffer.stats();
        }
        check(line != null && line.startsWith("fg, target 20 ms"), "a stats line comes after 5 s of audio");
        check(buffer.stats() == null, "and not again right after");
        System.out.println("AudioBufferTest OK");
    }
}
