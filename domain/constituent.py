from dataclasses import dataclass
from datetime import date


@dataclass
class Constituent:
    """指数/板块在某一交易日的成分股及其权重"""
    stock_code: str
    weight: float
    trade_date: date

    def __str__(self) -> str:
        return f"{self.stock_code}@{self.trade_date}({self.weight})"
