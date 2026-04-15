"""
app_docker.py
=============
Kairos Agent — Docker / headless mode.
No system tray. Web dashboard served at http://0.0.0.0:7432

Run:  python3 app_docker.py
      docker compose up
"""

import datetime
import json
import os
import pathlib
import threading
import time

import ssl_patch; ssl_patch.apply()

import server
from jobs.upload_sync import run_sync, LOG_FILE

STATE_FILE     = pathlib.Path.home() / '.kairos-agent' / 'state.json'
SERVER_PORT    = int(os.environ.get('PORT', 7432))
AUTO_SYNC_HOUR = int(os.environ.get('AUTO_SYNC_HOUR', 16))
AUTO_SYNC_MIN  = int(os.environ.get('AUTO_SYNC_MIN',  30))

_sync_lock = threading.Lock()


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


_state = load_state()


# ── Sync ──────────────────────────────────────────────────────────────────────

def do_sync(reason: str = 'manual') -> None:
    if not _sync_lock.acquire(blocking=False):
        print(f'[kairos] sync already running, skipping ({reason})')
        return
    print(f'[kairos] sync started ({reason})')
    try:
        result = run_sync()
        if result['ok']:
            _state['last_sync'] = datetime.date.today().isoformat()
            save_state(_state)
            print(f'[kairos] sync complete ({result["duration_s"]}s)')
        else:
            print(f'[kairos] sync failed at step={result["step"]}')
    finally:
        _sync_lock.release()


def _in_autosync_window() -> bool:
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    return (now.hour == AUTO_SYNC_HOUR and
            AUTO_SYNC_MIN <= now.minute < AUTO_SYNC_MIN + 5)


def _scheduler() -> None:
    """Runs in background thread — fires auto-sync at configured time."""
    while True:
        time.sleep(60)
        if _state.get('auto') and _in_autosync_window():
            today = datetime.date.today().isoformat()
            if _state.get('last_sync') != today:
                threading.Thread(target=do_sync, args=('auto',), daemon=True).start()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f'[kairos] starting — dashboard at http://127.0.0.1:{SERVER_PORT}')

    server.start(
        port     = SERVER_PORT,
        state    = _state,
        save_fn  = save_state,
        sync_fn  = lambda: threading.Thread(
            target=do_sync, args=('manual',), daemon=True
        ).start(),
        log_file = LOG_FILE,
        host     = '0.0.0.0',   # bind all interfaces inside container
    )

    threading.Thread(target=_scheduler, daemon=True).start()

    if not _state.get('setup_done'):
        print(f'[kairos] first run — open http://127.0.0.1:{SERVER_PORT}/setup to configure')
    else:
        print(f'[kairos] ready — auto-sync at {AUTO_SYNC_HOUR:02d}:{AUTO_SYNC_MIN:02d} on weekdays')

    # Keep main thread alive
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print('[kairos] shutting down')


if __name__ == '__main__':
    main()
