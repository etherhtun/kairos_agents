"""
sync/brokers/tiger.py
=====================
Tiger Brokers — Options (IC/BPS/BCS) + Stocks

Optimisations:
  - Leg cache (sync/leg_cache.json) — never re-fetches known orders
  - 30-day incremental sync for daily runs
  - Batched get_order() calls (10 per batch, 8s pause)
"""

import base64, json, os, subprocess, warnings, time
from datetime import datetime
from pathlib import Path
from typing import List

import pandas as pd
warnings.filterwarnings('ignore')

from .base import BrokerBase, Position, Trade, AccountSummary

try:
    from tigeropen.tiger_open_config import TigerOpenClientConfig
    from tigeropen.trade.trade_client import TradeClient
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

# Leg cache path — lives next to sync.py
LEG_CACHE_FILE = Path(__file__).parent.parent / 'leg_cache.json'


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_expiry(raw: str) -> str:
    raw = str(raw).strip()
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw

def _ts_to_sgt_date(ts) -> str:
    return (pd.to_datetime(int(ts), unit='ms', utc=True)
              .tz_convert('Asia/Singapore')
              .strftime('%Y-%m-%d'))

def _classify_legs(legs: list) -> dict:
    puts  = sorted([l for l in legs if str(l.get('put_call','')).upper() == 'PUT'],
                   key=lambda x: float(x.get('strike', 0)))
    calls = sorted([l for l in legs if str(l.get('put_call','')).upper() == 'CALL'],
                   key=lambda x: float(x.get('strike', 0)))

    if puts and calls:   strategy = 'iron_condor'
    elif puts:           strategy = 'bps'
    elif calls:          strategy = 'bcs'
    else:                strategy = 'unknown'

    put_s  = [float(l['strike']) for l in puts]
    call_s = [float(l['strike']) for l in calls]

    if put_s and call_s:
        strikes = f"P {put_s[0]:.0f}/{put_s[-1]:.0f}  C {call_s[0]:.0f}/{call_s[-1]:.0f}"
    elif put_s:  strikes = f"P {put_s[0]:.0f}/{put_s[-1]:.0f}"
    elif call_s: strikes = f"C {call_s[0]:.0f}/{call_s[-1]:.0f}"
    else:        strikes = ''

    short_puts  = [l for l in puts  if str(l.get('action','')).upper() == 'SELL']
    short_calls = [l for l in calls if str(l.get('action','')).upper() == 'SELL']
    opt_type = 'P' if short_puts else ('C' if short_calls else 'P')
    strike   = float(short_puts[0]['strike'])  if short_puts  else \
               float(short_calls[0]['strike']) if short_calls else 0.0
    expiry   = _parse_expiry(legs[0].get('expiry', '')) if legs else ''
    symbol   = legs[0].get('symbol', 'SPXW') if legs else 'SPXW'

    return {
        'symbol': symbol, 'expiry': expiry, 'strategy': strategy,
        'strikes': strikes, 'option_type': opt_type, 'strike': strike,
        'n_legs': len(legs),
    }

def _is_complete(parsed: dict) -> bool:
    s = parsed.get('strategy', '')
    strikes = parsed.get('strikes', '')
    if s == 'iron_condor': return 'C ' in strikes and 'P ' in strikes
    if s in ('bps', 'bcs'): return '/' in strikes
    return bool(strikes)


# ── Leg cache ──────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if LEG_CACHE_FILE.exists():
        try:
            with open(LEG_CACHE_FILE) as f:
                data = json.load(f)
            print(f'    [cache] Loaded {len(data)} cached leg records')
            return data
        except Exception as e:
            print(f'[tiger] warning: {e}')
    return {}

def _save_cache(cache: dict):
    with open(LEG_CACHE_FILE, 'w') as f:
        json.dump(cache, f)
    print(f'    [cache] Saved {len(cache)} leg records')


