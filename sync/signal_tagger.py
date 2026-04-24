"""
sync/signal_tagger.py
=====================
Reconstructs signal tier + regime for a trade given its date, strategy, and DTE.
Uses SPX + VIX daily history from Yahoo Finance (fetched once per run).

Python port of scoring.js _regime() / _score() / _dteGrid() logic.
NOTE: historical tagging is approximate — no intraday VWAP or Stoch available.
      Trend component dominates at longer DTE, timing at 0-1DTE.
"""

import json
import math
import urllib.request
import urllib.error
from datetime import datetime, timedelta


# ── Yahoo Finance fetch ───────────────────────────────────────────────────────

def _fetch_yahoo(symbol: str, years: int = 4) -> dict[str, float]:
    """
    Returns {date_str: close} for the given symbol over the last N years.
    Uses Yahoo Finance v8 API — no external dependencies.
    Returns {} on any failure (graceful degradation).
    """
    url = (
        f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
        f'?interval=1d&range={years}y'
    )
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        result = raw['chart']['result'][0]
        timestamps = result['timestamp']
        closes     = result['indicators']['quote'][0]['close']
        out = {}
        for ts, c in zip(timestamps, closes):
            if c is not None:
                date_str = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
                out[date_str] = round(float(c), 4)
        return out
    except Exception:
        return {}


def load_market_history() -> tuple[dict, dict]:
    """
    Fetch SPX and VIX daily closes.
    Returns (spx_by_date, vix_by_date) — both {date_str: close}.
    """
    spx = _fetch_yahoo('%5EGSPC')  # ^GSPC
    vix = _fetch_yahoo('%5EVIX')   # ^VIX
    return spx, vix


# ── Indicator helpers ─────────────────────────────────────────────────────────

def _closes_up_to(price_by_date: dict, date: str, n: int) -> list[float]:
    """Return up to n closes on or before `date`, sorted oldest→newest."""
    dates = sorted(d for d in price_by_date if d <= date)
    return [price_by_date[d] for d in dates[-n:]]


def _sma(closes: list, n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _rsi(closes: list, n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 1)


def _bb_pctb(closes: list, n: int = 20) -> float | None:
    if len(closes) < n:
        return None
    c = closes[-n:]
    mu = sum(c) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in c) / n)
    if sd == 0:
        return 50.0
    return round((closes[-1] - (mu - 2 * sd)) / (4 * sd) * 100, 1)


def _ivr(vix_closes: list, n: int = 252) -> float | None:
    """VIX percentile rank over last n days — proxy for IVR."""
    if len(vix_closes) < 2:
        return None
    window = vix_closes[-min(n, len(vix_closes)):]
    current = vix_closes[-1]
    rank = sum(1 for v in window if v < current) / len(window) * 100
    return round(rank, 1)


# ── Regime (port of scoring.js _regime) ──────────────────────────────────────

def _regime_label(vix: float, bullish: bool) -> str:
    if vix < 15  and bullish:      return 'STRONG BULL (MELT-UP)'
    if vix < 20  and bullish:      return 'BULL TREND'
    if vix >= 25 and not bullish:  return 'HIGH VOL (BEAR PANIC)'
    if vix >= 20 and not bullish:  return 'BEAR TREND'
    if vix >= 15 and not bullish:  return 'CORRECTIVE BEAR'
    if vix >= 25 and bullish:      return 'VOL SPIKE BULL RECOVERY'
    if vix >= 20 and bullish:      return 'HIGH VOL BULL RECOVERY'
    return 'NEUTRAL / RANGING'


# ── Score + tier (port of scoring.js _score / _dteGrid) ──────────────────────

