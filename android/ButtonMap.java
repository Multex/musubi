package io.github.multex.musubi;

import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

/** Which physical input drives which control. Keycodes are positive; axes acting as buttons are negative. */
final class ButtonMap {
    static final int HAT_LEFT = -1, HAT_RIGHT = -2, HAT_UP = -3, HAT_DOWN = -4;
    static final int LEFT_TRIGGER = -5, RIGHT_TRIGGER = -6;

    private final HashMap<Integer, Integer> controls = new HashMap<>();

    int control(int input) {
        Integer control = controls.get(input);
        return control == null ? -1 : control;
    }

    void set(int input, int control) {
        controls.put(input, control);
    }

    /** One press can fire several inputs (a key plus an axis), so a control takes all of them at once. */
    void assign(int control, Iterable<Integer> inputs) {
        Iterator<Integer> old = controls.values().iterator();
        while (old.hasNext()) {
            if (old.next() == control) old.remove();
        }
        for (int input : inputs) controls.put(input, control);
    }

    ButtonMap copy() {
        ButtonMap copy = new ButtonMap();
        copy.controls.putAll(controls);
        return copy;
    }

    String encode() {
        StringBuilder out = new StringBuilder();
        for (Map.Entry<Integer, Integer> entry : controls.entrySet()) {
            if (out.length() > 0) out.append(',');
            out.append(entry.getKey()).append(':').append(entry.getValue());
        }
        return out.toString();
    }

    /** Returns null for a stored value it can't read, so the caller falls back to the defaults. */
    static ButtonMap decode(String text) {
        ButtonMap map = new ButtonMap();
        if (text.isEmpty()) return map;
        try {
            for (String pair : text.split(",")) {
                String[] parts = pair.split(":");
                map.set(Integer.parseInt(parts[0]), Integer.parseInt(parts[1]));
            }
        } catch (RuntimeException ex) {
            return null;
        }
        return map;
    }
}
