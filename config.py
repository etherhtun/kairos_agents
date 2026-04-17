"""
config.py
=========
Single source of truth for all Kairos Agent configuration.
All values are read from environment variables — no hardcoded URLs or IDs.

Required env vars (set in ~/.kairos-agent/.env or system environment):
  KAIROS_PORTAL_URL   — e.g. https://mykairos.pages.dev
  KAIROS_OPTIX_URL    — e.g. https://mykairos-optix.pages.dev  (optional)
"""

import os
from pathlib import Path

# ── Load .env from agent directory ───────────────────────────────────────────
_env_file = Path.home() / '.kairos-agent' / '.env'
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        # Manual .env parse if dotenv not installed
        for line in _env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip())

# ── URLs ─────────────────────────────────────────────────────────────────────
PORTAL_URL = os.getenv('KAIROS_PORTAL_URL', '').rstrip('/')
OPTIX_URL  = os.getenv('KAIROS_OPTIX_URL', '').rstrip('/')

# Derived endpoints
UPLOAD_URL     = f"{PORTAL_URL}/api/upload"   if PORTAL_URL else ''
SIGNAL_URL     = f"{PORTAL_URL}/api/signals"  if PORTAL_URL else ''

# ── Server ────────────────────────────────────────────────────────────────────
SERVER_PORT    = int(os.getenv('KAIROS_SERVER_PORT', '7432'))
AUTO_SYNC_HOUR = int(os.getenv('KAIROS_SYNC_HOUR', '16'))
AUTO_SYNC_MIN  = int(os.getenv('KAIROS_SYNC_MIN',  '30'))


def validate():
    """Check required config is present. Returns list of missing vars."""
    missing = []
    if not PORTAL_URL:
        missing.append('KAIROS_PORTAL_URL')
    return missing
