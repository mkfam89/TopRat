#!/usr/bin/env python3
"""make_autostart.py - enable/disable launching the dashboard server at login.

  python src/ops/make_autostart.py install     # add a hidden launcher to the Windows Startup folder
  python src/ops/make_autostart.py uninstall   # remove it
  python src/ops/make_autostart.py status      # print whether autostart is enabled

Writes a tiny .vbs into your Startup folder that runs dashboard_server.py hidden (no console,
no browser popup) each time you log in. Only touches your own Startup folder.

The dashboard imports this module to offer an autostart toggle in the UI, so install()/uninstall()
return (ok, message) and is_enabled()/supported() report state without side effects.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys

HERE = _paths.ROOT
STARTUP = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows',
                       'Start Menu', 'Programs', 'Startup')
VBS = os.path.join(STARTUP, 'Top Rat Dashboard.vbs')

# Filenames this launcher has had before. Same hazard as the scheduled task: the
# .vbs lives in the user's Startup folder, not in this repo, so renaming the
# constant leaves the old file behind — still launching the dashboard at every
# login, while is_enabled() reports disabled and uninstall() finds nothing to
# remove. Cleaned up on both install and uninstall.
LEGACY_VBS = [os.path.join(STARTUP, 'Job Tracker Dashboard.vbs')]

def supported():
    """Autostart via the Startup folder is a Windows feature (needs APPDATA)."""
    return bool(os.environ.get('APPDATA'))

def is_enabled():
    """True if the current OR a pre-rename launcher is present.

    A legacy file counts: that copy genuinely does start at login, and reporting
    "off" would invite the user to switch it on and end up with two launchers.
    """
    return os.path.exists(VBS) or any(os.path.exists(p) for p in LEGACY_VBS)

def remove_legacy():
    """Delete any pre-rename launcher. Returns the basenames actually removed."""
    gone = []
    for p in LEGACY_VBS:
        try:
            if os.path.exists(p):
                os.remove(p)
                gone.append(os.path.basename(p))
        except OSError:
            pass
    return gone

def install():
    """Create the hidden login launcher. Returns (ok, message)."""
    if not supported():
        return False, 'Autostart is only supported on Windows (no Startup folder found).'
    content = (
        "' Auto-starts the Top Rat dashboard server (hidden) at login.\r\n"
        "' Delete this file (or toggle autostart off in the dashboard) to disable autostart.\r\n"
        'Set WShell = CreateObject("WScript.Shell")\r\n'
        'WShell.CurrentDirectory = "' + HERE + '"\r\n'
        'WShell.Run "python ""' + _paths.script('dashboard_server.py') + '"" --no-browser", 0, False\r\n'
    )
    try:
        os.makedirs(STARTUP, exist_ok=True)
        with open(VBS, 'w', encoding='ascii', errors='replace') as f:
            f.write(content)
        # Only after the new one is written, so a failure never leaves zero launchers.
        gone = remove_legacy()
        msg = 'Autostart enabled. Launcher: ' + VBS
        if gone:
            msg += ' Removed the old launcher (%s), so login starts it once.' % ', '.join(gone)
        return True, msg
    except Exception as e:
        return False, 'Could not enable autostart: ' + str(e)

def uninstall():
    """Remove the login launcher. Returns (ok, message)."""
    try:
        # Legacy first and unconditionally: "off" must mean nothing starts at login.
        gone = remove_legacy()
        if os.path.exists(VBS):
            os.remove(VBS)
            gone.append(os.path.basename(VBS))
        if gone:
            return True, 'Autostart disabled (removed %s).' % ', '.join(gone)
        return True, 'Autostart was not enabled (nothing to remove).'
    except Exception as e:
        return False, 'Could not disable autostart: ' + str(e)

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'install'
    if cmd == 'status':
        print('Autostart is ' + ('ENABLED' if is_enabled() else 'DISABLED')
              + (' (supported)' if supported() else ' (NOT supported on this OS)'))
    else:
        ok, msg = (uninstall() if cmd == 'uninstall' else install())
        print(msg)
