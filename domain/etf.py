from dataclasses import dataclass

@dataclass
class ETF:
    code: str          # 如 '510300'
    name: str          # 如 '沪深300ETF'
    market: str        # 'SH' 或 'SZ'