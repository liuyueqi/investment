import bisect
import threading
from datetime import date, datetime
from typing import List, Optional, Tuple

from infra.adapters.tushare_adapter import TushareAdapter
from infra.database.connection import get_db
from infra.log import logger


class TradingDayRepository:
    """交易日历数据仓库，管理 trading_days 表"""

    def __init__(self, adapter: TushareAdapter):
        self._adapter = adapter
        # 不可变快照：(升序交易日, 集合)；一写多读，读者持引用无需加锁
        self._cache: Optional[Tuple[Tuple[date, ...], frozenset[date]]] = None
        self._cache_lock = threading.RLock()

    def refresh(self, incr: bool = True, force: bool = False) -> None:
        """
            同步交易日历到数据库。

            Args:
                incr: 是否增量更新。False 时清空表后全量写入。
                force: 是否强制更新。False 且表中最新交易日已是今天时跳过拉取。
        """
        if not force and self._latest_is_today():
            logger.info("trading_days 最新日期已是今天，跳过刷新")
            return

        trading_days = self._adapter.get_all_trading_days()
        if not trading_days:
            logger.warning("获取交易日历失败或为空，跳过刷新")
            return

        if incr:
            self._insert_new(trading_days)
        else:
            self._replace_all(trading_days)
        self.clear_cache()

    def _latest_is_today(self) -> bool:
        latest = self._load_latest_trade_date()
        return bool(latest) and latest == date.today()

    def _load_latest_trade_date(self) -> Optional[date]:
        with get_db() as conn:
            row = conn.execute(
                """SELECT MAX(trade_date) AS max_date
                   FROM trading_days
                   WHERE is_deleted = 0"""
            ).fetchone()
        if not row or not row["max_date"]:
            return None
        return datetime.strptime(row["max_date"], "%Y-%m-%d").date()

    def _insert_new(self, trading_days: List[date]) -> None:
        latest = self._load_latest_trade_date()
        if latest:
            trading_days = [d for d in trading_days if d > latest]

        if not trading_days:
            logger.info("没有新的交易日需要写入")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [(day.isoformat(), now, now) for day in trading_days]
        with get_db() as conn:
            conn.executemany(
                """INSERT INTO trading_days (trade_date, created_at, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(trade_date) DO NOTHING""",
                rows,
            )
        logger.info(f"增量写入交易日历 {len(trading_days)} 条")

    def _replace_all(self, trading_days: List[date]) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [(day.isoformat(), now, now) for day in trading_days]
        with get_db() as conn:
            conn.execute("DELETE FROM trading_days")
            conn.executemany(
                """INSERT INTO trading_days (trade_date, created_at, updated_at)
                   VALUES (?, ?, ?)""",
                rows,
            )
        logger.info(f"全量替换交易日历，共 {len(trading_days)} 条")

    def find_trading_days(self) -> List[date]:
        """查询全部交易日，按日期升序"""
        days, _ = self._get_cache()
        return list(days)

    def find_trading_days_between(
        self, start_date: date, end_date: date,
    ) -> List[date]:
        """查询闭区间 [start_date, end_date] 内的交易日"""
        days, _ = self._get_cache()
        lo = bisect.bisect_left(days, start_date)
        hi = bisect.bisect_right(days, end_date)
        return list(days[lo:hi])

    def is_trading_day(self, day: date) -> bool:
        """判断指定日期是否为交易日"""
        _, day_set = self._get_cache()
        return day in day_set

    def find_latest_trading_day(self, before: Optional[date] = None) -> Optional[date]:
        """
            最近一个交易日；默认不晚于今天。

            Args:
                before: 可选的截止日期，默认为今天。

            Returns:
                Optional[date]: 最近一个交易日，或 None 如果找不到。
        """
        days, _ = self._get_cache()
        if not days:
            return None
        cutoff = before if before is not None else date.today()
        hi = bisect.bisect_right(days, cutoff)
        if hi == 0:
            return None
        return days[hi - 1]

    def _get_cache(self) -> Tuple[Tuple[date, ...], frozenset[date]]:
        cache = self._cache
        if cache is not None:
            return cache
        with self._cache_lock:
            if self._cache is not None:
                return self._cache
            days = tuple(self._load_all_from_db())
            self._cache = (days, frozenset(days))
            return self._cache

    def _load_all_from_db(self) -> List[date]:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT trade_date
                   FROM trading_days
                   WHERE is_deleted = 0
                   ORDER BY trade_date"""
            ).fetchall()
        return [self._row_to_date(row) for row in rows]

    def _row_to_date(self, row) -> date:
        return datetime.strptime(row["trade_date"], "%Y-%m-%d").date()

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache = None
