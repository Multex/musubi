# PyInstaller needs a spec file to be told which GTK version to collect.
import os

here = SPECPATH
a = Analysis([os.path.join(here, 'gtk_spike.py')],
             pathex=[os.path.dirname(here), os.path.join(os.path.dirname(here), 'linux'), here],
             # No icon themes, GTK themes or translations: the window draws its own look and is English only.
             hooksconfig={'gi': {'module-versions': {'Gtk': '4.0', 'Gdk': '4.0'},
                                 'icons': [], 'themes': [], 'languages': []}})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name='Musubi-gtk-test', console=False)
COLLECT(exe, a.binaries, a.datas, name='Musubi-gtk-test')
