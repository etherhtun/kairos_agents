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
# Interval mode: when > 0, sync every N minutes during US market hours (RTH) instead
# of once/day at HOUR:MIN. 0 (default) keeps the legacy once-daily-at-HOUR:MIN behavior.
AUTO_SYNC_INTERVAL_MIN = int(os.environ.get('AUTO_SYNC_INTERVAL_MIN', 0))

# US Eastern for the market-hours gate — resolved explicitly so it does NOT depend on
# the container's TZ. Needs the `tzdata` package in slim images (see requirements_docker).
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo('America/New_York')
except Exception as _e:  # missing tzdata / old runtime — fail open (don't block syncing)
    print(f'[kairos] warning: could not load US/Eastern tz ({_e}); market-hours gate disabled', flush=True)
    _ET = None

_sync_lock = threading.Lock()


def _us_market_open(now_utc=None) -> bool:
    """True during US equity regular hours: Mon–Fri, 09:30–16:00 ET (DST-aware)."""
    if _ET is None:
        return True  # tz unresolved → don't gate
    et = (now_utc or datetime.datetime.now(datetime.timezone.utc)).astimezone(_ET)
    if et.weekday() >= 5:
        return False
    mins = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= mins < 16 * 60


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as e:
            print(f'[kairos] warning: state.json unreadable: {e} — starting fresh', flush=True)
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
    # Stamp the attempt BEFORE running so the interval cooldown holds even when a sync
    # FAILS (e.g. upload blocked) — otherwise a failing sync would retry every minute.
    _state['last_attempt_ts'] = time.time()
    save_state(_state)
    print(f'[kairos] sync started ({reason})')
    try:
        result = run_sync()
        if result['ok']:
            _state['last_sync'] = datetime.date.today().isoformat()
            _state['last_sync_ts'] = time.time()
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
    """Runs in background thread. Two modes:
      • interval  (AUTO_SYNC_INTERVAL_MIN > 0): sync every N min during US market hours.
      • legacy    (0, default):                 once per weekday at AUTO_SYNC_HOUR:MIN.
    """
    while True:
        time.sleep(60)
        if not _state.get('auto'):
            continue

        if AUTO_SYNC_INTERVAL_MIN > 0:
            # Interval mode — only during RTH, and only if the interval has elapsed
            # since the last ATTEMPT (so failures don't hammer the brokers/OpenD).
            if not _us_market_open():
                continue
            last = _state.get('last_attempt_ts') or 0
            if time.time() - last >= AUTO_SYNC_INTERVAL_MIN * 60:
                threading.Thread(target=do_sync, args=('auto',), daemon=True).start()
        else:
            # Legacy fixed-time-once-a-day mode.
            if _in_autosync_window():
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
    elif AUTO_SYNC_INTERVAL_MIN > 0:
        print(f'[kairos] ready — auto-sync every {AUTO_SYNC_INTERVAL_MIN} min during US market hours (09:30–16:00 ET, weekdays)')
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
