package io.github.multex.musubi;

import java.util.Arrays;
import java.util.Collections;

public class PlayerSlotsTest {
    private static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    private static PlayerSlots.Device pad(int id, String name, String desc, int number) {
        return new PlayerSlots.Device(id, name, desc, number);
    }

    public static void main(String[] args) {
        PlayerSlots slots = new PlayerSlots();

        // Deterministic ordering by controllerNumber, then id when controllerNumber == 0.
        check(slots.sync(Arrays.asList(
            pad(12, "Pad B", "desc-b", 0),
            pad(8, "Pad C", "desc-c", 0),
            pad(20, "Pad A", "desc-a", 1))), "initial sync changes slots");
        check(slots.get(0).id == 20, "controllerNumber 1 gets slot 0");
        check(slots.get(1).id == 8, "lower id breaks tie when controllerNumber is 0");
        check(slots.get(2).id == 12 && slots.get(3) == null, "third pad gets slot 2, slot 3 free");
        check(slots.count() == 3 && slots.firstOccupied() == 0, "count and firstOccupied");

        // Idempotent sync returns false.
        check(!slots.sync(Arrays.asList(
            pad(20, "Pad A", "desc-a", 1),
            pad(8, "Pad C", "desc-c", 0),
            pad(12, "Pad B", "desc-b", 0))), "unchanged devices do not report a change");

        // Stability on disconnect: Player 1 leaves while Player 2 & 3 stay put.
        check(slots.sync(Arrays.asList(
            pad(8, "Pad C", "desc-c", 0),
            pad(12, "Pad B", "desc-b", 0))), "removing player 1 reports change");
        check(slots.get(0) == null && slots.get(1).id == 8 && slots.get(2).id == 12,
            "remaining players keep their slots when player 1 disconnects");
        check(slots.firstOccupied() == 1, "firstOccupied skips empty slot 0");

        // Rejoining / new pad takes the lowest free slot when no remembered slot matches, or its own remembered slot.
        check(slots.sync(Arrays.asList(
            pad(8, "Pad C", "desc-c", 0),
            pad(12, "Pad B", "desc-b", 0),
            pad(30, "Pad D", "desc-d", 2))), "new pad fills lowest free slot");
        check(slots.get(0).id == 30 && slots.get(1).id == 8 && slots.get(2).id == 12,
            "new pad takes slot 0 while existing slots stay put");

        // Descriptor slot memory: Player 2 sleeps and wakes with a new device id while others are active.
        check(slots.sync(Arrays.asList(
            pad(30, "Pad D", "desc-d", 2),
            pad(12, "Pad B", "desc-b", 0))), "pad C sleeps");
        check(slots.get(1) == null, "slot 1 is now free");
        check(slots.sync(Arrays.asList(
            pad(30, "Pad D", "desc-d", 2),
            pad(12, "Pad B", "desc-b", 0),
            pad(99, "Pad C", "desc-c", 5))), "pad C wakes with new id");
        check(slots.get(1).id == 99, "pad C reclaims remembered slot 1");

        // When all disconnect, remembered slots clear so the next pad alone starts at Player 1.
        check(slots.sync(Collections.<PlayerSlots.Device>emptyList()), "all disconnect");
        check(slots.count() == 0 && slots.firstOccupied() == -1, "all slots empty");
        check(slots.sync(Arrays.asList(pad(101, "Pad C", "desc-c", 0))), "pad C wakes alone");
        check(slots.get(0).id == 101, "solo pad starts at slot 0 after full disconnect");

        // 5th controller is ignored when 4 slots are full; ensure respects existing and full states.
        slots.sync(Arrays.asList(
            pad(1, "P1", "d1", 1),
            pad(2, "P2", "d2", 2),
            pad(3, "P3", "d3", 3),
            pad(4, "P4", "d4", 4),
            pad(5, "P5", "d5", 5)));
        check(slots.count() == 4 && slots.slotOf(5) == -1, "fifth pad ignored in sync");
        check(slots.ensure(pad(3, "P3", "d3", 3)) == 2, "ensure returns existing slot");
        check(slots.ensure(pad(6, "P6", "d6", 6)) == -1, "ensure returns -1 when full");

        // Pad filtering rules.
        check(PlayerSlots.isGamepad(true, true, true, false, false), "standard Xbox/PS controller accepted");
        check(!PlayerSlots.isGamepad(true, false, false, true, false), "PS4 touchpad-only sub-device rejected");
        check(PlayerSlots.isGamepad(true, true, true, true, false), "combined touchpad + sticks pad accepted");
        check(PlayerSlots.isGamepad(true, false, false, false, false), "D-pad-only retro gamepad accepted");
        check(!PlayerSlots.isGamepad(false, true, false, false, false), "stickless Bluetooth earbuds rejected");
        check(!PlayerSlots.isGamepad(true, true, true, false, true), "virtual input device rejected");

        System.out.println("PlayerSlotsTest OK");
    }
}
