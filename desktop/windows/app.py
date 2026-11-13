"""Musubi's receiver for Windows, in a small window."""
import ctypes
import logging
import os
import platform
import socket
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox

import main as win_main
from main import BUILD, LOG_FILE, core

BACKGROUND = '#0D0C0A'
BORDER = '#332E28'
TEXT = '#E6DEC8'
MUTED = '#8F8573'
ACTIVE = '#FF9E1B'
ALERT = '#E04F3E'
FONT = ('Segoe UI', 10)
WIDTH = 392  # 440 px window less 24 px padding on each side
# Explorer on Windows. webbrowser with a file address did nothing there, and it is only kept for trying this on Linux.
OPEN_FOLDER = getattr(os, 'startfile', webbrowser.open)
log = logging.getLogger('musubi')


def rows(status, capturing):
    """The Phone, Controller, Virtual pad and Sound values, and whether the pad is on, from a status dict or None."""
    pad = bool(status and status['pad'])
    sound = 'To the phone' if capturing else 'Off'
    phone = status and status['phone']
    if not phone:
        return 'Not connected', 'None', 'On' if pad else 'Off', pad, sound
    name = phone['name'] or phone['ip']
    if phone['rtt'] is not None:
        name += f" · {phone['rtt']} ms"
    return name, phone['controller'] or 'None', 'On' if pad else 'Off', pad, sound


def label(parent, text='', fg=TEXT, font=FONT, **options):
    return tk.Label(parent, text=text, fg=fg, bg=BACKGROUND, font=font, anchor='w', justify='left', **options)


def button(parent, text, command, primary):
    bg, fg, hover = (TEXT, BACKGROUND, '#F4EDDA') if primary else (BACKGROUND, TEXT, '#171512')
    outline = tk.Frame(parent, bg=BACKGROUND if primary else BORDER, padx=1, pady=1)
    widget = tk.Button(outline, text=text, command=command, font=FONT, bg=bg, fg=fg, activebackground=hover,
                       activeforeground=fg, relief='flat', bd=0, padx=14, pady=6, cursor='hand2',
                       highlightthickness=2, highlightcolor=ACTIVE, highlightbackground=bg)
    widget.pack()
    # tk on Windows shows activebackground only while pressed, so hover is done by hand.
    widget.bind('<Enter>', lambda event: widget.config(bg=hover, highlightbackground=hover))
    widget.bind('<Leave>', lambda event: widget.config(bg=bg, highlightbackground=bg))
    widget.bind('<Return>', lambda event: widget.invoke())
    return outline


def hairline(parent, row):
    tk.Frame(parent, bg=BORDER, height=1).grid(row=row, column=0, sticky='ew', pady=16)


