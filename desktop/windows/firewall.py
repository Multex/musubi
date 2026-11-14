"""The Windows Firewall rule that lets phones reach Musubi, and what the log should know about the network."""
import subprocess
import sys

from core import PORT

RULE = 'Musubi'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# netsh and PowerShell write piped output in the console code page, which is not UTF-8 on most PCs.
ENCODING = 'oem' if sys.platform == 'win32' else None
DESCRIBE = ("Get-NetConnectionProfile | % { 'network ' + $_.Name + ' ' + $_.NetworkCategory }; "
            "Get-NetFirewallProfile | % { 'firewall ' + $_.Name + ' ' + $_.Enabled }; "
            "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName FirewallProduct -ErrorAction SilentlyContinue"
            " | % { 'other firewall ' + $_.displayName }")


def _run(args, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, encoding=ENCODING, errors='replace',
                          creationflags=NO_WINDOW, timeout=timeout)


def _netsh(*args):
    return _run(['netsh', 'advfirewall', 'firewall', *args])


def rule_ok(exe):
    """True when our rule exists for this exe. Reading rules needs no admin."""
    try:
        result = _netsh('show', 'rule', f'name={RULE}', 'verbose')
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and exe.lower() in result.stdout.lower()


def open_port(exe):
    """Runs as admin. Replaces any rules for this exe with one allow rule for every network type."""
    # Block rules beat allow rules, and the Windows prompt makes a block rule for every network type left unticked.
    _netsh('delete', 'rule', 'name=all', f'program={exe}')
    _netsh('delete', 'rule', f'name={RULE}')
    added = _netsh('add', 'rule', f'name={RULE}', 'dir=in', 'action=allow', f'program={exe}',
                   'protocol=UDP', f'localport={PORT}', 'profile=any')
    return added.returncode == 0


def ask_to_open(exe):
    """Starts this exe again as admin to add the rule. Windows asks the user first."""
    quoted = exe.replace("'", "''")
    try:
        # No timeout: the user may take a while to answer the Windows prompt.
        _run(['powershell', '-NoProfile', '-Command',
              f"Start-Process -FilePath '{quoted}' -ArgumentList '--firewall' -Verb RunAs -Wait -WindowStyle Hidden"],
             timeout=None)
    except (OSError, subprocess.SubprocessError):
        return False
    # PowerShell's exit code says nothing about the rule, so read the rule back.
    return rule_ok(exe)


def describe():
    """Lines for the log: network type, firewall on or off per type, and any antivirus firewall."""
    try:
        result = _run(['powershell', '-NoProfile', '-Command', DESCRIBE])
    except (OSError, subprocess.SubprocessError) as ex:
        return [f'network check failed: {ex}']
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines or [f'network check failed: {result.stderr.strip()[:200]}']
