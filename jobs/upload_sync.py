"""
jobs/upload_sync.py
===================
Runs Tiger sync then uploads data.json to Cloudflare R2.
Tiger credentials are read from ~/.kairos-agent/tiger_openapi_config.properties.
Upload token is read from ~/.kairos-agent/credentials.json.
"""

import datetime
import json
import pathlib
import shutil
import os
import sys
import urllib.request
import urllib.error

import ssl_patch; ssl_patch.apply()

from jobs import creds

AGENT_DIR  = pathlib.Path.home() / '.kairos-agent'
DATA_FILE  = AGENT_DIR / 'data.json'
UPLOAD_URL = 'https://kairos-f3w.pages.dev/api/upload'
LOG_DIR    = AGENT_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE   = LOG_DIR / 'sync.log'


def _bundled_sync_dir() -> pathlib.Path:
    """Return sync/ from PyInstaller bundle or local dev path."""
    if getattr(sys, 'frozen', False):
        return pathlib.Path(sys._MEIPASS) / 'sync'
    return pathlib.Path(__file__).resolve().parent.parent / 'sync'


BUNDLE_VERSION = '2.0'  # bump this when sync code changes

def ensure_agent_dir():
    """Copy bundled sync code to ~/.kairos-agent/sync/, updating if stale."""
    dest        = AGENT_DIR / 'sync'
    ver_file    = AGENT_DIR / '.sync_version'
    src         = _bundled_sync_dir()

    current_ver = ver_file.read_text().strip() if ver_file.exists() else ''

    if dest.exists() and current_ver == BUNDLE_VERSION:
        return  # already up to date

    # Remove stale copy and re-extract
    if dest.exists():
        shutil.rmtree(dest)
    if src.exists():
        shutil.copytree(src, dest)
        ver_file.write_text(BUNDLE_VERSION)


def last_data_age_hours():
    if not DATA_FILE.exists():
        return None
    mtime = datetime.datetime.fromtimestamp(DATA_FILE.stat().st_mtime)
    return round((datetime.datetime.now() - mtime).total_seconds() / 3600, 1)


def run_sync(timeout: int = 300) -> dict:
    ensure_agent_dir()

    started = datetime.datetime.now()
    sep = f"\n{'='*60}\n  RUN @ {started.isoformat()}\n{'='*60}\n"

    with open(LOG_FILE, 'a') as f:
        f.write(sep)
        f.flush()

        # ── Step 1: Tiger sync (in-process) ──────────────────────────
        import io, contextlib

        # Tell tiger.py where to find the properties file
        os.environ['KAIROS_TIGER_PROPS'] = str(creds.TIGER_PROPS)

        # Add sync dir to path so imports work
        sync_dir = str(AGENT_DIR / 'sync')
        if sync_dir not in sys.path:
            sys.path.insert(0, sync_dir)

        try:
            import importlib

            # Clear cached broker/sync modules so updated code is re-imported
            for _key in list(sys.modules.keys()):
                if _key.startswith(('brokers', 'classifier')) or _key in ('sync',):
                    del sys.modules[_key]

            import sync as sync_mod
            importlib.reload(sync_mod)  # fresh state each run

            # Override output paths to ~/.kairos-agent/
            sync_mod.OUTPUT_FILE = DATA_FILE
            sync_mod.BACKUP_FILE = AGENT_DIR / 'data.backup.json'

            # Capture stdout
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sync_mod.run()

            output    = buf.getvalue()
            exit_code = 0
            f.write(output)
        except Exception as e:
            import traceback
            output = traceback.format_exc()
            f.write(output + '\n')
            exit_code = -2

        if exit_code != 0:
            finished = datetime.datetime.now()
            return {
                'ok': False, 'step': 'sync',
                'ts': finished.isoformat(),
                'duration_s': round((finished - started).total_seconds(), 1),
                'exit_code': exit_code,
                'tail': output.strip().splitlines()[-10:],
            }

        # ── Step 2: Upload ────────────────────────────────────────────
        if not DATA_FILE.exists():
            finished = datetime.datetime.now()
            return {
                'ok': False, 'step': 'upload',
                'ts': finished.isoformat(),
                'duration_s': round((finished - started).total_seconds(), 1),
                'exit_code': -3,
                'tail': ['data.json missing after sync'],
            }

        token = creds.get('upload_token')
        if not token:
            finished = datetime.datetime.now()
            return {
                'ok': False, 'step': 'upload',
                'ts': finished.isoformat(),
                'duration_s': round((finished - started).total_seconds(), 1),
                'exit_code': -4,
                'tail': ['upload_token not set — run Setup'],
            }

        body = DATA_FILE.read_bytes()
        req  = urllib.request.Request(
            UPLOAD_URL,
            data=body,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type':  'application/json',
                'User-Agent':    'kairos-agent/1.0',
            },
            method='POST',
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                f.write(f'Uploaded: {result}\n')
                upload_ok = True
        except urllib.error.HTTPError as e:
            f.write(f'Upload HTTP {e.code}: {e.read().decode()}\n')
            upload_ok = False
        except Exception as e:
            f.write(f'Upload error: {e}\n')
            upload_ok = False

    finished = datetime.datetime.now()
    return {
        'ok': upload_ok,
        'step': 'done' if upload_ok else 'upload_failed',
        'ts': finished.isoformat(),
        'duration_s': round((finished - started).total_seconds(), 1),
        'exit_code': 0 if upload_ok else -5,
        'tail': output.strip().splitlines()[-20:],
    }
