"""资金流聚合器：从 money_flows 原始数据计算生成 money_flow_aggregation"""

from datetime import date, datetime
from typing import Dict, List, Optional

from domain.money_flow import MoneyFlow
from domain.money_flow_aggregation import MoneyFlowAggregation
from domain.money_flow_aggregation_repository import MoneyFlowAggregationRepository
from domain.money_flow_repository import MoneyFlowRepository
from domain.sector_repository import SectorRepository
from infra.database.connection import get_db
from infra.log import logger


class MoneyFlowAggregator:
    """资金流聚合器

    职责：
    1. 从 money_flows 表读取原始日级资金流数据
    2. 按个股维度计算历史累计净流入
    3. 按板块维度汇总旗下个股，计算板块历史累计净流入
    4. 将结果写入 money_flow_aggregation 表（增量/全量）
    """

    def __init__(
        self,
        money_flow_repo: MoneyFlowRepository,
        sector_repo: SectorRepository,
        agg_repo: MoneyFlowAggregationRepository,
    ):
        self._mf_repo = money_flow_repo
        self._sector_repo = sector_repo
        self._agg_repo = agg_repo

    # ════════════════════════════════════════════════════════════
    #  公开接口
    # ════════════════════════════════════════════════════════════

    def aggregate_all(
        self,
        stock_codes: Optional[List[str]] = None,
        force: bool = False,
    ) -> None:
        """对所有股票及板块执行聚合计算

        Args:
            stock_codes: 股票代码列表，None 则从数据库获取所有
            force: 是否强制全量重算
        """
        if stock_codes is None:
            stock_codes = self._load_all_stock_codes()

        if not stock_codes:
            logger.warning("没有股票代码可聚合")
            return

        logger.info(f"开始计算 {len(stock_codes)} 只股票的累计净流入...")
        self._aggregate_stocks(stock_codes, force)

        logger.info("开始计算板块累计净流入...")
        self._aggregate_sectors(force)

        logger.info("资金流聚合完成")

    def aggregate_stocks(
        self,
        stock_codes: Optional[List[str]] = None,
        force: bool = False,
    ) -> None:
        """仅计算个股累计净流入"""
        if stock_codes is None:
            stock_codes = self._load_all_stock_codes()

        self._aggregate_stocks(stock_codes, force)

    def aggregate_sectors(self, force: bool = False) -> None:
        """仅计算板块累计净流入（基于已生成的个股聚合数据）"""
        self._aggregate_sectors(force)

    # ════════════════════════════════════════════════════════════
    #  个股聚合
    # ════════════════════════════════════════════════════════════

    def _aggregate_stocks(
        self, stock_codes: List[str], force: bool
    ) -> None:
        """计算每只个股的按日累计净流入"""
        latest_agg_date = self._agg_repo.get_latest_trade_date("stock")
        start_date = None if force or latest_agg_date is None else latest_agg_date

        total = len(stock_codes)
        for idx, code in enumerate(stock_codes, 1):
            logger.info(f"[{idx}/{total}] 聚合个股 {code}...")

            flows = self._mf_repo.find_by_code_and_date_range(
                code,
                start_date or date(2000, 1, 1),
                date.today(),
            )

            if not flows:
                logger.info(f"  股票 {code} 无资金流向数据，跳过")
                continue

            self._compute_and_save_stock_agg(code, flows, force)

    def _compute_and_save_stock_agg(
        self, code: str, flows: List[MoneyFlow], force: bool
    ) -> None:
        """计算单只股票的逐日累计值并保存"""
        # 按日期排序
        flows.sort(key=lambda f: f.time)

        # 先加载已有累计值（如果存在且非强制重算，则作为基准）
        base_agg: Optional[MoneyFlowAggregation] = None
        if not force:
            for flow in flows:
                existing = self._agg_repo.find_stock_aggregation(
                    code, flow.time.date()
                )
                if existing:
                    base_agg = existing
                    break

        # 逐日累加
        cum_main_net = base_agg.cumulative_main_net if base_agg else 0.0
        cum_main_cnt = base_agg.cumulative_main_cnt if base_agg else 0
        cum_net_amt = base_agg.cumulative_net_amount if base_agg else 0.0

        cum_huge_net = base_agg.cumulative_huge_net if base_agg and base_agg.cumulative_huge_net is not None else 0.0
        cum_huge_buy_net = base_agg.cumulative_huge_buy_net if base_agg and base_agg.cumulative_huge_buy_net is not None else 0.0
        cum_huge_sell_net = base_agg.cumulative_huge_sell_net if base_agg and base_agg.cumulative_huge_sell_net is not None else 0.0
        cum_huge_cnt = base_agg.cumulative_huge_cnt if base_agg and base_agg.cumulative_huge_cnt is not None else 0
        cum_huge_buy_cnt = base_agg.cumulative_huge_buy_cnt if base_agg and base_agg.cumulative_huge_buy_cnt is not None else 0
        cum_huge_sell_cnt = base_agg.cumulative_huge_sell_cnt if base_agg and base_agg.cumulative_huge_sell_cnt is not None else 0

        cum_large_net = base_agg.cumulative_large_net if base_agg and base_agg.cumulative_large_net is not None else 0.0
        cum_large_buy_net = base_agg.cumulative_large_buy_net if base_agg and base_agg.cumulative_large_buy_net is not None else 0.0
        cum_large_sell_net = base_agg.cumulative_large_sell_net if base_agg and base_agg.cumulative_large_sell_net is not None else 0.0
        cum_large_cnt = base_agg.cumulative_large_cnt if base_agg and base_agg.cumulative_large_cnt is not None else 0
        cum_large_buy_cnt = base_agg.cumulative_large_buy_cnt if base_agg and base_agg.cumulative_large_buy_cnt is not None else 0
        cum_large_sell_cnt = base_agg.cumulative_large_sell_cnt if base_agg and base_agg.cumulative_large_sell_cnt is not None else 0

        cum_medium_net = base_agg.cumulative_medium_net if base_agg and base_agg.cumulative_medium_net is not None else 0.0
        cum_medium_buy_net = base_agg.cumulative_medium_buy_net if base_agg and base_agg.cumulative_medium_buy_net is not None else 0.0
        cum_medium_sell_net = base_agg.cumulative_medium_sell_net if base_agg and base_agg.cumulative_medium_sell_net is not None else 0.0
        cum_medium_cnt = base_agg.cumulative_medium_cnt if base_agg and base_agg.cumulative_medium_cnt is not None else 0
        cum_medium_buy_cnt = base_agg.cumulative_medium_buy_cnt if base_agg and base_agg.cumulative_medium_buy_cnt is not None else 0
        cum_medium_sell_cnt = base_agg.cumulative_medium_sell_cnt if base_agg and base_agg.cumulative_medium_sell_cnt is not None else 0

        cum_small_net = base_agg.cumulative_small_net if base_agg and base_agg.cumulative_small_net is not None else 0.0
        cum_small_buy_net = base_agg.cumulative_small_buy_net if base_agg and base_agg.cumulative_small_buy_net is not None else 0.0
        cum_small_sell_net = base_agg.cumulative_small_sell_net if base_agg and base_agg.cumulative_small_sell_net is not None else 0.0
        cum_small_cnt = base_agg.cumulative_small_cnt if base_agg and base_agg.cumulative_small_cnt is not None else 0
        cum_small_buy_cnt = base_agg.cumulative_small_buy_cnt if base_agg and base_agg.cumulative_small_buy_cnt is not None else 0
        cum_small_sell_cnt = base_agg.cumulative_small_sell_cnt if base_agg and base_agg.cumulative_small_sell_cnt is not None else 0

        # 找出从哪条 flow 开始需要计算（已有基准的跳过）
        start_calc_idx = 0
        if base_agg and base_agg.trade_date:
            for i, f in enumerate(flows):
                if f.time.date() > base_agg.trade_date:
                    start_calc_idx = i
                    break
            else:
                # 所有日期都已有累计值
                return

        # 数据起始日期
        data_start = base_agg.data_start_date if base_agg else flows[0].time.date()
        trading_days = base_agg.trading_days_count if base_agg else 0

        for flow in flows[start_calc_idx:]:
            flow_date = flow.time.date()
            trading_days += 1

            # 累计主要指标
            cum_main_net += flow.main_net
            cum_main_cnt += flow.main_cnt
            cum_net_amt += flow.net_amount

            # 累计明细（处理 None 值）
            if flow.huge_net is not None:
                cum_huge_net += flow.huge_net
            if flow.huge_buy_net is not None:
                cum_huge_buy_net += flow.huge_buy_net
            if flow.huge_sell_net is not None:
                cum_huge_sell_net += flow.huge_sell_net
            if flow.huge_cnt is not None:
                cum_huge_cnt += flow.huge_cnt
            if flow.huge_buy_cnt is not None:
                cum_huge_buy_cnt += flow.huge_buy_cnt
            if flow.huge_sell_cnt is not None:
                cum_huge_sell_cnt += flow.huge_sell_cnt

            if flow.large_net is not None:
                cum_large_net += flow.large_net
            if flow.large_buy_net is not None:
                cum_large_buy_net += flow.large_buy_net
            if flow.large_sell_net is not None:
                cum_large_sell_net += flow.large_sell_net
            if flow.large_cnt is not None:
                cum_large_cnt += flow.large_cnt
            if flow.large_buy_cnt is not None:
                cum_large_buy_cnt += flow.large_buy_cnt
            if flow.large_sell_cnt is not None:
                cum_large_sell_cnt += flow.large_sell_cnt

            if flow.medium_net is not None:
                cum_medium_net += flow.medium_net
            if flow.medium_buy_net is not None:
                cum_medium_buy_net += flow.medium_buy_net
            if flow.medium_sell_net is not None:
                cum_medium_sell_net += flow.medium_sell_net
            if flow.medium_cnt is not None:
                cum_medium_cnt += flow.medium_cnt
            if flow.medium_buy_cnt is not None:
                cum_medium_buy_cnt += flow.medium_buy_cnt
            if flow.medium_sell_cnt is not None:
                cum_medium_sell_cnt += flow.medium_sell_cnt

            if flow.small_net is not None:
                cum_small_net += flow.small_net
            if flow.small_buy_net is not None:
                cum_small_buy_net += flow.small_buy_net
            if flow.small_sell_net is not None:
                cum_small_sell_net += flow.small_sell_net
            if flow.small_cnt is not None:
                cum_small_cnt += flow.small_cnt
            if flow.small_buy_cnt is not None:
                cum_small_buy_cnt += flow.small_buy_cnt
            if flow.small_sell_cnt is not None:
                cum_small_sell_cnt += flow.small_sell_cnt

            # 构建聚合实体
            agg = MoneyFlowAggregation(
                entity_type="stock",
                entity_code=code,
                trade_date=flow_date,
                period="day",
                cumulative_main_net=cum_main_net,
                cumulative_main_cnt=cum_main_cnt,
                cumulative_net_amount=cum_net_amt,
                cumulative_huge_net=cum_huge_net,
                cumulative_huge_buy_net=cum_huge_buy_net,
                cumulative_huge_sell_net=cum_huge_sell_net,
                cumulative_huge_cnt=cum_huge_cnt,
                cumulative_huge_buy_cnt=cum_huge_buy_cnt,
                cumulative_huge_sell_cnt=cum_huge_sell_cnt,
                cumulative_large_net=cum_large_net,
                cumulative_large_buy_net=cum_large_buy_net,
                cumulative_large_sell_net=cum_large_sell_net,
                cumulative_large_cnt=cum_large_cnt,
                cumulative_large_buy_cnt=cum_large_buy_cnt,
                cumulative_large_sell_cnt=cum_large_sell_cnt,
                cumulative_medium_net=cum_medium_net,
                cumulative_medium_buy_net=cum_medium_buy_net,
                cumulative_medium_sell_net=cum_medium_sell_net,
                cumulative_medium_cnt=cum_medium_cnt,
                cumulative_medium_buy_cnt=cum_medium_buy_cnt,
                cumulative_medium_sell_cnt=cum_medium_sell_cnt,
                cumulative_small_net=cum_small_net,
                cumulative_small_buy_net=cum_small_buy_net,
                cumulative_small_sell_net=cum_small_sell_net,
                cumulative_small_cnt=cum_small_cnt,
                cumulative_small_buy_cnt=cum_small_buy_cnt,
                cumulative_small_sell_cnt=cum_small_sell_cnt,
                data_start_date=data_start,
                data_end_date=flow_date,
                trading_days_count=trading_days,
            )
            self._agg_repo.save(agg)

    # ════════════════════════════════════════════════════════════
    #  板块聚合
    # ════════════════════════════════════════════════════════════

    def _aggregate_sectors(self, force: bool) -> None:
        """基于已聚合的个股累计数据，计算板块累计净流入"""
        sectors = self._sector_repo.find_all()
        if not sectors:
            logger.warning("没有板块数据，无法聚合板块资金流")
            return

        latest_agg_date = self._agg_repo.get_latest_trade_date("sector")

        for sector in sectors:
            members = sector.members
            if not members:
                continue

            logger.info(f"聚合板块 {sector.code} ({sector.name})，"
                        f"{len(members)} 只成分股...")

            # 获取该板块所有成分股的日期对齐的累计净流入
            stock_aggs = self._load_member_aggs(members, latest_agg_date, force)

            if not stock_aggs:
                continue

            # 按日期汇总
            date_groups: Dict[date, List[MoneyFlowAggregation]] = {}
            for agg in stock_aggs:
                if agg.trade_date is None:
                    continue
                date_groups.setdefault(agg.trade_date, []).append(agg)

            # 对每个日期汇总板块累计值
            for trade_date in sorted(date_groups.keys()):
                member_aggs = date_groups[trade_date]

                # 板块累计 = 成分股累计之和
                sector_agg = MoneyFlowAggregation(
                    entity_type="sector",
                    entity_code=sector.code,
                    entity_name=sector.name,
                    trade_date=trade_date,
                    period="day",
                    cumulative_main_net=sum(
                        a.cumulative_main_net for a in member_aggs
                    ),
                    cumulative_main_cnt=sum(
                        a.cumulative_main_cnt for a in member_aggs
                    ),
                    cumulative_net_amount=sum(
                        a.cumulative_net_amount for a in member_aggs
                    ),
                    cumulative_huge_net=self._safe_sum(
                        a.cumulative_huge_net for a in member_aggs
                    ),
                    cumulative_huge_buy_net=self._safe_sum(
                        a.cumulative_huge_buy_net for a in member_aggs
                    ),
                    cumulative_huge_sell_net=self._safe_sum(
                        a.cumulative_huge_sell_net for a in member_aggs
                    ),
                    cumulative_huge_cnt=self._safe_sum_int(
                        a.cumulative_huge_cnt for a in member_aggs
                    ),
                    cumulative_huge_buy_cnt=self._safe_sum_int(
                        a.cumulative_huge_buy_cnt for a in member_aggs
                    ),
                    cumulative_huge_sell_cnt=self._safe_sum_int(
                        a.cumulative_huge_sell_cnt for a in member_aggs
                    ),
                    cumulative_large_net=self._safe_sum(
                        a.cumulative_large_net for a in member_aggs
                    ),
                    cumulative_large_buy_net=self._safe_sum(
                        a.cumulative_large_buy_net for a in member_aggs
                    ),
                    cumulative_large_sell_net=self._safe_sum(
                        a.cumulative_large_sell_net for a in member_aggs
                    ),
                    cumulative_large_cnt=self._safe_sum_int(
                        a.cumulative_large_cnt for a in member_aggs
                    ),
                    cumulative_large_buy_cnt=self._safe_sum_int(
                        a.cumulative_large_buy_cnt for a in member_aggs
                    ),
                    cumulative_large_sell_cnt=self._safe_sum_int(
                        a.cumulative_large_sell_cnt for a in member_aggs
                    ),
                    cumulative_medium_net=self._safe_sum(
                        a.cumulative_medium_net for a in member_aggs
                    ),
                    cumulative_medium_buy_net=self._safe_sum(
                        a.cumulative_medium_buy_net for a in member_aggs
                    ),
                    cumulative_medium_sell_net=self._safe_sum(
                        a.cumulative_medium_sell_net for a in member_aggs
                    ),
                    cumulative_medium_cnt=self._safe_sum_int(
                        a.cumulative_medium_cnt for a in member_aggs
                    ),
                    cumulative_medium_buy_cnt=self._safe_sum_int(
                        a.cumulative_medium_buy_cnt for a in member_aggs
                    ),
                    cumulative_medium_sell_cnt=self._safe_sum_int(
                        a.cumulative_medium_sell_cnt for a in member_aggs
                    ),
                    cumulative_small_net=self._safe_sum(
                        a.cumulative_small_net for a in member_aggs
                    ),
                    cumulative_small_buy_net=self._safe_sum(
                        a.cumulative_small_buy_net for a in member_aggs
                    ),
                    cumulative_small_sell_net=self._safe_sum(
                        a.cumulative_small_sell_net for a in member_aggs
                    ),
                    cumulative_small_cnt=self._safe_sum_int(
                        a.cumulative_small_cnt for a in member_aggs
                    ),
                    cumulative_small_buy_cnt=self._safe_sum_int(
                        a.cumulative_small_buy_cnt for a in member_aggs
                    ),
                    cumulative_small_sell_cnt=self._safe_sum_int(
                        a.cumulative_small_sell_cnt for a in member_aggs
                    ),
                    data_start_date=min(
                        a.data_start_date for a in member_aggs
                        if a.data_start_date is not None
                    ),
                    data_end_date=trade_date,
                    trading_days_count=max(
                        a.trading_days_count for a in member_aggs
                    ),
                )
                self._agg_repo.save(sector_agg)

    # ════════════════════════════════════════════════════════════
    #  辅助方法
    # ════════════════════════════════════════════════════════════

    def _load_member_aggs(
        self,
        members: List[str],
        latest_date: Optional[date],
        force: bool,
    ) -> List[MoneyFlowAggregation]:
        """加载某板块所有成分股的聚合数据"""
        all_aggs: List[MoneyFlowAggregation] = []
        for code in members:
            if latest_date and not force:
                agg = self._agg_repo.find_stock_aggregation(code, latest_date)
                if agg:
                    all_aggs.append(agg)
            else:
                # 加载最新一条
                results = self._agg_repo.find_by_entity_type(
                    "stock", order_by="trade_date", limit=1
                )
                if results:
                    all_aggs.extend(results)
        return all_aggs

    @staticmethod
    def _load_all_stock_codes() -> List[str]:
        """从数据库获取所有未删除的股票代码"""
        from infra.database.connection import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT code FROM stocks WHERE is_deleted = 0 ORDER BY code"
            ).fetchall()
            return [row["code"] for row in rows]

    @staticmethod
    def _safe_sum(values) -> Optional[float]:
        """安全求和，忽略 None"""
        filtered = [v for v in values if v is not None]
        return sum(filtered) if filtered else None

    @staticmethod
    def _safe_sum_int(values) -> Optional[int]:
        """安全求和（整数），忽略 None"""
        filtered = [v for v in values if v is not None]
        return sum(filtered) if filtered else None
