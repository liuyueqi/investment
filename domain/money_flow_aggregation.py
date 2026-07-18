"""资金流聚合数据实体

按 entity_type + entity_code + trade_date 唯一标识一条记录：
- entity_type = 'stock'  → 个股维度的累计净流入
- entity_type = 'sector' → 板块维度的累计净流入（按日汇总旗下个股）
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class MoneyFlowAggregation:
    """资金流聚合数据（与原始 money_flows 数据分离）"""

    entity_type: str              # 实体类型: 'stock' / 'sector'
    entity_code: str              # 实体代码: 股票代码 / 板块代码
    entity_name: Optional[str] = None  # 实体名称（冗余，方便查表）

    # ── 维度 ──────────────────────────────────────────────
    trade_date: Optional[date] = None   # 该聚合数据对应的日期
    period: str = "day"                 # 聚合粒度: "day"

    # ── 累计统计（从有数据之日起至今） ────────────────────
    cumulative_main_net: float = 0.0    # 累计主力净流入（万元）
    cumulative_main_cnt: int = 0        # 累计主力笔数
    cumulative_net_amount: float = 0.0  # 累计净主动买入额（万元）

    # 明细累计
    cumulative_huge_net: Optional[float] = None   # 累计超大单净流入
    cumulative_huge_buy_net: Optional[float] = None
    cumulative_huge_sell_net: Optional[float] = None
    cumulative_huge_cnt: Optional[int] = None
    cumulative_huge_buy_cnt: Optional[int] = None
    cumulative_huge_sell_cnt: Optional[int] = None

    cumulative_large_net: Optional[float] = None
    cumulative_large_buy_net: Optional[float] = None
    cumulative_large_sell_net: Optional[float] = None
    cumulative_large_cnt: Optional[int] = None
    cumulative_large_buy_cnt: Optional[int] = None
    cumulative_large_sell_cnt: Optional[int] = None

    cumulative_medium_net: Optional[float] = None
    cumulative_medium_buy_net: Optional[float] = None
    cumulative_medium_sell_net: Optional[float] = None
    cumulative_medium_cnt: Optional[int] = None
    cumulative_medium_buy_cnt: Optional[int] = None
    cumulative_medium_sell_cnt: Optional[int] = None

    cumulative_small_net: Optional[float] = None
    cumulative_small_buy_net: Optional[float] = None
    cumulative_small_sell_net: Optional[float] = None
    cumulative_small_cnt: Optional[int] = None
    cumulative_small_buy_cnt: Optional[int] = None
    cumulative_small_sell_cnt: Optional[int] = None

    # ── 元数据 ────────────────────────────────────────────
    data_start_date: Optional[date] = None  # 统计期的起始日期
    data_end_date: Optional[date] = None    # 统计期的截止日期（即 trade_date）
    trading_days_count: int = 0             # 覆盖的交易天数
