"""
jobs/setup.py
=============
GUI setup wizard for Kairos Agent.
Step 1 — Upload token (from portal /connect page)
Step 2 — tiger_openapi_config.properties (downloaded from Tiger app)
"""

import subprocess
import rumps
from jobs import creds

PORTAL_URL = 'https://kairos-f3w.pages.dev/connect'


def _pick_file() -> str:
    """Open native macOS file picker. Returns selected path or ''."""
    script = (
        'tell application "System Events"\n'
        '  activate\n'
        'end tell\n'
        'set f to choose file with prompt '
        '"Select your tiger_openapi_config.properties file"\n'
        'return POSIX path of f'
    )
    r = subprocess.run(['osascript', '-e', script],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


def run_setup() -> bool:
    """
    Run the setup wizard. Returns True when all credentials are saved.
    """
    # ── Welcome ───────────────────────────────────────────────────────
    rumps.alert(
        title='Kairos Agent — Setup',
        message=(
            'Welcome! You need two things to get started:\n\n'
            '1. Your upload token — from the Kairos portal\n'
            '2. tiger_openapi_config.properties — from Tiger app\n\n'
            f'Open {PORTAL_URL} in your browser to get your token.'
        ),
        ok='Continue',
    )

    # ── Step 1: Upload token ──────────────────────────────────────────
    existing_token = creds.get('upload_token')
    w = rumps.Window(
        title='Step 1 — Upload Token',
        message=f'Paste your upload token from {PORTAL_URL}:',
        default_text=existing_token,
        ok='Next',
        cancel='Cancel',
        dimensions=(420, 24),
    )
    r = w.run()
    if not r.clicked:
        return False

    token = r.text.strip()
    if not token:
        rumps.alert('Setup', 'Upload token is required.')
        return False
    creds.set('upload_token', token)

    # ── Step 2: Tiger properties file ────────────────────────────────
    if creds.has_tiger_props():
        choice = rumps.alert(
            title='Step 2 — Tiger Config',
            message='Tiger config file already saved. Replace it?',
            ok='Select new file',
            cancel='Keep existing',
        )
        if not choice:
            return _finish()

    rumps.alert(
        title='⚠ Security Notice',
        message=(
            'Your tiger_openapi_config.properties file contains sensitive '
            'API credentials including your private key.\n\n'
            'Kairos stores this file locally on your Mac only '
            '(~/.kairos-agent/). It is never uploaded to any server.\n\n'
            'Only import a file you downloaded directly from Tiger app. '
            'Do not share this file with anyone.'
        ),
        ok='I understand',
    )

    rumps.alert(
        title='Step 2 — Tiger Config',
        message=(
            'Next, select your tiger_openapi_config.properties file.\n\n'
            'Download it from Tiger app:\n'
            'Tiger app → API Management → Download Config'
        ),
        ok='Select file',
    )

    path = _pick_file()
    if not path:
        if creds.has_tiger_props():
            return _finish()
        rumps.alert('Setup', 'Tiger config file is required.')
        return False

    if not path.endswith('.properties'):
        rumps.alert('Wrong file', 'Please select tiger_openapi_config.properties')
        return False

    if not creds.save_tiger_props(path):
        rumps.alert('Error', 'Could not copy the file. Check permissions.')
        return False

    return _finish()


def _finish() -> bool:
    if creds.is_complete():
        rumps.alert(
            title='Setup Complete ✓',
            message='All set! Click "Sync now" to sync your trades.',
        )
        return True
    else:
        rumps.alert(
            title='Setup Incomplete',
            message='Something is missing. Run Setup again.',
        )
        return False
