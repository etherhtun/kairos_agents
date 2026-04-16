"""
sync/brokers/base.py
Standard data models and abstract base class for all brokers.
To add a new broker: subclass BrokerBase and implement all methods.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass
class Position:
    broker: str; symbol: str; contract: str
    asset_type: str    # 'OPT' | 'STK'
    expiry: str; quantity: float; avg_cost: float
    market_price: float; market_value: float
    unrealized_pnl: float; realized_pnl: float
    option_type: str  = ''   # 'C' | 'P'
    strike: float     = 0.0
    strategy: str     = ''   # set by broker parser


@dataclass
class Trade:
    broker: str; trade_id: str; date: str
    symbol: str; contract: str
    asset_type: str   # 'OPT' | 'STK'
    action: str; quantity: float; avg_price: float
    realized_pnl: float
    strategy: str    = ''
    option_type: str = ''
    strike: float    = 0.0
    expiry: str      = ''
    entry_time: str  = ''   # UTC ISO — used for ±2h signal matching


@dataclass
class AccountSummary:
    broker: str; account_id: str
    net_value: float = 0.0; cash: float = 0.0
    unrealized_pnl: float = 0.0; realized_pnl: float = 0.0
    currency: str = 'USD'


class BrokerBase(ABC):
    def __init__(self, name: str):
        self.name = name
        self.connected = False

    @abstractmethod
    def connect(self) -> bool: pass

    @abstractmethod
    def get_account(self) -> AccountSummary: pass

    @abstractmethod
    def get_positions(self) -> List[Position]: pass

    @abstractmethod
    def get_trades(self, start_date: str, end_date: str) -> List[Trade]: pass
