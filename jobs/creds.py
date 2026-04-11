"""
jobs/creds.py
=============
Credential store for Kairos Agent.

~/.kairos-agent/credentials.json        — upload token only
~/.kairos-agent/tiger_openapi_config.properties — Tiger config (from Tiger app)
"""

import json
import os
import pathlib
import shutil
import stat

AGENT_DIR   = pathlib.Path.home() / '.kairos-agent'
CREDS_FILE  = AGENT_DIR / 'credentials.json'
TIGER_PROPS = AGENT_DIR / 'tiger_openapi_config.properties'


def _load() -> dict:
    if CREDS_FILE.exists():
        try:
            return json.loads(CREDS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(CREDS_FILE, stat.S_IRUSR | stat.S_IWUSR)


def get(key: str) -> str:
    return _load().get(key, '')


def set(key: str, value: str) -> None:
    data = _load()
    data[key] = value
    _save(data)


def has_tiger_props() -> bool:
    return TIGER_PROPS.exists()


def save_tiger_props(src_path: str) -> bool:
    """Copy tiger_openapi_config.properties to ~/.kairos-agent/"""
    try:
        AGENT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, TIGER_PROPS)
        os.chmod(TIGER_PROPS, stat.S_IRUSR | stat.S_IWUSR)
        return True
    except Exception:
        return False


def is_complete() -> bool:
    return bool(get('upload_token')) and has_tiger_props()
