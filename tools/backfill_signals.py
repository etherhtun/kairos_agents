"""
tools/backfill_signals.py
=========================
One-time script: tags all existing trades in data.json with signal_tier,
signal_score, and regime_at_entry using reconstructed daily indicators.

Usage:
    cd kairos_agents
    python3 tools/backfill_signals.py [--dry-run] [--upload]

Options:
    --dry-run   Print stats without writing data.json
    --upload    After writing data.json, re-upload to R2

Run off-hours (not during an active sync).
"""

import json
import pathlib
import sys
import urllib.request
import datetime

# Allow running from the tools/ dir or from project root
_root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / 'sync'))

from signal_tagger import load_market_history, tag_untagged

AGENT_DIR = pathlib.Path.home() / '.kairos-agent'
DATA_FILE  = AGENT_DIR / 'data.json'


def _upload(data_file: pathlib.Path) -> bool:
    """Re-upload data.json to R2 using stored credentials."""
    try:
        import config
        from jobs import creds
        token = creds.get('upload_token')
        if not token:
            print('  ✗ upload_token not set — skipping upload')
            return False
        body = data_file.read_bytes()
        req  = urllib.request.Request(
            config.UPLOAD_URL,
            data=body,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type':  'application/json',
                'User-Agent':    'kairos-agent/1.0',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            print(f'  ✓ Uploaded: {result}')
            return True
    except Exception as e:
        print(f'  ✗ Upload failed: {e}')
        return False


def run(dry_run: bool = False, upload: bool = False):
    print(f"\n{'='*55}")
    print(f"  Signal Backfill  {datetime.datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*55}")

    if not DATA_FILE.exists():
        print('✗ data.json not found — run a sync first')
        return

    with open(DATA_FILE) as f:
        data = json.load(f)

    trades = data.get('trades', [])
    untagged_before = sum(1 for t in trades if t.get('signal_tier') is None)
    print(f'\n  Trades total:    {len(trades)}')
    print(f'  Untagged:        {untagged_before}')

    if untagged_before == 0:
        print('\n  All trades already tagged — nothing to do.')
        return

    print('\n▶ Fetching SPX + VIX history from Yahoo Finance...')
    spx, vix = load_market_history()
    print(f'  SPX bars: {len(spx)}  |  VIX bars: {len(vix)}')

    if not spx or not vix:
        print('✗ Could not fetch market history — check internet connection')
        return

    print('\n▶ Tagging trades...')
    tagged = tag_untagged(trades, spx, vix)
    still_null = sum(1 for t in trades if t.get('signal_tier') is None)

    print(f'  Tagged:          {tagged}')
    print(f'  Still untagged:  {still_null} (missing price data for those dates)')

    # Summary by tier
    from collections import Counter
    tier_counts = Counter(t['signal_tier'] for t in trades if t.get('signal_tier'))
    regime_counts = Counter(t['regime_at_entry'] for t in trades if t.get('regime_at_entry'))

    print('\n  By signal tier:')
    for tier in ['PRIME', 'VALID', 'WATCH', 'SKIP']:
        n = tier_counts.get(tier, 0)
        if n:
            # Win rate for this tier
            tier_trades = [t for t in trades if t.get('signal_tier') == tier and t.get('realized_pnl', 0) != 0]
            wr = sum(1 for t in tier_trades if t['realized_pnl'] > 0) / len(tier_trades) * 100 if tier_trades else 0
            total_pnl = sum(t['realized_pnl'] for t in tier_trades)
            print(f'    {tier:<8} {n:>4} trades  WR={wr:.0f}%  P&L=${total_pnl:,.2f}')

    print('\n  By regime:')
    for regime, n in sorted(regime_counts.items(), key=lambda x: -x[1])[:8]:
        regime_trades = [t for t in trades if t.get('regime_at_entry') == regime and t.get('realized_pnl', 0) != 0]
        total_pnl = sum(t['realized_pnl'] for t in regime_trades)
        print(f'    {regime:<35} {n:>4} trades  P&L=${total_pnl:,.2f}')

    if dry_run:
        print('\n  [dry-run] — data.json NOT written')
        return

    # Write back
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    print(f'\n  ✓ data.json updated')

    if upload:
        print('\n▶ Uploading to R2...')
        _upload(DATA_FILE)

    print(f'\n{"="*55}\n')


if __name__ == '__main__':
    dry   = '--dry-run' in sys.argv
    up    = '--upload'  in sys.argv
    run(dry_run=dry, upload=up)
