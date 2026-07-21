"""资金流聚合数据实体

按 (code, type, start_date, end_date) 唯一标识一条记录：
- type = 'stock'  → 个股累计净流入
- type = 'sector'  → 板块累计净流入（汇总旗下个股）

start_date ~ end_date 表示该累计值覆盖的时间范围。
例如对某只股票：
  (start_date='2026-01-01', end_date='2026-01-05') 表示
  从 2026-01-01 至 2026-01-05 的历史累计净流入。
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from domain.money_flow import MoneyFlow


@dataclass
class MoneyFlowAggregation:
    
    TYPE_STOCK = 'stock'
    TYPE_SECTOR = 'sector'

    """资金流聚合数据（与原始 money_flows 数据分离）"""

    code: str                     # 实体代码: 股票代码 / 板块代码
    type: str                     # 实体类型: 'stock' / 'sector'

    # ── 维度（联合主键） ──────────────────────────────────
    start_date: date              # 统计起始日期
    end_date: date                # 统计结束日期
    trading_days: int = 1          # 覆盖的交易天数

    # ── 累计统计（从 start_date 至 end_date） ────────────
    main_net: float = 0.0          # 累计主力净流入（万元）
    main_cnt: int = 0              # 累计主力笔数
    net_amount: float = 0.0        # 累计净主动买入额（万元）

    # 累计明细
    huge_net: Optional[float] = None
    huge_buy_net: Optional[float] = None
    huge_sell_net: Optional[float] = None
    huge_cnt: Optional[int] = None
    huge_buy_cnt: Optional[int] = None
    huge_sell_cnt: Optional[int] = None

    large_net: Optional[float] = None
    large_buy_net: Optional[float] = None
    large_sell_net: Optional[float] = None
    large_cnt: Optional[int] = None
    large_buy_cnt: Optional[int] = None
    large_sell_cnt: Optional[int] = None

    medium_net: Optional[float] = None
    medium_buy_net: Optional[float] = None
    medium_sell_net: Optional[float] = None
    medium_cnt: Optional[int] = None
    medium_buy_cnt: Optional[int] = None
    medium_sell_cnt: Optional[int] = None

    small_net: Optional[float] = None
    small_buy_net: Optional[float] = None
    small_sell_net: Optional[float] = None
    small_cnt: Optional[int] = None
    small_buy_cnt: Optional[int] = None
    small_sell_cnt: Optional[int] = None

    name: Optional[str] = None    # 实体名称（冗余，方便查表）
    accumulative: bool = False        

    @staticmethod
    def start_with_money_flows(*flows: MoneyFlow, accumulative: bool = False) -> "MoneyFlowAggregation":
        """基于多条资金流向数据构造聚合实例，将所有 flow 各项值直接相加后返回"""
        if not flows:
            raise ValueError('至少需要一条 MoneyFlow 数据')

        first = flows[0]
        last = flows[-1]
        mf_type = (
            MoneyFlowAggregation.TYPE_STOCK
            if first.code and first.code[0].isdigit()
            else MoneyFlowAggregation.TYPE_SECTOR
        )

        # 直接累加所有 flow 的各项值
        total_main_net = sum(f.main_net for f in flows)
        total_main_cnt = sum(f.main_cnt for f in flows)
        total_net_amount = sum(f.net_amount for f in flows)

        total_huge_net = MoneyFlowAggregation._sum_opt(f.huge_net for f in flows)
        total_huge_buy_net = MoneyFlowAggregation._sum_opt(f.huge_buy_net for f in flows)
        total_huge_sell_net = MoneyFlowAggregation._sum_opt(f.huge_sell_net for f in flows)
        total_huge_cnt = MoneyFlowAggregation._sum_opt_int(f.huge_cnt for f in flows)
        total_huge_buy_cnt = MoneyFlowAggregation._sum_opt_int(f.huge_buy_cnt for f in flows)
        total_huge_sell_cnt = MoneyFlowAggregation._sum_opt_int(f.huge_sell_cnt for f in flows)

        total_large_net = MoneyFlowAggregation._sum_opt(f.large_net for f in flows)
        total_large_buy_net = MoneyFlowAggregation._sum_opt(f.large_buy_net for f in flows)
        total_large_sell_net = MoneyFlowAggregation._sum_opt(f.large_sell_net for f in flows)
        total_large_cnt = MoneyFlowAggregation._sum_opt_int(f.large_cnt for f in flows)
        total_large_buy_cnt = MoneyFlowAggregation._sum_opt_int(f.large_buy_cnt for f in flows)
        total_large_sell_cnt = MoneyFlowAggregation._sum_opt_int(f.large_sell_cnt for f in flows)

        total_medium_net = MoneyFlowAggregation._sum_opt(f.medium_net for f in flows)
        total_medium_buy_net = MoneyFlowAggregation._sum_opt(f.medium_buy_net for f in flows)
        total_medium_sell_net = MoneyFlowAggregation._sum_opt(f.medium_sell_net for f in flows)
        total_medium_cnt = MoneyFlowAggregation._sum_opt_int(f.medium_cnt for f in flows)
        total_medium_buy_cnt = MoneyFlowAggregation._sum_opt_int(f.medium_buy_cnt for f in flows)
        total_medium_sell_cnt = MoneyFlowAggregation._sum_opt_int(f.medium_sell_cnt for f in flows)

        total_small_net = MoneyFlowAggregation._sum_opt(f.small_net for f in flows)
        total_small_buy_net = MoneyFlowAggregation._sum_opt(f.small_buy_net for f in flows)
        total_small_sell_net = MoneyFlowAggregation._sum_opt(f.small_sell_net for f in flows)
        total_small_cnt = MoneyFlowAggregation._sum_opt_int(f.small_cnt for f in flows)
        total_small_buy_cnt = MoneyFlowAggregation._sum_opt_int(f.small_buy_cnt for f in flows)
        total_small_sell_cnt = MoneyFlowAggregation._sum_opt_int(f.small_sell_cnt for f in flows)

        return MoneyFlowAggregation(
            code=first.code,
            type=mf_type,
            start_date=first.time.date(),
            end_date=last.time.date(),
            trading_days=len(flows),
            main_net=total_main_net,
            main_cnt=total_main_cnt,
            net_amount=total_net_amount,
            huge_net=total_huge_net,
            huge_buy_net=total_huge_buy_net,
            huge_sell_net=total_huge_sell_net,
            huge_cnt=total_huge_cnt,
            huge_buy_cnt=total_huge_buy_cnt,
            huge_sell_cnt=total_huge_sell_cnt,
            large_net=total_large_net,
            large_buy_net=total_large_buy_net,
            large_sell_net=total_large_sell_net,
            large_cnt=total_large_cnt,
            large_buy_cnt=total_large_buy_cnt,
            large_sell_cnt=total_large_sell_cnt,
            medium_net=total_medium_net,
            medium_buy_net=total_medium_buy_net,
            medium_sell_net=total_medium_sell_net,
            medium_cnt=total_medium_cnt,
            medium_buy_cnt=total_medium_buy_cnt,
            medium_sell_cnt=total_medium_sell_cnt,
            small_net=total_small_net,
            small_buy_net=total_small_buy_net,
            small_sell_net=total_small_sell_net,
            small_cnt=total_small_cnt,
            small_buy_cnt=total_small_buy_cnt,
            small_sell_cnt=total_small_sell_cnt,
            accumulative=accumulative,
        )
    
    @staticmethod
    def sector_aggregation_from_members(code: str, name: str, *members: "MoneyFlowAggregation") -> "MoneyFlowAggregation":
        """基于多条成分股聚合数据，生成板块的累计聚合对象（直接求和，不产生中间对象）"""
        if not members:
            raise ValueError('至少需要一条成分股数据')

        return MoneyFlowAggregation(
            code=code,
            type=MoneyFlowAggregation.TYPE_SECTOR,
            name=name,
            start_date=min(m.start_date for m in members),
            end_date=max(m.end_date for m in members),
            trading_days=sum(m.trading_days for m in members) // len(members),
            main_net=sum(m.main_net for m in members),
            main_cnt=sum(m.main_cnt for m in members),
            net_amount=sum(m.net_amount for m in members),
            huge_net=MoneyFlowAggregation._sum_opt(m.huge_net for m in members),
            huge_buy_net=MoneyFlowAggregation._sum_opt(m.huge_buy_net for m in members),
            huge_sell_net=MoneyFlowAggregation._sum_opt(m.huge_sell_net for m in members),
            huge_cnt=MoneyFlowAggregation._sum_opt_int(m.huge_cnt for m in members),
            huge_buy_cnt=MoneyFlowAggregation._sum_opt_int(m.huge_buy_cnt for m in members),
            huge_sell_cnt=MoneyFlowAggregation._sum_opt_int(m.huge_sell_cnt for m in members),
            large_net=MoneyFlowAggregation._sum_opt(m.large_net for m in members),
            large_buy_net=MoneyFlowAggregation._sum_opt(m.large_buy_net for m in members),
            large_sell_net=MoneyFlowAggregation._sum_opt(m.large_sell_net for m in members),
            large_cnt=MoneyFlowAggregation._sum_opt_int(m.large_cnt for m in members),
            large_buy_cnt=MoneyFlowAggregation._sum_opt_int(m.large_buy_cnt for m in members),
            large_sell_cnt=MoneyFlowAggregation._sum_opt_int(m.large_sell_cnt for m in members),
            medium_net=MoneyFlowAggregation._sum_opt(m.medium_net for m in members),
            medium_buy_net=MoneyFlowAggregation._sum_opt(m.medium_buy_net for m in members),
            medium_sell_net=MoneyFlowAggregation._sum_opt(m.medium_sell_net for m in members),
            medium_cnt=MoneyFlowAggregation._sum_opt_int(m.medium_cnt for m in members),
            medium_buy_cnt=MoneyFlowAggregation._sum_opt_int(m.medium_buy_cnt for m in members),
            medium_sell_cnt=MoneyFlowAggregation._sum_opt_int(m.medium_sell_cnt for m in members),
            small_net=MoneyFlowAggregation._sum_opt(m.small_net for m in members),
            small_buy_net=MoneyFlowAggregation._sum_opt(m.small_buy_net for m in members),
            small_sell_net=MoneyFlowAggregation._sum_opt(m.small_sell_net for m in members),
            small_cnt=MoneyFlowAggregation._sum_opt_int(m.small_cnt for m in members),
            small_buy_cnt=MoneyFlowAggregation._sum_opt_int(m.small_buy_cnt for m in members),
            small_sell_cnt=MoneyFlowAggregation._sum_opt_int(m.small_sell_cnt for m in members),
            accumulative=any(m.accumulative for m in members),
        )

    def accumulate(self, flow: MoneyFlow) -> "MoneyFlowAggregation":
        """基于一条资金流向数据重新计算聚合值，累加后返回新对象（不修改当前实例）"""
        
        flow_date = flow.time.date()
        return MoneyFlowAggregation(
            code=self.code,
            type=self.type,
            start_date=min(self.start_date, flow_date),
            end_date=max(self.end_date, flow_date),
            trading_days=self.trading_days + 1,
            main_net=self.main_net + flow.main_net,
            main_cnt=self.main_cnt + flow.main_cnt,
            net_amount=self.net_amount + flow.net_amount,
            huge_net=self._add_flow_opt(self.huge_net, flow.huge_net),
            huge_buy_net=self._add_flow_opt(self.huge_buy_net, flow.huge_buy_net),
            huge_sell_net=self._add_flow_opt(self.huge_sell_net, flow.huge_sell_net),
            huge_cnt=self._add_flow_opt_int(self.huge_cnt, flow.huge_cnt),
            huge_buy_cnt=self._add_flow_opt_int(self.huge_buy_cnt, flow.huge_buy_cnt),
            huge_sell_cnt=self._add_flow_opt_int(self.huge_sell_cnt, flow.huge_sell_cnt),
            large_net=self._add_flow_opt(self.large_net, flow.large_net),
            large_buy_net=self._add_flow_opt(self.large_buy_net, flow.large_buy_net),
            large_sell_net=self._add_flow_opt(self.large_sell_net, flow.large_sell_net),
            large_cnt=self._add_flow_opt_int(self.large_cnt, flow.large_cnt),
            large_buy_cnt=self._add_flow_opt_int(self.large_buy_cnt, flow.large_buy_cnt),
            large_sell_cnt=self._add_flow_opt_int(self.large_sell_cnt, flow.large_sell_cnt),
            medium_net=self._add_flow_opt(self.medium_net, flow.medium_net),
            medium_buy_net=self._add_flow_opt(self.medium_buy_net, flow.medium_buy_net),
            medium_sell_net=self._add_flow_opt(self.medium_sell_net, flow.medium_sell_net),
            medium_cnt=self._add_flow_opt_int(self.medium_cnt, flow.medium_cnt),
            medium_buy_cnt=self._add_flow_opt_int(self.medium_buy_cnt, flow.medium_buy_cnt),
            medium_sell_cnt=self._add_flow_opt_int(self.medium_sell_cnt, flow.medium_sell_cnt),
            small_net=self._add_flow_opt(self.small_net, flow.small_net),
            small_buy_net=self._add_flow_opt(self.small_buy_net, flow.small_buy_net),
            small_sell_net=self._add_flow_opt(self.small_sell_net, flow.small_sell_net),
            small_cnt=self._add_flow_opt_int(self.small_cnt, flow.small_cnt),
            small_buy_cnt=self._add_flow_opt_int(self.small_buy_cnt, flow.small_buy_cnt),
            small_sell_cnt=self._add_flow_opt_int(self.small_sell_cnt, flow.small_sell_cnt),
            accumulative=self.accumulative
        )
    
    def merge(self, other: "MoneyFlowAggregation") -> "MoneyFlowAggregation":
        """与另一个聚合实例合并，将两者的各值相加后返回新对象（不修改当前实例）"""
        return MoneyFlowAggregation(
            code=self.code,
            type=self.type,
            start_date=min(self.start_date, other.start_date),
            end_date=max(self.end_date, other.end_date),
            trading_days=self.trading_days + other.trading_days,
            main_net=self.main_net + other.main_net,
            main_cnt=self.main_cnt + other.main_cnt,
            net_amount=self.net_amount + other.net_amount,
            huge_net=self._add_flow_opt(self.huge_net, other.huge_net),
            huge_buy_net=self._add_flow_opt(self.huge_buy_net, other.huge_buy_net),
            huge_sell_net=self._add_flow_opt(self.huge_sell_net, other.huge_sell_net),
            huge_cnt=self._add_flow_opt_int(self.huge_cnt, other.huge_cnt),
            huge_buy_cnt=self._add_flow_opt_int(self.huge_buy_cnt, other.huge_buy_cnt),
            huge_sell_cnt=self._add_flow_opt_int(self.huge_sell_cnt, other.huge_sell_cnt),
            large_net=self._add_flow_opt(self.large_net, other.large_net),
            large_buy_net=self._add_flow_opt(self.large_buy_net, other.large_buy_net),
            large_sell_net=self._add_flow_opt(self.large_sell_net, other.large_sell_net),
            large_cnt=self._add_flow_opt_int(self.large_cnt, other.large_cnt),
            large_buy_cnt=self._add_flow_opt_int(self.large_buy_cnt, other.large_buy_cnt),
            large_sell_cnt=self._add_flow_opt_int(self.large_sell_cnt, other.large_sell_cnt),
            medium_net=self._add_flow_opt(self.medium_net, other.medium_net),
            medium_buy_net=self._add_flow_opt(self.medium_buy_net, other.medium_buy_net),
            medium_sell_net=self._add_flow_opt(self.medium_sell_net, other.medium_sell_net),
            medium_cnt=self._add_flow_opt_int(self.medium_cnt, other.medium_cnt),
            medium_buy_cnt=self._add_flow_opt_int(self.medium_buy_cnt, other.medium_buy_cnt),
            medium_sell_cnt=self._add_flow_opt_int(self.medium_sell_cnt, other.medium_sell_cnt),
            small_net=self._add_flow_opt(self.small_net, other.small_net),
            small_buy_net=self._add_flow_opt(self.small_buy_net, other.small_buy_net),
            small_sell_net=self._add_flow_opt(self.small_sell_net, other.small_sell_net),
            small_cnt=self._add_flow_opt_int(self.small_cnt, other.small_cnt),
            small_buy_cnt=self._add_flow_opt_int(self.small_buy_cnt, other.small_buy_cnt),
            small_sell_cnt=self._add_flow_opt_int(self.small_sell_cnt, other.small_sell_cnt),
            accumulative=self.accumulative or other.accumulative,
        )

    def _add_flow_opt(self, self_val: Optional[float], flow_val: Optional[float]) -> Optional[float]:
        if self_val is None and flow_val is None:
            return None
        return (self_val or 0.0) + (flow_val or 0.0)

    def _add_flow_opt_int(self, self_val: Optional[int], flow_val: Optional[int]) -> Optional[int]:
        if self_val is None and flow_val is None:
            return None
        return (self_val or 0) + (flow_val or 0)

    @staticmethod
    def _sum_opt(values) -> Optional[float]:
        filtered = [v for v in values if v is not None]
        return sum(filtered) if filtered else None

    @staticmethod
    def _sum_opt_int(values) -> Optional[int]:
        filtered = [v for v in values if v is not None]
        return sum(filtered) if filtered else None
