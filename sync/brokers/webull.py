"""sync/brokers/webull.py — Stub. Implement when connecting Webull."""
import os
from typing import List
from .base import BrokerBase, Position, Trade, AccountSummary

class WebullBroker(BrokerBase):
    def __init__(self): super().__init__('webull')
    def connect(self) -> bool:
        if not os.getenv('WEBULL_USERNAME'):
            print(f'  [{self.name}] Not configured — skipping'); return False
        print(f'  [{self.name}] TODO: implement connect()'); return False
    def get_account(self)  -> AccountSummary:  return AccountSummary(broker=self.name, account_id='')
    def get_positions(self) -> List[Position]: return []
    def get_trades(self, s, e) -> List[Trade]: return []
