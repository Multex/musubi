"""The notification area icon over plain Win32, so the hub needs no extra package or GUI toolkit."""
import ctypes
import logging
import sys
import threading
from pathlib import Path

log = logging.getLogger('musubi')

WINDOW_CLASS = 'MusubiTrayWindow'
ICON = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'musubi.ico'

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_CANCELMODE = 0x001F
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_OPEN = WM_APP + 2
WM_STATUS = WM_APP + 3
ID_STATUS = 1
ID_OPEN = 2
ID_QUIT = 3
MF_STRING = 0
MF_GRAYED = 1
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
IDI_APPLICATION = 32512
SM_CXSMICON = 49
SM_CYSMICON = 50


class GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16), ('Data3', ctypes.c_uint16),
                ('Data4', ctypes.c_ubyte * 8)]


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [('hWnd', ctypes.c_void_p), ('message', ctypes.c_uint), ('wParam', ctypes.c_size_t),
                ('lParam', ctypes.c_ssize_t), ('time', ctypes.c_uint32), ('pt', POINT)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint32), ('hWnd', ctypes.c_void_p), ('uID', ctypes.c_uint),
                ('uFlags', ctypes.c_uint), ('uCallbackMessage', ctypes.c_uint), ('hIcon', ctypes.c_void_p),
                ('szTip', ctypes.c_wchar * 128), ('dwState', ctypes.c_uint32), ('dwStateMask', ctypes.c_uint32),
                ('szInfo', ctypes.c_wchar * 256), ('uVersion', ctypes.c_uint),
                ('szInfoTitle', ctypes.c_wchar * 64), ('dwInfoFlags', ctypes.c_uint32), ('guidItem', GUID),
                ('hBalloonIcon', ctypes.c_void_p)]


HANDLE = ctypes.c_void_p
TEXT = ctypes.c_wchar_p
UINT = ctypes.c_uint
INT = ctypes.c_int
WPARAM = ctypes.c_size_t
LPARAM = LRESULT = ctypes.c_ssize_t
# Return type, then argument types. Without them ctypes cuts 64-bit handles down to int.
USER32 = {
    'FindWindowW': (HANDLE, TEXT, TEXT),
    'PostMessageW': (INT, HANDLE, UINT, WPARAM, LPARAM),
    'RegisterWindowMessageW': (UINT, TEXT),
    'RegisterClassExW': (ctypes.c_uint16, HANDLE),
    'UnregisterClassW': (INT, TEXT, HANDLE),
    'CreateWindowExW': (HANDLE, ctypes.c_uint32, TEXT, TEXT, ctypes.c_uint32, INT, INT, INT, INT, HANDLE, HANDLE,
                        HANDLE, HANDLE),
    'DestroyWindow': (INT, HANDLE),
    'DefWindowProcW': (LRESULT, HANDLE, UINT, WPARAM, LPARAM),
    'GetMessageW': (INT, HANDLE, HANDLE, UINT, UINT),
    'TranslateMessage': (INT, HANDLE),
    'DispatchMessageW': (LRESULT, HANDLE),
    'PostQuitMessage': (None, INT),
    'GetSystemMetrics': (INT, INT),
    'LoadImageW': (HANDLE, HANDLE, TEXT, UINT, INT, INT, UINT),
    'LoadIconW': (HANDLE, HANDLE, HANDLE),
    'DestroyIcon': (INT, HANDLE),
    'CreatePopupMenu': (HANDLE,),
    'AppendMenuW': (INT, HANDLE, UINT, WPARAM, TEXT),
    'SetMenuDefaultItem': (INT, HANDLE, UINT, UINT),
    'DestroyMenu': (INT, HANDLE),
    'GetCursorPos': (INT, HANDLE),
    'SetForegroundWindow': (INT, HANDLE),
    'TrackPopupMenu': (INT, HANDLE, UINT, INT, INT, INT, HANDLE, HANDLE),
}
SHELL32 = {'Shell_NotifyIconW': (INT, ctypes.c_uint32, HANDLE)}
KERNEL32 = {'GetModuleHandleW': (HANDLE, TEXT)}


def _library(name, functions):
    # A private handle, so these types don't change what other modules see through ctypes.windll.
    library = ctypes.WinDLL(name, use_last_error=True)
    for function, (restype, *argtypes) in functions.items():
        getattr(library, function).restype = restype
        getattr(library, function).argtypes = argtypes
    return library


def wake_existing(class_name=WINDOW_CLASS):
    """Asks a running Musubi to show its window, so a second launch doesn't start a second receiver."""
    if sys.platform != 'win32':
        return False
    user32 = _library('user32', USER32)
    hwnd = user32.FindWindowW(class_name, None)
    if not hwnd:
        return False
    user32.PostMessageW(hwnd, WM_OPEN, 0, 0)
    return True


