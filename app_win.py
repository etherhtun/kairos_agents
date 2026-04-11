"""
Kairos Agent — Windows system tray app
Syncs Tiger Brokers trades → Cloudflare R2 → Kairos portal.

Run:  python3 app_win.py
Dist: pyinstaller kairos_win.spec
"""

import json
import pathlib
import datetime
import threading
import subprocess
import sys
import io

from PIL import Image, ImageDraw
import pystray

from jobs.upload_sync import run_sync, last_data_age_hours, LOG_FILE
from jobs.setup_win import run_setup

STATE_FILE     = pathlib.Path.home() / '.kairos-agent' / 'state.json'
PORTAL_URL     = 'https://kairos-f3w.pages.dev'
AGENT_DIR      = pathlib.Path.home() / '.kairos-agent'
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


# ── Icon image ────────────────────────────────────────────────────────────────

def _make_icon(state: str = 'ok') -> Image.Image:
    """Generate a simple 64×64 tray icon. state: ok | warn | syncing"""
    colours = {'ok': (102, 187, 106), 'warn': (255, 152, 0), 'syncing': (79, 195, 247)}
    colour = colours.get(state, colours['ok'])
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([6, 6, 58, 58], fill=colour)
    return img


# ── Notification ──────────────────────────────────────────────────────────────

def _notify(title: str, message: str) -> None:
    try:
        from plyer import notification
        notification.notify(title=title, message=message, app_name='Kairos', timeout=6)
    except Exception:
        pass


# ── App ───────────────────────────────────────────────────────────────────────

class KairosApp:
    def __init__(self):
        self.state      = load_state()
        self._sync_lock = threading.Lock()
        self._icon      = None
        self._auto_timer = None

    # ── Tray icon setup ───────────────────────────────────────────────────────

    def _build_menu(self) -> pystray.Menu:
        age   = last_data_age_hours()
        label = 'Last sync: never' if age is None \
                else (f'Last sync: {age}h ago ✓' if age < 24
                      else f'Last sync: {int(age/24)}d ago ⚠')

        auto_label = '✓ Auto-sync at 4:30 PM' if self.state.get('auto') else '  Auto-sync at 4:30 PM'

        return pystray.Menu(
            pystray.MenuItem('— Kairos Agent —', None, enabled=False),
            pystray.MenuItem(label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Sync now',    self._on_sync_now),
            pystray.MenuItem(auto_label,    self._on_toggle_auto),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Open portal', self._on_open_portal),
            pystray.MenuItem('View logs',   self._on_view_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Setup / reconfigure', self._on_setup),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Quit', self._on_quit),
        )

    def _refresh(self) -> None:
        """Rebuild menu and update icon colour."""
        if not self._icon:
            return
        age = last_data_age_hours()
        state = 'ok' if (age is not None and age < 24) else 'warn'
        self._icon.icon  = _make_icon(state)
        self._icon.menu  = self._build_menu()
        self._icon.title = 'Kairos Agent'

    # ── Auto-sync timer ───────────────────────────────────────────────────────

    def _schedule_tick(self) -> None:
        self._auto_timer = threading.Timer(60, self._tick)
        self._auto_timer.daemon = True
        self._auto_timer.start()

    def _tick(self) -> None:
        self._refresh()
        if self.state.get('auto') and self._in_autosync_window():
            today = datetime.date.today().isoformat()
            if self.state.get('last_sync') != today:
                self._do_sync(reason='auto')
        self._schedule_tick()

    def _in_autosync_window(self) -> bool:
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return False
        return (now.hour == AUTO_SYNC_HOUR and
                AUTO_SYNC_MIN <= now.minute < AUTO_SYNC_MIN + 5)

    # ── Sync ──────────────────────────────────────────────────────────────────

    def _do_sync(self, reason: str = 'manual') -> None:
        if not self._sync_lock.acquire(blocking=False):
            return

        if self._icon:
            self._icon.icon = _make_icon('syncing')

        def worker():
            try:
                result = run_sync()
                if result['ok']:
                    self.state['last_sync'] = datetime.date.today().isoformat()
                    save_state(self.state)
                    _notify('Kairos', f'✓ Sync complete ({result["duration_s"]}s) — portal updated')
                else:
                    _notify('Kairos', f'✗ Sync failed ({result["step"]}) — check logs')
            finally:
                self._refresh()
                self._sync_lock.release()

        threading.Thread(target=worker, daemon=True).start()

    # ── Menu callbacks ────────────────────────────────────────────────────────

    def _on_sync_now(self, icon, item) -> None:
        self._do_sync(reason='manual')

    def _on_toggle_auto(self, icon, item) -> None:
        self.state['auto'] = not self.state.get('auto', True)
        save_state(self.state)
        self._refresh()

    def _on_open_portal(self, icon, item) -> None:
        subprocess.run(['start', PORTAL_URL], shell=True)

    def _on_view_logs(self, icon, item) -> None:
        subprocess.run(['start', str(LOG_FILE)], shell=True)

    def _on_setup(self, icon, item) -> None:
        threading.Thread(target=run_setup, daemon=True).start()

    def _on_quit(self, icon, item) -> None:
        if self._auto_timer:
            self._auto_timer.cancel()
        icon.stop()

    # ── First launch ──────────────────────────────────────────────────────────

    def _first_launch(self) -> None:
        import time
        time.sleep(1)
        ok = run_setup()
        if ok:
            self.state['setup_done'] = True
            save_state(self.state)
            self._do_sync(reason='initial')

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self) -> None:
        self._icon = pystray.Icon(
            name='kairos',
            icon=_make_icon('warn' if last_data_age_hours() is None else 'ok'),
            title='Kairos Agent',
            menu=self._build_menu(),
        )

        self._schedule_tick()

        if not self.state.get('setup_done'):
            threading.Thread(target=self._first_launch, daemon=True).start()

        self._icon.run()


if __name__ == '__main__':
    KairosApp().run()
