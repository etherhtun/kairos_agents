"""
jobs/setup_win.py
=================
GUI setup wizard for Kairos Agent — Windows version.
Uses tkinter (built-in) for dialogs and file picker.
"""

import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog

from jobs import creds

PORTAL_URL = 'https://kairos-f3w.pages.dev/connect-tiger'


def _root() -> tk.Tk:
    """Hidden Tk root window — required for dialogs."""
    r = tk.Tk()
    r.withdraw()
    r.lift()
    r.attributes('-topmost', True)
    return r


def run_setup() -> bool:
    """Run the setup wizard. Returns True when all credentials are saved."""

    # ── Welcome ───────────────────────────────────────────────────────────────
    r = _root()
    messagebox.showinfo(
        'Kairos Agent — Setup',
        'Welcome! You need two things to get started:\n\n'
        '1. Your upload token — from the Kairos portal\n'
        '2. tiger_openapi_config.properties — from Tiger app\n\n'
        f'Open {PORTAL_URL} in your browser to get your token.',
        parent=r,
    )
    r.destroy()

    # ── Step 1: Upload token ──────────────────────────────────────────────────
    r = _root()
    token = simpledialog.askstring(
        'Step 1 — Upload Token',
        f'Paste your upload token from:\n{PORTAL_URL}',
        initialvalue=creds.get('upload_token') or '',
        parent=r,
    )
    r.destroy()

    if token is None:
        return False  # cancelled
    token = token.strip()
    if not token:
        r = _root()
        messagebox.showerror('Setup', 'Upload token is required.', parent=r)
        r.destroy()
        return False

    creds.set('upload_token', token)

    # ── Step 2: Tiger config file ─────────────────────────────────────────────
    if creds.has_tiger_props():
        r = _root()
        replace = messagebox.askyesno(
            'Step 2 — Tiger Config',
            'Tiger config file already saved. Replace it?',
            parent=r,
        )
        r.destroy()
        if not replace:
            return _finish()

    # Security notice
    r = _root()
    messagebox.showwarning(
        '⚠ Security Notice',
        'Your tiger_openapi_config.properties file contains sensitive '
        'API credentials including your private key.\n\n'
        'Kairos stores this file locally on your PC only '
        '(%USERPROFILE%\\.kairos-agent\\). It is never uploaded to any server.\n\n'
        'Only import a file you downloaded directly from Tiger app. '
        'Do not share this file with anyone.',
        parent=r,
    )
    r.destroy()

    # Instruction
    r = _root()
    messagebox.showinfo(
        'Step 2 — Tiger Config',
        'Next, select your tiger_openapi_config.properties file.\n\n'
        'Download it from Tiger app:\n'
        'Tiger app → API Management → Download Config',
        parent=r,
    )
    r.destroy()

    # File picker
    r = _root()
    path = filedialog.askopenfilename(
        title='Select tiger_openapi_config.properties',
        filetypes=[('Properties files', '*.properties'), ('All files', '*.*')],
        parent=r,
    )
    r.destroy()

    if not path:
        if creds.has_tiger_props():
            return _finish()
        r = _root()
        messagebox.showerror('Setup', 'Tiger config file is required.', parent=r)
        r.destroy()
        return False

    if not path.endswith('.properties'):
        r = _root()
        messagebox.showerror('Wrong file', 'Please select tiger_openapi_config.properties', parent=r)
        r.destroy()
        return False

    if not creds.save_tiger_props(path):
        r = _root()
        messagebox.showerror('Error', 'Could not copy the file. Check permissions.', parent=r)
        r.destroy()
        return False

    return _finish()


def _finish() -> bool:
    r = _root()
    if creds.is_complete():
        messagebox.showinfo('Setup Complete ✓', 'All set! Click "Sync now" to sync your trades.', parent=r)
        r.destroy()
        return True
    else:
        messagebox.showerror('Setup Incomplete', 'Something is missing. Run Setup again.', parent=r)
        r.destroy()
        return False
