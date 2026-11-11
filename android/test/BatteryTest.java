package io.github.multex.musubi;

import java.util.Arrays;
import java.util.Collections;

public class BatteryTest {
    private static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    public static void main(String[] args) {
        check(Battery.percent(Float.NaN) == Battery.UNKNOWN, "NaN is not reported");
        check(Battery.percent(-1f) == Battery.UNKNOWN, "a negative capacity is not reported");
        check(Battery.percent(0.62f) == 62 && Battery.percent(0.185f) == 19, "the pad's own percent, rounded");
        check(Battery.percent(1f) == 100 && Battery.percent(0f) == 0, "the ends");

        check(Battery.low(25, false) && Battery.low(0, false), "25% or less is low");
        check(!Battery.low(26, false), "26% is not low");
        check(!Battery.low(18, true), "never low while charging");
        check(!Battery.low(Battery.UNKNOWN, false), "an unknown level is never low");

        check(Battery.bars(0) == 0 && Battery.bars(1) == 1 && Battery.bars(25) == 1, "first quarter");
        check(Battery.bars(26) == 2 && Battery.bars(62) == 3 && Battery.bars(75) == 3, "middle quarters");
        check(Battery.bars(76) == 4 && Battery.bars(100) == 4, "last quarter");

        check(Battery.lowText(Collections.<Integer>emptyList()).isEmpty(), "nobody low, no line");
        check(Battery.lowText(Arrays.asList(1)).equals("Player 1 battery low"), "one player");
        check(Battery.lowText(Arrays.asList(1, 3)).equals("Players 1 and 3 battery low"), "two players");
        check(Battery.lowText(Arrays.asList(1, 2, 4)).equals("Players 1, 2 and 4 battery low"), "three players");

        check(Battery.line(62, false).equals("Battery 62%"), "normal line");
        check(Battery.line(18, false).equals("Battery low · 18%. Charge it soon."), "low line");
        check(Battery.line(40, true).equals("Charging · 40%"), "charging line");
        check(Battery.line(18, true).equals("Charging · 18%"), "charging wins over low");
        System.out.println("BatteryTest OK");
    }
}
