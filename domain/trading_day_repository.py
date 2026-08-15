from datetime import date, datetime
from typing import List, Optional

from infra.adapters.tushare_adapter import TushareAdapter
from infra.database.connection import get_db
from infra.log import logger


class TradingDayRepository:
    """交易日历数据仓库，管理 trading_days 表"""

    def __init__(self, adapter: TushareAdapter):
        self._adapter = adapter

    def refresh(self, incr: bool = True, force: bool = False) -> None:
        """同步交易日历到数据库。

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
            self._save_incremental(trading_days)
        else:
            self._replace_all(trading_days)

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

    def _save_incremental(self, trading_days: List[date]) -> None:
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
                   ON CONFLICT(trade_date) DO UPDATE SET
                       updated_at = excluded.updated_at,
                       is_deleted = 0""",
                rows,
            )
        logger.info(f"增量写入交易日历 {len(trading_days)} 条")

    def find_trading_days(self) -> List[date]:
        """查询全部交易日，按日期升序"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT trade_date
                   FROM trading_days
                   WHERE is_deleted = 0
                   ORDER BY trade_date"""
            ).fetchall()
        return [self._row_to_date(row) for row in rows]

    def find_trading_days_between(
        self, start_date: date, end_date: date,
    ) -> List[date]:
        """查询闭区间 [start_date, end_date] 内的交易日"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT trade_date
                   FROM trading_days
                   WHERE is_deleted = 0
                     AND trade_date >= ?
                     AND trade_date <= ?
                   ORDER BY trade_date""",
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [self._row_to_date(row) for row in rows]

    def is_trading_day(self, day: date) -> bool:
        """判断指定日期是否为交易日"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT 1
                   FROM trading_days
                   WHERE trade_date = ? AND is_deleted = 0""",
                (day.isoformat(),),
            ).fetchone()
        return bool(row)

    @staticmethod
    def _row_to_date(row) -> date:
        return datetime.strptime(row["trade_date"], "%Y-%m-%d").date()
