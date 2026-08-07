import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from domain.daily_quote import DailyQuote
from infra.adapters.efinance_adapter import EfinanceAdapter
from infra.adapters.tushare_adapter import TushareAdapter
from infra.config import get_market_earliest_date
from infra.database.connection import get_db
from infra.log import logger


class DailyQuoteRepository:
    """日线行情数据仓库，管理 daily_quotes 表"""

    _REQUEST_INTERVAL_SECONDS = 0.3
    _CACHE_TTL_SECONDS = 24 * 60 * 60

    def __init__(
        self,
        stock_adapter: EfinanceAdapter,
        quote_adapter: TushareAdapter,
    ):
        """
        Args:
            stock_adapter: 用于获取股票列表的适配器
            quote_adapter: 用于获取日线行情数据的适配器
        """
        self._stock_adapter = stock_adapter
        self._quote_adapter = quote_adapter
        self._find_by_code_cache: Dict[str, List[DailyQuote]] = {}

    def refresh(self, stock_codes: Optional[List[str]] = None,
                force: bool = True) -> None:
        """同步日线行情数据到数据库

        Args:
            stock_codes: 股票代码列表，为 None 则自动获取全市场
            force: 是否强制刷新
        """
        if not force and self._latest():
            logger.info("日线行情缓存有效，跳过刷新")
            return
        self._update_from_adapter(stock_codes)

    def _latest(self) -> bool:
        """检查数据库中是否有在缓存有效期内的数据"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS cnt, MAX(updated_at) AS max_updated
                   FROM daily_quotes WHERE is_deleted = 0"""
            ).fetchone()
            count = row["cnt"]
            if count == 0:
                return False
            max_updated = row["max_updated"]
            if max_updated is None:
                return False
            updated_dt = datetime.strptime(max_updated, "%Y-%m-%d %H:%M:%S")
            return (time.time() - updated_dt.timestamp()) < self._CACHE_TTL_SECONDS

    def _update_from_adapter(self, stock_codes: Optional[List[str]] = None) -> None:
        """从适配器获取日线行情，增量更新到数据库"""
        if stock_codes is None:
            stocks = self._stock_adapter.get_all_stocks()
            stock_codes = [stock.code for stock in stocks]

        if not stock_codes:
            logger.warning("未提供股票代码列表，且无法从适配器获取股票信息，无法更新日线行情")
            return

        logger.info(f"开始更新日线行情数据，共 {len(stock_codes)} 只股票")
        last_quote_dates = self._load_last_quote_dates()
        total_saved = 0
        index = 0

        for code in stock_codes:
            last_date = last_quote_dates.get(code)
            if self._is_up_to_date(last_date):
                logger.info(f"股票 {code} 最新行情日期 {last_date} 已同步到最新交易日，跳过")
                continue

            if last_date:
                start_date = last_date + timedelta(days=1)
            else:
                start_date = get_market_earliest_date()

            today = date.today()
            if start_date > today:
                logger.warning(f"股票 {code} 最新行情起始日期 {start_date} 大于今天 {today}，跳过")
                continue

            index += 1
            logger.info(
                f"{index}: 正在获取股票 {code} 日线行情 "
                f"[{start_date} -> {today}]..."
            )
            quotes = self._quote_adapter.get_daily_quote(code, start_date, today)
            if quotes:
                self._save_quotes_to_db(quotes)
                total_saved += len(quotes)

            time.sleep(self._REQUEST_INTERVAL_SECONDS)

        logger.info(f"日线行情数据更新完成，共保存 {total_saved} 条新记录")

    def _load_last_quote_dates(self) -> Dict[str, Optional[date]]:
        """查询每只股票已有的最后交易日期"""
        result: Dict[str, Optional[date]] = {}
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, MAX(trade_date) AS max_date
                   FROM daily_quotes
                   WHERE is_deleted = 0
                   GROUP BY code"""
            ).fetchall()
            for row in rows:
                max_date_str = row["max_date"]
                if max_date_str:
                    result[row["code"]] = datetime.strptime(
                        max_date_str, "%Y-%m-%d"
                    ).date()
                else:
                    result[row["code"]] = None
        return result

    def _is_up_to_date(self, last_date: Optional[date]) -> bool:
        if last_date is None:
            return False
        return last_date >= self._get_last_trading_day()

    def _get_last_trading_day(self) -> date:
        today = date.today()
        weekday = today.weekday()
        if weekday == 5:
            return today - timedelta(days=1)
        if weekday == 6:
            return today - timedelta(days=2)
        return today

    def _save_quotes_to_db(self, quotes: List[DailyQuote]) -> None:
        """将日线行情写入数据库"""
        if not quotes:
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            for quote in quotes:
                trade_date = quote.date.strftime("%Y-%m-%d")
                conn.execute(
                    """INSERT INTO daily_quotes (
                           code, trade_date,
                           open, high, low, close,
                           volume, amount, change, pct_chg,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        quote.code, trade_date,
                        quote.open, quote.high, quote.low, quote.close,
                        quote.volume, quote.amount, quote.change, quote.pct_chg,
                        now, now,
                    ),
                )

    def find_by_code(self, code: str, force: bool = False) -> List[DailyQuote]:
        """
        根据股票代码查询所有日线行情。
        结果会缓存在内存中，优先从缓存读取。
        """
        if not force and code in self._find_by_code_cache:
            return self._find_by_code_cache[code]

        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, trade_date,
                          open, high, low, close,
                          volume, amount, change, pct_chg
                   FROM daily_quotes
                   WHERE code = ? AND is_deleted = 0
                   ORDER BY trade_date""",
                (code,),
            ).fetchall()
            result = [self._row_to_daily_quote(row) for row in rows]
            self._find_by_code_cache[code] = result
            return result

    def find_by_code_and_date_range(
        self,
        code: str,
        start_date: date,
        end_date: date,
        force: bool = False,
    ) -> List[DailyQuote]:
        """
        按股票代码和日期范围查询日线行情。
        优先走 find_by_code 的缓存，在内存中过滤日期范围。
        """
        all_quotes = self.find_by_code(code, force)
        return [
            q for q in all_quotes
            if start_date <= q.date <= end_date
        ]

    def _row_to_daily_quote(self, row) -> DailyQuote:
        return DailyQuote(
            code=row["code"],
            date=datetime.strptime(row["trade_date"], "%Y-%m-%d").date(),
            open=row["open"] or 0.0,
            high=row["high"] or 0.0,
            low=row["low"] or 0.0,
            close=row["close"] or 0.0,
            volume=row["volume"] or 0,
            amount=row["amount"] or 0.0,
            change=row["change"] or 0.0,
            pct_chg=row["pct_chg"] or 0.0,
        )

    def clear_cache(self) -> None:
        self._find_by_code_cache.clear()
