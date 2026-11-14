package io.github.multex.musubi;

public class DeadzoneTest {
    private static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    public static void main(String[] args) {
        short[] out = new short[2];

        Deadzone.apply(out, 0, 0.5f, -0.25f, 0);
        check(out[0] == (short) (0.5f * 32767) && out[1] == (short) (-0.25f * 32767), "off passes the stick through");

        Deadzone.apply(out, 0, 0.06f, 0.0f, 0.1f);
        check(out[0] == 0 && out[1] == 0, "inside the deadzone is ignored");

        Deadzone.apply(out, 0, 0.0f, 0.1f, 0.1f);
        check(out[0] == 0 && out[1] == 0, "the edge itself is ignored");

        Deadzone.apply(out, 0, 0.55f, 0.0f, 0.1f);
        check(Math.abs(out[0] - 16383) <= 1 && out[1] == 0, "outside starts again from zero");

        Deadzone.apply(out, 0, -1f, 0.0f, 0.3f);
        check(out[0] == -32767, "full tilt still reaches the end");

        Deadzone.apply(out, 0, 1f, 1f, 0.2f);
        check(out[0] == 32767 && out[1] == 32767, "a square gate keeps its full diagonal");

        short[] two = new short[4];
        Deadzone.apply(two, 2, 0.0f, -1f, 0.1f);
        check(two[0] == 0 && two[2] == 0 && two[3] == -32767, "writes at the given index");
        System.out.println("DeadzoneTest OK");
    }
}