class TigerBroker(BrokerBase):

    def __init__(self):
        super().__init__('tiger')
        self._client = None

    def connect(self) -> bool:
        if not AVAILABLE:
            raise RuntimeError('pip install tigeropen')

        # Prefer tiger_openapi_config.properties from ~/.kairos-agent/
        props = (
            os.getenv('KAIROS_TIGER_PROPS') or
            str(Path.home() / '.kairos-agent' / 'tiger_openapi_config.properties')
        )

        config = TigerOpenClientConfig()
        if Path(props).exists():
            self._load_props(config, props)
        else:
            # Fallback: env vars (for kairos-agent.py CLI users)
            config.private_key = self._load_key()
            config.tiger_id    = os.getenv('TIGER_ID')
            config.account     = os.getenv('TIGER_ACCOUNT')
            config.license     = os.getenv('TIGER_LICENSE', 'TBSG')
            config.env_type    = os.getenv('TIGER_ENV', 'PROD')

        class RO(TradeClient):
            def place_order(s,*a,**k):  raise PermissionError('read-only')
            def cancel_order(s,*a,**k): raise PermissionError('read-only')
            def modify_order(s,*a,**k): raise PermissionError('read-only')

        self._client   = RO(config)
        self.connected = True
        print(f'  [{self.name}] ✅ Connected (account: {config.account})')
        return True

    def _load_props(self, config, path: str) -> None:
        """Parse tiger_openapi_config.properties and populate config."""
        p = {}
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    p[k.strip()] = v.strip()

        config.tiger_id    = p.get('tiger_id', '')
        config.account     = p.get('account', '')
        config.license     = p.get('license', 'TBSG')
        config.env_type    = p.get('env_type', 'PROD')

        raw = p.get('private_key_pk1', '')
        if raw:
            if '-----' in raw:
                raw = ''.join(l for l in raw.split('\n')
                              if not l.startswith('-----') and l.strip())
            pad = 4 - len(raw) % 4
            if pad != 4:
                raw += '=' * pad
            config.private_key = raw

    def _load_key(self) -> str:
        """Fallback key loader for env var based setup (kairos-agent.py CLI)."""
        raw = os.getenv('TIGER_PRIVATE_KEY', '').strip()
        if not raw:
            # Legacy macOS Keychain fallback
            r = subprocess.run(['security', 'find-generic-password',
                '-a', 'tiger_quant', '-s', 'tiger_private_key', '-w'],
                capture_output=True, text=True)
            raw = r.stdout.strip() if r.returncode == 0 else ''
        if not raw:
            raise RuntimeError('Tiger private key not found. Run setup.')
        if '-----' in raw:
            raw = ''.join(l for l in raw.split('\n')
                          if not l.startswith('-----') and l.strip())
        p = 4 - len(raw) % 4
        if p != 4:
            raw += '=' * p
        base64.b64decode(raw)
        return raw

    def close(self):
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    def get_account(self) -> AccountSummary:
        try:
            f        = vars(self._client.get_prime_assets())
            segments = f.get('_segments', {})
            # Prefer segment 'F' (combined/full view); fall back to any segment
            seg = segments.get('F') or segments.get('S') or (
                next(iter(segments.values())) if segments else None
            )
            net_value = float(getattr(seg, 'net_liquidation', 0) or 0) if seg else 0.0
            cash      = float(getattr(seg, 'cash_available_for_trade', 0) or 0) if seg else 0.0
            return AccountSummary(
                broker=self.name, account_id=str(f.get('account', '')),
                net_value=net_value,
                cash=cash,
                currency='USD',
            )
        except Exception as e:
            print(f'  [{self.name}] ⚠️  get_account: {e}')
            return AccountSummary(broker=self.name, account_id='')

    def get_positions(self) -> List[Position]:
        positions = []
        for sec in ['OPT', 'STK']:
            try:
                for p in (self._client.get_positions(sec_type=sec) or []):
                    pos = self._parse_position(p, sec)
                    if pos: positions.append(pos)
            except Exception as e:
                print(f'  [{self.name}] ⚠️  {sec} positions: {e}')
        return positions

    def _parse_position(self, p, asset_type: str):
        try:
            contract = str(getattr(p, 'contract', ''))
            parts    = contract.strip().split()
            symbol   = parts[0] if parts else 'SPXW'
            opt_code = parts[1].split('/')[0] if len(parts) > 1 else ''
            expiry = opt_type = ''; strike = 0.0
            if len(opt_code) >= 7:
                exp_raw  = opt_code[:6]
                expiry   = f"20{exp_raw[:2]}-{exp_raw[2:4]}-{exp_raw[4:]}"
                opt_type = opt_code[6]
                try: strike = int(opt_code[7:]) / 1000 if len(opt_code) > 7 else 0.0
                except (ValueError, TypeError): strike = 0.0
            return Position(
                broker=self.name, symbol=symbol, contract=contract,
                asset_type=asset_type, expiry=expiry,
                quantity=float(getattr(p,'position_qty',0) or getattr(p,'quantity',0) or 0),
                avg_cost=float(getattr(p,'average_cost',0) or 0),
                market_price=float(getattr(p,'market_price',0) or 0),
                market_value=float(getattr(p,'market_value',0) or 0),
                unrealized_pnl=float(getattr(p,'unrealized_pnl',0) or 0),
                realized_pnl=float(getattr(p,'realized_pnl',0) or 0),
                option_type=opt_type, strike=strike, strategy='',
            )
        except Exception as e:
            print(f'  [{self.name}] ⚠️  parse_position: {e}')
            return None

    def get_trades(self, start_date: str, end_date: str) -> List[Trade]:
        # ── Step 1: Load leg cache ────────────────────────────────────────────
        leg_cache = _load_cache()
        initial_cache_size = len(leg_cache)

        # ── Step 2: Fetch all filled orders via pagination ────────────────────
        print(f'    [{self.name}] Fetching order history (paginated)...')
        all_orders = []
        params     = {'start_time': 0, 'limit': 300}
        page       = 0
        start_dt   = datetime.strptime(start_date, '%Y-%m-%d')

        while True:
            page += 1
            try:
                batch = self._client.get_orders(**params)
            except Exception as e:
                print(f'    [{self.name}] ⚠️  get_orders page {page}: {e}')
                break
            if not batch:
                break

            filled = [o for o in batch if getattr(o, 'filled', 0) > 0]
            all_orders.extend(filled)

            oldest_ts = batch[-1].order_time
            oldest_dt = pd.to_datetime(oldest_ts, unit='ms', utc=True)\
                          .tz_convert('Asia/Singapore')
            print(f'    [{self.name}] Page {page:2d}: {len(batch):3d} records, '
                  f'{len(filled):2d} filled → oldest: {oldest_dt.date()}')

            if oldest_dt.date() < start_dt.date():
                break
            if len(batch) >= 300:
                params['end_time'] = oldest_ts - 1
            else:
                break

        # Filter by date range
        start_ts = int(datetime.strptime(start_date,'%Y-%m-%d').timestamp()*1000)
        end_ts   = int(datetime.strptime(end_date,  '%Y-%m-%d').timestamp()*1000) + 86400000
        in_range = [o for o in all_orders
                    if start_ts <= (getattr(o,'order_time',0) or 0) <= end_ts]
        print(f'    [{self.name}] {len(in_range)} orders in range')

        # ── Step 3: Fetch legs only for NEW orders not in cache ───────────────
        mleg_orders = [o for o in in_range
                       if 'MLEG' in str(getattr(o,'contract',''))]
        new_orders  = [o for o in mleg_orders
                       if str(getattr(o,'id','')) not in leg_cache]

        cached_count = len(mleg_orders) - len(new_orders)
        print(f'    [{self.name}] MLEG orders: {len(mleg_orders)} total, '
              f'{cached_count} cached, {len(new_orders)} new to fetch')

        if new_orders:
            BATCH = 10
            PAUSE = 8
            print(f'    [{self.name}] Fetching {len(new_orders)} new leg details '
                  f'(batch={BATCH}, pause={PAUSE}s)...')

            for i in range(0, len(new_orders), BATCH):
                batch = new_orders[i:i+BATCH]
                for o in batch:
                    oid    = str(getattr(o,'id',''))
                    parsed = self._get_legs(oid)
                    leg_cache[oid] = parsed

                done = min(i+BATCH, len(new_orders))
                ok   = sum(1 for v in leg_cache.values() if _is_complete(v))
                print(f'    [{self.name}] Fetched {done}/{len(new_orders)} new legs '
                      f'({ok} complete in cache)')

                if i + BATCH < len(new_orders):
                    print(f'    [{self.name}] Pausing {PAUSE}s...')
                    time.sleep(PAUSE)
        else:
            print(f'    [{self.name}] ✅ All legs from cache — no API calls needed')

        # ── Step 4: Fix incomplete legs using best match by expiry ────────────
        expiry_best: dict = {}
        for oid, parsed in leg_cache.items():
            if not _is_complete(parsed): continue
            exp = parsed.get('expiry','')
            if not exp: continue
            existing = expiry_best.get(exp)
            if not existing or parsed.get('n_legs',0) > existing.get('n_legs',0):
                expiry_best[exp] = parsed

        fixed = 0
        for oid, parsed in leg_cache.items():
            if not _is_complete(parsed):
                best = expiry_best.get(parsed.get('expiry',''))
                if best:
                    leg_cache[oid] = best.copy()
                    fixed += 1

        if fixed:
            print(f'    [{self.name}] Fixed {fixed} incomplete leg records')

        # Save updated cache if changed
        if len(leg_cache) > initial_cache_size or fixed:
            _save_cache(leg_cache)

        # ── Step 5: Build trade records ───────────────────────────────────────
        trades = []
        seen   = set()

        for o in in_range:
            oid = str(getattr(o,'id',''))
            if oid in seen: continue
            seen.add(oid)

            contract     = str(getattr(o,'contract',''))
            trade_date   = _ts_to_sgt_date(getattr(o,'order_time',0) or 0)
            realized_pnl = round(float(getattr(o,'realized_pnl',0) or 0), 2)
            action       = str(getattr(o,'action',''))
            qty          = float(getattr(o,'filled',0) or 0)
            avg_price    = float(getattr(o,'avg_fill_price',0) or 0)

            if 'MLEG' in contract:
                parsed = leg_cache.get(oid, {
                    'symbol':'SPXW','expiry':'','strategy':'unknown',
                    'option_type':'','strike':0.0,'strikes':''
                })
                trades.append(Trade(
                    broker=self.name, trade_id=oid, date=trade_date,
                    symbol=parsed.get('symbol','SPXW'),
                    contract=parsed.get('strikes') or contract,
                    asset_type='OPT', action=action,
                    quantity=qty, avg_price=avg_price,
                    realized_pnl=realized_pnl,
                    strategy=parsed.get('strategy','unknown'),
                    option_type=parsed.get('option_type',''),
                    strike=parsed.get('strike',0.0),
                    expiry=parsed.get('expiry',''),
                ))

            elif '/OPT/' in contract:
                parsed = self._parse_single_leg(contract)
                trades.append(Trade(
                    broker=self.name, trade_id=oid, date=trade_date,
                    symbol=parsed['symbol'],
                    contract=parsed.get('strikes') or contract,
                    asset_type='OPT', action=action,
                    quantity=qty, avg_price=avg_price,
                    realized_pnl=realized_pnl,
                    strategy=parsed['strategy'],
                    option_type=parsed['option_type'],
                    strike=parsed['strike'],
                    expiry=parsed['expiry'],
                ))

            else:
                symbol = contract.split('/')[0].strip()
                trades.append(Trade(
                    broker=self.name, trade_id=oid, date=trade_date,
                    symbol=symbol, contract=contract,
                    asset_type='STK', action=action,
                    quantity=qty, avg_price=avg_price,
                    realized_pnl=realized_pnl,
                    strategy='long_stock' if action=='BUY' else 'short_stock',
                ))

        print(f'    [{self.name}] ✅ {len(trades)} trade records built')
        return trades

    def _get_legs(self, order_id: str) -> dict:
        empty = {'symbol':'SPXW','expiry':'','strategy':'unknown',
                 'option_type':'','strike':0.0,'strikes':'','n_legs':0}
        try:
            detail = self._client.get_order(id=int(order_id))
            legs   = getattr(detail, 'contract_legs', None) or []
            if not legs: return empty
            leg_dicts = []
            for leg in legs:
                if isinstance(leg, dict):
                    leg_dicts.append(leg)
                else:
                    leg_dicts.append({
                        'put_call': getattr(leg,'put_call',''),
                        'strike':   getattr(leg,'strike',0),
                        'action':   getattr(leg,'action',''),
                        'expiry':   getattr(leg,'expiry',''),
                        'symbol':   getattr(leg,'symbol','SPXW'),
                    })
            return _classify_legs(leg_dicts)
        except Exception as e:
            if 'rate limit' not in str(e).lower():
                print(f'    [{self.name}] ⚠️  get_order({order_id}): {e}')
            return empty

    def _parse_single_leg(self, contract: str) -> dict:
        parts    = contract.strip().split()
        symbol   = parts[0] if parts else 'SPXW'
        opt_code = parts[1].split('/')[0] if len(parts) > 1 else ''
        expiry = opt_type = ''; strike = 0.0
        if len(opt_code) >= 7:
            e = opt_code[:6]
            expiry   = f"20{e[:2]}-{e[2:4]}-{e[4:]}"
            opt_type = opt_code[6]
            try: strike = int(opt_code[7:]) / 1000 if len(opt_code) > 7 else 0.0
            except: strike = 0.0
        strikes = f"{opt_type} {strike:.0f}" if opt_type and strike else ''
        return {
            'symbol': symbol, 'expiry': expiry,
            'strategy': 'bps' if opt_type=='P' else 'bcs' if opt_type=='C' else 'unknown',
            'option_type': opt_type, 'strike': strike, 'strikes': strikes,
        }
