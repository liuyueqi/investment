from dataclasses import dataclass
from datetime import date


@dataclass
class DCSectorMemberData:
    """
        东财概念板块成分（Tushare dc_member）
        接口文档：https://tushare.pro/document/2?doc_id=363
    """
    trade_date: date        # 交易日期
    ts_code: str            # 概念代码
    con_code: str           # 成分代码
    name: str               # 成分股名称
