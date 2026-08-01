from dataclasses import dataclass
from datetime import date


@dataclass
class TradingDay:
    """A 股交易日"""
    trade_date: date

    def __str__(self) -> str:
        return self.trade_date.isoformat()
