"""
sync/sync.py
============
Main sync script — fetches all brokers, writes data.json.

Improvements:
  - Incremental sync: only fetches recent 90 days, merges with existing data
  - Backup data.json before overwriting
  - Win rate calculated per closed trade-group (not per leg)
  - Deduplication across runs

Run:  python3 sync/sync.py
      (or use run_sync.sh which also git pushes)
"""

import json, os, sys, shutil, warnings, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings('ignore')

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from brokers.tiger  import TigerBroker
from brokers.webull import WebullBroker
from brokers.moomoo import MooMooBroker
from classifier import classify_trades, group_positions

# ── Config ────────────────────────────────────────────────────────────────────
BROKERS      = [TigerBroker(), WebullBroker(), MooMooBroker()]
OUTPUT_FILE  = Path(__file__).parent.parent / 'data.json'
BACKUP_FILE  = Path(__file__).parent.parent / 'data.backup.json'

# Incremental: only fetch last N days from broker, merge with existing history
INCREMENTAL_DAYS = 90
FULL_HISTORY_YEARS = 3

END_DATE = datetime.now().strftime('%Y-%m-%d')


# ── Load existing data.json ───────────────────────────────────────────────────

def load_existing() -> dict:
    """Load existing data.json if present. Returns empty structure if not."""
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE) as f:
                data = json.load(f)
            existing_trades = len(data.get('trades', []))
            print(f'  Loaded existing data.json — {existing_trades} trades')
            return data
        except Exception as e:
            print(f'  ⚠️  Could not parse existing data.json: {e} — starting fresh')
    return {'trades': [], 'accounts': [], 'open_positions': [],
            'daily_pnl': [], 'cumulative_pnl': [], 'stats': [],
            'meta': {}}


def backup_existing():
    """Backup data.json before overwriting."""
    if OUTPUT_FILE.exists():
        shutil.copy2(OUTPUT_FILE, BACKUP_FILE)
        print(f'  Backed up → data.backup.json')


# ── Win rate: per closed trade-group (not per leg) ────────────────────────────

def _group_key(t) -> str:
    """Group option legs by broker+date+symbol+expiry — same position = same group."""
    if t.get('asset_type') == 'OPT':
        return f"{t['broker']}|{t['date']}|{t['symbol']}|{t['expiry']}"
    # Stocks: each trade is its own group
    return f"{t['broker']}|{t['trade_id']}"


def build_stats(trade_records: list) -> list:
    """
    Calculate win rate per closed position group, not per leg.
    An IC = 4 legs but counts as 1 trade for win/loss purposes.
    Stock trades with pnl=0 (still open) are excluded from win rate.
    """
    # Group by broker + strategy
    broker_strat: dict = defaultdict(list)
    for t in trade_records:
        key = (t['broker'], t['strategy'])
        broker_strat[key].append(t)

    rows = []
    for (broker, strategy), group in sorted(broker_strat.items()):

        # For options: group legs into positions first
        if any(t['asset_type'] == 'OPT' for t in group):
            pos_groups: dict = defaultdict(list)
            for t in group:
                pos_groups[_group_key(t)].append(t)

            # Each position group = 1 trade result
            position_pnls = []
            for legs in pos_groups.values():
                total_pnl = sum(l['realized_pnl'] for l in legs)
                # Only count as closed if pnl != 0 (0 = still open or not filled)
                if any(l['realized_pnl'] != 0 for l in legs):
                    position_pnls.append(total_pnl)

            total = len(position_pnls)
            wins  = sum(1 for p in position_pnls if p > 0)
            total_pnl = sum(position_pnls)
            avg_pnl   = total_pnl / total if total > 0 else 0

        else:
            # Stocks: exclude zero-pnl (open positions)
            closed = [t for t in group if t['realized_pnl'] != 0]
            total  = len(closed)
            wins   = sum(1 for t in closed if t['realized_pnl'] > 0)
            total_pnl = sum(t['realized_pnl'] for t in closed)
            avg_pnl   = total_pnl / total if total > 0 else 0

        rows.append({
            'broker':        broker,
            'strategy':      strategy,
            'total_trades':  total,
            'win_rate_pct':  round(wins / total * 100, 1) if total > 0 else 0,
            'total_pnl':     round(total_pnl, 2),
            'avg_pnl':       round(avg_pnl, 2),
        })
    return rows


# ── Daily P&L ─────────────────────────────────────────────────────────────────

def build_daily_pnl(trade_records: list) -> list:
    daily: dict = defaultdict(lambda: defaultdict(float))
    for t in trade_records:
        key = f"{t['broker']}_{t['strategy']}"
        daily[t['date']][key] += t['realized_pnl']

    # Collect all keys
    all_keys = set()
    for row in daily.values():
        all_keys.update(row.keys())

    rows = []
    for date in sorted(daily.keys()):
        row = {'date': date}
        for k in all_keys:
            v = round(daily[date].get(k, 0), 2)
            if v != 0:
                row[k] = v
        rows.append(row)
    return rows


