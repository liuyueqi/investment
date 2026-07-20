"""资金流聚合器：从 money_flows 原始数据计算生成 money_flow_aggregation"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional

from domain.stock import Stock
from domain.sector import Sector
from domain.money_flow import MoneyFlow
from domain.money_flow_aggregation import MoneyFlowAggregation
from domain.stock_repository import StockRepository
from domain.sector_repository import SectorRepository
from domain.money_flow_repository import MoneyFlowRepository
from domain.money_flow_aggregation_repository import MoneyFlowAggregationRepository
from infra.database.connection import get_db
from infra.log import logger


class MoneyFlowAggregator:
    """资金流聚合器

    从 money_flows 表读取原始日级资金流数据，计算并保存到 money_flow_aggregation 表。
    包含 4 种聚合：
      1. 个股从最早日期到每一天的累计净流入
      2. 个股的 3/5/10/20 日净流入
      3. 板块从最早日期到每一天的累计净流入
      4. 板块的 3/5/10/20 日净流入
    """

    _TRADING_DAYS = [3, 5, 10, 20]  # 需要计算的滑动窗口
    _MAX_WORKERS = 8                 # 线程池并发数

    def __init__(
        self,
        stock_reop: StockRepository,
        sector_repo: SectorRepository,
        money_flow_repo: MoneyFlowRepository,
        agg_repo: MoneyFlowAggregationRepository,
    ):
        self._stock_repo = stock_reop
        self._money_flow_repo = money_flow_repo
        self._sector_repo = sector_repo
        self._money_flow_agg_repo = agg_repo

    # ════════════════════════════════════════════════════════════
    #  公开接口
    # ════════════════════════════════════════════════════════════

    def aggregate_all(self) -> None:
        """对所有股票及板块执行聚合计算"""

        stocks = self._stock_repo.find_all()
        if not stocks:
            logger.warning("没有股票代码可聚合")
            return

        logger.info(f"开始计算 {len(stocks)} 只股票的累计净流入...")
        self._aggregate_stocks(stocks)

        sectors = self._sector_repo.find_all()
        if not sectors:
            return

        logger.info("开始计算板块累计净流入...")
        self._aggregate_sectors(sectors)

        logger.info("资金流聚合完成")

    # ════════════════════════════════════════════════════════════
    #  个股聚合（并发）
    # ════════════════════════════════════════════════════════════

    def _aggregate_stocks(self, stocks: List[Stock]) -> None:
        """并发处理多只股票的聚合"""
        
        total = len(stocks)
        with ThreadPoolExecutor(max_workers=self._MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._aggregate_stock, stock): stock
                for stock in stocks
            }
            for i, future in enumerate(as_completed(futures), 1):
                stock = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"股票 {stock} 聚合失败: {e}")
                if i % 50 == 0 or i == total:
                    logger.info(f"个股聚合进度: {i}/{total}")

    def _aggregate_stock(self, stock: Stock) -> None:
        """聚合单只股票的累计净流入和滑动窗口净流入"""

        # ── 第 1 种：从最早的日期开始，计算到每一天的累计净流入 ──
        self._aggregate_stock_accumulation(stock)

        # ── 第 2 种：计算每一天的 3/5/10/20 日净流入 ──────────
        self._aggregate_stock_sliding(stock)

    def _aggregate_stock_accumulation(self, stock: Stock) -> None:
        """
            个股：从最早日期到每一天的累计资金净流入
            先查数据库已有的最长累计记录（trading_days 最大），如果没有则返回 earliest_date。
            然后从该记录的 end_date 次日开始，逐日累加并保存。
        """

        # 查找已有累计记录
        existing = self._money_flow_agg_repo.find_longest_accumulation(
            stock.code, MoneyFlowAggregation.TYPE_STOCK
        )

        if existing:
            today = date.today()
            if existing.end_date >= today:
                return
            # 从 existing.end_date 开始读取 flow
            since = existing.end_date + timedelta(days=1)
            flows = self._money_flow_repo.find_by_code_and_date_range(
                stock.code, since, today,
            )
        else:
            flows = self._money_flow_repo.find_by_code(stock.code)

        if not flows:
            return

        new_aggs = []
        new_agg: Optional[MoneyFlowAggregation] = None
        for flow in flows:
            if new_agg:
                new_agg = new_agg.accumulate(flow)
            else:
                if existing:
                    new_agg = existing.accumulate(flow)
                else:
                    new_agg = MoneyFlowAggregation.start_with_money_flows(flow, accumulative=True)
            new_aggs.append(new_agg)

        # 批量保存
        for agg in new_aggs:
            self._money_flow_agg_repo.save(agg)

    def _aggregate_stock_sliding(self, stock: Stock) -> None:
        """
            个股：计算每一天的 3/5/10/20 日净流入
        """
        
        for window in self._TRADING_DAYS:
            self._aggregate_stock_sliding_by_window(stock, window)

    def _aggregate_stock_sliding_by_window(self, stock: Stock, window: int) -> None:
        """
            个股：按指定滑动窗口天数计算净流入
        """

        # 查找已有记录，确定从哪里开始续算
        existing = self._money_flow_agg_repo.find_latest_by_trading_days(
            stock.code, MoneyFlowAggregation.TYPE_STOCK, window,
        )

        if existing:
            today = date.today()
            if existing.end_date >= today:
                return
            since = existing.start_date + timedelta(days=1)
            flows = self._money_flow_repo.find_by_code_and_date_range(
                stock.code, since, today,
            )
        else:
            flows = self._money_flow_repo.find_by_code(stock.code)

        if not flows:
            return

        # 滑动窗口计算
        new_aggs: List[MoneyFlowAggregation] = []
        count = len(flows)
        for i in range(count):

            if i + window > count:
                break

            slice_flows = flows[i : i + window]
            agg = MoneyFlowAggregation.start_with_money_flows(*slice_flows)
            new_aggs.append(agg)

        # 批量保存
        for agg in new_aggs:
            self._money_flow_agg_repo.save(agg)

    # ════════════════════════════════════════════════════════════
    #  板块聚合（第 3、4 种）
    # ════════════════════════════════════════════════════════════

    def _aggregate_sectors(self, sectors: List[Sector]) -> None:
        """
            基于已聚合的个股累计数据，计算板块累计净流入
        """
    
        with ThreadPoolExecutor(max_workers=self._MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._aggregate_sector, sector): sector
                for sector in sectors
            }
            total = len(futures)
            for i, future in enumerate(as_completed(futures), 1):
                sector = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"板块 {sector} 聚合失败: {e}")
                if i % 20 == 0 or i == total:
                    logger.info(f"板块聚合进度: {i}/{total}")

    def _aggregate_sector(self, sector: Sector) -> None:
        """
            聚合单个板块：累计 + 滑动窗口
        """

        (earliest_date, _) = self._money_flow_repo.get_date_range(*sector.members)
        if earliest_date is None:
            logger.warning(f"板块 {sector} 无资金流向数据，跳过")
            return
        
        self._aggregate_sector_accumulation(sector)

        for window in self._TRADING_DAYS:
            self._aggregate_sector_sliding(sector, window)

    def _aggregate_sector_accumulation(self, sector: Sector) -> None:
        """
            板块：计算从最早日期到每一天的累计净流入
        """

        # 查找已有板块累计记录
        existing = self._money_flow_agg_repo.find_longest_accumulation(
            sector.code, MoneyFlowAggregation.TYPE_SECTOR,
        )

        if existing:
            if existing.end_date >= date.today():
                return
            since = existing.end_date + timedelta(days=1)
        else:
            since = None

        sector_accumulation: Dict[date, MoneyFlowAggregation] = {}
        for member in sector.members:
            member_accumulation = self._aggregate_member_accumulation(member, since)
            for accu_date, accu in member_accumulation.items():
                ex_accu = sector_accumulation[accu_date]
                if ex_accu:
                    sector_accumulation[accu_date] = ex_accu.merge(accu)

        for accu in sector_accumulation.values():
            self._money_flow_agg_repo.save(accu)
            
    def _aggregate_member_accumulation(
        self, member: 
        str, since: 
        Optional[date] = None,
    ) -> Dict[date, MoneyFlowAggregation]:
        """
            计算给定的板块成分股（代码）的资金总量，以：统计日期 -> （统计当天）资金总量 的字典格式返回

            Args:
                member (str): 板块成分股代码
                since  (date): 从哪一天开始的资金总量

            Returns:
                指定板块成分股在指定日期的资金总量集合
        """

        existing_accumulations = self._money_flow_agg_repo.find_accumulations_by_code(
            member, MoneyFlowAggregation.TYPE_STOCK, since
        )

        member_accumulations: Dict[date, MoneyFlowAggregation] = {} # 资金流向累计总数（日期 -> 值）
        if existing_accumulations:
            for ex_accu in existing_accumulations:
                member_accumulations[ex_accu.end_date] = ex_accu

        return member_accumulations

    def _aggregate_sector_sliding(self, sector: Sector, window: int) -> None:
        """
            板块：计算每一天的 3/5/10/20 日净流入
        """
        
        existing = self._money_flow_agg_repo.find_latest_by_trading_days(
            sector.code, MoneyFlowAggregation.TYPE_SECTOR, window
        )

        if existing:
            today = date.today()
            if existing.end_date >= today:
                return
            since = existing.start_date + timedelta(days=1)
        else:
            since = None

        sector_sliding: Dict[date, MoneyFlowAggregation] = {}
        for member in sector.members:
            member_sliding = self._aggregate_member_sliding(member, window, since)
            for sliding_date, sliding in member_sliding.items():
                ex_sliding = sector_sliding[sliding_date]
                if ex_sliding:
                    sector_sliding[sliding_date] = ex_sliding.merge(sliding)

        for sliding in sector_sliding.values():
            self._money_flow_agg_repo.save(sliding)

    def _aggregate_member_sliding(
            self, member: str, window: int, since: Optional[date]
    ) -> Dict[date, MoneyFlowAggregation]:
        
        existing_sliding = self._money_flow_agg_repo.find_by_trading_days(
            member, MoneyFlowAggregation.TYPE_STOCK, window, since
        )

        member_sliding: Dict[date, MoneyFlowAggregation] = {}
        if existing_sliding:
            for sliding in existing_sliding:
                member_sliding[sliding.start_date] = sliding

        return member_sliding

    # ════════════════════════════════════════════════════════════
    #  辅助方法
    # ════════════════════════════════════════════════════════════

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
        filtered = [v for v in values if v is not None]
        return sum(filtered) if filtered else None

    @staticmethod
    def _safe_sum_int(values) -> Optional[int]:
        filtered = [v for v in values if v is not None]
        return sum(filtered) if filtered else None
