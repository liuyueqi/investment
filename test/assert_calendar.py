"""验证交易日历完整：与 Tushare 全量对比。

用法：
  python test/assert_calendar.py
  python test/assert_calendar.py -v
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from infra.container import container
from infra.database.connection import get_db
from test.report import Issue, ValidationReport, print_report


def _load_all_trading_days(conn) -> List[date]:
    rows = conn.execute(
        "SELECT trade_date FROM trading_days WHERE is_deleted = 0 ORDER BY trade_date"
    ).fetchall()
    return [datetime.strptime(row["trade_date"], "%Y-%m-%d").date() for row in rows]


def validate_trading_days(conn, report: ValidationReport) -> None:
    print("拉取 Tushare 交易日历...")
    adapter = container.tushare_adapter()
    try:
        remote_days = set(adapter.get_all_trading_days())
    except Exception as exc:
        report.add(Issue("market", "*", "trading_days", f"Tushare 拉取交易日历失败: {exc}"))
        return

    db_days = set(_load_all_trading_days(conn))
    only_remote = sorted(remote_days - db_days)
    only_db = sorted(db_days - remote_days)

    print(
        f"trading_days 一致性：Tushare {len(remote_days)} 天，DB {len(db_days)} 天，"
        f"仅远端 {len(only_remote)}，仅 DB {len(only_db)}"
    )
    if only_remote:
        report.add(Issue(
            "market", "*", "trading_days",
            f"DB 缺失 {len(only_remote)} 个交易日，示例: {only_remote[:10]}",
        ))
    if only_db:
        report.add(Issue(
            "market", "*", "trading_days",
            f"DB 多余 {len(only_db)} 个交易日，示例: {only_db[:10]}",
        ))


def main() -> int:
    parser = argparse.ArgumentParser(description="验证交易日历与外部接口全量一致")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出全部问题详情")
    args = parser.parse_args()

    report = ValidationReport()
    print("开始验证交易日历...")
    with get_db() as conn:
        validate_trading_days(conn, report)
    print_report(report, verbose=args.verbose)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