# ── Cumulative ────────────────────────────────────────────────────────────────

def build_cumulative(daily_rows: list) -> list:
    """Two-pass: collect all keys first, then build cumulative with all keys."""
    all_keys = set()
    for row in daily_rows:
        all_keys.update(k for k in row.keys() if k != 'date')

    running: dict = defaultdict(float)
    result = []
    for row in daily_rows:
        for k in all_keys:
            running[k] = round(running[k] + float(row.get(k, 0)), 2)
        cum = {'date': row['date']}
        cum.update({k: running[k] for k in all_keys})
        result.append(cum)
    return result


# ── Trade records ─────────────────────────────────────────────────────────────

def build_trade_records(trades: list) -> list:
    return sorted([{
        'broker':       t.broker,
        'trade_id':     t.trade_id,
        'date':         t.date,
        'entry_time':   getattr(t, 'entry_time', ''),
        'symbol':       t.symbol,
        'contract':     t.contract,
        'asset_type':   t.asset_type,
        'action':       t.action,
        'quantity':     t.quantity,
        'avg_price':    t.avg_price,
        'realized_pnl': t.realized_pnl,
        'strategy':     t.strategy or 'unknown',
        'option_type':  t.option_type,
        'strike':       t.strike,
        'expiry':       t.expiry,
        # Signal tagging — populated by tag_trades_with_signals()
        'signal_tier':     None,
        'signal_score':    None,
        'regime_at_entry': None,
    } for t in trades], key=lambda x: x['date'])


# ── Signal tagging ────────────────────────────────────────────────────────────

def _find_closest_signal(day_signals: list, entry_time_iso: str) -> dict | None:
    """Return the signal closest to entry_time within ±2 hours, or None."""
    if not day_signals:
        return None

    if not entry_time_iso:
        # No timestamp — return highest-tier signal of the day
        rank = {'PRIME': 2, 'VALID': 1}
        return max(day_signals, key=lambda s: rank.get(s.get('tier', ''), 0))

    try:
        entry_dt = datetime.fromisoformat(entry_time_iso.replace('Z', '+00:00'))
    except ValueError:
        return None

    window = timedelta(hours=2)
    candidates = []
    for sig in day_signals:
        try:
            sig_dt = datetime.fromisoformat(sig['ts'].replace('Z', '+00:00'))
        except (KeyError, ValueError):
            continue
        diff = abs((sig_dt - entry_dt).total_seconds())
        if diff <= window.total_seconds():
            candidates.append((diff, sig))

    return min(candidates, key=lambda x: x[0])[1] if candidates else None


def tag_trades_with_signals(records: list) -> list:
    """
    Look up Optix signal within ±2 hours of each trade entry.
    Reads KAIROS_PORTAL_URL from environment — silently skips if not set.
    Only tags trades that don't already have signal_tier set.
    Preserves existing tags when merging with history.
    """
    portal_url = os.getenv('KAIROS_PORTAL_URL', '').rstrip('/')
    if not portal_url:
        print('  ℹ️  KAIROS_PORTAL_URL not set — skipping signal tagging')
        print('     Set it in .env to enable: KAIROS_PORTAL_URL=https://your-portal.pages.dev')
        return records

    # Only tag untagged trades
    untagged = [t for t in records if not t.get('signal_tier')]
    if not untagged:
        print('  ✅ All trades already tagged with signals')
        return records

    # Collect unique dates to fetch
    dates = sorted(set(t['date'] for t in untagged))
    print(f'  Fetching signals for {len(dates)} dates from {portal_url}...')

    url = f"{portal_url}/api/signal-lookup?dates={','.join(dates)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            signals_by_date = json.loads(resp.read())
    except Exception as e:
        print(f'  ⚠️  Signal lookup failed: {e} — trades will remain untagged')
        return records

    # Tag each untagged trade
    tagged = 0
    for trade in untagged:
        date     = trade.get('date', '')
        day_sigs = signals_by_date.get(date, [])
        sig      = _find_closest_signal(day_sigs, trade.get('entry_time', ''))
        if not sig:
            continue

        rec   = sig.get('recommendation', '')
        tier  = ('PRIME' if 'PRIME' in rec else
                 'VALID' if 'VALID' in rec else None)
        # Score: use bear score for BCS signals, bull score otherwise
        score = (sig.get('bear', {}).get('score') if 'BCS' in rec
                 else sig.get('bull', {}).get('score'))

        trade['signal_tier']     = tier
        trade['signal_score']    = round(score, 2) if score is not None else None
        trade['regime_at_entry'] = sig.get('regime')
        if tier:
            tagged += 1

    print(f'  Signal tagging: {tagged} PRIME/VALID matched, '
          f'{len(untagged) - tagged} untagged (no signal within ±2h)')


