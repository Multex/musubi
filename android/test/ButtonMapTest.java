package padbridge.app;

import java.util.Arrays;

public class ButtonMapTest {
    private static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    public static void main(String[] args) {
        ButtonMap map = new ButtonMap();
        map.set(96, 0);
        map.set(97, 1);

        map.assign(0, Arrays.asList(97));
        check(map.control(97) == 0, "the new input drives the control");
        check(map.control(96) == -1, "the old input no longer drives it");

        map.assign(11, Arrays.asList(104, ButtonMap.LEFT_TRIGGER));
        check(map.control(104) == 11 && map.control(ButtonMap.LEFT_TRIGGER) == 11, "one press can bring two inputs");

        ButtonMap copy = map.copy();
        copy.assign(0, Arrays.asList(96));
        check(map.control(97) == 0, "a copy doesn't change the original");

        ButtonMap decoded = ButtonMap.decode(map.encode());
        check(decoded.control(97) == 0 && decoded.control(ButtonMap.LEFT_TRIGGER) == 11, "survives saving");
        check(ButtonMap.decode("").control(97) == -1, "empty is a valid map");
        check(ButtonMap.decode("not:a:map") == null, "bad data is rejected");
        check(ButtonMap.decode("x") == null, "a lone token is rejected");
        System.out.println("ButtonMapTest OK");
    }
}
