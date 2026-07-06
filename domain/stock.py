from dataclasses import dataclass
from datetime import date


@dataclass
class Stock:
    """个股基本信息"""
    code: str          # 完整代码，如 '000001.SZ'
    name: str          # 股票名称，如 '平安银行'
    market: str        # 市场：主板/创业板/科创板