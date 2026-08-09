"""数据下载器：从外部接口下载交易日 / 股票 / 板块 / 资金流向 / 日线行情到本地数据库"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from typing import Callable, Iterable, List, Optional, Set

from domain.daily_quote_repository import DailyQuoteRepository
from domain.money_flow_repository import MoneyFlowRepository
from domain.sector_repository import SectorRepository
from domain.stock_repository import StockRepository
from domain.trading_day_repository import TradingDayRepository
from infra.database.schema import init_db
from infra.log import logger

SEPARATOR = "=" * 50


class DownloadScope(str, Enum):
    STOCK = "stock"
    SECTOR = "sector"
    QUOTE = "quote"
    FLOW = "flow"


_ALL_SCOPES = frozenset(DownloadScope)
_VALID_SCOPE_VALUES = {s.value for s in DownloadScope}


class Downloader:
    """数据下载器，通过 IoC 容器获取 Repository 执行数据下载"""

    def __init__(
        self,
        stock_repo: StockRepository,
        sector_repo: SectorRepository,
        money_flow_repo: MoneyFlowRepository,
        daily_quote_repo: DailyQuoteRepository,
        trading_day_repo: TradingDayRepository,
        default_pool: ThreadPoolExecutor,
    ):
        self._stock_repo = stock_repo
        self._sector_repo = sector_repo
        self._money_flow_repo = money_flow_repo
        self._daily_quote_repo = daily_quote_repo
        self._trading_day_repo = trading_day_repo
        self._default_pool = default_pool

    # ── 数据库初始化 ──────────────────────────────────────────


    def _init_database(self) -> None:
        init_db()
        logger.info("数据库初始化完成!")


    def _resolve_scopes(self, scope: Optional[Iterable[str]]) -> Set[DownloadScope]:
        """解析 scope；为空则下载全部。支持逗号或空格分隔的多选。"""
        if not scope:
            return set(_ALL_SCOPES)

        raw_parts: List[str] = []
        for item in scope:
            raw_parts.extend(
                p.strip() for p in str(item).replace(",", " ").split() if p.strip()
            )

        resolved: Set[DownloadScope] = set()
        unknown: List[str] = []
        for part in raw_parts:
            key = part.lower()
            if key not in _VALID_SCOPE_VALUES:
                unknown.append(part)
            else:
                resolved.add(DownloadScope(key))

        if unknown:
            valid = ", ".join(sorted(_VALID_SCOPE_VALUES))
            raise ValueError(f"未知 download scope: {unknown}；可选: {valid}")
        # 解析后仍为空（例如只有空白）时，与 scope 为空相同：下载全部
        return resolved or set(_ALL_SCOPES)

    def _latest_stock_codes(self) -> List[str]:
        """从 stock_repository 获取最新股票代码列表"""
        return [stock.code for stock in self._stock_repo.find_all()]

    # ── 步骤方法 ──────────────────────────────────────────────

    def _download_trading_days(self) -> None:
        """下载交易日历（默认增量，非强制）"""
        logger.info(f"\n{SEPARATOR}")
        logger.info("下载交易日历")
        logger.info(SEPARATOR)

        self._trading_day_repo.refresh(incr=True, force=False)

    def _download_stocks(self) -> None:
        """下载股票数据到数据库"""
        logger.info(f"\n{SEPARATOR}")
        logger.info("下载股票数据")
        logger.info(SEPARATOR)

        self._stock_repo.refresh(force=True)
        stocks = self._stock_repo.find_all()
        logger.info(f"共下载 {len(stocks)} 只股票")
        if stocks:
            logger.info("前5只股票：")
            for stock in stocks[:5]:
                logger.info(f"  {stock.code} - {stock.name} ({stock.market})")

    def _download_sectors(self, stock_codes: List[str]) -> None:
        """下载板块数据到数据库"""
        logger.info(f"\n{SEPARATOR}")
        logger.info("下载板块数据")
        logger.info(SEPARATOR)

        try:
            self._sector_repo.refresh(force=True)
            sectors = self._sector_repo.find_all()
            logger.info(f"共下载 {len(sectors)} 个板块")
        except ValueError as e:
            logger.error(f"下载板块数据失败: {e}")

    def _download_daily_quotes(self, stock_codes: List[str]) -> None:
        """下载日线行情数据到数据库"""
        logger.info(f"\n{SEPARATOR}")
        logger.info("下载日线行情数据")
        logger.info(SEPARATOR)

        self._daily_quote_repo.refresh(stock_codes, force=True)

    def _download_money_flows(self, stock_codes: List[str]) -> None:
        """下载资金流向数据到数据库"""
        logger.info(f"\n{SEPARATOR}")
        logger.info("下载资金流向数据")
        logger.info(SEPARATOR)

        self._money_flow_repo.refresh(stock_codes, force=True)

    def _run_tasks(self, tasks: List[tuple[str, Callable[[], None]]]) -> None:
        if not tasks:
            return
        if len(tasks) == 1:
            name, fn = tasks[0]
            try:
                fn()
                logger.info(f"{name}下载完成")
            except Exception as e:
                logger.error(f"{name}下载失败: {e}", exc_info=True)
            return

        logger.info(f"\n{SEPARATOR}")
        logger.info(f"并行下载: {', '.join(name for name, _ in tasks)}")
        logger.info(SEPARATOR)
        futures = {
            self._default_pool.submit(fn): name
            for name, fn in tasks
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
                logger.info(f"{name}下载完成")
            except Exception as e:
                logger.error(f"{name}下载失败: {e}", exc_info=True)

    # ── 入口 ──────────────────────────────────────────────────

    def download(self, scope: Optional[Iterable[str]] = None) -> None:
        """按 scope 下载数据；可多选。

        Args:
            scope: 下载范围，支持 stock / sector / quote / flow。
                   为空则依次下载全部：stock → sector → quote → flow。
                   无论 scope 如何，都会先刷新交易日历（incr=True, force=False）。

        依赖关系：
            - trading_days 每次 download 都会刷新
            - stock 独立下载
            - sector / quote / flow 均依赖 stock_repository 中的最新股票列表
        """
        scopes = self._resolve_scopes(scope)
        download_all = scopes == _ALL_SCOPES
        start_time = time.time()
        logger.info(f"开始下载，scope={sorted(s.value for s in scopes)}")

        self._init_database()

        # 交易日历：每次 download 默认执行
        self._download_trading_days()

        if DownloadScope.STOCK in scopes:
            self._download_stocks()

        dependent = scopes & {
            DownloadScope.SECTOR,
            DownloadScope.QUOTE,
            DownloadScope.FLOW,
        }
        if not dependent:
            elapsed = time.time() - start_time
            logger.info(f"\n{SEPARATOR}")
            logger.info(f"数据下载完成，耗时 {elapsed:.2f} 秒")
            return

        codes = self._latest_stock_codes()
        if not codes:
            logger.warning(
                "stock_repository 中没有股票数据，跳过 "
                f"{sorted(s.value for s in dependent)}；请先 download stock"
            )
            elapsed = time.time() - start_time
            logger.info(f"\n{SEPARATOR}")
            logger.info(f"数据下载完成，耗时 {elapsed:.2f} 秒")
            return

        logger.info(f"依赖股票列表共 {len(codes)} 只（来自 stock_repository）")

        if download_all:
            # scope 为空：严格依次下载全部
            self._download_sectors(codes)
            self._download_daily_quotes(codes)
            self._download_money_flows(codes)
        else:
            if DownloadScope.SECTOR in dependent:
                self._download_sectors(codes)

            parallel_tasks = []
            if DownloadScope.QUOTE in dependent:
                parallel_tasks.append((
                    "日线行情",
                    lambda: self._download_daily_quotes(codes),
                ))
            if DownloadScope.FLOW in dependent:
                parallel_tasks.append((
                    "资金流向",
                    lambda: self._download_money_flows(codes),
                ))
            self._run_tasks(parallel_tasks)

        elapsed = time.time() - start_time
        logger.info(f"\n{SEPARATOR}")
        logger.info(f"数据下载完成，耗时 {elapsed:.2f} 秒")
