"""The virtual Xbox 360 pad on Windows, through the ViGEmBus driver."""
import ctypes
import sys
from pathlib import Path

DRIVER_PAGE = 'https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0'
# PyInstaller unpacks the DLL into _MEIPASS; from source it sits next to this file.
DLL = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'ViGEmClient.dll'
SUCCESS = 0x20000000
BUS_NOT_FOUND = 0xE0000001
# Same bit order as the packet: A, B, X, Y, LB, RB, Back, Start, Guide, left stick, right stick.
BUTTON_BITS = [0x1000, 0x2000, 0x4000, 0x8000, 0x0100, 0x0200, 0x0020, 0x0010, 0x0400, 0x0040, 0x0080]
DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT = 0x1, 0x2, 0x4, 0x8


class Report(ctypes.Structure):
    _fields_ = [('buttons', ctypes.c_ushort), ('left_trigger', ctypes.c_ubyte), ('right_trigger', ctypes.c_ubyte),
                ('lx', ctypes.c_short), ('ly', ctypes.c_short), ('rx', ctypes.c_short), ('ry', ctypes.c_short)]


# Client, target, large motor, small motor, LED number, user data.
NOTIFY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte,
                          ctypes.c_void_p)


def xbox_report(state):
    """Our (button bits, axes) as (buttons, left trigger, right trigger, lx, ly, rx, ry) for XInput."""
    bits, (lx, ly, rx, ry, left, right, hat_x, hat_y) = state
    buttons = sum(mask for bit, mask in enumerate(BUTTON_BITS) if bits >> bit & 1)
    buttons |= (DPAD_LEFT if hat_x < 0 else DPAD_RIGHT if hat_x > 0 else 0)
    buttons |= (DPAD_UP if hat_y < 0 else DPAD_DOWN if hat_y > 0 else 0)
    # The phone sends down as positive, and XInput wants up as positive.
    return buttons, left, right, lx, -ly, rx, -ry


def load():
    lib = ctypes.CDLL(str(DLL))
    pointer = ctypes.c_void_p
    # Without these ctypes cuts the 64-bit pointers down to 32 bits.
    for name, result, arguments in [
        ('vigem_alloc', pointer, []),
        ('vigem_free', None, [pointer]),
        ('vigem_connect', ctypes.c_uint, [pointer]),
        ('vigem_disconnect', None, [pointer]),
        ('vigem_target_x360_alloc', pointer, []),
        ('vigem_target_free', None, [pointer]),
        ('vigem_target_add', ctypes.c_uint, [pointer, pointer]),
        ('vigem_target_remove', ctypes.c_uint, [pointer, pointer]),
        ('vigem_target_x360_update', ctypes.c_uint, [pointer, pointer, Report]),
        ('vigem_target_x360_register_notification', ctypes.c_uint, [pointer, pointer, NOTIFY, pointer]),
        ('vigem_target_x360_unregister_notification', None, [pointer]),
    ]:
        function = getattr(lib, name)
        function.restype = result
        function.argtypes = arguments
    return lib


class Pad:
    """One Xbox 360 pad. `on_rumble(large, small)` gets motor levels 0..255 on a driver thread."""

    def __init__(self, on_rumble):
        self.lib = load()
        self.client = self.lib.vigem_alloc()
        self.target = None
        try:
            error = self.lib.vigem_connect(self.client)
            if error == BUS_NOT_FOUND:
                raise OSError(f'The ViGEmBus driver is not installed. Get it from {DRIVER_PAGE}')
            self._check(error, 'connect to the driver')
            self.target = self.lib.vigem_target_x360_alloc()
            self._check(self.lib.vigem_target_add(self.client, self.target), 'plug in the virtual pad')
            # Kept on self: ctypes frees a callback nobody holds, and the driver would call freed memory.
            self.notify = NOTIFY(lambda client, target, large, small, led, data: on_rumble(large, small))
            self._check(self.lib.vigem_target_x360_register_notification(self.client, self.target, self.notify, None),
                        'listen for rumble')
        except OSError:
            self.close()
            raise

    def _check(self, error, action):
        if error != SUCCESS:
            raise OSError(f'ViGEm could not {action} (error {error:#x}).')

    def apply(self, state):
        self.lib.vigem_target_x360_update(self.client, self.target, Report(*xbox_report(state)))

    def close(self):
        if self.target:
            self.lib.vigem_target_x360_unregister_notification(self.target)
            self.lib.vigem_target_remove(self.client, self.target)
            self.lib.vigem_target_free(self.target)
            self.target = None
        if self.client:
            self.lib.vigem_disconnect(self.client)
            self.lib.vigem_free(self.client)
            self.client = None
