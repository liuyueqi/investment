from dataclasses import dataclass
from datetime import date

@dataclass
class DailyQuote:
    """
    每日行情数据
    适用于任何有OHLCV数据的金融实体（股票、指数、ETF等）
    """
    code: str            # 实体代码，如 '000001.SZ' 或 '000001.SH'
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int          # 成交量（手）
    amount: float        # 成交额（万元）
    pct_chg: float       # 涨跌幅（%）