def _timing_score(strategy: str, rsi: float | None, pctb: float | None,
                  ivr: float | None) -> float:
    """
    Simplified timing score from daily indicators only.
    Missing: Stoch (0.8 max), VWAP (0.5 max) — unavailable from daily bars.
    Max possible here: 3.8 vs 6.1 in live scoring. Scaled accordingly.
    """
    iv_pts = (1.5 if ivr and ivr > 50 else
              1.0 if ivr and ivr > 30 else
              0.5 if ivr and ivr > 20 else 0.0)

    if strategy in ('bcs',):
        rb   = (1.5 if rsi and rsi >= 70 else 1.0 if rsi and rsi >= 60 else
                0.5 if rsi and rsi >= 50 else 0.0)
        bb_b = 0.8 if pctb is not None and pctb >= 90 else 0.0
        return round(rb + bb_b + iv_pts, 2)

    if strategy in ('bps',):
        ru   = (1.5 if rsi and rsi <= 30 else 1.0 if rsi and rsi <= 40 else
                0.5 if rsi and rsi <= 50 else 0.0)
        bb_u = 0.8 if pctb is not None and pctb <= 10 else 0.0
        return round(ru + bb_u + iv_pts, 2)

    # iron_condor / unknown — average bear+bull timing
    rb = (1.5 if rsi and rsi >= 70 else 1.0 if rsi and rsi >= 60 else
          0.5 if rsi and rsi >= 50 else 0.0)
    ru = (1.5 if rsi and rsi <= 30 else 1.0 if rsi and rsi <= 40 else
          0.5 if rsi and rsi <= 50 else 0.0)
    return round((rb + ru) / 2 + iv_pts, 2)


def _trend_val(strategy: str, regime: str) -> float:
    """
    Trend alignment value (0.0–1.0) — mirrors scoring.js trendVal logic.
    """
    bull = 'BULL' in regime
    bear = 'BEAR' in regime
    neutral = 'NEUTRAL' in regime or 'RANGING' in regime

    if strategy == 'bcs':  # bearish bet — want bear trend
        return 1.0 if bear else 0.5 if neutral else 0.0
    if strategy == 'bps':  # bullish bet — want bull trend
        return 1.0 if bull else 0.5 if neutral else 0.0
    # IC — wants neutral/ranging
    return 1.0 if neutral else 0.2


def _dte_weights(dte: int) -> tuple[float, float]:
    """Returns (timing_weight, trend_weight) for the given DTE."""
    if dte <= 1:  return 0.6, 0.4
    if dte <= 10: return 0.3, 0.7
    return 0.1, 0.9


def _tier(score: float) -> str:
    if score >= 4.0:  return 'PRIME'
    if score >= 2.5:  return 'VALID'
    if score >= 1.0:  return 'WATCH'
    return 'SKIP'


# ── Public API ────────────────────────────────────────────────────────────────

def tag_trade(trade: dict, spx: dict, vix: dict) -> dict:
    """
    Returns {'signal_tier': str, 'signal_score': float, 'regime_at_entry': str}
    or {'signal_tier': None, 'signal_score': None, 'regime_at_entry': None}
    on data gap.
    """
    null = {'signal_tier': None, 'signal_score': None, 'regime_at_entry': None}

    date     = trade.get('date', '')
    strategy = trade.get('strategy', 'unknown')
    expiry   = trade.get('expiry', '')

    if not date or not spx or not vix:
        return null

    # Compute DTE
    try:
        dte = (datetime.strptime(expiry, '%Y-%m-%d') -
               datetime.strptime(date,   '%Y-%m-%d')).days
    except Exception:
        dte = 7  # fallback

    # Gather indicator series up to trade date
    spx_closes = _closes_up_to(spx, date, 280)
    vix_closes = _closes_up_to(vix, date, 280)

    if len(spx_closes) < 22 or len(vix_closes) < 2:
        return null

    current_vix = vix_closes[-1]
    sma20       = _sma(spx_closes, 20)
    bullish     = spx_closes[-1] > sma20 if sma20 else True

    regime  = _regime_label(current_vix, bullish)
    rsi     = _rsi(spx_closes)
    pctb    = _bb_pctb(spx_closes)
    ivr_val = _ivr(vix_closes)

    timing = _timing_score(strategy, rsi, pctb, ivr_val)
    tw, rw = _dte_weights(dte)
    trend  = _trend_val(strategy, regime)
    score  = round(timing * tw + trend * 5.0 * rw, 2)
    tier   = _tier(score)

    return {
        'signal_tier':     tier,
        'signal_score':    score,
        'regime_at_entry': regime,
    }


def tag_untagged(trades: list, spx: dict, vix: dict) -> int:
    """
    In-place tags any trade where signal_tier is None.
    Returns count of trades tagged.
    """
    tagged = 0
    for t in trades:
        if t.get('signal_tier') is None:
            result = tag_trade(t, spx, vix)
            t.update(result)
            if result['signal_tier'] is not None:
                tagged += 1
    return tagged
