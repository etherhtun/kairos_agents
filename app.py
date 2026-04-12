"""
Kairos Agent — cross-platform system tray app
macOS + Windows

Single entry point for all platforms.
Setup wizard and dashboard are served via local browser at http://127.0.0.1:7432

Run:  python3 app.py
Dist: pyinstaller kairos.spec         (macOS)
      pyinstaller kairos_win.spec     (Windows)
"""

import json
import pathlib
import datetime
import threading
import webbrowser
import sys
import os

# ── SSL fix — before ANY network import ───────────────────────────────────────
# Strategy 1: truststore uses the OS native cert store (Keychain / Windows cert store).
# Strategy 2: certifi fallback with ssl.create_default_context monkey-patch.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    try:
        import ssl, certifi
        _ca = certifi.where()
        os.environ['SSL_CERT_FILE']      = _ca
        os.environ['REQUESTS_CA_BUNDLE'] = _ca
        _orig_ssl = ssl.create_default_context
        def _ssl_patch(*a, **kw):
            if not any(k in kw for k in ('cafile', 'cadata', 'capath')):
                kw['cafile'] = _ca
            return _orig_ssl(*a, **kw)
        ssl.create_default_context = _ssl_patch
    except Exception:
        pass
# ─────────────────────────────────────────────────────────────────────────────

import pystray
from PIL import Image, ImageDraw

import server
from jobs.upload_sync import run_sync, last_data_age_hours, LOG_FILE

STATE_FILE     = pathlib.Path.home() / '.kairos-agent' / 'state.json'
PORTAL_URL     = 'https://kairos-f3w.pages.dev'
SERVER_PORT    = 7432
AUTO_SYNC_HOUR = 16
AUTO_SYNC_MIN  = 30


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {'last_sync': None, 'auto': True, 'setup_done': False}


def save_state(s: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2))


_state     = load_state()
_sync_lock = threading.Lock()


# ── Tray icon image ───────────────────────────────────────────────────────────

def _make_icon(syncing: bool = False) -> Image.Image:
    """Create a simple 64×64 tray icon — green circle with K."""
    size  = 64
    color = (255, 200, 0) if syncing else (0, 200, 100)
    img   = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(img)
    draw.ellipse([4, 4, size - 4, size - 4], fill=color)
    # Draw letter K manually (no font file dependency)
    cx, cy = size // 2, size // 2
    lw = 4
    draw.rectangle([cx - 12, cy - 18, cx - 12 + lw, cy + 18], fill=(0, 0, 0))
    draw.polygon([(cx - 8, cy), (cx + 14, cy - 18), (cx + 14, cy - 18 + lw),
                  (cx - 8, cy + lw)], fill=(0, 0, 0))
    draw.polygon([(cx - 8, cy), (cx + 14, cy + 18), (cx + 14, cy + 18 - lw),
                  (cx - 8, cy - lw)], fill=(0, 0, 0))
    return img


# ── Sync ──────────────────────────────────────────────────────────────────────

def _do_sync(reason: str = 'manual') -> None:
    if not _sync_lock.acquire(blocking=False):
        return
    try:
        result = run_sync()
        if result['ok']:
            _state['last_sync'] = datetime.date.today().isoformat()
            save_state(_state)
    finally:
        _sync_lock.release()


def _in_autosync_window() -> bool:
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    return (now.hour == AUTO_SYNC_HOUR and
            AUTO_SYNC_MIN <= now.minute < AUTO_SYNC_MIN + 5)


def _tick() -> None:
    """Background thread: fires auto-sync at 4:30 PM on weekdays."""
    while True:
        threading.Event().wait(60)
        if _state.get('auto') and _in_autosync_window():
            today = datetime.date.today().isoformat()
            if _state.get('last_sync') != today:
                _do_sync(reason='auto')


# ── Menu actions ──────────────────────────────────────────────────────────────

def _open_dashboard(_icon=None, _item=None):
    webbrowser.open(f'http://127.0.0.1:{SERVER_PORT}/')


def _sync_now(_icon=None, _item=None):
    threading.Thread(target=_do_sync, kwargs={'reason': 'manual'}, daemon=True).start()


def _toggle_auto(_icon=None, _item=None):
    _state['auto'] = not _state.get('auto', True)
    save_state(_state)


def _open_portal(_icon=None, _item=None):
    webbrowser.open(PORTAL_URL)


def _open_setup(_icon=None, _item=None):
    webbrowser.open(f'http://127.0.0.1:{SERVER_PORT}/setup')


def _quit(icon, _item=None):
    icon.stop()


def _build_menu():
    return pystray.Menu(
        pystray.MenuItem('Kairos Agent', None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Open Dashboard', _open_dashboard, default=True),
        pystray.MenuItem('Sync Now',       _sync_now),
        pystray.MenuItem(
            'Auto-sync at 4:30 PM',
            _toggle_auto,
            checked=lambda item: _state.get('auto', True),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Open Portal',         _open_portal),
        pystray.MenuItem('Setup / Reconfigure', _open_setup),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit', _quit),
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Start web dashboard in background thread
    server.start(
        port      = SERVER_PORT,
        state     = _state,
        save_fn   = save_state,
        sync_fn   = lambda: threading.Thread(
            target=_do_sync, kwargs={'reason': 'manual'}, daemon=True
        ).start(),
        log_file  = LOG_FILE,
    )

    # Auto-sync tick
    threading.Thread(target=_tick, daemon=True).start()

    # First launch → open setup page in browser after 1.5s
    if not _state.get('setup_done'):
        threading.Timer(
            1.5, lambda: webbrowser.open(f'http://127.0.0.1:{SERVER_PORT}/setup')
        ).start()

    icon = pystray.Icon('kairos', _make_icon(), 'Kairos Agent', _build_menu())
    icon.run()


if __name__ == '__main__':
    main()
