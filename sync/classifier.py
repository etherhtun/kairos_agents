"""
sync/classifier.py
Classifies trades and positions into strategies.
Shared across all brokers.
"""

from typing import List
from brokers.base import Trade, Position


STRATEGIES_OPT = ['iron_condor', 'bps', 'bcs', 'csp', 'cc']


def classify_trades(trades: List[Trade]) -> List[Trade]:
    """
    Group option trades by broker+symbol+expiry then classify.
    If strategy already set by broker parser (combo orders), skip re-classification.
    """
    # Separate already-classified (Tiger combo orders) from unclassified
    already_done = [t for t in trades if t.strategy and t.strategy != 'unknown' and t.asset_type == 'OPT']
    opt = [t for t in trades if t.asset_type == 'OPT' and (not t.strategy or t.strategy == 'unknown')]
    stk = [t for t in trades if t.asset_type == 'STK']

    # Stocks
    for t in stk:
        t.strategy = 'long_stock' if t.action == 'BUY' else 'short_stock'

    # Group options by broker + symbol + expiry (date excluded — legs may close on diff days)
    groups: dict = {}
    for t in opt:
        key = (t.broker, t.symbol, t.expiry)
        groups.setdefault(key, []).append(t)

    for key, group in groups.items():
        strat = _classify_opt_group(group)
        for t in group:
            t.strategy = strat

    # Merge already-classified back in
    for t in already_done:
        pass  # strategy already set
    return trades


