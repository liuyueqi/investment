"""资金流聚合数据仓库，管理 money_flow_aggregation 表"""

from datetime import date, datetime
from typing import List, Optional

from domain.money_flow_aggregation import MoneyFlowAggregation
from infra.database.connection import get_db
from infra.log import logger


class MoneyFlowAggregationRepository:
    """资金流聚合数据仓库"""

    def save(self, agg: MoneyFlowAggregation) -> None:
        """保存一条聚合记录（UPSERT，幂等安全）"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            conn.execute(
                """INSERT INTO money_flow_aggregation (
                       entity_type, entity_code, trade_date, period,
                       cumulative_main_net, cumulative_main_cnt, cumulative_net_amount,
                       cumulative_huge_net, cumulative_huge_buy_net, cumulative_huge_sell_net,
                       cumulative_huge_cnt, cumulative_huge_buy_cnt, cumulative_huge_sell_cnt,
                       cumulative_large_net, cumulative_large_buy_net, cumulative_large_sell_net,
                       cumulative_large_cnt, cumulative_large_buy_cnt, cumulative_large_sell_cnt,
                       cumulative_medium_net, cumulative_medium_buy_net, cumulative_medium_sell_net,
                       cumulative_medium_cnt, cumulative_medium_buy_cnt, cumulative_medium_sell_cnt,
                       cumulative_small_net, cumulative_small_buy_net, cumulative_small_sell_net,
                       cumulative_small_cnt, cumulative_small_buy_cnt, cumulative_small_sell_cnt,
                       data_start_date, data_end_date, trading_days_count,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?, ?,
                             ?, ?)
                   ON CONFLICT(entity_type, entity_code, trade_date, period)
                   DO UPDATE SET
                       cumulative_main_net       = excluded.cumulative_main_net,
                       cumulative_main_cnt       = excluded.cumulative_main_cnt,
                       cumulative_net_amount     = excluded.cumulative_net_amount,
                       cumulative_huge_net       = excluded.cumulative_huge_net,
                       cumulative_huge_buy_net   = excluded.cumulative_huge_buy_net,
                       cumulative_huge_sell_net  = excluded.cumulative_huge_sell_net,
                       cumulative_huge_cnt       = excluded.cumulative_huge_cnt,
                       cumulative_huge_buy_cnt   = excluded.cumulative_huge_buy_cnt,
                       cumulative_huge_sell_cnt  = excluded.cumulative_huge_sell_cnt,
                       cumulative_large_net      = excluded.cumulative_large_net,
                       cumulative_large_buy_net  = excluded.cumulative_large_buy_net,
                       cumulative_large_sell_net = excluded.cumulative_large_sell_net,
                       cumulative_large_cnt      = excluded.cumulative_large_cnt,
                       cumulative_large_buy_cnt  = excluded.cumulative_large_buy_cnt,
                       cumulative_large_sell_cnt = excluded.cumulative_large_sell_cnt,
                       cumulative_medium_net     = excluded.cumulative_medium_net,
                       cumulative_medium_buy_net = excluded.cumulative_medium_buy_net,
                       cumulative_medium_sell_net= excluded.cumulative_medium_sell_net,
                       cumulative_medium_cnt     = excluded.cumulative_medium_cnt,
                       cumulative_medium_buy_cnt = excluded.cumulative_medium_buy_cnt,
                       cumulative_medium_sell_cnt= excluded.cumulative_medium_sell_cnt,
                       cumulative_small_net      = excluded.cumulative_small_net,
                       cumulative_small_buy_net  = excluded.cumulative_small_buy_net,
                       cumulative_small_sell_net = excluded.cumulative_small_sell_net,
                       cumulative_small_cnt      = excluded.cumulative_small_cnt,
                       cumulative_small_buy_cnt  = excluded.cumulative_small_buy_cnt,
                       cumulative_small_sell_cnt = excluded.cumulative_small_sell_cnt,
                       data_start_date           = excluded.data_start_date,
                       data_end_date             = excluded.data_end_date,
                       trading_days_count        = excluded.trading_days_count,
                       updated_at                = excluded.updated_at""",
                (
                    agg.entity_type, agg.entity_code,
                    agg.trade_date.isoformat() if agg.trade_date else None,
                    agg.period,
                    agg.cumulative_main_net, agg.cumulative_main_cnt,
                    agg.cumulative_net_amount,
                    agg.cumulative_huge_net, agg.cumulative_huge_buy_net,
                    agg.cumulative_huge_sell_net,
                    agg.cumulative_huge_cnt, agg.cumulative_huge_buy_cnt,
                    agg.cumulative_huge_sell_cnt,
                    agg.cumulative_large_net, agg.cumulative_large_buy_net,
                    agg.cumulative_large_sell_net,
                    agg.cumulative_large_cnt, agg.cumulative_large_buy_cnt,
                    agg.cumulative_large_sell_cnt,
                    agg.cumulative_medium_net, agg.cumulative_medium_buy_net,
                    agg.cumulative_medium_sell_net,
                    agg.cumulative_medium_cnt, agg.cumulative_medium_buy_cnt,
                    agg.cumulative_medium_sell_cnt,
                    agg.cumulative_small_net, agg.cumulative_small_buy_net,
                    agg.cumulative_small_sell_net,
                    agg.cumulative_small_cnt, agg.cumulative_small_buy_cnt,
                    agg.cumulative_small_sell_cnt,
                    agg.data_start_date.isoformat() if agg.data_start_date else None,
                    agg.data_end_date.isoformat() if agg.data_end_date else None,
                    agg.trading_days_count,
                    now, now,
                ),
            )

    def find_stock_aggregation(
        self, code: str, trade_date: date
    ) -> Optional[MoneyFlowAggregation]:
        """查询某只股票在指定日期的累计聚合数据"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT * FROM money_flow_aggregation
                   WHERE entity_type = 'stock'
                     AND entity_code = ?
                     AND trade_date = ?
                     AND period = 'day'""",
                (code, trade_date.isoformat()),
            ).fetchone()
            return self._row_to_agg(row) if row else None

    def find_sector_aggregation(
        self, code: str, trade_date: date
    ) -> Optional[MoneyFlowAggregation]:
        """查询某个板块在指定日期的累计聚合数据"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT * FROM money_flow_aggregation
                   WHERE entity_type = 'sector'
                     AND entity_code = ?
                     AND trade_date = ?
                     AND period = 'day'""",
                (code, trade_date.isoformat()),
            ).fetchone()
            return self._row_to_agg(row) if row else None

    def find_by_entity_type(
        self, entity_type: str, trade_date: Optional[date] = None,
        order_by: str = "cumulative_main_net", limit: int = 100
    ) -> List[MoneyFlowAggregation]:
        """按实体类型查询聚合数据，支持按累计净流入排序"""
        with get_db() as conn:
            if trade_date:
                rows = conn.execute(
                    f"""SELECT * FROM money_flow_aggregation
                       WHERE entity_type = ? AND trade_date = ?
                       ORDER BY ? DESC
                       LIMIT ?""",
                    (entity_type, trade_date.isoformat(), order_by, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT * FROM money_flow_aggregation
                       WHERE entity_type = ?
                       ORDER BY trade_date DESC, ? DESC
                       LIMIT ?""",
                    (entity_type, order_by, limit),
                ).fetchall()
            return [self._row_to_agg(r) for r in rows]

    def get_latest_trade_date(self, entity_type: str) -> Optional[date]:
        """查询某类实体的最新聚合日期"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT MAX(trade_date) AS max_date
                   FROM money_flow_aggregation
                   WHERE entity_type = ?""",
                (entity_type,),
            ).fetchone()
            if row and row["max_date"]:
                return datetime.strptime(row["max_date"], "%Y-%m-%d").date()
            return None

    def count_by_type(self, entity_type: str) -> int:
        """查询某类实体的聚合记录数"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS cnt FROM money_flow_aggregation
                   WHERE entity_type = ?""",
                (entity_type,),
            ).fetchone()
            return row["cnt"] if row else 0

    @staticmethod
    def _row_to_agg(row) -> Optional[MoneyFlowAggregation]:
        if row is None:
            return None

        def _opt_date(val):
            return datetime.strptime(val, "%Y-%m-%d").date() if val else None

        return MoneyFlowAggregation(
            entity_type=row["entity_type"],
            entity_code=row["entity_code"],
            trade_date=_opt_date(row["trade_date"]),
            period=row["period"],
            cumulative_main_net=row["cumulative_main_net"] or 0.0,
            cumulative_main_cnt=row["cumulative_main_cnt"] or 0,
            cumulative_net_amount=row["cumulative_net_amount"] or 0.0,
            cumulative_huge_net=row["cumulative_huge_net"],
            cumulative_huge_buy_net=row["cumulative_huge_buy_net"],
            cumulative_huge_sell_net=row["cumulative_huge_sell_net"],
            cumulative_huge_cnt=row["cumulative_huge_cnt"],
            cumulative_huge_buy_cnt=row["cumulative_huge_buy_cnt"],
            cumulative_huge_sell_cnt=row["cumulative_huge_sell_cnt"],
            cumulative_large_net=row["cumulative_large_net"],
            cumulative_large_buy_net=row["cumulative_large_buy_net"],
            cumulative_large_sell_net=row["cumulative_large_sell_net"],
            cumulative_large_cnt=row["cumulative_large_cnt"],
            cumulative_large_buy_cnt=row["cumulative_large_buy_cnt"],
            cumulative_large_sell_cnt=row["cumulative_large_sell_cnt"],
            cumulative_medium_net=row["cumulative_medium_net"],
            cumulative_medium_buy_net=row["cumulative_medium_buy_net"],
            cumulative_medium_sell_net=row["cumulative_medium_sell_net"],
            cumulative_medium_cnt=row["cumulative_medium_cnt"],
            cumulative_medium_buy_cnt=row["cumulative_medium_buy_cnt"],
            cumulative_medium_sell_cnt=row["cumulative_medium_sell_cnt"],
            cumulative_small_net=row["cumulative_small_net"],
            cumulative_small_buy_net=row["cumulative_small_buy_net"],
            cumulative_small_sell_net=row["cumulative_small_sell_net"],
            cumulative_small_cnt=row["cumulative_small_cnt"],
            cumulative_small_buy_cnt=row["cumulative_small_buy_cnt"],
            cumulative_small_sell_cnt=row["cumulative_small_sell_cnt"],
            data_start_date=_opt_date(row["data_start_date"]),
            data_end_date=_opt_date(row["data_end_date"]),
            trading_days_count=row["trading_days_count"] or 0,
        )
