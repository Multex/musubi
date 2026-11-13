# PyInstaller needs a spec file to be told which GTK version to collect.
import os

here = SPECPATH
a = Analysis([os.path.join(here, 'gtk_spike.py')],
             pathex=[os.path.dirname(here), os.path.join(os.path.dirname(here), 'linux'), here],
             # The drawings get their cairo context through this module, which nothing imports by name.
             hiddenimports=['gi._gi_cairo', 'cairo'],
             # Adwaita's icons are the title bar buttons. No GTK themes or translations: the look is the window's own.
             hooksconfig={'gi': {'module-versions': {'Gtk': '4.0', 'Gdk': '4.0'},
                                 'icons': ['Adwaita'], 'themes': [], 'languages': []}})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name='Musubi-gtk-test', console=False)
COLLECT(exe, a.binaries, a.datas, name='Musubi-gtk-test')