class Tray:
    """The icon and its menu, on a thread of their own. The callbacks run on that thread."""

    def __init__(self, on_open, on_quit, icon_path=ICON, class_name=WINDOW_CLASS):
        if sys.platform != 'win32':
            raise OSError('Tray is only available on Windows.')
        self.on_open = on_open
        self.on_quit = on_quit
        self.icon_path = icon_path
        self.class_name = class_name
        self.status = 'Musubi'
        self.hwnd = None
        self.error = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(5) or self.error:
            raise self.error or OSError('The tray icon did not start in time.')

    def set_status(self, text):
        if text == self.status:
            return
        self.status = text
        hwnd = self.hwnd
        if hwnd:
            self._user32.PostMessageW(hwnd, WM_STATUS, 0, 0)

    def close(self):
        hwnd, self.hwnd = self.hwnd, None
        if not hwnd:
            return
        # An open menu has its own message loop, which has to end first.
        self._user32.PostMessageW(hwnd, WM_CANCELMODE, 0, 0)
        self._user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if threading.current_thread() is not self.thread:
            self.thread.join(2)

    def _run(self):
        try:
            self._serve()
        except Exception as ex:
            if self.ready.is_set():
                log.exception('tray stopped')
            else:
                # The hub carries on without a tray after an OSError, so every startup failure becomes one.
                self.error = ex if isinstance(ex, OSError) else OSError(str(ex))
        finally:
            self.ready.set()

    def _serve(self):
        user32 = self._user32 = _library('user32', USER32)
        shell32 = _library('shell32', SHELL32)
        kernel32 = _library('kernel32', KERNEL32)
        # Explorer broadcasts this after a restart, which empties the notification area.
        taskbar_created = user32.RegisterWindowMessageW('TaskbarCreated')
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HANDLE, UINT, WPARAM, LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [('cbSize', UINT), ('style', UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', INT),
                        ('cbWndExtra', INT), ('hInstance', HANDLE), ('hIcon', HANDLE), ('hCursor', HANDLE),
                        ('hbrBackground', HANDLE), ('lpszMenuName', TEXT), ('lpszClassName', TEXT),
                        ('hIconSm', HANDLE)]

        nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), uID=1, uCallbackMessage=WM_TRAY)

        def notify(action, flags):
            nid.uFlags = flags
            nid.szTip = self.status[:127]
            return shell32.Shell_NotifyIconW(action, ctypes.byref(nid))

        def wnd_proc(hwnd, msg, wparam, lparam):
            try:
                if msg == WM_TRAY:
                    if lparam == WM_LBUTTONUP:
                        self.on_open()
                    elif lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                        self._show_menu(user32, hwnd)
                    return 0
                if msg == WM_OPEN:
                    self.on_open()
                    return 0
                if msg == WM_STATUS:
                    notify(NIM_MODIFY, NIF_TIP)
                    return 0
                if taskbar_created and msg == taskbar_created:
                    notify(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP)
                    return 0
                if msg == WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                    user32.PostQuitMessage(0)
                    return 0
            except Exception:
                log.exception('tray message %s', msg)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        # Windows keeps only the raw pointer, so this reference stops the callback being collected.
        self._wndproc = WNDPROC(wnd_proc)
        hinst = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc, hInstance=hinst,
                                   lpszClassName=self.class_name)
        if not user32.RegisterClassExW(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        hwnd = custom_icon = None
        try:
            # Top level, not message-only: those get no broadcasts and FindWindowW skips them.
            hwnd = user32.CreateWindowExW(0, self.class_name, 'Musubi', 0, 0, 0, 0, 0, None, None, hinst, None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            custom_icon = user32.LoadImageW(None, str(self.icon_path), IMAGE_ICON,
                                            user32.GetSystemMetrics(SM_CXSMICON),
                                            user32.GetSystemMetrics(SM_CYSMICON), LR_LOADFROMFILE)
            nid.hWnd = hwnd
            nid.hIcon = custom_icon or user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
            if not notify(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP):
                raise OSError('Windows did not accept the tray icon.')
            self.hwnd = hwnd
            log.info('tray icon added')
            self.ready.set()
            msg = MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if hwnd:
                user32.DestroyWindow(hwnd)  # Does nothing after a normal close.
            if custom_icon:
                user32.DestroyIcon(custom_icon)
            user32.UnregisterClassW(self.class_name, hinst)
            self.hwnd = None

    def _show_menu(self, user32, hwnd):
        menu = user32.CreatePopupMenu()
        # A lone & would underline the next letter of a phone's name instead of showing.
        user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, ID_STATUS, self.status.replace('&', '&&'))
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_OPEN, 'Open')
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, 'Quit')
        user32.SetMenuDefaultItem(menu, ID_OPEN, 0)
        point = POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # Foreground first and a null message after, or the menu won't close on a click elsewhere.
        user32.SetForegroundWindow(hwnd)
        command = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if command == ID_OPEN:
            self.on_open()
        elif command == ID_QUIT:
            self.on_quit()
