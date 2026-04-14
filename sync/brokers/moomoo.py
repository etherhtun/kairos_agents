"""
sync/brokers/moomoo.py
======================
Moomoo/Futu broker via OpenD gateway.

Requires OpenD running on the host machine:
  - macOS/Windows native agent: connects to 127.0.0.1:11111
  - Docker agent: connects to host.docker.internal:11111

No credentials file needed — OpenD handles authentication.
"""

import os
import re
from typing import List, Optional

from .base import BrokerBase, Position, Trade, AccountSummary

try:
    import moomoo as ft
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

# In Docker, OpenD runs on the host — use host.docker.internal
_IN_DOCKER = os.path.exists('/.dockerenv')
OPEND_HOST = os.getenv('MOOMOO_OPEND_HOST',
                        'host.docker.internal' if _IN_DOCKER else '127.0.0.1')
OPEND_PORT = int(os.getenv('MOOMOO_OPEND_PORT', '11111'))

# Security firms to try in order (FUTUSG first — most common for SG users)
_FIRMS = ['FUTUSG', 'FUTUINC', 'FUTUSECURITIES', 'FUTUCA', 'FUTUAU', 'FUTUMY', 'FUTUJP']

# Regex to detect Moomoo option codes: e.g. US.IRM260424C110000
_OPT_RE = re.compile(r'^[A-Z]+\.[A-Z]+\d{6}[CP]\d+$')
_OPT_PARSE = re.compile(r'^([A-Z]+)(\d{6})([CP])(\d+)$')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_option_code(code: str) -> dict:
    """Parse Moomoo option code — e.g. US.IRM260424C110000"""
    raw = code.split('.', 1)[-1]          # strip market prefix
    m = _OPT_PARSE.match(raw)
    if not m:
        return {'symbol': raw, 'expiry': '', 'option_type': '',
                'strike': 0.0, 'strategy': 'unknown', 'contract': code}

    symbol, date_str, opt_type, strike_raw = m.groups()
    expiry   = f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:]}"
    strike   = int(strike_raw) / 1000.0
    strategy = 'bps' if opt_type == 'P' else 'bcs'
    contract = f"{opt_type} {strike:.2f} {expiry}"

    return {
        'symbol': symbol, 'expiry': expiry,
        'option_type': opt_type, 'strike': strike,
        'strategy': strategy, 'contract': contract,
    }


def _stock_symbol(code: str) -> str:
    """US.SPYI → SPYI"""
    return code.split('.', 1)[-1]


def _trade_date(timestamp: str) -> str:
    """'2026-04-09 10:55:07.023' → '2026-04-09'"""
    return str(timestamp)[:10] if timestamp else ''


# ── Broker ────────────────────────────────────────────────────────────────────

