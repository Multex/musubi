package io.github.multex.musubi;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

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

        map.set(110, 8);
        map.assign(11, Collections.emptyList());
        check(map.control(104) == -1 && map.control(ButtonMap.LEFT_TRIGGER) == -1,
            "skipping a control removes all of its previous inputs");
        check(map.control(97) == 0 && map.control(110) == 8,
            "skipping leaves other controls including Guide intact");

        List<Integer> slowL2 = new ArrayList<>(Arrays.asList(104));
        ButtonMap.pairTriggerAxis(slowL2, 104, ButtonMap.LEFT_TRIGGER, false);
        check(slowL2.equals(Arrays.asList(104, ButtonMap.LEFT_TRIGGER)), "slow L2 pull pairs left trigger axis");

        List<Integer> slowR2 = new ArrayList<>(Arrays.asList(105));
        ButtonMap.pairTriggerAxis(slowR2, 105, ButtonMap.RIGHT_TRIGGER, false);
        check(slowR2.equals(Arrays.asList(105, ButtonMap.RIGHT_TRIGGER)), "slow R2 pull pairs right trigger axis");

        List<Integer> alreadyPaired = new ArrayList<>(Arrays.asList(104, ButtonMap.LEFT_TRIGGER));
        ButtonMap.pairTriggerAxis(alreadyPaired, 104, ButtonMap.LEFT_TRIGGER, false);
        check(alreadyPaired.size() == 2 && alreadyPaired.equals(Arrays.asList(104, ButtonMap.LEFT_TRIGGER)),
            "fast L2 pull does not duplicate axis");

        List<Integer> mixedAxis = new ArrayList<>(Arrays.asList(104, ButtonMap.RIGHT_TRIGGER));
        ButtonMap.pairTriggerAxis(mixedAxis, 104, ButtonMap.LEFT_TRIGGER, false);
        check(mixedAxis.equals(Arrays.asList(104, ButtonMap.RIGHT_TRIGGER)),
            "does not add left trigger axis when another trigger axis was captured");

        List<Integer> claimedAxis = new ArrayList<>(Arrays.asList(104));
        ButtonMap.pairTriggerAxis(claimedAxis, 104, ButtonMap.LEFT_TRIGGER, true);
        check(claimedAxis.equals(Arrays.asList(104)), "does not steal an already-claimed trigger axis");

        List<Integer> nonTrigger = new ArrayList<>(Arrays.asList(96));
        ButtonMap.pairTriggerAxis(nonTrigger, 104, ButtonMap.LEFT_TRIGGER, false);
        check(nonTrigger.equals(Arrays.asList(96)), "unrelated keycode stays untouched");

        System.out.println("ButtonMapTest OK");
    }
}
