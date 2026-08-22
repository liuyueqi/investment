"""验证股票列表完整：与 Tushare 全量对比。

用法：
  python test/assert_stocks.py
  python test/assert_stocks.py -v
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from domain.stock import Stock
from test.report import Issue, ValidationReport, print_report
from infra.container import container
from infra.database.connection import get_db


def _load_db_stocks(conn) -> Dict[str, Stock]:
    rows = conn.execute(
        """SELECT code, name, market FROM stocks
           WHERE is_deleted = 0 ORDER BY code"""
    ).fetchall()
    return {
        row["code"]: Stock(code=row["code"], name=row["name"], market=row["market"])
        for row in rows
    }


def validate_stocks(conn, report: ValidationReport) -> None:
    print("拉取 Tushare 股票列表...")
    adapter = container.tushare_adapter()
    try:
        remote_stocks = {s.code: s for s in adapter.get_all_stocks()}
    except Exception as exc:
        report.add(Issue("market", "*", "stocks", f"Tushare 拉取股票列表失败: {exc}"))
        return

    db_stocks = _load_db_stocks(conn)
    only_remote = sorted(set(remote_stocks) - set(db_stocks))
    only_db = sorted(set(db_stocks) - set(remote_stocks))
    both = set(remote_stocks) & set(db_stocks)

    mismatch = 0
    for code in sorted(both):
        r, d = remote_stocks[code], db_stocks[code]
        diffs = []
        if (r.name or "") != (d.name or ""):
            diffs.append(f"name {d.name!r}->{r.name!r}")
        if (r.market or "") != (d.market or ""):
            diffs.append(f"market {d.market!r}->{r.market!r}")
        if diffs:
            mismatch += 1
            if mismatch <= 20:
                report.add(Issue(
                    "stock", code, "stocks",
                    "字段不一致: " + ", ".join(diffs),
                ))

    print(
        f"stocks 一致性：Tushare {len(remote_stocks)} 只，DB {len(db_stocks)} 只，"
        f"仅远端 {len(only_remote)}，仅 DB {len(only_db)}，字段不一致 {mismatch}"
    )
    if only_remote:
        report.add(Issue(
            "market", "*", "stocks",
            f"DB 缺失 {len(only_remote)} 只股票，示例: {only_remote[:10]}",
        ))
    if only_db:
        report.add(Issue(
            "market", "*", "stocks",
            f"DB 多余 {len(only_db)} 只股票，示例: {only_db[:10]}",
        ))
    if mismatch > 20:
        report.add(Issue(
            "market", "*", "stocks",
            f"另有 {mismatch - 20} 只股票字段不一致未逐条列出",
        ))


def main() -> int:
    parser = argparse.ArgumentParser(description="验证股票列表与外部接口全量一致")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出全部问题详情")
    args = parser.parse_args()

    report = ValidationReport()
    print("开始验证股票列表...")
    with get_db() as conn:
        validate_stocks(conn, report)
    print_report(report, verbose=args.verbose)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
