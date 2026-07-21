"""资金流聚合数据仓库，管理 money_flow_aggregation 表"""

from datetime import date, datetime
from typing import List, Optional

from domain.money_flow_aggregation import MoneyFlowAggregation, AggregationType
from infra.database.connection import get_db
from typing_extensions import deprecated
from infra.log import logger


class MoneyFlowAggregationRepository:
    """资金流聚合数据仓库"""

    def save(self, agg: MoneyFlowAggregation) -> None:
        """
            保存一条聚合记录（UPSERT，幂等安全）。
            若相同 (code, type, start_date, end_date) 的记录已存在，则先删除再插入，
            实现幂等更新。

            Args:
                agg (MoneyFlowAggregation): 要保存的聚合数据对象
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:

            existing = conn.execute(
                """SELECT 1 FROM money_flow_aggregation
                   WHERE code = ? AND type = ? AND start_date = ? AND end_date = ?""",
                (agg.code, agg.type, agg.start_date, agg.end_date),
            ).fetchone()

            if existing:
                conn.execute(
                    """DELETE FROM money_flow_aggregation
                       WHERE code = ? AND type = ? AND start_date = ? AND end_date = ?""",
                    (agg.code, agg.type, agg.start_date, agg.end_date),
                )

            conn.execute(
                """INSERT INTO money_flow_aggregation (
                       code, type, start_date, end_date, trading_days, is_accumulative,
                       main_net, main_cnt, net_amount,
                       huge_net, huge_buy_net, huge_sell_net,
                       huge_cnt, huge_buy_cnt, huge_sell_cnt,
                       large_net, large_buy_net, large_sell_net,
                       large_cnt, large_buy_cnt, large_sell_cnt,
                       medium_net, medium_buy_net, medium_sell_net,
                       medium_cnt, medium_buy_cnt, medium_sell_cnt,
                       small_net, small_buy_net, small_sell_net,
                       small_cnt, small_buy_cnt, small_sell_cnt,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?)""",
                (
                    agg.code, agg.type,
                    agg.start_date.isoformat(),
                    agg.end_date.isoformat(),
                    agg.trading_days,
                    int(agg.accumulative),
                    agg.main_net, agg.main_cnt, agg.net_amount,
                    agg.huge_net, agg.huge_buy_net, agg.huge_sell_net,
                    agg.huge_cnt, agg.huge_buy_cnt, agg.huge_sell_cnt,
                    agg.large_net, agg.large_buy_net, agg.large_sell_net,
                    agg.large_cnt, agg.large_buy_cnt, agg.large_sell_cnt,
                    agg.medium_net, agg.medium_buy_net, agg.medium_sell_net,
                    agg.medium_cnt, agg.medium_buy_cnt, agg.medium_sell_cnt,
                    agg.small_net, agg.small_buy_net, agg.small_sell_net,
                    agg.small_cnt, agg.small_buy_cnt, agg.small_sell_cnt,
                    now, now,
                ),
            )

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
    ) -> List[MoneyFlowAggregation]:
        """
            查询指定标的从 since 日期开始的资金总量记录。
            用于板块聚合时获取成分股的累计数据。

            Args:
                code (str):        股票代码或板块代码
                type (str):        实体类型，AggregationType.STOCK / AggregationType.SECTOR
                since (date):      起始日期（含）。
                                   例如 since=2026-07-01，则返回 end_date >= 2026-07-01 的累计记录。

            Returns:
                符合条件的累计聚合记录列表（按 trading_days 升序）
        """

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
            
            aggs = []
            for row in rows:
                aggs.append(self._row_to_agg(row))
            return aggs

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
    ) -> List[MoneyFlowAggregation]:
        """
            查询指定标的和窗口天数的滑动窗口聚合记录。
            用于板块聚合时获取成分股的滑动窗口数据。

            Args:
                code (str):         股票代码或板块代码
                type (str):         实体类型，AggregationType.STOCK / AggregationType.SECTOR
                trading_days (int): 窗口天数，如 3、5、10、20
                since (date):       起始日期（含）。若为 None 则查询全部。

            Returns:
                符合条件的滑动窗口聚合记录列表（按 start_date 升序）
        """

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

            aggs = []
            for row in rows:
                aggs.append(self._row_to_agg(row))
            return aggs

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
            net_amount=row["net_amount"] or 0.0,
            huge_net=row["huge_net"],
            huge_buy_net=row["huge_buy_net"],
            huge_sell_net=row["huge_sell_net"],
            huge_cnt=row["huge_cnt"],
            huge_buy_cnt=row["huge_buy_cnt"],
            huge_sell_cnt=row["huge_sell_cnt"],
            large_net=row["large_net"],
            large_buy_net=row["large_buy_net"],
            large_sell_net=row["large_sell_net"],
            large_cnt=row["large_cnt"],
            large_buy_cnt=row["large_buy_cnt"],
            large_sell_cnt=row["large_sell_cnt"],
            medium_net=row["medium_net"],
            medium_buy_net=row["medium_buy_net"],
            medium_sell_net=row["medium_sell_net"],
            medium_cnt=row["medium_cnt"],
            medium_buy_cnt=row["medium_buy_cnt"],
            medium_sell_cnt=row["medium_sell_cnt"],
            small_net=row["small_net"],
            small_buy_net=row["small_buy_net"],
            small_sell_net=row["small_sell_net"],
            small_cnt=row["small_cnt"],
            small_buy_cnt=row["small_buy_cnt"],
            small_sell_cnt=row["small_sell_cnt"],
        )
