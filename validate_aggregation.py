"""验证 money_flow_aggregation 数据的完整性与正确性

检查项：
  1. 个股 accumulation / sliding：与 money_flows 直接计算结果比对
  2. 板块 accumulation：按「历史快照」模型，end_date 当日已纳入板块的成员 stock accumulation 之和
  3. 板块 sliding：按记录写入日的成分股快照，对窗口内各交易日 flow 求和（与 incremental 写入时一致）

板块成分股有效性暂以 sector_members.created_at 近似（精确验证需成分股变动历史表）。

用法：
  python validate_aggregation.py                  # 检查全部
  python validate_aggregation.py --scope stock    # 仅检查个股
  python validate_aggregation.py --code 000001    # 检查指定代码
  python validate_aggregation.py --limit 50       # 抽样检查（各类各取前 N 个）
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from infra.database.connection import get_db
from domain.money_flow_aggregation_repository import MoneyFlowAggregationRepository
from domain.money_flow_aggregation import MoneyFlowAggregation, AggregationType

WINDOWS = (3, 5, 10, 20)
TOLERANCE = 0.01  # 万元，允许浮点误差

_agg_repo = MoneyFlowAggregationRepository()

FlowRow = Tuple[date, float, int]  # trade_date, main_net, main_cnt
AggRow = Tuple[date, date, int, float, int]  # start_date, end_date, trading_days, main_net, main_cnt
SectorAccRow = Tuple[date, date, int, float, int, date]  # + snapshot_date（记录写入日）
SectorSlideRow = SectorAccRow
MemberRecord = Tuple[str, date]  # stock_code, joined_date（以 created_at 日期近似）


@dataclass
class Issue:
    entity_type: str  # stock / sector
    code: str
    category: str  # accumulation / sliding
    message: str

    def __str__(self) -> str:
        return f"[{self.entity_type}:{self.code}] {self.category}: {self.message}"


@dataclass
class ValidationReport:
    issues: List[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)

    def merge(self, other: ValidationReport) -> None:
        self.issues.extend(other.issues)


def _parse_flow_date(value: str) -> date:
    """解析 money_flows.trade_date（格式固定为 YYYY-MM-DD）"""
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _agg_to_row(agg: MoneyFlowAggregation) -> AggRow:
    return (agg.start_date, agg.end_date, agg.trading_days, agg.main_net, agg.main_cnt)


def _sector_acc_to_row(row: dict, agg: MoneyFlowAggregation) -> SectorAccRow:
    snapshot = _parse_member_joined_at(row["created_at"])
    return (*_agg_to_row(agg), snapshot)


def _sector_slide_to_row(row: dict, agg: MoneyFlowAggregation) -> SectorSlideRow:
    snapshot = _parse_member_joined_at(row["created_at"])
    return (*_agg_to_row(agg), snapshot)


def _parse_member_joined_at(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _member_active_on(joined_date: date, on_date: date) -> bool:
    """成分股在 on_date 是否已纳入板块（以 created_at 日期近似）"""
    return joined_date <= on_date


def _members_at_snapshot(member_records: Sequence[MemberRecord], snapshot_date: date) -> List[str]:
    """记录写入日已知已纳入板块的成员（用于 accumulation 历史快照验证）"""
    return [code for code, joined in member_records if _member_active_on(joined, snapshot_date)]


def _merge_member_agg(existing: AggRow, other: AggRow) -> AggRow:
    """与 aggregator merge 行为一致：保留首个 trading_days，累加数值"""
    return (
        min(existing[0], other[0]),
        max(existing[1], other[1]),
        existing[2],
        existing[3] + other[3],
        existing[4] + other[4],
    )


def _close(a: float, b: float, tol: float = TOLERANCE) -> bool:
    return abs(a - b) <= tol


def _load_stocks(conn) -> List[str]:
    rows = conn.execute(
        "SELECT code FROM stocks WHERE is_deleted = 0 ORDER BY code"
    ).fetchall()
    return [row["code"] for row in rows]


def _load_sectors(conn) -> Dict[str, List[MemberRecord]]:
    """加载板块及成分股（含 created_at 日期，用于历史快照验证）"""
    sectors: Dict[str, List[MemberRecord]] = {}
    sector_rows = conn.execute(
        "SELECT code FROM sectors WHERE is_deleted = 0 ORDER BY code"
    ).fetchall()
    for row in sector_rows:
        code = row["code"]
        members = conn.execute(
            """SELECT stock_code, created_at FROM sector_members
               WHERE sector_code = ? AND is_deleted = 0
               ORDER BY stock_code""",
            (code,),
        ).fetchall()
        sectors[code] = [
            (m["stock_code"], _parse_member_joined_at(m["created_at"]))
            for m in members
        ]
    return sectors


def _load_flows(conn, codes: Optional[Iterable[str]] = None) -> Dict[str, List[FlowRow]]:
    sql = """SELECT code, trade_date, main_net, main_cnt
             FROM money_flows
             WHERE period = 'day' AND is_deleted = 0"""
    params: List = []
    if codes is not None:
        code_list = list(codes)
        if not code_list:
            return {}
        placeholders = ",".join("?" * len(code_list))
        sql += f" AND code IN ({placeholders})"
        params.extend(code_list)
    sql += " ORDER BY code, trade_date"

    flows: Dict[str, List[FlowRow]] = defaultdict(list)
    for row in conn.execute(sql, params).fetchall():
        flows[row["code"]].append(
            (
                _parse_flow_date(row["trade_date"]),
                row["main_net"] or 0.0,
                row["main_cnt"] or 0,
            )
        )
    return flows


def _load_aggregations(
    conn,
    codes: Optional[Iterable[str]],
    report: ValidationReport,
) -> Dict[str, Dict[str, List[AggRow]]]:
    """通过 MoneyFlowAggregationRepository._row_to_agg 加载聚合数据"""
    sql = """SELECT * FROM money_flow_aggregation"""
    params: List = []
    if codes is not None:
        code_list = list(codes)
        if not code_list:
            return {}
        placeholders = ",".join("?" * len(code_list))
        sql += f" WHERE code IN ({placeholders})"
        params.extend(code_list)
    sql += " ORDER BY code, is_accumulative DESC, trading_days, start_date"

    result: Dict[str, Dict] = defaultdict(
        lambda: {"accumulation": [], "sliding": {w: [] for w in WINDOWS}}
    )
    for row in conn.execute(sql, params).fetchall():
        code = row["code"]
        entity_type = "stock" if row["type"] == AggregationType.STOCK.value else "sector"
        category = "accumulation" if row["is_accumulative"] else f"sliding({row['trading_days']}日)"

        try:
            agg_obj = _agg_repo._row_to_agg(row)
        except ValueError as exc:
            report.add(Issue(
                entity_type, code, category,
                f"_row_to_agg 无法解析: start={row['start_date']} end={row['end_date']} ({exc})",
            ))
            continue

        if agg_obj is None:
            continue

        if row["is_accumulative"]:
            if row["type"] == AggregationType.SECTOR.value:
                result[code]["accumulation"].append(_sector_acc_to_row(row, agg_obj))
            else:
                result[code]["accumulation"].append(_agg_to_row(agg_obj))
        else:
            window = row["trading_days"]
            if window in result[code]["sliding"]:
                if row["type"] == AggregationType.SECTOR.value:
                    result[code]["sliding"][window].append(_sector_slide_to_row(row, agg_obj))
                else:
                    result[code]["sliding"][window].append(_agg_to_row(agg_obj))
    return result


def _expected_stock_accumulation(flows: Sequence[FlowRow]) -> List[AggRow]:
    if not flows:
        return []

    expected: List[AggRow] = []
    start_date = flows[0][0]
    cum_net = 0.0
    cum_cnt = 0
    for i, (trade_date, main_net, main_cnt) in enumerate(flows):
        cum_net += main_net
        cum_cnt += main_cnt
        expected.append((start_date, trade_date, i + 1, cum_net, cum_cnt))
    return expected


def _expected_stock_sliding(flows: Sequence[FlowRow], window: int) -> List[AggRow]:
    if len(flows) < window:
        return []

    expected: List[AggRow] = []
    for i in range(len(flows) - window + 1):
        chunk = flows[i : i + window]
        expected.append(
            (
                chunk[0][0],
                chunk[-1][0],
                window,
                sum(x[1] for x in chunk),
                sum(x[2] for x in chunk),
            )
        )
    return expected


def _expected_sector_accumulation_row(
    member_records: Sequence[MemberRecord],
    stock_accumulations: Dict[str, List[AggRow]],
    end_date: date,
    snapshot_date: date,
) -> Optional[AggRow]:
    """按记录写入日的成分股快照，计算某日板块 accumulation 期望值"""
    member_aggs: List[AggRow] = []
    for stock_code in _members_at_snapshot(member_records, snapshot_date):
        for agg in stock_accumulations.get(stock_code, []):
            if agg[1] == end_date:
                member_aggs.append(agg)
                break

    if not member_aggs:
        return None

    merged = member_aggs[0]
    for agg in member_aggs[1:]:
        merged = _merge_member_agg(merged, agg)
    return merged


def _validate_sector_accumulation(
    code: str,
    member_records: Sequence[MemberRecord],
    stock_accumulations: Dict[str, List[AggRow]],
    actual_rows: Sequence[SectorAccRow],
    report: ValidationReport,
) -> None:
    """逐条验证板块 accumulation（按每条记录的 created_at 确定有效成分股）"""
    for row in actual_rows:
        start_date, end_date, trading_days, main_net, main_cnt, snapshot_date = row
        expected = _expected_sector_accumulation_row(
            member_records, stock_accumulations, end_date, snapshot_date,
        )
        if expected is None:
            report.add(Issue(
                "sector", code, "accumulation",
                f"写入日 {snapshot_date} 无有效成分股数据 (end={end_date})",
            ))
            continue

        if trading_days != expected[2]:
            report.add(Issue(
                "sector", code, "accumulation",
                f"trading_days 错误 {trading_days} != {expected[2]} (end={end_date})",
            ))
        if not _close(main_net, expected[3]):
            report.add(Issue(
                "sector", code, "accumulation",
                f"main_net 错误 {main_net:.4f} != {expected[3]:.4f} "
                f"(end={end_date}, snapshot={snapshot_date})",
            ))
        if main_cnt != expected[4]:
            report.add(Issue(
                "sector", code, "accumulation",
                f"main_cnt 错误 {main_cnt} != {expected[4]} "
                f"(end={end_date}, snapshot={snapshot_date})",
            ))


def _expected_sector_sliding(
    member_records: Sequence[MemberRecord],
    flows_by_code: Dict[str, List[FlowRow]],
    window: int,
    snapshot_date: date,
) -> List[AggRow]:
    """按写入日成分股快照，在交易日并集上滑动（与 aggregator 写入时成员集合一致）"""
    member_codes = _members_at_snapshot(member_records, snapshot_date)
    flow_lookup: Dict[Tuple[str, date], FlowRow] = {}
    trading_dates: set[date] = set()
    for stock_code in member_codes:
        for trade_date, main_net, main_cnt in flows_by_code.get(stock_code, []):
            trading_dates.add(trade_date)
            flow_lookup[(stock_code, trade_date)] = (trade_date, main_net, main_cnt)

    dates = sorted(trading_dates)
    if len(dates) < window:
        return []

    expected: List[AggRow] = []
    for i in range(len(dates) - window + 1):
        window_dates = dates[i : i + window]
        total_net = 0.0
        total_cnt = 0
        for d in window_dates:
            for stock_code in member_codes:
                flow = flow_lookup.get((stock_code, d))
                if flow:
                    total_net += flow[1]
                    total_cnt += flow[2]
        expected.append((
            window_dates[0],
            window_dates[-1],
            window,
            total_net,
            total_cnt,
        ))
    return expected


def _validate_sector_sliding(
    code: str,
    member_records: Sequence[MemberRecord],
    flows_by_code: Dict[str, List[FlowRow]],
    actual_rows: Sequence[SectorSlideRow],
    window: int,
    report: ValidationReport,
) -> None:
    """逐条验证板块 sliding（按每条记录的 created_at 确定有效成分股）"""
    label = f"sliding({window}日)"
    by_snapshot: Dict[date, List[SectorSlideRow]] = defaultdict(list)
    for row in actual_rows:
        by_snapshot[row[5]].append(row)

    for snapshot_date, rows in by_snapshot.items():
        expected_map = {
            (row[0], row[1]): row
            for row in _expected_sector_sliding(
                member_records, flows_by_code, window, snapshot_date,
            )
        }
        for row in rows:
            start_date, end_date, trading_days, main_net, main_cnt, _ = row
            expected = expected_map.get((start_date, end_date))
            if expected is None:
                report.add(Issue(
                    "sector", code, label,
                    f"写入日 {snapshot_date} 无法复现窗口 "
                    f"(start={start_date}, end={end_date})",
                ))
                continue

            if trading_days != expected[2]:
                report.add(Issue(
                    "sector", code, label,
                    f"trading_days 错误 {trading_days} != {expected[2]} "
                    f"(start={start_date}, end={end_date})",
                ))
            if not _close(main_net, expected[3]):
                report.add(Issue(
                    "sector", code, label,
                    f"main_net 错误 {main_net:.4f} != {expected[3]:.4f} "
                    f"(start={start_date}, end={end_date}, snapshot={snapshot_date})",
                ))
            if main_cnt != expected[4]:
                report.add(Issue(
                    "sector", code, label,
                    f"main_cnt 错误 {main_cnt} != {expected[4]} "
                    f"(start={start_date}, end={end_date}, snapshot={snapshot_date})",
                ))


def _compare_agg_series(
    entity_type: str,
    code: str,
    category: str,
    expected: Sequence[AggRow],
    actual: Sequence[AggRow],
    report: ValidationReport,
    window: Optional[int] = None,
) -> None:
    label = f"{category}" + (f"({window}日)" if window else "")

    if len(expected) != len(actual):
        report.add(Issue(
            entity_type, code, label,
            f"记录数不一致：期望 {len(expected)} 条，实际 {len(actual)} 条",
        ))

    exp_map = {(a[0], a[1]): a for a in expected}
    act_map = {(a[0], a[1]): a for a in actual}

    missing = set(exp_map) - set(act_map)
    extra = set(act_map) - set(exp_map)
    if missing:
        sample = sorted(missing)[:3]
        report.add(Issue(
            entity_type, code, label,
            f"缺少 {len(missing)} 条记录，示例 start/end: {sample}",
        ))
    if extra:
        sample = sorted(extra)[:3]
        report.add(Issue(
            entity_type, code, label,
            f"多余 {len(extra)} 条记录，示例 start/end: {sample}",
        ))

    for key in sorted(set(exp_map) & set(act_map)):
        exp = exp_map[key]
        act = act_map[key]
        if exp[2] != act[2]:
            report.add(Issue(
                entity_type, code, label,
                f"trading_days 错误 {act[2]} != {exp[2]} "
                f"(start={key[0]}, end={key[1]})",
            ))
        if not _close(exp[3], act[3]):
            report.add(Issue(
                entity_type, code, label,
                f"main_net 错误 {act[3]:.4f} != {exp[3]:.4f} "
                f"(start={key[0]}, end={key[1]})",
            ))
        if exp[4] != act[4]:
            report.add(Issue(
                entity_type, code, label,
                f"main_cnt 错误 {act[4]} != {exp[4]} "
                f"(start={key[0]}, end={key[1]})",
            ))


def validate_stock(
    code: str,
    flows: List[FlowRow],
    aggs: Dict,
    report: ValidationReport,
) -> None:
    if not flows:
        if aggs["accumulation"] or any(aggs["sliding"][w] for w in WINDOWS):
            report.add(Issue("stock", code, "accumulation", "无原始 flow 但存在聚合数据"))
        return

    expected_acc = _expected_stock_accumulation(flows)
    _compare_agg_series("stock", code, "accumulation", expected_acc, aggs["accumulation"], report)

    for window in WINDOWS:
        expected_slide = _expected_stock_sliding(flows, window)
        _compare_agg_series(
            "stock", code, "sliding", expected_slide,
            aggs["sliding"][window], report, window=window,
        )


def validate_sector(
    code: str,
    member_records: List[MemberRecord],
    flows_by_code: Dict[str, List[FlowRow]],
    stock_aggs: Dict[str, Dict],
    sector_aggs: Dict,
    report: ValidationReport,
) -> None:
    if not member_records:
        return

    member_codes = [m[0] for m in member_records]
    member_flows_exist = any(flows_by_code.get(m) for m in member_codes)
    if not member_flows_exist:
        if sector_aggs["accumulation"] or any(sector_aggs["sliding"][w] for w in WINDOWS):
            report.add(Issue("sector", code, "accumulation", "成分股无 flow 但存在聚合数据"))
        return

    stock_accumulations = {
        m: stock_aggs.get(m, {}).get("accumulation", [])
        for m in member_codes
    }
    _validate_sector_accumulation(
        code, member_records, stock_accumulations,
        sector_aggs["accumulation"], report,
    )

    for window in WINDOWS:
        _validate_sector_sliding(
            code, member_records, flows_by_code,
            sector_aggs["sliding"][window], window, report,
        )


def run_validation(
    scope: str = "all",
    codes: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> ValidationReport:
    report = ValidationReport()

    with get_db() as conn:
        all_stocks = _load_stocks(conn)
        all_sectors = _load_sectors(conn)

        if codes:
            stock_codes = [c for c in codes if c in all_stocks or c[0].isdigit()]
            sector_codes = [c for c in codes if c in all_sectors or not c[0].isdigit()]
        else:
            stock_codes = all_stocks
            sector_codes = list(all_sectors)

        if limit is not None:
            stock_codes = stock_codes[:limit]
            sector_codes = sector_codes[:limit]

        member_codes: set[str] = set()
        if scope in ("all", "sector"):
            member_codes = {
                m[0] for sc in sector_codes for m in all_sectors.get(sc, [])
            }

        relevant_flow_codes: set[str] = set()
        if scope in ("all", "stock"):
            relevant_flow_codes |= set(stock_codes)
        if scope in ("all", "sector"):
            relevant_flow_codes |= member_codes
        flows_by_code = _load_flows(conn, relevant_flow_codes)

        agg_codes: set[str] = set()
        if scope in ("all", "stock"):
            agg_codes |= set(stock_codes)
        if scope in ("all", "sector"):
            agg_codes |= set(sector_codes) | member_codes
        aggregations = _load_aggregations(conn, agg_codes, report)

    if scope in ("all", "stock"):
        for code in stock_codes:
            validate_stock(
                code,
                flows_by_code.get(code, []),
                aggregations.get(code, {"accumulation": [], "sliding": {w: [] for w in WINDOWS}}),
                report,
            )

    if scope in ("all", "sector"):
        for code in sector_codes:
            validate_sector(
                code,
                all_sectors.get(code, []),
                flows_by_code,
                aggregations,
                aggregations.get(code, {"accumulation": [], "sliding": {w: [] for w in WINDOWS}}),
                report,
            )

    return report


def _print_report(report: ValidationReport, verbose: bool) -> None:
    if report.ok:
        print("✅ 全部检查通过")
        return

    print(f"❌ 发现 {len(report.issues)} 个问题\n")

    by_category: Dict[str, int] = defaultdict(int)
    for issue in report.issues:
        by_category[f"{issue.entity_type}/{issue.category}"] += 1

    print("问题汇总：")
    for key in sorted(by_category):
        print(f"  {key}: {by_category[key]}")
    print()

    if verbose:
        for issue in report.issues:
            print(issue)
    else:
        print("加 --verbose 查看全部详情。前 20 条：")
        for issue in report.issues[:20]:
            print(issue)


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 aggregation 数据完整性与正确性")
    parser.add_argument(
        "--scope", choices=["all", "stock", "sector"], default="all",
        help="检查范围（默认 all）",
    )
    parser.add_argument(
        "--code", nargs="+", metavar="CODE",
        help="指定股票 / 板块代码",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N",
        help="每类最多检查 N 个实体（用于快速抽样）",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="输出全部问题详情",
    )
    args = parser.parse_args()

    print("开始验证 aggregation 数据...")
    report = run_validation(scope=args.scope, codes=args.code, limit=args.limit)
    _print_report(report, verbose=args.verbose)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
