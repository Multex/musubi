"""What the Linux window reads from system.py, for Windows, where Musubi leaves the firewall and autostart alone."""
import main as win_main
from core import PORT, load_config, save_config  # noqa: F401

AUTOSTART_MISSING = 'Starting with Windows is not available in this test build yet.'


def read_network():
    return win_main.local_ip(), None


def firewall_status():
    return 'unknown', []


def blocked_sources(text):
    return set()


def covers(sources, network):
    return False


def open_port_command(network):
    return []


def autostart_enabled():
    return False


def startup_command():
    return ''


def set_autostart(enabled):
    return AUTOSTART_MISSING
