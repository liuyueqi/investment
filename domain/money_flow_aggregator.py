"""资金流聚合器：从 money_flows 原始数据计算生成 money_flow_aggregation"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional

from domain.stock import Stock
from domain.sector import Sector
from domain.money_flow import MoneyFlow
from domain.money_flow_aggregation import MoneyFlowAggregation, AggregationType
from domain.stock_repository import StockRepository
from domain.sector_repository import SectorRepository
from domain.money_flow_repository import MoneyFlowRepository
from domain.money_flow_aggregation_repository import MoneyFlowAggregationRepository
from domain.trading_day_repository import TradingDayRepository
from domain.ts_code_util import code_from_ts_code
from infra.database.connection import get_db
from infra.log import logger


class MoneyFlowAggregator:
    """
        资金流聚合器

        从 money_flows 表读取原始日级资金流数据，计算并保存到 money_flow_aggregation 表。
        包含 4 种聚合：
        1. 个股从最早日期到每一天的累计净流入
        2. 个股的 3/5/10/20 日净流入
        3. 板块从最早日期到每一天的累计净流入
        4. 板块的 3/5/10/20 日净流入
    """

    _TRADING_DAYS = [3, 5, 10, 20]  # 需要计算的滑动窗口

    def __init__(
        self,
        stock_repo: StockRepository,
        sector_repo: SectorRepository,
        money_flow_repo: MoneyFlowRepository,
        agg_repo: MoneyFlowAggregationRepository,
        trading_day_repo: TradingDayRepository,
        default_pool: ThreadPoolExecutor,
    ):
        self._stock_repo = stock_repo
        self._sector_repo = sector_repo
        self._money_flow_repo = money_flow_repo
        self._money_flow_agg_repo = agg_repo
        self._trading_day_repo = trading_day_repo
        self._default_pool = default_pool

    # ════════════════════════════════════════════════════════════
    #  公开接口
    # ════════════════════════════════════════════════════════════

    def aggregate(self, scope: Optional[str] = None, codes: Optional[List[str]] = None) -> None:
        """
            对所有股票及板块执行聚合计算（入口方法）。
            依次计算：
              1. 所有个股的资金总量（accumulation）
              2. 所有个股的 N 天净流入（sliding，窗口：3、5、10、20）
              3. 所有板块的资金总量
              4. 所有板块的 N 天净流入
        """

        if not scope or "stock" in scope:

            if codes:
                stocks = self._stock_repo.find_by_codes(codes)
            else:
                stocks = self._stock_repo.find_all()

            if not stocks:
                logger.warning("没有股票可聚合")
                return

            logger.info(f"开始计算 {len(stocks)} 只股票的累计净流入...")
            self._aggregate_stocks(stocks)

        if not scope or "sector" in scope:

            if codes:
                sectors_date_range = self._sector_repo.find_dc_sectors_date_range(codes)
            else:
                sectors_date_range = self._sector_repo.find_dc_sectors_date_range()

            if not sectors_date_range:
                logger.warning("没有板块可聚合")
                return

            logger.info("开始计算板块累计净流入...")
            self._aggregate_sectors(sectors_date_range)

        self._money_flow_repo.clear_cache()
        self._money_flow_agg_repo.clear_cache()
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
        futures = {
            self._default_pool.submit(self._aggregate_stock, stock): stock
            for stock in stocks
        }
        for i, future in enumerate(as_completed(futures), 1):
            stock = futures[future]
            try:
                future.result()
                logger.info(f"{i}: 股票 {stock} 聚合完成")
            except Exception as e:
                logger.error(f"{i}: 股票 {stock} 聚合失败: {e}")
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

        # 查找已有资金总量
        existing = self._money_flow_agg_repo.find_longest_accumulation(stock.code)
        if existing:
            today = date.today()
            if existing.end_date >= today:
                logger.info(f"股票 {stock} 的资金总量已统计到今天。")
                return
            # 从次日开始读取 flow
            since = existing.end_date + timedelta(days=1)
            logger.info(f"从 {since} 开始读取股票 {stock} 的资金净流入")
            flows = self._money_flow_repo.find_by_code_and_date_range(
                stock.code, since, today,
            )
        else:
            # 读该股票的全量flow
            logger.info(f"没有找到股票 {stock} 的资金总量，读取它的全量flow")
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
                    new_agg = MoneyFlowAggregation.create(
                        code=stock.code,
                        start_date=flow.time.date(),
                        end_date=flow.time.date(),
                        trading_days=1,
                        accumulative=True,
                        money_flows=[flow]
                    )
            new_aggs.append(new_agg)

        # 批量保存
        self._money_flow_agg_repo.save(*new_aggs)
        logger.info(f"保存了 {len(new_aggs)} 条股票 {stock} 的资金总量数据")

    def _aggregate_stock_sliding(self, stock: Stock) -> None:
        """
            计算单只股票的 3/5/10/20 天滑动窗口净流入。
            内部遍历 _TRADING_DAYS 并逐个调用 _aggregate_stock_sliding_by_window。

            Args:
                stock (Stock): 待计算的股票对象
        """
        
        existing_of_windows = self._money_flow_agg_repo.find_latest_sliding_for_windows(stock.code, self._TRADING_DAYS)
        for window in self._TRADING_DAYS:
            self._aggregate_stock_sliding_by_window(stock, window, existing_of_windows.get(window))

    def _aggregate_stock_sliding_by_window(
            self, 
            stock: Stock, 
            window: int, 
            existing: Optional[MoneyFlowAggregation] = None
    ) -> None:
        """
            计算单只股票指定窗口天数的滑动窗口净流入。
            支持增量续算：先查该窗口的最新记录，从次日开始追加。

            Args:
                stock (Stock):  待计算的股票对象
                window (int):   窗口天数，如 3、5、10、20
                existing (Optional[MoneyFlowAggregation]): 该窗口已存在的最新记录

            逻辑：
              1. 若已有该窗口的最新记录，从次日开始追加
              2. 以 window 为单位滑动计算并保存
        """

        if existing:
            today = date.today()
            if existing.end_date >= today:
                logger.info(f"股票 {stock} 的 {window}天 净流入数据已统计到今天。")
                return
            since = existing.start_date + timedelta(days=1)
            # 从次日开始读取 flow
            logger.info(f"从 {since} 开始读取股票 {stock} 的资金净流入")
            flows = self._money_flow_repo.find_by_code_and_date_range(
                stock.code, since, today,
            )
        else:
            # 读取全量 flow
            logger.info(f"没有找到股票 {stock} 的 {window}日 净流入，读取它的全量flow")
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

            slide_flows = flows[i : i + window]
            agg = MoneyFlowAggregation.create(
                code = stock.code,
                start_date=slide_flows[0].time.date(),
                end_date=slide_flows[-1].time.date(),
                trading_days=len(slide_flows),
                accumulative=False,
                money_flows=slide_flows
            )
            new_aggs.append(agg)

        # 批量保存
        self._money_flow_agg_repo.save(*new_aggs)
        logger.info(f"保存了 {len(new_aggs)} 条股票 {stock} 的 {window}天 净流入数据")

    # ════════════════════════════════════════════════════════════
    #  板块聚合（第 3、4 种）
    # ════════════════════════════════════════════════════════════

    def _aggregate_sectors(self, sectors_date_range: Dict[str, tuple[str, date, date]]) -> None:
        """
            并发处理多个板块的聚合计算。
            依赖已计算好的个股聚合数据。

            对每个板块异步提交：
              1. 资金总量（_aggregate_sector_accumulation）
              2. 3/5/10/20 日滑动窗口（_aggregate_sector_sliding_by_window）
        """

        futures: Dict = {}
        for sector_code, (_, min_date, max_date) in sectors_date_range.items():
            
            # 资金总量
            future = self._default_pool.submit(
                self._aggregate_sector_accumulation, 
                sector_code, 
                min_date, 
                max_date,
            )
            futures[future] = sector_code

            # 滑动窗口
            for window in self._TRADING_DAYS:
                future = self._default_pool.submit(
                    self._aggregate_sector_sliding_by_window,
                    sector_code,
                    window,
                    min_date,
                    max_date,
                )
                futures[future] = sector_code

        total = len(futures)
        for i, future in enumerate(as_completed(futures), 1):
            sector_code = futures[future]
            try:
                future.result()
            except Exception as e:
                logger.error(f"板块 {sector_code} 计算子任务失败: {e}")
            if i % 20 == 0 or i == total:
                logger.info(f"板块聚合进度: {i}/{total}")

    def _aggregate_sector_accumulation(
        self,
        sector_code: str,
        min_date: date,
        max_date: date,
    ) -> None:
        """
            计算板块的资金总量（accumulation）。
            按交易日取当日成分股，对成员 MoneyFlow 求和后逐日累加。

            Args:
                sector_code: 板块代码
                min_date: 板块行情最早日期
                max_date: 板块行情最晚日期
        """
        existing = self._money_flow_agg_repo.find_longest_accumulation(sector_code)
        if existing:
            if existing.end_date >= max_date:
                logger.info(f"板块 {sector_code} 的资金总量已统计到 {existing.end_date}。")
                return
            since = existing.end_date + timedelta(days=1)
        else:
            since = min_date

        if since > max_date:
            return

        logger.info(f"将从 {since} 开始聚合板块 {sector_code} 的资金总量，截止 {max_date}")

        daily_flows = self._sector_member_daily_flows(sector_code, since, max_date)
        if not daily_flows:
            logger.warning(f"板块 {sector_code} 在 {since} ~ {max_date} 没有可聚合的成员资金流")
            return

        running: Optional[MoneyFlowAggregation] = existing
        new_aggs: List[MoneyFlowAggregation] = []
        for trading_day, member_flows in daily_flows:
            day_agg = MoneyFlowAggregation.create(
                code=sector_code,
                start_date=trading_day,
                end_date=trading_day,
                trading_days=1,
                accumulative=True,
                money_flows=member_flows,
            )

            if running:
                running = replace(
                    running.merge(day_agg),
                    trading_days=running.trading_days + 1,
                    type=AggregationType.SECTOR,
                    accumulative=True,
                )
            else:
                running = replace(
                    day_agg,
                    type=AggregationType.SECTOR,
                    accumulative=True,
                )
            new_aggs.append(running)

        self._money_flow_agg_repo.save(*new_aggs)
        logger.info(f"保存了 {len(new_aggs)} 条板块 {sector_code} 的资金总量数据")

    def _aggregate_sector_sliding_by_window(
        self,
        sector_code: str,
        window: int,
        min_date: date,
        max_date: date,
    ) -> None:
        """
            计算板块指定窗口的滑动净流入。
            按交易日从 dc_member 取成分，汇总当日成员 flow 后再滑窗。
        """
        latest_of_window = self._money_flow_agg_repo.find_latest_sliding_for_windows(
            sector_code, [window],
        )
        existing = latest_of_window.get(window)
        if existing and existing.end_date >= max_date:
            logger.info(f"板块 {sector_code} 的 {window}日 净流入已统计到 {existing.end_date}。")
            return

        daily_flows = self._sector_member_daily_flows(sector_code, min_date, max_date)
        if not daily_flows:
            logger.warning(f"板块 {sector_code} 在 {min_date} ~ {max_date} 没有可滑动的成员资金流")
            return

        count = len(daily_flows)
        if count < window:
            logger.warning(f"板块 {sector_code} 有效交易日不足 {window} 天，跳过 {window}日 滑窗")
            return

        new_aggs: List[MoneyFlowAggregation] = []
        for i in range(count - window + 1):
            start_date = daily_flows[i][0]
            end_date = daily_flows[i + window - 1][0]
            if existing and start_date <= existing.start_date:
                continue
            if end_date > max_date:
                break

            if not new_aggs:
                logger.info(
                    f"将从 {start_date} 开始聚合板块 {sector_code} 的 {window}日 净流入"
                )

            window_flows: List[MoneyFlow] = []
            for j in range(i, i + window):
                window_flows.extend(daily_flows[j][1])

            agg = MoneyFlowAggregation.create(
                code=sector_code,
                start_date=start_date,
                end_date=end_date,
                trading_days=window,
                accumulative=False,
                money_flows=window_flows,
            )
            new_aggs.append(replace(agg, type=AggregationType.SECTOR))

        if not new_aggs:
            logger.info(f"板块 {sector_code} 的 {window}日 净流入无需更新")
            return

        self._money_flow_agg_repo.save(*new_aggs)
        logger.info(f"保存了 {len(new_aggs)} 条板块 {sector_code} 的 {window}日 净流入数据")

    def _sector_member_daily_flows(
        self,
        sector_code: str,
        start_date: date,
        end_date: date,
    ) -> List[tuple[date, List[MoneyFlow]]]:
        """
            按交易日构建板块当日成员 flow 序列（跳过无成员资金流的日期）。
        """

        trading_days = self._trading_day_repo.find_trading_days_between(
            start_date, end_date,
        )
        daily_flows: List[tuple[date, List[MoneyFlow]]] = []
        for trading_day in trading_days:
            member_flows = self._sector_member_flows(sector_code, trading_day)
            if member_flows:
                daily_flows.append((trading_day, member_flows))
        return daily_flows

    def _sector_member_flows(
        self,
        sector_code: str,
        trading_day: date,
    ) -> List[MoneyFlow]:
        """
            取指定交易日板块成分股的 MoneyFlow 列表。
        """
        
        members = self._sector_repo.find_dc_members_by_date(sector_code, trading_day)
        member_flows: List[MoneyFlow] = []
        for member in members:
            member_code = code_from_ts_code(str(member))
            member_flow = self._money_flow_repo.find_by_code_and_date(
                member_code, trading_day,
            )
            if member_flow:
                member_flows.append(member_flow)
        return member_flows