from dataclasses import dataclass
from datetime import date


@dataclass
class DailyQuote:
    """每日行情数据（适用于股票、指数、ETF 等）"""
    code: str         # 实体代码，如 '000001.SZ'
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int       # 成交量（手）
    amount: float     # 成交额（万元）
    pct_chg: float    # 涨跌幅（%）
