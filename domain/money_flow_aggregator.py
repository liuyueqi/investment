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
        """
            对所有股票及板块执行聚合计算（入口方法）。
            依次计算：
              1. 所有个股的资金总量（accumulation）
              2. 所有个股的 N 天净流入（sliding，窗口：3、5、10、20）
              3. 所有板块的资金总量
              4. 所有板块的 N 天净流入
        """

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
        """
            并发处理多只股票的聚合计算。

            Args:
                stocks (List[Stock]): 待聚合的股票列表

            使用 ThreadPoolExecutor 并发执行，每完成 50 只记录一次进度日志。
        """
        
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
                    logger.info(f"股票 {stock} 聚合完成")
                except Exception as e:
                    logger.error(f"股票 {stock} 聚合失败: {e}")
                if i % 50 == 0 or i == total:
                    logger.info(f"个股聚合进度: {i}/{total}")

    def _aggregate_stock(self, stock: Stock) -> None:
        """
            聚合单只股票的累计净流入和滑动窗口净流入。

            按顺序执行：
              1. 计算该股票的资金总量（accumulation）
              2. 计算该股票 3/5/10/20 天的滑动窗口（sliding）

            Args:
                stock (Stock): 待聚合的股票对象
        """

        # ── 第 1 种：从最早的日期开始，计算到每一天的累计净流入 ──
        self._aggregate_stock_accumulation(stock)

        # ── 第 2 种：计算每一天的 3/5/10/20 日净流入 ──────────
        self._aggregate_stock_sliding(stock)

    def _aggregate_stock_accumulation(self, stock: Stock) -> None:
        """
            计算单只股票的资金总量（accumulation）。
            从最早有资金流数据的日期开始，逐日累加到当天。
            支持增量续算：先查已有的最长累计记录，从次日开始追加。

            Args:
                stock (Stock): 待计算的股票对象

            逻辑：
              1. 查询数据库中该股票已有的最长累计记录
              2. 若已统计到今天则跳过
              3. 从现有累计的 end_date 次日开始拉取原始 flow
              4. 逐日累加并保存新的累计记录
        """

        # 查找已有累计记录
        existing = self._money_flow_agg_repo.find_longest_accumulation(
            stock.code, MoneyFlowAggregation.TYPE_STOCK
        )

        if existing:
            today = date.today()
            if existing.end_date >= today:
                logger.info(f"股票 {stock} 的资金总量已统计到今天。")
                return
            # 从次日开始读取 flow
            since = existing.end_date + timedelta(days=1)
            flows = self._money_flow_repo.find_by_code_and_date_range(
                stock.code, since, today,
            )
        else:
            # 读该股票的全量flow
            flows = self._money_flow_repo.find_by_code(stock.code)

        if not flows:
            logger.warning(f"没有股票 {stock} 的资金流入数据")
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
        logger.info(f"保存了 {len(new_aggs)} 条股票 {stock} 的资金总量数据")

    def _aggregate_stock_sliding(self, stock: Stock) -> None:
        """
            计算单只股票的 3/5/10/20 天滑动窗口净流入。
            内部遍历 _TRADING_DAYS 并逐个调用 _aggregate_stock_sliding_by_window。

            Args:
                stock (Stock): 待计算的股票对象
        """
        
        for window in self._TRADING_DAYS:
            self._aggregate_stock_sliding_by_window(stock, window)

    def _aggregate_stock_sliding_by_window(self, stock: Stock, window: int) -> None:
        """
            计算单只股票指定窗口天数的滑动窗口净流入。
            支持增量续算：先查该窗口的最新记录，从次日开始追加。

            Args:
                stock (Stock):  待计算的股票对象
                window (int):   窗口天数，如 3、5、10、20

            逻辑：
              1. 查询该股票该窗口的最新已有记录
              2. 若已统计到今天则跳过
              3. 从现有记录之后拉取原始 flow
              4. 以 window 为单位滑动计算并保存
        """

        # 查找已有记录，确定从哪里开始续算
        existing = self._money_flow_agg_repo.find_latest_by_trading_days(
            stock.code, MoneyFlowAggregation.TYPE_STOCK, window,
        )

        if existing:
            today = date.today()
            if existing.end_date >= today:
                logger.info(f"股票 {stock} 的 {window}天 净流入数据已统计到今天。")
                return
            since = existing.start_date + timedelta(days=1)
            # 从次日开始读取 flow
            flows = self._money_flow_repo.find_by_code_and_date_range(
                stock.code, since, today,
            )
        else:
            # 读取全量 flow
            flows = self._money_flow_repo.find_by_code(stock.code)

        if not flows:
            logger.warning(f"没有股票 {stock} 的资金流入数据")
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
        logger.info(f"保存了 {len(new_aggs)} 条股票 {stock} 的 {window}天 净流入数据")

    # ════════════════════════════════════════════════════════════
    #  板块聚合（第 3、4 种）
    # ════════════════════════════════════════════════════════════

    def _aggregate_sectors(self, sectors: List[Sector]) -> None:
        """
            并发处理多个板块的聚合计算。
            依赖已计算好的个股聚合数据。

            Args:
                sectors (List[Sector]): 待聚合的板块列表

            使用 ThreadPoolExecutor 并发执行，每完成 20 个记录一次进度日志。
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
                    logger.info(f"板块 {sector} 聚合完成")
                except Exception as e:
                    logger.error(f"板块 {sector} 聚合失败: {e}")
                if i % 20 == 0 or i == total:
                    logger.info(f"板块聚合进度: {i}/{total}")

    def _aggregate_sector(self, sector: Sector) -> None:
        """
            聚合单个板块的资金总量和滑动窗口净流入。
            按顺序执行：
              1. 计算板块的资金总量
              2. 计算板块 3/5/10/20 天的滑动窗口

            Args:
                sector (Sector): 待聚合的板块对象
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
            计算板块的资金总量（accumulation）。
            基于各成分股已经计算好的个股资金总量，按 end_date 对齐后合并为板块数据。

            Args:
                sector (Sector): 待计算的板块对象

            逻辑：
              1. 查询板块已有的最长累计记录，确定增量起始日期
              2. 遍历所有成分股，读取其资金总量数据
              3. 按 end_date 分组，将所有成分股的同日数据通过 sector_aggregation_from_members 合并为一个板块记录
              4. 保存板块记录
        """

        # 查找已有板块累计记录
        existing = self._money_flow_agg_repo.find_longest_accumulation(
            sector.code, MoneyFlowAggregation.TYPE_SECTOR,
        )

        if existing:
            if existing.end_date >= date.today():
                logger.info(f"板块 {sector} 的资金总量已统计到今天。")
                return
            # 资金总量关注end_date，从次日开始读取flow
            since = existing.end_date + timedelta(days=1)
        else:
            since = None

        sector_accumulation: Dict[date, MoneyFlowAggregation] = {}
        for member in sector.members:
            # 成分股每一天的资金总量
            member_accumulation = self._aggregate_member_accumulation(member, since)
            for accu_date, accu in member_accumulation.items():
                if accu_date in sector_accumulation:
                    ex_accu = sector_accumulation[accu_date]
                    sector_accumulation[accu_date] = ex_accu.merge(accu)
                else:
                    sector_accumulation[accu_date] = MoneyFlowAggregation.sector_aggregation_from_members(
                        sector.code, sector.name, accu
                    )

        for accu in sector_accumulation.values():
            self._money_flow_agg_repo.save(accu)
        logger.info(f"保存了 {len(sector_accumulation)} 条板块 {sector} 的资金总量数据")
            
    def _aggregate_member_accumulation(
            self, member: str, since: Optional[date] = None,
    ) -> Dict[date, MoneyFlowAggregation]:
        """
            读取板块指定成分股的资金总量数据，以 end_date -> MoneyFlowAggregation 的字典格式返回。

            Args:
                member (str):     板块成分股代码
                since (date):     起始日期（含），只返回 end_date >= since 的记录。为 None 时返回全部。

            Returns:
                key 为 end_date，value 为对应日期的资金总量聚合对象
        """

        # 读取成分股的资金总量数据
        existing_accumulations = self._money_flow_agg_repo.find_accumulations_by_code(
            member, MoneyFlowAggregation.TYPE_STOCK, since
        )

        # 每一天的资金总量，key为资金总量的截止日期
        member_accumulations: Dict[date, MoneyFlowAggregation] = {} 
        if existing_accumulations:
            for ex_accu in existing_accumulations:
                member_accumulations[ex_accu.end_date] = ex_accu

        return member_accumulations

    def _aggregate_sector_sliding(self, sector: Sector, window: int) -> None:
        """
            计算板块指定窗口天数的滑动窗口净流入。
            基于各成分股已计算好的个股滑动窗口数据，按 start_date 对齐后合并为板块数据。

            Args:
                sector (Sector): 待计算的板块对象
                window (int):    窗口天数，如 3、5、10、20
        """
        
        existing = self._money_flow_agg_repo.find_latest_by_trading_days(
            sector.code, MoneyFlowAggregation.TYPE_SECTOR, window
        )

        if existing:
            today = date.today()
            if existing.end_date >= today:
                logger.info(f"板块 {sector} 的资金总量已统计到今天。")
                return
            # N天净流入关注start_date，从次日开始读取flow
            since = existing.start_date + timedelta(days=1)
        else:
            since = None

        sector_sliding: Dict[date, MoneyFlowAggregation] = {}
        for member in sector.members:
            # 成分股的N天净流入
            member_sliding = self._aggregate_member_sliding(member, window, since)
            for sliding_date, sliding in member_sliding.items():
                if sliding_date in sector_sliding:
                    ex_sliding = sector_sliding[sliding_date]
                    sector_sliding[sliding_date] = ex_sliding.merge(sliding)
                else:
                    sector_sliding[sliding_date] = MoneyFlowAggregation.sector_aggregation_from_members(
                        sector.code, sector.name, sliding
                    )

        for sliding in sector_sliding.values():
            self._money_flow_agg_repo.save(sliding)
        logger.info(f"保存了 {len(sector_sliding)} 条板块 {sector} 的 {window}天 净流入数据")

    def _aggregate_member_sliding(
            self, member: str, window: int, since: Optional[date],
    ) -> Dict[date, MoneyFlowAggregation]:
        """
            读取板块指定成分股的滑动窗口数据，以 start_date -> MoneyFlowAggregation 的字典格式返回。

            Args:
                member (str):     板块成分股代码
                window (int):     窗口天数，如 3、5、10、20
                since (date):     起始日期（含），只返回 start_date >= since 的记录。为 None 时返回全部。

            Returns:
                key 为 start_date，value 为对应日期的滑动窗口聚合对象
        """
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
        """
            从数据库获取所有未删除的股票代码列表。

            Returns:
                未删除的股票代码列表（按 code 升序）
        """
        from infra.database.connection import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT code FROM stocks WHERE is_deleted = 0 ORDER BY code"
            ).fetchall()
            return [row["code"] for row in rows]

    @staticmethod
    def _safe_sum(values) -> Optional[float]:
        """
            安全求和：过滤 None 值后求和。

            Args:
                values: 可迭代的 float 值（可能含 None）

            Returns:
                总和（若全部为 None 则返回 None）
        """
        filtered = [v for v in values if v is not None]
        return sum(filtered) if filtered else None

    @staticmethod
    def _safe_sum_int(values) -> Optional[int]:
        """
            安全求和：过滤 None 值后求和（整数版本）。

            Args:
                values: 可迭代的 int 值（可能含 None）

            Returns:
                总和（若全部为 None 则返回 None）
        """
        filtered = [v for v in values if v is not None]
        return sum(filtered) if filtered else None