class MooMooBroker(BrokerBase):

    def __init__(self):
        super().__init__('moomoo')
        self._ctx:          Optional[object] = None
        self._acc_id:       Optional[int]    = None
        self._security_firm: Optional[str]   = None

    def connect(self) -> bool:
        if not AVAILABLE:
            raise RuntimeError('pip install moomoo-api')

        # Auto-detect security firm that holds a real account
        for firm_name in _FIRMS:
            firm = getattr(ft.SecurityFirm, firm_name, None)
            if firm is None:
                continue
            try:
                ctx = ft.OpenSecTradeContext(
                    filter_trdmarket=ft.TrdMarket.US,
                    host=OPEND_HOST, port=OPEND_PORT,
                    security_firm=firm,
                )
                ret, data = ctx.get_acc_list()
                if ret != 0 or data.empty:
                    ctx.close()
                    continue

                real = data[data['trd_env'] == 'REAL']
                if not real.empty:
                    self._acc_id        = int(real.iloc[0]['acc_id'])
                    self._security_firm = firm_name
                    self._ctx           = ctx
                    print(f'  [{self.name}] ✅ Connected via {firm_name} '
                          f'(acc: {self._acc_id})')
                    return True
                ctx.close()
            except Exception as e:
                # OpenD not running or unreachable
                if 'connect' in str(e).lower() or 'refused' in str(e).lower():
                    print(f'  [{self.name}] OpenD not reachable at '
                          f'{OPEND_HOST}:{OPEND_PORT} — skipping')
                    return False

        print(f'  [{self.name}] No real account found — skipping')
        return False

    # ── Account ──────────────────────────────────────────────────────────────

    def get_account(self) -> AccountSummary:
        try:
            ret, data = self._ctx.accinfo_query(
                trd_env=ft.TrdEnv.REAL,
                acc_id=self._acc_id,
            )
            if ret != 0 or data.empty:
                return AccountSummary(broker=self.name,
                                      account_id=str(self._acc_id))
            row = data.iloc[0]
            return AccountSummary(
                broker=self.name,
                account_id=str(self._acc_id),
                net_value=float(row.get('total_assets', 0) or 0),
                cash=float(row.get('cash', 0) or 0),
                currency='USD',
            )
        except Exception as e:
            print(f'  [{self.name}] ⚠️  get_account: {e}')
            return AccountSummary(broker=self.name,
                                  account_id=str(self._acc_id or ''))

    # ── Positions ────────────────────────────────────────────────────────────

    def get_positions(self) -> List[Position]:
        positions = []
        try:
            ret, data = self._ctx.position_list_query(
                trd_env=ft.TrdEnv.REAL,
                acc_id=self._acc_id,
            )
            if ret != 0 or data.empty:
                return []

            for _, row in data.iterrows():
                code = str(row.get('code', ''))
                qty  = float(row.get('qty', 0) or 0)
                if qty == 0:
                    continue

                # Short positions have negative quantity
                if str(row.get('position_side', 'LONG')) == 'SHORT':
                    qty = -qty

                common = dict(
                    broker       = self.name,
                    avg_cost     = float(row.get('cost_price', 0) or 0),
                    market_price = float(row.get('current_price', 0) or 0),
                    market_value = float(row.get('market_val', 0) or 0),
                    unrealized_pnl = float(row.get('unrealized_pl', 0) or 0),
                    realized_pnl   = float(row.get('realized_pl', 0) or 0),
                )

                if _OPT_RE.match(code):
                    parsed = _parse_option_code(code)
                    positions.append(Position(
                        **common,
                        symbol    = parsed['symbol'],
                        contract  = parsed['contract'],
                        asset_type = 'OPT',
                        expiry    = parsed['expiry'],
                        quantity  = qty,
                        option_type = parsed['option_type'],
                        strike    = parsed['strike'],
                        strategy  = parsed['strategy'],
                    ))
                else:
                    positions.append(Position(
                        **common,
                        symbol    = _stock_symbol(code),
                        contract  = code,
                        asset_type = 'STK',
                        expiry    = '',
                        quantity  = qty,
                    ))

        except Exception as e:
            print(f'  [{self.name}] ⚠️  get_positions: {e}')
        return positions

    # ── Trades ───────────────────────────────────────────────────────────────

    def get_trades(self, start_date: str, end_date: str) -> List[Trade]:
        trades = []
        try:
            ret, data = self._ctx.history_order_list_query(
                status_filter_list=[
                    ft.OrderStatus.FILLED_ALL,
                    ft.OrderStatus.FILLED_PART,
                ],
                start    = start_date,
                end      = end_date,
                trd_env  = ft.TrdEnv.REAL,
                acc_id   = self._acc_id,
            )

            if ret != 0:
                print(f'  [{self.name}] ⚠️  history_order: {ret}')
                return []
            if data.empty:
                print(f'  [{self.name}] No trades in range')
                return []

            print(f'  [{self.name}] {len(data)} filled orders fetched')

            # Map Moomoo trade sides → standard action
            _ACTION = {
                'BUY': 'BUY', 'SELL': 'SELL',
                'SELL_SHORT': 'SELL', 'BUY_BACK': 'BUY',
            }

            for _, row in data.iterrows():
                code      = str(row.get('code', ''))
                qty       = float(row.get('dealt_qty', 0) or
                                  row.get('qty', 0) or 0)
                if qty == 0:
                    continue

                trd_side  = str(row.get('trd_side', ''))
                action    = _ACTION.get(trd_side, trd_side)
                trade_date = _trade_date(str(row.get('create_time', '')))
                avg_price  = float(row.get('dealt_avg_price', 0) or 0)
                order_id   = str(row.get('order_id', ''))

                if _OPT_RE.match(code):
                    parsed = _parse_option_code(code)
                    trades.append(Trade(
                        broker      = self.name,
                        trade_id    = order_id,
                        date        = trade_date,
                        symbol      = parsed['symbol'],
                        contract    = parsed['contract'],
                        asset_type  = 'OPT',
                        action      = action,
                        quantity    = qty,
                        avg_price   = avg_price,
                        realized_pnl = 0.0,
                        strategy    = parsed['strategy'],
                        option_type = parsed['option_type'],
                        strike      = parsed['strike'],
                        expiry      = parsed['expiry'],
                    ))
                else:
                    trades.append(Trade(
                        broker      = self.name,
                        trade_id    = order_id,
                        date        = trade_date,
                        symbol      = _stock_symbol(code),
                        contract    = code,
                        asset_type  = 'STK',
                        action      = action,
                        quantity    = qty,
                        avg_price   = avg_price,
                        realized_pnl = 0.0,
                        strategy    = 'long_stock' if action == 'BUY' else 'short_stock',
                    ))

        except Exception as e:
            print(f'  [{self.name}] ⚠️  get_trades: {e}')

        print(f'  [{self.name}] ✅ {len(trades)} trade records built')
        return trades