# ── Merge new trades with existing ───────────────────────────────────────────

def merge_trades(existing_records: list, new_records: list) -> list:
    """
    Merge new trades into existing history.
    Deduplicates by trade_id — existing records are the source of truth
    for old data, new records update/add recent data.
    """
    trade_map = {t['trade_id']: t for t in existing_records}
    for t in new_records:
        trade_map[t['trade_id']] = t  # new data overwrites old
    merged = sorted(trade_map.values(), key=lambda x: x['date'])
    print(f'  Merged: {len(existing_records)} existing + {len(new_records)} new = {len(merged)} total')
    return merged


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*55}")
    print(f"  KAIROS Sync  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*55}")

    # Load + backup existing data
    print('\n▶ Loading existing data...')
    existing = load_existing()
    backup_existing()

    # Determine fetch range
    # Incremental: fetch last 90 days to catch recent trades
    # First run (no existing): fetch full history
    existing_trades = existing.get('trades', [])
    if existing_trades:
        start_date = (datetime.now() - timedelta(days=INCREMENTAL_DAYS)).strftime('%Y-%m-%d')
        print(f'  Incremental sync: fetching {start_date} → {END_DATE}')
    else:
        start_date = (datetime.now() - timedelta(days=365 * FULL_HISTORY_YEARS)).strftime('%Y-%m-%d')
        print(f'  Full sync: fetching {start_date} → {END_DATE}')

    all_new_trades = []
    all_positions  = []
    accounts       = []
    active_brokers = []

    for broker in BROKERS:
        print(f'\n▶ {broker.name.upper()}')
        try:
            if not broker.connect():
                continue

            active_brokers.append(broker.name)

            acct = broker.get_account()
            accounts.append({
                'broker':     broker.name,
                'account_id': acct.account_id,
                'net_value':  acct.net_value,
                'cash':       acct.cash,
                'currency':   acct.currency,
            })
            print(f'  Account: {acct.account_id}  Net: {acct.net_value:,.2f} {acct.currency}')

            positions = broker.get_positions()
            all_positions.extend(positions)
            print(f'  Positions: {len(positions)}')

            print(f'  Fetching trades {start_date} → {END_DATE}...')
            trades = broker.get_trades(start_date, END_DATE)
            all_new_trades.extend(trades)
            print(f'  New trades fetched: {len(trades)}')

        except Exception as e:
            print(f'  ❌ {broker.name} error: {e}')
        finally:
            if hasattr(broker, 'close'):
                broker.close()

    # Classify new trades
    print(f'\n▶ Classifying new trades...')
    seen = set()
    unique_new = []
    for t in all_new_trades:
        if t.trade_id not in seen:
            seen.add(t.trade_id)
            unique_new.append(t)
    classify_trades(unique_new)
    new_records = build_trade_records(unique_new)

    # Tag new trades with Optix signals (±2h match)
    print(f'\n▶ Tagging trades with Optix signals...')
    tag_trades_with_signals(new_records)

    # Merge with existing history
    print(f'\n▶ Merging with history...')
    all_records = merge_trades(existing_trades, new_records)

    # Rebuild analytics from full merged history
    print(f'\n▶ Rebuilding analytics...')
    daily      = build_daily_pnl(all_records)
    cumulative = build_cumulative(daily)
    stats      = build_stats(all_records)
    positions  = group_positions(all_positions)

    # Build final data.json
    data = {
        'meta': {
            'last_sync':        datetime.now().isoformat(),
            'start_date':       min(t['date'] for t in all_records) if all_records else start_date,
            'end_date':         END_DATE,
            'total_trades':     len(all_records),
            'brokers':          active_brokers,
            'incremental':      bool(existing_trades),
        },
        'accounts':       accounts,
        'open_positions':  positions,
        'daily_pnl':      daily,
        'cumulative_pnl': cumulative,
        'stats':          stats,
        'trades':         all_records,
    }

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(data, f, indent=2, default=str)

    size_kb = OUTPUT_FILE.stat().st_size / 1024
    total_pnl = sum(t['realized_pnl'] for t in all_records)
    open_upnl = sum(p.get('unrealized_pnl', 0) for p in positions)

    print(f'\n{"="*55}')
    print(f'  Total trades:   {len(all_records)}')
    print(f'  New this run:   {len(unique_new)}')
    print(f'  Open positions: {len(positions)}')
    print(f'  Realized P&L:   ${total_pnl:,.2f}')
    print(f'  Unrealized P&L: ${open_upnl:,.2f}')
    print(f'  File size:      {size_kb:.1f} KB')
    print(f'{"="*55}\n')


if __name__ == '__main__':
    run()
