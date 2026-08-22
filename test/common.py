"""测试脚本共用：交易日加载与校验截止日。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional


def validation_end_date() -> date:
    """校验截止日：不含今天。"""
    return date.today() - timedelta(days=1)


def load_all_trading_days(
    conn,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[date]:
    sql = "SELECT trade_date FROM trading_days WHERE is_deleted = 0"
    params: List = []
    if start_date:
        sql += " AND trade_date >= ?"
        params.append(start_date.isoformat())
    if end_date:
        sql += " AND trade_date <= ?"
        params.append(end_date.isoformat())
    sql += " ORDER BY trade_date"
    rows = conn.execute(sql, params).fetchall()
    return [datetime.strptime(row["trade_date"], "%Y-%m-%d").date() for row in rows]
