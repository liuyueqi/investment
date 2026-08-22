"""验证个股资金流覆盖率（排除北交所，按交易日）。

用法：
  python test/assert_flow_coverage.py
  python test/assert_flow_coverage.py -v
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from infra.config import get_market_earliest_date
from infra.database.connection import get_db
from test.common import load_all_trading_days, validation_end_date
from test.report import Issue, ValidationReport, print_report

FLOW_COVERAGE_MIN_RATE = 0.95


def _load_flow_counts_by_date(
    conn, start_date: date, end_date: date,
) -> Dict[date, int]:
    rows = conn.execute(
        """SELECT f.trade_date, COUNT(DISTINCT f.code) AS cnt
           FROM dc_money_flows f
           JOIN stocks s ON s.code = f.code AND s.is_deleted = 0
           WHERE f.is_deleted = 0
             AND s.market != 'BJ'
             AND f.trade_date >= ?
             AND f.trade_date <= ?
           GROUP BY f.trade_date""",
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    return {
        datetime.strptime(row["trade_date"], "%Y-%m-%d").date(): int(row["cnt"])
        for row in rows
    }


def validate_flow_coverage(conn, report: ValidationReport) -> None:
    stock_count = conn.execute(
        """SELECT COUNT(*) AS cnt FROM stocks
           WHERE is_deleted = 0 AND market != 'BJ'"""
    ).fetchone()["cnt"]
    if stock_count <= 0:
        report.add(Issue(
            "market", "*", "flow_coverage",
            "stocks 表无有效沪深个股，无法校验有数据率",
        ))
        return

    validation_start = get_market_earliest_date()
    validation_end = validation_end_date()
    trading_days = load_all_trading_days(
        conn, start_date=validation_start, end_date=validation_end,
    )
    if not trading_days:
        report.add(Issue(
            "market", "*", "flow_coverage",
            f"trading_days 在 {validation_start} ~ {validation_end} 无数据，"
            "请先 download 交易日历",
        ))
        return

    start_date, end_date = trading_days[0], trading_days[-1]
    flow_counts = _load_flow_counts_by_date(conn, start_date, end_date)
    failed = 0
    min_rate = 1.0
    min_rate_date: Optional[date] = None

    for trade_date in trading_days:
        have = flow_counts.get(trade_date, 0)
        rate = have / stock_count
        if rate < min_rate:
            min_rate = rate
            min_rate_date = trade_date
        if rate < FLOW_COVERAGE_MIN_RATE:
            failed += 1
            report.add(Issue(
                "market", "*", "flow_coverage",
                f"{trade_date} 有数据率 {rate:.2%} < {FLOW_COVERAGE_MIN_RATE:.0%} "
                f"({have}/{stock_count})",
            ))

    print(
        f"flow 覆盖率检查（排除北交所，自 {validation_start}）："
        f"交易日 {len(trading_days)} 天（{start_date} ~ {end_date}），"
        f"股票池 {stock_count} 只，低于 {FLOW_COVERAGE_MIN_RATE:.0%} 的交易日 {failed} 天"
        + (f"；最低 {min_rate:.2%} @ {min_rate_date}" if min_rate_date else "")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="验证个股资金流按日覆盖率")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出全部问题详情")
    args = parser.parse_args()

    report = ValidationReport()
    print("开始验证资金流覆盖率...")
    with get_db() as conn:
        validate_flow_coverage(conn, report)
    print_report(report, verbose=args.verbose)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