def group_positions(positions: List[Position]) -> List[dict]:
    """
    Group individual option legs into one row per position.
    Returns list of dicts ready for data.json.
    """
    opt_pos = [p for p in positions if p.asset_type == 'OPT']
    stk_pos = [p for p in positions if p.asset_type == 'STK']

    result = []

    # Group options by broker + symbol + expiry
    groups: dict = {}
    for p in opt_pos:
        key = (p.broker, p.symbol, p.expiry)
        groups.setdefault(key, []).append(p)

    for (broker, symbol, expiry), group in groups.items():
        # Use strategy from position if already set (e.g. Tiger MLEG)
        preset = [p.strategy for p in group if hasattr(p,'strategy') and getattr(p,'strategy','')]
        strategy = preset[0] if preset else _classify_opt_group_pos(group)
        puts     = sorted([p for p in group if p.option_type == 'P'], key=lambda x: x.strike)
        calls    = sorted([p for p in group if p.option_type == 'C'], key=lambda x: x.strike)

        # Build strikes display
        if strategy == 'iron_condor':
            strikes = (f"P {puts[0].strike:.0f}/{puts[-1].strike:.0f}  "
                       f"C {calls[0].strike:.0f}/{calls[-1].strike:.0f}") if puts and calls else '—'
        elif strategy == 'bps':
            strikes = f"P {puts[0].strike:.0f}/{puts[-1].strike:.0f}" if len(puts) >= 2 else '—'
        elif strategy == 'bcs':
            strikes = f"C {calls[0].strike:.0f}/{calls[-1].strike:.0f}" if len(calls) >= 2 else '—'
        elif strategy == 'csp':
            strikes = f"P {puts[0].strike:.0f}" if puts else '—'
        elif strategy == 'cc':
            strikes = f"C {calls[0].strike:.0f}" if calls else '—'
        else:
            all_strikes = sorted(set(p.strike for p in group))
            strikes = '/'.join(f"{s:.0f}" for s in all_strikes)

        # Entry credit — Tiger MLEG: avg_cost is the NET credit of the whole position
        # For a short spread/IC (net credit): avg_cost on short legs is negative = received
        # For long legs: avg_cost is positive = paid
        # Net credit = short_receipts - long_payments
        short_legs   = [p for p in group if p.quantity < 0]
        long_legs    = [p for p in group if p.quantity > 0]
        contracts    = abs(short_legs[0].quantity) if short_legs else 1

        # Net premium per contract × 100 per share
        short_receipts = sum(abs(p.avg_cost) * abs(p.quantity) * 100 for p in short_legs)
        long_payments  = sum(abs(p.avg_cost) * abs(p.quantity) * 100 for p in long_legs)
        entry_credit_gross = short_receipts - long_payments

        # Sanity check: credit should be < spread_width × contracts × 100
        all_s = sorted(set(p.strike for p in group))
        spread_width_pts = (all_s[-1] - all_s[0]) if len(all_s) >= 2 else 0
        max_possible_credit = spread_width_pts * 100 * contracts

        if entry_credit_gross > max_possible_credit and max_possible_credit > 0:
            # Tiger avg_cost is already the total dollar amount, not per-share
            # Divide by 100 to get per-share equivalent
            entry_credit = round(entry_credit_gross / 100, 2)
        else:
            entry_credit = round(max(0, entry_credit_gross), 2)

        # Max profit = credit received
        max_profit = round(entry_credit, 2)

        # Spread width (worst case max loss per contract × contracts)
        if strategy in ('csp', 'cc'):
            # Single-leg: max loss not capped — leave as 0
            max_loss = 0.0
        elif strategy in ('bps', 'bcs') and len(group) >= 2:
            all_strikes_list = sorted(set(p.strike for p in group))
            spread_width_pts = all_strikes_list[-1] - all_strikes_list[0]
            width_dollars    = spread_width_pts * 100 * contracts
            # Net max loss = width - net credit received
            # Tiger avg_cost for short legs = premium per share received
            # entry_credit = sum(abs(avg_cost) × qty × 100) for short legs
            # credit_per_contract = entry_credit / contracts
            credit_per_contract = entry_credit / contracts if contracts else 0
            net_max_loss = (spread_width_pts * 100) - credit_per_contract
            max_loss = round(max(0, net_max_loss) * contracts, 2)
            if max_loss == 0:
                # Fallback: use width only (credit data unreliable)
                max_loss = round(width_dollars, 2)
        elif strategy == 'iron_condor' and puts and calls:
            put_w  = (puts[-1].strike  - puts[0].strike)  * 100 if len(puts)  >= 2 else 0
            call_w = (calls[-1].strike - calls[0].strike) * 100 if len(calls) >= 2 else 0
            max_side_width = max(put_w, call_w)
            credit_per_contract = entry_credit / contracts if contracts else 0
            net_max_loss = max_side_width - credit_per_contract
            max_loss = round(max(0, net_max_loss) * contracts, 2)
            if max_loss == 0:
                # Fallback: use worst-case width (credit data unreliable)
                max_loss = round(max_side_width * contracts, 2)
        else:
            max_loss = 0.0

        result.append({
            'broker':         broker,
            'strategy':       strategy,
            'symbol':         symbol,
            'expiry':         expiry,
            'legs':           len(group),
            'strikes':        strikes,
            'entry_credit':   round(entry_credit, 2),
            'max_profit':     max_profit,
            'max_loss':       max_loss,
            'unrealized_pnl': round(sum(p.unrealized_pnl for p in group), 2),
            'realized_pnl':   round(sum(p.realized_pnl   for p in group), 2),
            'market_value':   round(sum(p.market_value    for p in group), 2),
        })

    # Stocks
    for p in stk_pos:
        side = 'long_stock' if p.quantity > 0 else 'short_stock'
        result.append({
            'broker':         p.broker,
            'strategy':       side,
            'symbol':         p.symbol,
            'expiry':         '',
            'legs':           1,
            'strikes':        f"{abs(p.quantity):.0f} shares @ ${p.avg_cost:.2f}",
            'entry_credit':   0,
            'max_profit':     0,
            'max_loss':       0,
            'unrealized_pnl': round(p.unrealized_pnl, 2),
            'realized_pnl':   round(p.realized_pnl,   2),
            'market_value':   round(p.market_value,    2),
        })

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _classify_opt_group(trades: list) -> str:
    has_call = any(t.option_type == 'C' for t in trades)
    has_put  = any(t.option_type == 'P' for t in trades)
    sells    = [t for t in trades if t.action == 'SELL']
    buys     = [t for t in trades if t.action == 'BUY']
    if has_call and has_put: return 'iron_condor'
    # Single-leg: only sells (no hedge leg)
    if len(trades) == 1:
        t = trades[0]
        if t.action == 'SELL':
            return 'csp' if t.option_type == 'P' else 'cc'
    if has_put:  return 'bps'
    if has_call: return 'bcs'
    return 'unknown'

def _classify_opt_group_pos(positions: list) -> str:
    has_call  = any(p.option_type == 'C' for p in positions)
    has_put   = any(p.option_type == 'P' for p in positions)
    short_pos = [p for p in positions if p.quantity < 0]
    if has_call and has_put: return 'iron_condor'
    # Single short leg = CSP or CC
    if len(positions) == 1 and positions[0].quantity < 0:
        return 'csp' if positions[0].option_type == 'P' else 'cc'
    if has_put:  return 'bps'
    if has_call: return 'bcs'
    return 'unknown'
