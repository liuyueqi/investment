from dataclasses import dataclass


@dataclass
class Stock:
    """个股基本信息"""
    code: str         # 完整代码，如 '000001'
    name: str         # 股票名称，如 '平安银行'
    market: str       # 市场：SH / SZ / BJ
