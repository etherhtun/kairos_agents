"""sync/brokers/moomoo.py — Stub. Implement when connecting MooMoo."""
import os
from typing import List
from .base import BrokerBase, Position, Trade, AccountSummary

class MooMooBroker(BrokerBase):
    def __init__(self): super().__init__('moomoo')
    def connect(self) -> bool:
        if not os.getenv('MOOMOO_ACCOUNT_ID'):
            print(f'  [{self.name}] Not configured — skipping'); return False
        print(f'  [{self.name}] TODO: implement connect()'); return False
    def get_account(self)  -> AccountSummary:  return AccountSummary(broker=self.name, account_id='')
    def get_positions(self) -> List[Position]: return []
    def get_trades(self, s, e) -> List[Trade]: return []
