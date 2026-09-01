"""验证资金流聚合正确率（个股默认抽样 500，板块默认抽样 100）。

检查 accumulation / sliding(3/5/10/20)：齐全连续且数值正确。

用法：
  python test/assert_flow_aggregation.py              # 先 stock 再 sector
  python test/assert_flow_aggregation.py stock
  python test/assert_flow_aggregation.py sector
  python test/assert_flow_aggregation.py stock --code 000001
  python test/assert_flow_aggregation.py stock --stock-sample 500 --seed 42 -v
  python test/assert_flow_aggregation.py sector --sector-sample 100 --seed 42 -v
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from domain.money_flow_aggregation import AggregationType
from domain.money_flow_aggregation_repository import MoneyFlowAggregationRepository
from domain.ts_code_util import code_from_ts_code
from infra.config import get_market_earliest_date
from infra.database.connection import get_db
from test.common import load_all_trading_days, validation_end_date
from test.report import Issue, ValidationReport, print_report

WINDOWS = (3, 5, 10, 20)
TOLERANCE = 0.01  # 万元

_agg_repo = MoneyFlowAggregationRepository()

FlowRow = Tuple[date, float, int]  # trade_date, main_net, main_cnt
AggRow = Tuple[date, date, int, float, int]  # start, end, trading_days, main_net, main_cnt


def _close(a: float, b: float, tol: float = TOLERANCE) -> bool:
    return abs(a - b) <= tol


def _parse_flow_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _load_flows(
    conn, codes: Optional[Iterable[str]] = None,
) -> Dict[str, List[FlowRow]]:
    sql = """SELECT code, trade_date, net_amount
             FROM dc_money_flows
             WHERE is_deleted = 0"""
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
            (_parse_flow_date(row["trade_date"]), row["net_amount"] or 0.0, 0)
        )
    return flows


def _load_aggregations(
    conn,
    codes: Optional[Iterable[str]],
    report: ValidationReport,
) -> Dict[str, Dict]:
    sql = "SELECT * FROM money_flow_aggregation"
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
        category = (
            "accumulation" if row["is_accumulative"]
            else f"sliding({row['trading_days']}日)"
        )
        try:
            agg_obj = _agg_repo._row_to_agg(row)
        except ValueError as exc:
            report.add(Issue(
                entity_type, code, category,
                f"_row_to_agg 无法解析: start={row['start_date']} end={row['end_date']} ({exc})",
            ))
            continue
        if not agg_obj:
            continue
        agg_row = (
            agg_obj.start_date,
            agg_obj.end_date,
            agg_obj.trading_days,
            agg_obj.main_net,
            agg_obj.main_cnt,
        )
        if row["is_accumulative"]:
            result[code]["accumulation"].append(agg_row)
        else:
            window = row["trading_days"]
            if window in result[code]["sliding"]:
                result[code]["sliding"][window].append(agg_row)
    return result


def _load_agg_end_dates(
    conn, codes: Sequence[str], agg_type: AggregationType,
) -> Dict[str, Dict]:
    result: Dict[str, Dict] = {
        code: {"accumulation": set(), "sliding": {w: set() for w in WINDOWS}}
        for code in codes
    }
    if not codes:
        return result
    placeholders = ",".join("?" * len(codes))
    rows = conn.execute(
        f"""SELECT code, end_date, trading_days, is_accumulative
            FROM money_flow_aggregation
            WHERE type = ? AND code IN ({placeholders})""",
        [agg_type.value, *codes],
    ).fetchall()
    for row in rows:
        code = row["code"]
        if code not in result:
            continue
        end = datetime.strptime(row["end_date"][:10], "%Y-%m-%d").date()
        if row["is_accumulative"]:
            result[code]["accumulation"].add(end)
        else:
            window = int(row["trading_days"])
            if window in result[code]["sliding"]:
                result[code]["sliding"][window].add(end)
    return result


def _expected_accumulation(flows: Sequence[FlowRow]) -> List[AggRow]:
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


def _expected_sliding(flows: Sequence[FlowRow], window: int) -> List[AggRow]:
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
        report.add(Issue(
            entity_type, code, label,
            f"缺少 {len(missing)} 条记录，示例 start/end: {sorted(missing)[:3]}",
        ))
    if extra:
        report.add(Issue(
            entity_type, code, label,
            f"多余 {len(extra)} 条记录，示例 start/end: {sorted(extra)[:3]}",
        ))

    for key in sorted(set(exp_map) & set(act_map)):
        exp, act = exp_map[key], act_map[key]
        if exp[2] != act[2]:
            report.add(Issue(
                entity_type, code, label,
                f"trading_days 错误 {act[2]} != {exp[2]} (start={key[0]}, end={key[1]})",
            ))
        if not _close(exp[3], act[3]):
            report.add(Issue(
                entity_type, code, label,
                f"main_net 错误 {act[3]:.4f} != {exp[3]:.4f} (start={key[0]}, end={key[1]})",
            ))
        if exp[4] != act[4]:
            report.add(Issue(
                entity_type, code, label,
                f"main_cnt 错误 {act[4]} != {exp[4]} (start={key[0]}, end={key[1]})",
            ))


def _report_date_gaps(
    report: ValidationReport,
    entity_type: str,
    code: str,
    category: str,
    expected: Sequence[date],
    actual: Set[date],
    sample_limit: int = 5,
) -> int:
    expected_set = set(expected)
    missing = sorted(expected_set - actual)
    extra = sorted(actual - expected_set)
    if missing:
        report.add(Issue(
            entity_type, code, category,
            f"缺失 {len(missing)} 天，示例: {missing[:sample_limit]}",
        ))
    if extra:
        report.add(Issue(
            entity_type, code, category,
            f"多余 {len(extra)} 天，示例: {extra[:sample_limit]}",
        ))
    return len(missing)


# ── stock ───────────────────────────────────────────────────────────────────

def _load_stock_codes(conn, exclude_bj: bool = True) -> List[str]:
    if exclude_bj:
        rows = conn.execute(
            """SELECT code FROM stocks
               WHERE is_deleted = 0 AND market != 'BJ'
               ORDER BY code"""
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT code FROM stocks WHERE is_deleted = 0 ORDER BY code"
        ).fetchall()
    return [row["code"] for row in rows]


def _check_stock_completeness(
    conn,
    report: ValidationReport,
    stock_codes: List[str],
    validation_start: date,
    validation_end: date,
) -> None:
    flows_by_code = _load_flows(conn, stock_codes)
    aggs = _load_agg_end_dates(conn, stock_codes, AggregationType.STOCK)
    no_flow = 0
    missing_acc = 0
    incomplete_acc = 0
    incomplete_slide = 0

    for code in stock_codes:
        flow_dates = sorted(
            d for d, _, _ in flows_by_code.get(code, [])
            if validation_start <= d <= validation_end
        )
        data = aggs.get(code) or {
            "accumulation": set(),
            "sliding": {w: set() for w in WINDOWS},
        }
        acc_dates: Set[date] = {
            d for d in data["accumulation"]
            if validation_start <= d <= validation_end
        }

        if not flow_dates:
            no_flow += 1
            if acc_dates or any(data["sliding"][w] for w in WINDOWS):
                report.add(Issue(
                    "stock", code, "accumulation",
                    f"{validation_start} ~ {validation_end} 无 dc_money_flows 但存在聚合数据",
                ))
            continue

        if not acc_dates:
            missing_acc += 1
            report.add(Issue(
                "stock", code, "accumulation",
                f"有 {len(flow_dates)} 天 flow，但无 accumulation 数据",
            ))
            continue

        if _report_date_gaps(report, "stock", code, "accumulation", flow_dates, acc_dates):
            incomplete_acc += 1

        for window in WINDOWS:
            slide_dates: Set[date] = {
                d for d in data["sliding"][window]
                if validation_start <= d <= validation_end
            }
            if len(flow_dates) < window:
                if slide_dates:
                    incomplete_slide += 1
                    report.add(Issue(
                        "stock", code, f"sliding({window}日)",
                        f"flow 仅 {len(flow_dates)} 天，不应存在 sliding 数据",
                    ))
                continue
            if not slide_dates:
                incomplete_slide += 1
                report.add(Issue(
                    "stock", code, f"sliding({window}日)",
                    "有足够 flow 但无 sliding 数据",
                ))
                continue
            expected_slide = flow_dates[window - 1 :]
            if _report_date_gaps(
                report, "stock", code, f"sliding({window}日)", expected_slide, slide_dates,
            ):
                incomplete_slide += 1

    print(
        f"齐全/连续（排除北交所，自 {validation_start}）："
        f"股票 {len(stock_codes)} 只，无 flow {no_flow}，无 accumulation {missing_acc}，"
        f"accumulation 不齐 {incomplete_acc}，sliding 不齐 {incomplete_slide}"
    )


def _check_stock_correctness(
    conn,
    report: ValidationReport,
    stock_codes: List[str],
) -> None:
    flows_by_code = _load_flows(conn, stock_codes)
    aggregations = _load_aggregations(conn, stock_codes, report)
    checked = 0
    for code in stock_codes:
        flows = flows_by_code.get(code, [])
        aggs = aggregations.get(
            code, {"accumulation": [], "sliding": {w: [] for w in WINDOWS}},
        )
        if not flows:
            if aggs["accumulation"] or any(aggs["sliding"][w] for w in WINDOWS):
                report.add(Issue(
                    "stock", code, "accumulation", "无原始 flow 但存在聚合数据",
                ))
            continue

        checked += 1
        _compare_agg_series(
            "stock", code, "accumulation",
            _expected_accumulation(flows), aggs["accumulation"], report,
        )
        for window in WINDOWS:
            _compare_agg_series(
                "stock", code, "sliding",
                _expected_sliding(flows, window),
                aggs["sliding"][window],
                report,
                window=window,
            )
    print(f"数值比对：完成 {checked} 只股票")


def validate_stock(
    conn,
    report: ValidationReport,
    *,
    codes: Optional[List[str]] = None,
    sample: int = 500,
    seed: Optional[int] = None,
) -> None:
    validation_start = get_market_earliest_date()
    validation_end = validation_end_date()
    print("开始验证个股资金流聚合...")
    all_codes = _load_stock_codes(conn, exclude_bj=True)
    if codes:
        stock_codes = [c for c in all_codes if c in set(codes)]
    else:
        if not all_codes:
            report.add(Issue(
                "market", "*", "stock",
                "stocks 表无有效沪深个股，请先 download stock",
            ))
            return
        rng = random.Random(seed)
        k = min(sample, len(all_codes))
        stock_codes = rng.sample(all_codes, k)
        print(
            f"随机抽样 {k} 只股票"
            + (f"（seed={seed}）" if seed is not None else "")
        )
    _check_stock_completeness(
        conn, report, stock_codes, validation_start, validation_end,
    )
    _check_stock_correctness(conn, report, stock_codes)


# ── sector ──────────────────────────────────────────────────────────────────

def _load_sector_codes(conn) -> List[str]:
    rows = conn.execute(
        "SELECT code FROM sectors WHERE is_deleted = 0 ORDER BY code"
    ).fetchall()
    return [row["code"] for row in rows]


def _load_dc_members_by_date(conn, sector_code: str, trade_date: date) -> List[str]:
    rows = conn.execute(
        """SELECT con_code FROM dc_sector_members
           WHERE is_deleted = 0 AND code = ? AND trade_date = ?
           ORDER BY con_code""",
        (sector_code, trade_date.isoformat()),
    ).fetchall()
    return [code_from_ts_code(str(row["con_code"])) for row in rows]


def _expected_sector_daily_flows(
    conn,
    sector_code: str,
    start_date: date,
    end_date: date,
    flows_by_code: Dict[str, List[FlowRow]],
) -> List[FlowRow]:
    trading_days = load_all_trading_days(conn, start_date=start_date, end_date=end_date)
    flow_lookup: Dict[Tuple[str, date], FlowRow] = {}
    for code, rows in flows_by_code.items():
        for row in rows:
            flow_lookup[(code, row[0])] = row

    daily: List[FlowRow] = []
    for day in trading_days:
        members = _load_dc_members_by_date(conn, sector_code, day)
        if not members:
            continue
        total_net = 0.0
        total_cnt = 0
        has_flow = False
        for member in members:
            flow = flow_lookup.get((member, day))
            if flow:
                has_flow = True
                total_net += flow[1]
                total_cnt += flow[2]
        if has_flow:
            daily.append((day, total_net, total_cnt))
    return daily


def _sector_date_range(conn, sector_code: str) -> Optional[tuple[date, date]]:
    row = conn.execute(
        """SELECT MIN(trade_date) AS min_date, MAX(trade_date) AS max_date
           FROM dc_sectors
           WHERE code = ? AND is_deleted = 0""",
        (sector_code,),
    ).fetchone()
    if not row or not row["min_date"] or not row["max_date"]:
        return None
    return (
        date.fromisoformat(row["min_date"][:10]),
        date.fromisoformat(row["max_date"][:10]),
    )


def _member_codes_in_range(
    conn, sector_code: str, start_date: date, end_date: date,
) -> Set[str]:
    rows = conn.execute(
        """SELECT DISTINCT con_code FROM dc_sector_members
           WHERE is_deleted = 0 AND code = ?
             AND trade_date >= ? AND trade_date <= ?""",
        (sector_code, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    return {code_from_ts_code(str(r["con_code"])) for r in rows}


def _validate_one_sector(
    conn,
    sector_code: str,
    report: ValidationReport,
    validation_start: date,
    validation_end: date,
) -> None:
    date_range = _sector_date_range(conn, sector_code)
    if not date_range:
        report.add(Issue("sector", sector_code, "accumulation", "dc_sectors 无该板块数据"))
        return

    min_date, max_date = date_range
    start = max(min_date, validation_start)
    end = min(max_date, validation_end)
    if start > end:
        return

    member_codes = _member_codes_in_range(conn, sector_code, start, end)
    flows_by_code = _load_flows(conn, member_codes)
    daily = _expected_sector_daily_flows(conn, sector_code, start, end, flows_by_code)
    daily_dates = [d for d, _, _ in daily]

    aggs_ends = _load_agg_end_dates(
        conn, [sector_code], AggregationType.SECTOR,
    ).get(sector_code) or {
        "accumulation": set(),
        "sliding": {w: set() for w in WINDOWS},
    }
    acc_dates: Set[date] = {
        d for d in aggs_ends["accumulation"] if start <= d <= end
    }
    aggregations = _load_aggregations(conn, [sector_code], report)
    sector_aggs = aggregations.get(
        sector_code, {"accumulation": [], "sliding": {w: [] for w in WINDOWS}},
    )

    if not daily_dates:
        if acc_dates or any(aggs_ends["sliding"][w] for w in WINDOWS):
            report.add(Issue(
                "sector", sector_code, "accumulation",
                "区间内无成员资金流但存在聚合数据",
            ))
        return

    if not acc_dates:
        report.add(Issue(
            "sector", sector_code, "accumulation",
            f"有 {len(daily_dates)} 个有效交易日，但无 accumulation",
        ))
    else:
        _report_date_gaps(
            report, "sector", sector_code, "accumulation", daily_dates, acc_dates,
        )

    _compare_agg_series(
        "sector", sector_code, "accumulation",
        _expected_accumulation(daily),
        [r for r in sector_aggs["accumulation"] if start <= r[1] <= end],
        report,
    )

    for window in WINDOWS:
        slide_dates: Set[date] = {
            d for d in aggs_ends["sliding"][window] if start <= d <= end
        }
        if len(daily_dates) < window:
            if slide_dates:
                report.add(Issue(
                    "sector", sector_code, f"sliding({window}日)",
                    f"有效交易日仅 {len(daily_dates)} 天，不应存在 sliding",
                ))
            continue
        expected_slide_dates = daily_dates[window - 1 :]
        if not slide_dates:
            report.add(Issue(
                "sector", sector_code, f"sliding({window}日)",
                "有足够有效交易日但无 sliding 数据",
            ))
        else:
            _report_date_gaps(
                report, "sector", sector_code, f"sliding({window}日)",
                expected_slide_dates, slide_dates,
            )
        _compare_agg_series(
            "sector", sector_code, "sliding",
            _expected_sliding(daily, window),
            [r for r in sector_aggs["sliding"][window] if start <= r[1] <= end],
            report,
            window=window,
        )


def validate_sector(
    conn,
    report: ValidationReport,
    *,
    codes: Optional[List[str]] = None,
    sample: int = 100,
    seed: Optional[int] = None,
) -> None:
    validation_start = get_market_earliest_date()
    validation_end = validation_end_date()
    print("开始验证板块资金流聚合...")
    all_codes = _load_sector_codes(conn)
    if codes:
        sector_codes = list(dict.fromkeys(codes))
    else:
        if not all_codes:
            report.add(Issue(
                "market", "*", "sector",
                "sectors 表为空，请先 download sector",
            ))
            return
        rng = random.Random(seed)
        k = min(sample, len(all_codes))
        sector_codes = rng.sample(all_codes, k)
        print(
            f"随机抽样 {k} 个板块"
            + (f"（seed={seed}）" if seed is not None else "")
        )

    for i, code in enumerate(sector_codes, 1):
        print(f"[{i}/{len(sector_codes)}] 检查板块 {code}...")
        _validate_one_sector(
            conn, code, report, validation_start, validation_end,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="验证资金流聚合正确且连续")
    parser.add_argument(
        "target",
        nargs="?",
        choices=("stock", "sector"),
        default=None,
        help="stock / sector；省略则先 stock 再 sector",
    )
    parser.add_argument("--code", nargs="+", metavar="CODE", help="指定代码")
    parser.add_argument(
        "--stock-sample", type=int, default=500, metavar="N",
        help="个股随机抽样数（默认 500）",
    )
    parser.add_argument(
        "--sector-sample", type=int, default=100, metavar="N",
        help="板块随机抽样数（默认 100）",
    )
    parser.add_argument("--seed", type=int, default=None, help="随机抽样种子")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出全部问题详情")
    args = parser.parse_args()

    targets = [args.target] if args.target else ["stock", "sector"]
    report = ValidationReport()

    with get_db() as conn:
        if "stock" in targets:
            validate_stock(
                conn, report,
                codes=args.code if args.target == "stock" else None,
                sample=args.stock_sample,
                seed=args.seed,
            )
        if "sector" in targets:
            validate_sector(
                conn, report,
                codes=args.code if args.target == "sector" else None,
                sample=args.sector_sample,
                seed=args.seed,
            )

    print_report(report, verbose=args.verbose)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