def run(make_pad, port, config_path):
    log.info('build %s, %s, python %s, %s MB', BUILD, platform.platform(), platform.python_version(),
             win_main.memory_mb() if sys.platform == 'win32' else None)
    driver_error = win_main.check_driver()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(('0.0.0.0', port))
    except OSError as ex:
        log.error('bind: %s', ex)
        sock.close()
        sock = None
    address = win_main.local_ip()
    if sock:
        log.info('listening, local address %s', address)

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Without it Windows scales the window up and blurs the text.
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    root.title('Musubi')
    root.configure(bg=BACKGROUND)
    root.resizable(False, False)
    root.report_callback_exception = lambda *error: log.error('window error', exc_info=error)
    frame = tk.Frame(root, bg=BACKGROUND, padx=24, pady=24)
    frame.pack()
    frame.columnconfigure(0, minsize=WIDTH)

    label(frame, "This PC's address", MUTED).grid(row=0, column=0, sticky='w')
    label(frame, address or 'Unknown', font=('Consolas', 26)).grid(row=1, column=0, sticky='w', pady=(2, 2))
    if not address:
        label(frame, 'Check your network settings.', MUTED).grid(row=2, column=0, sticky='w')
    label(frame, 'Type it in the Musubi app on your phone.', MUTED).grid(row=3, column=0, sticky='w')
    hairline(frame, 4)

    table = tk.Frame(frame, bg=BACKGROUND)
    table.grid(row=5, column=0, sticky='ew')
    table.columnconfigure(0, minsize=110)
    values = []
    for row, name in enumerate(('Phone', 'Controller', 'Virtual pad', 'Sound')):
        label(table, name, MUTED).grid(row=row, column=0, sticky='w', pady=3)
        values.append(label(table, wraplength=WIDTH - 110))
        values[-1].grid(row=row, column=1, sticky='w', pady=3)

    pending_box = tk.Frame(frame, bg=BACKGROUND)
    pending_box.grid(row=6, column=0, sticky='ew', pady=(16, 0))
    pending_box.columnconfigure(0, weight=1)
    problem_box = tk.Frame(frame, bg=BACKGROUND)
    problem_box.grid(row=7, column=0, sticky='ew', pady=(16, 0))
    hairline(frame, 8)

    footer = tk.Frame(frame, bg=BACKGROUND)
    footer.grid(row=9, column=0, sticky='ew')
    footer.columnconfigure(0, weight=1)
    label(footer, f'Test build {BUILD}', MUTED).grid(row=0, column=0, sticky='w')
    button(footer, 'Open log folder', lambda: OPEN_FOLDER(str(LOG_FILE.parent)), False).grid(row=0, column=1)

    notice = ''
    built = [None, None]

    def say(text):
        nonlocal notice
        notice = text

    def render(status, notice, capturing, sound_error):
        phone, controller, pad, pad_on, sound = rows(status, capturing)
        connected = bool(status and status['phone'])
        values[0].config(text=phone, fg=TEXT if connected else MUTED)
        values[1].config(text=controller, fg=TEXT if connected and controller != 'None' else MUTED)
        values[2].config(text=pad, fg=ACTIVE if pad_on else MUTED)
        values[3].config(text=sound, fg=TEXT if capturing else MUTED)

        pending = [(phone['ip'], phone['name']) for phone in status['pending']] if status else []
        problems = []
        if driver_error:
            problems.append((f'{driver_error} The controller will not work until that is fixed. '
                             'Then start Musubi again.', ALERT))
        if not sock:
            problems.append((f'Another program is using UDP port {port}. Is Musubi already running?', ALERT))
        if status and status['pad_error']:
            problems.append((f"The virtual pad could not start: {status['pad_error']}", ALERT))
        if sound_error:
            problems.append((sound_error, ALERT))
        if notice:
            problems.append((notice, TEXT))
        # The ping changes every second. Rebuilding these then would take the keyboard focus off Allow.
        if [pending, problems] == built:
            return
        built[:] = pending, problems
        for child in pending_box.winfo_children() + problem_box.winfo_children():
            child.destroy()
        for row, (ip, name) in enumerate(pending):
            label(pending_box, f"{name or 'A phone'} ({ip}) wants to connect.",
                  wraplength=WIDTH - 100).grid(row=row, column=0, sticky='w', pady=3)
            button(pending_box, 'Allow', lambda ip=ip: console.post('allow', ip), True).grid(
                row=row, column=1, sticky='e', pady=3)
        pending_box.grid() if pending else pending_box.grid_remove()
        for text, colour in problems:
            label(problem_box, text, colour, wraplength=WIDTH).pack(anchor='w', pady=2)
        problem_box.grid() if problems else problem_box.grid_remove()

    console = thread = None
    if sock:
        console = win_main.Console(sock, make_pad, config_path, say=lambda text: None)

        def receive():
            try:
                win_main.serve(console, say)
            except Exception:
                log.exception('receiver crashed')

        # shortcut: the receiver shares this process with the window, split it into its own process if the log shows slow loop steps
        thread = threading.Thread(target=receive, daemon=True)
        thread.start()

    shown = None

    def refresh():
        nonlocal shown
        now = (console.shown, '' if console.heard else notice, console.sound.capturing, console.sound.error)
        if now != shown:
            shown = now
            render(*now)
        root.after(250, refresh)

    def close():
        if thread:
            console.post('quit')
            thread.join(1)
        root.destroy()

    render(None, '', False, None)
    if console:
        refresh()
    root.protocol('WM_DELETE_WINDOW', close)
    log.info('window open')
    root.mainloop()


def main(make_pad=None, port=core.PORT, config_path=core.CONFIG_FILE):
    win_main.setup_log()
    try:
        run(make_pad, port, config_path)
    except Exception:
        log.exception('crashed')
        messagebox.showerror('Musubi', f'Musubi stopped because of an error. Please send this file: {LOG_FILE}')


if __name__ == '__main__':
    main()
