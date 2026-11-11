package io.github.multex.musubi;

import java.util.List;

/** Battery rules and wording for the controllers, kept free of Android so they run in the unit tests. */
final class Battery {
    /** The pad didn't report a level. */
    static final int UNKNOWN = -1;

    private Battery() {
    }

    /** BatteryState.getCapacity() is 0 to 1, or NaN when the pad doesn't say. */
    static int percent(float capacity) {
        if (!(capacity >= 0)) return UNKNOWN;
        return Math.min(100, Math.round(capacity * 100));
    }

    static boolean low(int level, boolean charging) {
        return level >= 0 && level <= 25 && !charging;
    }

    /** Four bars, one per quarter, rounded up so any charge left shows at least one. */
    static int bars(int level) {
        return level <= 0 ? 0 : Math.min(4, (level + 24) / 25);
    }

    /** "Player 1", "Players 1 and 3", "Players 1, 2 and 3". */
    static String players(List<Integer> numbers) {
        int last = numbers.size() - 1;
        if (last == 0) return "Player " + numbers.get(0);
        StringBuilder out = new StringBuilder("Players ");
        for (int i = 0; i < last; i++) out.append(i > 0 ? ", " : "").append(numbers.get(i));
        return out.append(" and ").append(numbers.get(last)).toString();
    }

    /** The red line under the player row on Home; empty when nobody is low. */
    static String lowText(List<Integer> numbers) {
        return numbers.isEmpty() ? "" : players(numbers) + " battery low";
    }

    /** The line under the controller name; the level must be known. */
    static String line(int level, boolean charging) {
        if (charging) return "Charging · " + level + "%";
        if (low(level, false)) return "Battery low · " + level + "%. Charge it soon.";
        return "Battery " + level + "%";
    }
}
