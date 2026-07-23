"""资金流聚合数据仓库，管理 money_flow_aggregation 表"""

from datetime import date, datetime
from typing import List, Dict, Optional

from domain.money_flow_aggregation import MoneyFlowAggregation, AggregationType
from infra.database.connection import get_db
from infra.log import logger


class MoneyFlowAggregationRepository:
    """资金流聚合数据仓库"""

    def __init__(self):
        self._accumulation_cache: Dict[str, List[MoneyFlowAggregation]] = {}
        self._sliding_cache: Dict[str, List[MoneyFlowAggregation]] = {}

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
        """
            保存一条或多条聚合记录（UPSERT，幂等安全）。
            利用 INSERT OR REPLACE + executemany 实现，若主键冲突则覆盖整行。
            传入单个对象或批量传入多个对象均可。

            Args:
                *aggs: 一个或多个 MoneyFlowAggregation 对象
        """
        if not aggs:
            return

        # 清除缓存
        self._accumulation_cache.clear()
        self._sliding_cache.clear()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        params = [self._upsert_params(agg, now) for agg in aggs]
        with get_db() as conn:
            conn.executemany(self._UPSERT_SQL, params)

    # ── 参数辅助 ──────────────────────────────────────────────

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

    # ── 查询方法 ──────────────────────────────────────────────

    def find_by_date_range(
            self, code: str, type: str, start_date: date, end_date: date
    ) -> Optional[MoneyFlowAggregation]:
        """
            根据统计数据的日期范围精确查询指定标的的聚合记录。

            Args:
                code (str):       股票代码或板块代码
                type (str):       实体类型，AggregationType.STOCK / AggregationType.SECTOR
                start_date (date): 统计起始日期
                end_date (date):   统计结束日期

            Returns:
                匹配的 MoneyFlowAggregation 对象，若不存在则返回 None
        """
        
        with get_db() as conn:
            row = conn.execute(
                """SELECT * FROM money_flow_aggregation
                   WHERE code = ?
                     AND type = ?
                     AND start_date = ?
                     AND end_date = ?""",
                (code, type, start_date.isoformat(), end_date.isoformat()),
            ).fetchone()
            return self._row_to_agg(row) if row else None
        
    def find_longest_accumulation(
            self,
            code: str,
            type: str,
    ) -> Optional[MoneyFlowAggregation]:
        """
            查询指定标的中 trading_days 最大的累计资金总量记录。
            用于增量续算：从该记录的 end_date 次日开始追加计算即可。

            Args:
                code (str):  股票代码或板块代码
                type (str):  实体类型，AggregationType.STOCK / AggregationType.SECTOR

            Returns:
                trading_days 最大的累计记录，若无累计记录则返回 None
        """

        with get_db() as conn:
            row = conn.execute(
                """SELECT * FROM money_flow_aggregation
                   WHERE code = ?
                     AND type = ?
                     AND is_accumulative = 1
                   ORDER BY trading_days DESC
                   LIMIT 1""",
                (code, type),
            ).fetchone()
            return self._row_to_agg(row) if row else None

    def find_accumulations_by_code(
            self,
            code: str,
            type: str,
            since: Optional[date],
            force: bool = False,
    ) -> List[MoneyFlowAggregation]:
        """
            查询指定标的从 since 日期开始的资金总量记录。
            用于板块聚合时获取成分股的累计数据。
            结果会缓存在内存中，优先从缓存读取。

            Args:
                code (str):        股票代码或板块代码
                type (str):        实体类型，AggregationType.STOCK / AggregationType.SECTOR
                since (date):      起始日期（含）。
                force (bool):      是否强制从数据库读取并更新缓存。

            Returns:
                符合条件的累计聚合记录列表（按 trading_days 升序）
        """

        # 构建缓存 key
        cache_key = f"{type}:{code}"
    
        if not force and cache_key in self._accumulation_cache:
            cached = self._accumulation_cache[cache_key]
            if since is None:
                return cached
            return [c for c in cached if c.end_date >= since]

        sql = """SELECT * FROM money_flow_aggregation
                   WHERE code = ?
                     AND type = ? """
        params: List = [code, type]

        if since:
            sql = sql + """ AND end_date >= ? """
            params.append(since)

        sql = sql + """ AND is_accumulative = 1
                   ORDER BY trading_days"""

        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = self._rows_to_aggs(rows)
            # 缓存（全量缓存，忽略 since）
            self._accumulation_cache[cache_key] = result
            return result

    def find_latest_by_trading_days(
            self, code: str, type: str, trading_days: int
    ) -> Optional[MoneyFlowAggregation]:
        """
            查询指定标的和窗口天数的最新一条滑动窗口聚合记录。
            用于增量续算：从该记录的 start_date 次日开始追加计算即可。

            Args:
                code (str):         股票代码或板块代码
                type (str):         实体类型，AggregationType.STOCK / AggregationType.SECTOR
                trading_days (int): 窗口天数，如 3、5、10、20

            Returns:
                start_date 最新的滑动窗口记录，若不存在则返回 None
        """
        
        with get_db() as conn:
            row = conn.execute(
                """SELECT * FROM money_flow_aggregation
                   WHERE code = ?
                     AND type = ?
                     AND trading_days = ?
                   ORDER BY start_date DESC
                   LIMIT 1""",
                (code, type, trading_days),
            ).fetchone()
            return self._row_to_agg(row) if row else None
        
    def find_by_trading_days(
            self,
            code: str,
            type: str,
            trading_days: int,
            since: Optional[date],
            force: bool = False,
    ) -> List[MoneyFlowAggregation]:
        """
            查询指定标的和窗口天数的滑动窗口聚合记录。
            用于板块聚合时获取成分股的滑动窗口数据。
            结果会缓存在内存中，优先从缓存读取。

            Args:
                code (str):         股票代码或板块代码
                type (str):         实体类型，AggregationType.STOCK / AggregationType.SECTOR
                trading_days (int): 窗口天数，如 3、5、10、20
                since (date):       起始日期（含）。若为 None 则查询全部。
                force (bool):       是否强制从数据库读取并更新缓存。

            Returns:
                符合条件的滑动窗口聚合记录列表（按 start_date 升序）
        """

        # 构建缓存 key
        cache_key = f"{type}:{code}:{trading_days}d"
    
        if not force and cache_key in self._sliding_cache:
            cached = self._sliding_cache[cache_key]
            if since is None:
                return cached
            return [c for c in cached if c.start_date >= since]

        sql = """SELECT * FROM money_flow_aggregation
                   WHERE code = ?
                     AND type = ?
                     AND trading_days = ? """
        params: List = [code, type, trading_days]

        if since:
            sql = sql + """ AND start_date >= ? """
            params.append(since)
        sql = sql + """ ORDER BY start_date """

        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = self._rows_to_aggs(rows)
            # 缓存全量
            self._sliding_cache[cache_key] = result
            return result

    def _rows_to_aggs(self, rows) -> List[MoneyFlowAggregation]:
        """将多行数据库记录转换为 MoneyFlowAggregation 列表"""
        result: List[MoneyFlowAggregation] = []
        for row in rows:
            agg = self._row_to_agg(row)
            if agg is not None:
                result.append(agg)
        return result

    def _row_to_agg(self, row: Optional[dict]) -> Optional[MoneyFlowAggregation]:
        """
            将数据库行记录转换为 MoneyFlowAggregation 实体。

            Args:
                row (dict): 数据库查询结果行

            Returns:
                MoneyFlowAggregation 对象，若 row 为 None 则返回 None
        """
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
