from dataclasses import dataclass
from datetime import date


@dataclass
class DCSectorData:
    """
        东财概念板块行情（Tushare dc_index）
        接口文档：https://tushare.pro/document/2?doc_id=362
    """

    ts_code: str            # 概念代码
    trade_date: date        # 交易日期
    name: str               # 概念名称
    leading: str            # 领涨股票名称
    leading_code: str       # 领涨股票代码
    pct_change: float       # 涨跌幅
    leading_pct: float      # 领涨股票涨跌幅
    total_mv: float         # 总市值（万元）
    turnover_rate: float    # 换手率
    up_num: int             # 上涨家数
    down_num: int           # 下降家数
    idx_type: str           # 板块类型(行业板块、概念板块、地域板块)
    level: str              # 行业层级
