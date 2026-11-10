"""A tray icon over D-Bus: GTK4 has no tray API, and the usual library needs GTK3 in the same process."""
import os

from gi.repository import Gio, GLib

WATCHER = 'org.kde.StatusNotifierWatcher'
ITEM_PATH = '/StatusNotifierItem'
MENU_PATH = '/MenuBar'
ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  <signal name="NewTitle"/>
  <signal name="NewToolTip"/>
</interface></node>"""
MENU_XML = """<node><interface name="com.canonical.dbusmenu">
  <property name="Version" type="u" access="read"/>
  <method name="GetLayout">
    <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
    <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
  </method>
  <method name="GetGroupProperties">
    <arg type="ai" direction="in"/><arg type="as" direction="in"/>
    <arg type="a(ia{sv})" direction="out"/>
  </method>
  <method name="Event">
    <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/>
  </method>
  <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
  <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
</interface></node>"""
STATUS, OPEN, QUIT = 1, 2, 3


def tray_available():
    """Without a tray host, hiding the window would leave Musubi running with no way back to it."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        reply = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                              'NameHasOwner', GLib.Variant('(s)', (WATCHER,)), GLib.VariantType('(b)'),
                              Gio.DBusCallFlags.NONE, 1000, None)
        return reply[0]
    except GLib.Error:
        return False


class Tray:
    def __init__(self, on_open, on_quit):
        self.on_open = on_open
        self.on_quit = on_quit
        self.status = 'Musubi'
        self.revision = 1
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.bus_name = f'org.kde.StatusNotifierItem-{os.getpid()}-1'
        self.bus.register_object(ITEM_PATH, Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0],
                                 self._item_call, self._item_get, None)
        self.bus.register_object(MENU_PATH, Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0],
                                 self._menu_call, self._menu_get, None)
        self.owner = Gio.bus_own_name_on_connection(self.bus, self.bus_name, Gio.BusNameOwnerFlags.NONE,
                                                    self._watch, None)

    def _watch(self, *_):
        # The tray host can restart, like when the shell reloads, so register again each time its watcher appears.
        self.watcher = Gio.bus_watch_name_on_connection(self.bus, WATCHER, Gio.BusNameWatcherFlags.NONE,
                                                        self._register, None)

    def _register(self, *_):
        self.bus.call(WATCHER, '/StatusNotifierWatcher', WATCHER, 'RegisterStatusNotifierItem',
                      GLib.Variant('(s)', (self.bus_name,)), None, Gio.DBusCallFlags.NONE, -1, None, None)

    def set_status(self, text):
        if text == self.status:
            return
        self.status = text
        self.revision += 1
        for signal in ('NewTitle', 'NewToolTip'):
            self.bus.emit_signal(None, ITEM_PATH, 'org.kde.StatusNotifierItem', signal, None)
        self.bus.emit_signal(None, MENU_PATH, 'com.canonical.dbusmenu', 'LayoutUpdated',
                             GLib.Variant('(ui)', (self.revision, 0)))

    def _item_get(self, _bus, _sender, _path, _iface, name):
        values = {
            'Category': GLib.Variant('s', 'Hardware'),
            'Id': GLib.Variant('s', 'musubi'),
            'Title': GLib.Variant('s', self.status),
            'Status': GLib.Variant('s', 'Active'),
            'IconName': GLib.Variant('s', 'io.github.multex.musubi'),
            'ItemIsMenu': GLib.Variant('b', False),
            'Menu': GLib.Variant('o', MENU_PATH),
            'ToolTip': GLib.Variant('(sa(iiay)ss)', ('input-gaming', [], 'Musubi', self.status)),
        }
        return values.get(name)

    def _item_call(self, _bus, _sender, _path, _iface, method, _params, invocation):
        if method == 'Activate':
            self.on_open()
        invocation.return_value(None)

    def _menu_get(self, _bus, _sender, _path, _iface, name):
        return GLib.Variant('u', 3) if name == 'Version' else None

    def _entry(self, item, label, enabled=True):
        return GLib.Variant('(ia{sv}av)', (item, {
            'label': GLib.Variant('s', label),
            'enabled': GLib.Variant('b', enabled),
        }, []))

    def _menu_call(self, _bus, _sender, _path, _iface, method, params, invocation):
        if method == 'GetLayout':
            children = [self._entry(STATUS, self.status, False), self._entry(OPEN, 'Open'), self._entry(QUIT, 'Quit')]
            invocation.return_value(GLib.Variant('(u(ia{sv}av))', (
                self.revision, (0, {'children-display': GLib.Variant('s', 'submenu')}, children))))
        elif method == 'GetGroupProperties':
            invocation.return_value(GLib.Variant('(a(ia{sv}))', ([],)))
        elif method == 'AboutToShow':
            invocation.return_value(GLib.Variant('(b)', (False,)))
        else:
            item, event = params[0], params[1]
            if event == 'clicked':
                if item == OPEN:
                    GLib.idle_add(self.on_open)
                elif item == QUIT:
                    GLib.idle_add(self.on_quit)
            invocation.return_value(None)
