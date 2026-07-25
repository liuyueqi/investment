"""资金流聚合数据仓库，管理 money_flow_aggregation 表"""

import time
import threading
from datetime import date, datetime
from typing import List, Dict, Optional

from domain.money_flow_aggregation import MoneyFlowAggregation
from infra.database.connection import get_db
from infra.log import logger


class MoneyFlowAggregationRepository:
    """资金流聚合数据仓库"""

    _save_lock = threading.Lock()

    def __init__(self):
        self._accumulation_cache: Dict[str, List[MoneyFlowAggregation]] = {}
        self._sliding_cache: Dict[str, List[MoneyFlowAggregation]] = {}
        self._accumulation_lock = threading.RLock()
        self._sliding_lock = threading.RLock()

    _UPSERT_SQL = """INSERT OR REPLACE INTO money_flow_aggregation (
                       code, type, start_date, end_date, trading_days, is_accumulative,
                       main_net, main_cnt,
                       huge_buy_net, huge_sell_net,
                       huge_buy_cnt, huge_sell_cnt,
                       large_buy_net, large_sell_net,
                       large_buy_cnt, large_sell_cnt,
                       medium_buy_net, medium_sell_net,
                       medium_buy_cnt, medium_sell_cnt,
                       small_buy_net, small_sell_net,
                       small_buy_cnt, small_sell_cnt,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?,
                             ?, ?,
                             ?, ?,
                             ?, ?,
                             ?, ?,
                             ?, ?,
                             ?, ?,
                             ?, ?,
                             ?, ?,
                             ?, ?,
                             ?, ?)"""

    def save(self, *aggs: MoneyFlowAggregation) -> None:
        if not aggs:
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        params = [self._upsert_params(agg, now) for agg in aggs]

        with self._save_lock:
            with get_db() as conn:
                conn.executemany(self._UPSERT_SQL, params)

    def clear_cache(self) -> None:
        with self._accumulation_lock:
            self._accumulation_cache.clear()
        with self._sliding_lock:
            self._sliding_cache.clear()

    def _upsert_params(self, agg: MoneyFlowAggregation, now: str) -> tuple:
        return (
            agg.code, agg.type,
            agg.start_date.isoformat(),
            agg.end_date.isoformat(),
            agg.trading_days,
            int(agg.accumulative),
            agg.main_net, agg.main_cnt,
            agg.huge_buy_net, agg.huge_sell_net,
            agg.huge_buy_cnt, agg.huge_sell_cnt,
            agg.large_buy_net, agg.large_sell_net,
            agg.large_buy_cnt, agg.large_sell_cnt,
            agg.medium_buy_net, agg.medium_sell_net,
            agg.medium_buy_cnt, agg.medium_sell_cnt,
            agg.small_buy_net, agg.small_sell_net,
            agg.small_buy_cnt, agg.small_sell_cnt,
            now, now,
        )

    def find_by_date_range(
            self, 
            code: str, 
            start_date: date, 
            end_date: date, 
            accumulative: bool
    ) -> Optional[MoneyFlowAggregation]:
        with get_db() as conn:
            row = conn.execute(
                """SELECT * FROM money_flow_aggregation
                   WHERE code = ?
                     AND start_date = ?
                     AND end_date = ?
                     AND is_accumulative = ?""",
                (code, start_date.isoformat(), end_date.isoformat(), int(accumulative)),
            ).fetchone()
            return self._row_to_agg(row) if row else None

    def find_longest_accumulation(self, code: str) -> Optional[MoneyFlowAggregation]:
        with get_db() as conn:
            row = conn.execute(
                """SELECT * FROM money_flow_aggregation
                   WHERE code = ?
                     AND is_accumulative = 1
                   ORDER BY trading_days DESC
                   LIMIT 1""", (code,)
            ).fetchone()
            return self._row_to_agg(row) if row else None

    def find_accumulations_by_code(
            self, 
            code: str, 
            since: Optional[date], 
            force: bool = False,
    ) -> List[MoneyFlowAggregation]:
        cache_key = f"{code}"

        with self._accumulation_lock:
            if not force and cache_key in self._accumulation_cache:
                cached = self._accumulation_cache[cache_key]
                if since is None:
                    return cached
                return [c for c in cached if c.end_date >= since]

        sql = """SELECT * FROM money_flow_aggregation
                   WHERE code = ? """
        params: List = [code]

        if since:
            sql = sql + """ AND end_date >= ? """
            params.append(since)

        sql = sql + """ AND is_accumulative = 1
                   ORDER BY trading_days"""

        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = self._rows_to_aggs(rows)
            with self._accumulation_lock:
                self._accumulation_cache[cache_key] = result
            return result

    def find_latest_by_trading_days(self, code: str, trading_days: int) -> Optional[MoneyFlowAggregation]:
        with get_db() as conn:
            row = conn.execute(
                """SELECT * FROM money_flow_aggregation
                   WHERE code = ?
                     AND trading_days = ?
                     AND is_accumulative = 0
                   ORDER BY start_date DESC
                   LIMIT 1""",
                (code, trading_days),
            ).fetchone()
            return self._row_to_agg(row) if row else None

    def find_by_trading_days(
            self, 
            code: str, 
            trading_days: int,
            since: Optional[date], 
            force: bool = False,
    ) -> List[MoneyFlowAggregation]:
        cache_key = f"{code}:{trading_days}d"

        with self._sliding_lock:
            if not force and cache_key in self._sliding_cache:
                cached = self._sliding_cache[cache_key]
                if since is None:
                    return cached
                return [c for c in cached if c.start_date >= since]

        sql = """SELECT * FROM money_flow_aggregation 
                    WHERE code = ?
                    AND trading_days = ?
                    AND is_accumulative = 0 """
        params: List = [code, trading_days]

        if since:
            sql = sql + """ AND start_date >= ? """
            params.append(since)
        sql = sql + """ ORDER BY start_date """

        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = self._rows_to_aggs(rows)
            with self._sliding_lock:
                self._sliding_cache[cache_key] = result
            return result

    def _rows_to_aggs(self, rows) -> List[MoneyFlowAggregation]:
        result: List[MoneyFlowAggregation] = []
        for row in rows:
            agg = self._row_to_agg(row)
            if agg is not None:
                result.append(agg)
        return result

    def _row_to_agg(self, row: Optional[dict]) -> Optional[MoneyFlowAggregation]:
        if row is None:
            return None

        def _opt_date(val):
            return datetime.strptime(val, "%Y-%m-%d").date()

        return MoneyFlowAggregation(
            code=row["code"],
            type=row["type"],
            start_date=_opt_date(row["start_date"]),
            end_date=_opt_date(row["end_date"]),
            trading_days=row["trading_days"] or 1,
            accumulative=bool(row["is_accumulative"]),
            main_net=row["main_net"] or 0.0,
            main_cnt=row["main_cnt"] or 0,
            huge_buy_net=row["huge_buy_net"],
            huge_sell_net=row["huge_sell_net"],
            huge_buy_cnt=row["huge_buy_cnt"],
            huge_sell_cnt=row["huge_sell_cnt"],
            large_buy_net=row["large_buy_net"],
            large_sell_net=row["large_sell_net"],
            large_buy_cnt=row["large_buy_cnt"],
            large_sell_cnt=row["large_sell_cnt"],
            medium_buy_net=row["medium_buy_net"],
            medium_sell_net=row["medium_sell_net"],
            medium_buy_cnt=row["medium_buy_cnt"],
            medium_sell_cnt=row["medium_sell_cnt"],
            small_buy_net=row["small_buy_net"],
            small_sell_net=row["small_sell_net"],
            small_buy_cnt=row["small_buy_cnt"],
            small_sell_cnt=row["small_sell_cnt"],
        )
