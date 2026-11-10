"""What the desktop app needs from the system: saved settings, the firewall, the network and autostart."""
import ipaddress
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # core.py is shared with Windows, one folder up

from core import CONFIG_DIR, PORT, load_config, save_config  # noqa: F401 (gui.py reads the config through here)

UNIT_NAME = 'musubi-desktop.service'
UNIT_FILE = CONFIG_DIR / 'systemd' / 'user' / UNIT_NAME
GUI_SCRIPT = Path(__file__).resolve().parent / 'gui.py'
UFW_RULES = Path('/etc/ufw/user.rules')
UFW_CONF = Path('/etc/ufw/ufw.conf')


def ufw_allowed_sources(rules_text, port=PORT):
    """Networks that user.rules lets reach this UDP port."""
    sources = []
    for line in rules_text.splitlines():
        fields = line.split()
        # ### tuple ### allow udp 9999 0.0.0.0/0 any 192.168.1.16 in
        if fields[:3] != ['###', 'tuple', '###'] or len(fields) < 10:
            continue
        action, protocol, destination_port, _, _, source, direction = fields[3:10]
        if action == 'allow' and protocol in ('udp', 'any') and destination_port == str(port) and direction == 'in':
            try:
                sources.append(ipaddress.ip_network(source, strict=False))
            except ValueError:
                continue
    return sources


def covers(sources, network):
    try:
        network = ipaddress.ip_network(network, strict=False)
    except ValueError:
        return False
    return any(network.version == source.version and network.subnet_of(source) for source in sources)


def ufw_enabled(conf_text):
    return any(line.strip().lower() == 'enabled=yes' for line in conf_text.splitlines())


def firewall_status():
    """Return ('none' | 'ufw' | 'unknown', sources). Only ufw can be read and opened from here."""
    try:
        if not ufw_enabled(UFW_CONF.read_text()):
            return 'none', []
        return 'ufw', ufw_allowed_sources(UFW_RULES.read_text())
    except FileNotFoundError:
        return 'none', []
    except OSError:
        return 'unknown', []


def blocked_sources(log_text, port=PORT):
    """Addresses in kernel log lines where ufw dropped traffic to this port."""
    sources = set()
    for line in log_text.splitlines():
        if 'UFW BLOCK' not in line or f'DPT={port} ' not in line + ' ':
            continue
        for field in line.split():
            if field.startswith('SRC='):
                sources.add(field[4:])
    return sources


def default_network(route_json, addr_json):
    """Return (this PC's IP, its private network), or (None, None) with no usable network."""
    try:
        routes = json.loads(route_json)
        device = routes[0]['dev']
        for info in json.loads(addr_json):
            if info['ifname'] != device:
                continue
            for address in info['addr_info']:
                interface = ipaddress.ip_interface(f"{address['local']}/{address['prefixlen']}")
                # Never offer to open the port to a public network.
                if interface.version == 4 and interface.ip.is_private:
                    return str(interface.ip), str(interface.network)
    except (ValueError, KeyError, IndexError, TypeError):
        pass
    return None, None


def read_network():
    try:
        route = subprocess.run(['ip', '-j', 'route', 'show', 'default'], capture_output=True, text=True, timeout=2)
        addr = subprocess.run(['ip', '-j', '-4', 'addr'], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None, None
    return default_network(route.stdout, addr.stdout)


def open_port_command(network):
    return ['ufw', 'allow', 'from', network, 'to', 'any', 'port', str(PORT), 'proto', 'udp']


def startup_command():
    return shlex.join([sys.executable, str(GUI_SCRIPT), '--hidden'])


def autostart_unit():
    return f"""[Unit]
Description=Musubi desktop app
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart={startup_command()}
# The display may not be ready the moment the session starts.
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
"""


def _systemctl(*args):
    return subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True, timeout=10)


def autostart_enabled():
    try:
        return _systemctl('is-enabled', UNIT_NAME).stdout.strip() == 'enabled'
    except (OSError, subprocess.SubprocessError):
        return False


def set_autostart(enabled):
    """Return an error message, or None if it worked."""
    try:
        if enabled:
            UNIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            UNIT_FILE.write_text(autostart_unit())
            result = _systemctl('enable', UNIT_NAME)
        else:
            result = _systemctl('disable', UNIT_NAME)
            UNIT_FILE.unlink(missing_ok=True)
        _systemctl('daemon-reload')
    except (OSError, subprocess.SubprocessError) as ex:
        return str(ex)
    return None if result.returncode == 0 else result.stderr.strip() or 'systemctl failed'
