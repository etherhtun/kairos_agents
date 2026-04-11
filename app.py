"""
Kairos Agent — macOS menubar app
Syncs Tiger Brokers trades → Cloudflare R2 → Kairos portal.

Run:  python3 app.py
Dist: pyinstaller kairos.spec
"""

import json
import pathlib
import datetime
import threading
import subprocess
import sys
import os

# ── SSL fix — must run before ANY network import (PyInstaller bundles lack CA certs) ──
try:
    import ssl, certifi
    _ca = certifi.where()
    os.environ['SSL_CERT_FILE']      = _ca
    os.environ['REQUESTS_CA_BUNDLE'] = _ca
    # Monkey-patch ssl.create_default_context so every HTTP library picks up certifi
    _orig_ssl_ctx = ssl.create_default_context
    def _ssl_ctx_patch(*args, **kwargs):
        if not any(k in kwargs for k in ('cafile', 'cadata', 'capath')):
            kwargs['cafile'] = _ca
        return _orig_ssl_ctx(*args, **kwargs)
    ssl.create_default_context = _ssl_ctx_patch
except Exception:
    pass
# ─────────────────────────────────────────────────────────────────────────────

import rumps

from jobs.upload_sync import run_sync, last_data_age_hours, LOG_FILE
from jobs.setup import run_setup

STATE_FILE  = pathlib.Path.home() / '.kairos-agent' / 'state.json'
PORTAL_URL  = 'https://kairos-f3w.pages.dev'
AGENT_DIR   = pathlib.Path.home() / '.kairos-agent'

AUTO_SYNC_HOUR = 16
AUTO_SYNC_MIN  = 30


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


class KairosApp(rumps.App):
    def __init__(self):
        super().__init__('Kairos', icon=None, quit_button=None)
        self.state      = load_state()
        self._sync_lock = threading.Lock()

        self.menu = [
            rumps.MenuItem('— Kairos Agent —'),
            rumps.MenuItem('Last sync: …', callback=None),
            rumps.MenuItem('Sync now',              callback=self.on_sync_now),
            rumps.MenuItem('Auto-sync at 4:30 PM',  callback=self.on_toggle_auto),
            None,
            rumps.MenuItem('Open portal',  callback=self.on_open_portal),
            rumps.MenuItem('View logs',    callback=self.on_view_logs),
            None,
            rumps.MenuItem('Setup / reconfigure', callback=self.on_setup),
            None,
            rumps.MenuItem('Quit', callback=rumps.quit_application),
        ]

        self.refresh_status()

        self._timer = rumps.Timer(self.tick, 60)
        self._timer.start()

        # First launch — run setup if not done (timer fires on main run loop)
        if not self.state.get('setup_done'):
            t = rumps.Timer(self._run_first_setup, 1.0)
            t.start()

    # ── First launch ─────────────────────────────────────────────────────────

    def _run_first_setup(self, sender):
        sender.stop()
        ok = run_setup()
        if ok:
            self.state['setup_done'] = True
            save_state(self.state)
            self._do_sync(reason='initial')

    # ── Status ───────────────────────────────────────────────────────────────

    def refresh_status(self) -> None:
        age = last_data_age_hours()
        if age is None:
            label = 'Last sync: never'
            self.title = 'Kairos ⚠'
        elif age < 24:
            label = f'Last sync: {age}h ago ✓'
            self.title = 'Kairos'
        elif age < 48:
            label = f'Last sync: {age}h ago (stale)'
            self.title = 'Kairos ⚠'
        else:
            label = f'Last sync: {int(age/24)}d ago ⚠'
            self.title = 'Kairos ⚠'

        self.menu['Last sync: …'].title = label
        self.menu['Auto-sync at 4:30 PM'].state = 1 if self.state.get('auto') else 0

    def tick(self, _sender) -> None:
        self.refresh_status()
        if self.state.get('auto') and self._in_autosync_window():
            today = datetime.date.today().isoformat()
            if self.state.get('last_sync') != today:
                self._do_sync(reason='auto')

    def _in_autosync_window(self) -> bool:
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return False
        return (now.hour == AUTO_SYNC_HOUR and
                AUTO_SYNC_MIN <= now.minute < AUTO_SYNC_MIN + 5)

    # ── Sync ─────────────────────────────────────────────────────────────────

    def _do_sync(self, reason: str) -> None:
        if not self._sync_lock.acquire(blocking=False):
            return
        self.menu['Sync now'].title = 'Syncing… (please wait)'
        self.title = 'Kairos ⟳'

        def worker():
            try:
                result = run_sync()
                if result['ok']:
                    self.state['last_sync'] = datetime.date.today().isoformat()
                    save_state(self.state)
                    rumps.notification(
                        title='Kairos',
                        subtitle=f'✓ Sync complete ({result["duration_s"]}s)',
                        message='Portal updated',
                    )
                else:
                    rumps.notification(
                        title='Kairos',
                        subtitle=f'✗ Sync failed ({result["step"]})',
                        message='Check logs for details',
                    )
            finally:
                self.menu['Sync now'].title = 'Sync now'
                self.refresh_status()
                self._sync_lock.release()

        threading.Thread(target=worker, daemon=True).start()

    # ── Menu callbacks ────────────────────────────────────────────────────────

    def on_sync_now(self, _sender) -> None:
        self._do_sync(reason='manual')

    def on_toggle_auto(self, sender) -> None:
        self.state['auto'] = not self.state.get('auto', True)
        save_state(self.state)
        sender.state = 1 if self.state['auto'] else 0

    def on_open_portal(self, _sender) -> None:
        subprocess.run(['open', PORTAL_URL])

    def on_view_logs(self, _sender) -> None:
        subprocess.run(['open', str(LOG_FILE)])

    def on_setup(self, _sender) -> None:
        run_setup()


if __name__ == '__main__':
    KairosApp().run()
