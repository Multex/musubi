package io.github.multex.musubi;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Objects;

/**
 * Assigns up to four connected gamepads to stable player slots (0..3) and keeps Android's
 * separate touchpad sub-devices out of the slot table. Kept free of Android imports for unit tests.
 */
final class PlayerSlots {
    static final int MAX = 4;

    static final class Device {
        final int id;
        final String name;
        final String descriptor;
        final int controllerNumber;

        Device(int id, String name, String descriptor, int controllerNumber) {
            this.id = id;
            this.name = name;
            this.descriptor = descriptor;
            this.controllerNumber = controllerNumber;
        }
    }

    private final Device[] slots = new Device[MAX];
    private final HashMap<String, Integer> remembered = new HashMap<>();

    Device get(int index) {
        return slots[index];
    }

    int slotOf(int deviceId) {
        for (int i = 0; i < MAX; i++) {
            if (slots[i] != null && slots[i].id == deviceId) return i;
        }
        return -1;
    }

    int count() {
        int n = 0;
        for (int i = 0; i < MAX; i++) {
            if (slots[i] != null) n++;
        }
        return n;
    }

    int firstOccupied() {
        for (int i = 0; i < MAX; i++) {
            if (slots[i] != null) return i;
        }
        return -1;
    }

    boolean sync(List<Device> present) {
        boolean changed = false;
        HashMap<Integer, Device> byId = new HashMap<>();
        for (Device device : present) {
            byId.put(device.id, device);
        }

        for (int i = 0; i < MAX; i++) {
            Device cur = slots[i];
            if (cur == null) continue;
            Device next = byId.get(cur.id);
            if (next == null) {
                slots[i] = null;
                changed = true;
            } else if (!Objects.equals(cur.name, next.name) || !Objects.equals(cur.descriptor, next.descriptor)) {
                slots[i] = next;
                if (next.descriptor != null && !next.descriptor.isEmpty()) {
                    remembered.put(next.descriptor, i);
                }
                changed = true;
            }
        }

        if (count() == 0) {
            remembered.clear();
        }

        List<Device> newcomers = new ArrayList<>();
        for (Device device : present) {
            if (slotOf(device.id) == -1) {
                newcomers.add(device);
            }
        }
        Collections.sort(newcomers, new Comparator<Device>() {
            @Override
            public int compare(Device a, Device b) {
                int na = a.controllerNumber > 0 ? a.controllerNumber : Integer.MAX_VALUE;
                int nb = b.controllerNumber > 0 ? b.controllerNumber : Integer.MAX_VALUE;
                if (na != nb) return Integer.compare(na, nb);
                return Integer.compare(a.id, b.id);
            }
        });

        for (Device device : newcomers) {
            if (assign(device) >= 0) {
                changed = true;
            }
        }
        return changed;
    }

    int ensure(Device device) {
        int existing = slotOf(device.id);
        if (existing != -1) return existing;
        if (count() == 0) {
            remembered.clear();
        }
        return assign(device);
    }

    private int assign(Device device) {
        if (device.descriptor != null && !device.descriptor.isEmpty()) {
            Integer pref = remembered.get(device.descriptor);
            if (pref != null && slots[pref] == null) {
                slots[pref] = device;
                return pref;
            }
        }
        for (int i = 0; i < MAX; i++) {
            if (slots[i] == null) {
                slots[i] = device;
                if (device.descriptor != null && !device.descriptor.isEmpty()) {
                    remembered.put(device.descriptor, i);
                }
                return i;
            }
        }
        return -1;
    }

    /** Rejects virtual devices, standalone PS4 touchpad sub-devices, and stickless Bluetooth earbuds. */
    static boolean isGamepad(boolean gamepad, boolean joystick, boolean hasSticks, boolean pointer, boolean virtual) {
        if (virtual) return false;
        if (joystick && hasSticks) return true;
        return gamepad && !pointer;
    }
}
