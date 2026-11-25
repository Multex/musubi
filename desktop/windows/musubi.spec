# PyInstaller needs a spec file to be told which GTK version to collect.
# hub.py imports gtk_window, and gtk_window imports window, inside functions or after setup; plain import statements
# still let PyInstaller find both, so they need no hiddenimports.
import os

here = SPECPATH
desktop = os.path.dirname(here)
a = Analysis([os.path.join(here, 'hub.py')],
             pathex=[desktop, os.path.join(desktop, 'linux'), here],
             # pad.py loads it from _MEIPASS.
             binaries=[(os.path.join(here, 'ViGEmClient.dll'), '.')],
             # The drawings get their cairo context through this module, which nothing imports by name.
             hiddenimports=['gi._gi_cairo', 'cairo'],
             # Adwaita's icons are the title bar buttons. No GTK themes or translations: the look is the window's own.
             hooksconfig={'gi': {'module-versions': {'Gtk': '4.0', 'Gdk': '4.0'},
                                 'icons': ['Adwaita'], 'themes': [], 'languages': []}})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name='Musubi', console=False)
COLLECT(exe, a.binaries, a.datas, name='Musubi')
