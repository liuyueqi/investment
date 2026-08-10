"""验证基础数据与 money_flow_aggregation 的完整性与正确性

检查项：
  1. trading_days：与 TushareAdapter.get_all_trading_days 比对是否一致
  2. stocks：与 TushareAdapter.get_all_stocks 比对是否一致
  3. 自 config.toml market.earliest_date 起按库内 trading_days，每个交易日有 money_flows 的个股占比 >= 95%（排除北交所）
  4. 自 config.toml market.earliest_date 起，按个股 money_flows 交易日校验 accumulation / sliding(3/5/10/20) 齐全且连续（兼容停牌）
  5. （可选）个股/板块聚合数值与 money_flows 重算结果比对

用法：
  python validate_aggregation.py                     # 检查全部（含 1~4）
  python validate_aggregation.py --scope calendar    # 仅交易日历
  python validate_aggregation.py --scope stocks      # 仅股票列表
  python validate_aggregation.py --scope coverage    # 仅 flow 覆盖率
  python validate_aggregation.py --scope completeness # 仅聚合齐全/连续
  python validate_aggregation.py --scope stock       # 个股数值比对
  python validate_aggregation.py --code 000001
  python validate_aggregation.py --limit 50
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from infra.config import get_market_earliest_date
from infra.container import container
from infra.database.connection import get_db
from domain.money_flow_aggregation_repository import MoneyFlowAggregationRepository
from domain.money_flow_aggregation import MoneyFlowAggregation, AggregationType
from domain.sector_change_log import SectorChangeAction, SectorChangeLog
from domain.stock import Stock

WINDOWS = (3, 5, 10, 20)
TOLERANCE = 0.01  # 万元，允许浮点误差
FLOW_COVERAGE_MIN_RATE = 0.95


def _validation_end_date() -> date:
    """校验截止日：不含今天（当日可能尚未开盘/收盘，不参与验证）。"""
    return date.today() - timedelta(days=1)

_agg_repo = MoneyFlowAggregationRepository()

FlowRow = Tuple[date, float, int]  # trade_date, main_net, main_cnt
AggRow = Tuple[date, date, int, float, int]  # start_date, end_date, trading_days, main_net, main_cnt
SectorAccRow = Tuple[date, date, int, float, int, datetime]  # + 聚合记录 created_at
SectorSlideRow = SectorAccRow
MemberHistory = Dict[int, List[str]]  # version -> members


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
    created_at = _parse_datetime(row["created_at"])
    return (*_agg_to_row(agg), created_at)


def _sector_slide_to_row(row: dict, agg: MoneyFlowAggregation) -> SectorSlideRow:
    created_at = _parse_datetime(row["created_at"])
    return (*_agg_to_row(agg), created_at)


def _parse_datetime(value: str) -> datetime:
    return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")


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


def _load_sectors(conn) -> Dict[str, Tuple[List[str], int]]:
    """加载板块当前成分股及 version"""
    sectors: Dict[str, Tuple[List[str], int]] = {}
    sector_rows = conn.execute(
        "SELECT code, version FROM sectors WHERE is_deleted = 0 ORDER BY code"
    ).fetchall()
    if not sector_rows:
        return sectors

    codes = [row["code"] for row in sector_rows]
    placeholders = ",".join("?" * len(codes))
    member_rows = conn.execute(
        f"""SELECT sector_code, stock_code FROM sector_members
            WHERE sector_code IN ({placeholders}) AND is_deleted = 0
            ORDER BY sector_code, stock_code""",
        codes,
    ).fetchall()
    members_by_code: Dict[str, List[str]] = defaultdict(list)
    for row in member_rows:
        members_by_code[row["sector_code"]].append(row["stock_code"])

    for row in sector_rows:
        code = row["code"]
        sectors[code] = (members_by_code.get(code, []), row["version"])
    return sectors


def _load_sector_change_logs(conn, codes: Iterable[str]) -> Dict[str, List[SectorChangeLog]]:
    code_list = list(codes)
    if not code_list:
        return {}

    placeholders = ",".join("?" * len(code_list))
    rows = conn.execute(
        f"""SELECT sector_code, action, old_value, new_value, version,
                   changed_at, created_at
            FROM sector_change_logs
            WHERE sector_code IN ({placeholders})
            ORDER BY sector_code, version, id""",
        code_list,
    ).fetchall()

    logs: Dict[str, List[SectorChangeLog]] = defaultdict(list)
    for row in rows:
        changed_raw = row["changed_at"] if "changed_at" in row.keys() else None
        logs[row["sector_code"]].append(SectorChangeLog(
            sector_code=row["sector_code"],
            action=SectorChangeAction(row["action"]),
            old_value=row["old_value"],
            new_value=row["new_value"],
            version=row["version"],
            changed_at=_parse_datetime(changed_raw or row["created_at"]),
            created_at=_parse_datetime(row["created_at"]),
        ))
    return logs


def _build_member_history(
    current_members: Sequence[str],
    current_version: int,
    change_logs: Sequence[SectorChangeLog],
) -> MemberHistory:
    """根据 change logs 回放，得到各 version 对应的成分股集合"""
    if not change_logs:
        return {current_version: sorted(current_members)}

    logs_by_version: Dict[int, List[SectorChangeLog]] = defaultdict(list)
    for log in change_logs:
        logs_by_version[log.version].append(log)

    history: MemberHistory = {current_version: sorted(current_members)}
    members = set(current_members)
    for version in sorted(logs_by_version.keys(), reverse=True):
        for log in logs_by_version[version]:
            if log.action == SectorChangeAction.ADD_MEMBER:
                members.discard(log.new_value)
            elif log.action == SectorChangeAction.REMOVE_MEMBER:
                members.add(log.old_value)
        history[version - 1] = sorted(members)
    return history


def _version_at_time(
    agg_created_at: datetime,
    change_logs: Sequence[SectorChangeLog],
) -> int:
    """根据聚合记录写入时间，对照板块变更时间确定当时有效的 version"""
    version = 0
    version_times: Dict[int, datetime] = {}
    for log in change_logs:
        if not log.changed_at:
            continue
        prev = version_times.get(log.version)
        if not prev or log.changed_at < prev:
            version_times[log.version] = log.changed_at

    for ver in sorted(version_times):
        if version_times[ver] <= agg_created_at:
            version = ver
    return version


def _members_at_version(history: MemberHistory, version: int) -> List[str]:
    if version in history:
        return history[version]
    lower = [v for v in history if v <= version]
    if lower:
        return history[max(lower)]
    return history[min(history)]


def _all_members_from_history(history: MemberHistory) -> set[str]:
    members: set[str] = set()
    for codes in history.values():
        members.update(codes)
    return members


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
    member_history: MemberHistory,
    change_logs: Sequence[SectorChangeLog],
    stock_accumulations: Dict[str, List[AggRow]],
    end_date: date,
    agg_created_at: datetime,
) -> Optional[AggRow]:
    """按 change logs 确定的版本快照，计算某日板块 accumulation 期望值"""
    version = _version_at_time(agg_created_at, change_logs)
    member_aggs: List[AggRow] = []
    for stock_code in _members_at_version(member_history, version):
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
    member_history: MemberHistory,
    change_logs: Sequence[SectorChangeLog],
    stock_accumulations: Dict[str, List[AggRow]],
    actual_rows: Sequence[SectorAccRow],
    report: ValidationReport,
) -> None:
    """逐条验证板块 accumulation（按 change logs 确定写入时有效成分股）"""
    for row in actual_rows:
        start_date, end_date, trading_days, main_net, main_cnt, agg_created_at = row
        version = _version_at_time(agg_created_at, change_logs)
        expected = _expected_sector_accumulation_row(
            member_history, change_logs, stock_accumulations, end_date, agg_created_at,
        )
        if expected is None:
            report.add(Issue(
                "sector", code, "accumulation",
                f"version {version} 无有效成分股数据 (end={end_date})",
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
                f"(end={end_date}, version={version})",
            ))
        if main_cnt != expected[4]:
            report.add(Issue(
                "sector", code, "accumulation",
                f"main_cnt 错误 {main_cnt} != {expected[4]} "
                f"(end={end_date}, version={version})",
            ))


def _expected_sector_sliding(
    member_history: MemberHistory,
    change_logs: Sequence[SectorChangeLog],
    flows_by_code: Dict[str, List[FlowRow]],
    window: int,
    agg_created_at: datetime,
) -> List[AggRow]:
    """按 change logs 确定的版本快照，在交易日并集上滑动"""
    version = _version_at_time(agg_created_at, change_logs)
    member_codes = _members_at_version(member_history, version)
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
    member_history: MemberHistory,
    change_logs: Sequence[SectorChangeLog],
    flows_by_code: Dict[str, List[FlowRow]],
    actual_rows: Sequence[SectorSlideRow],
    window: int,
    report: ValidationReport,
) -> None:
    """逐条验证板块 sliding（按 change logs 确定写入时有效成分股）"""
    label = f"sliding({window}日)"
    by_created_at: Dict[datetime, List[SectorSlideRow]] = defaultdict(list)
    for row in actual_rows:
        by_created_at[row[5]].append(row)

    for agg_created_at, rows in by_created_at.items():
        version = _version_at_time(agg_created_at, change_logs)
        expected_map = {
            (row[0], row[1]): row
            for row in _expected_sector_sliding(
                member_history, change_logs, flows_by_code, window, agg_created_at,
            )
        }
        for row in rows:
            start_date, end_date, trading_days, main_net, main_cnt, _ = row
            expected = expected_map.get((start_date, end_date))
            if expected is None:
                report.add(Issue(
                    "sector", code, label,
                    f"version {version} 无法复现窗口 "
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
                    f"(start={start_date}, end={end_date}, version={version})",
                ))
            if main_cnt != expected[4]:
                report.add(Issue(
                    "sector", code, label,
                    f"main_cnt 错误 {main_cnt} != {expected[4]} "
                    f"(start={start_date}, end={end_date}, version={version})",
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


def _load_flow_counts_by_date(
    conn, start_date: date, end_date: date,
) -> Dict[date, int]:
    """按日统计有 flow 的个股数（排除北交所）"""
    rows = conn.execute(
        """SELECT f.trade_date, COUNT(DISTINCT f.code) AS cnt
           FROM money_flows f
           JOIN stocks s ON s.code = f.code AND s.is_deleted = 0
           WHERE f.period = 'day'
             AND f.is_deleted = 0
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


def _load_all_trading_days(
    conn,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[date]:
    """加载库内交易日；可按 start_date / end_date 截取闭区间。"""
    sql = """SELECT trade_date FROM trading_days
             WHERE is_deleted = 0"""
    params: List = []
    if start_date is not None:
        sql += " AND trade_date >= ?"
        params.append(start_date.isoformat())
    if end_date is not None:
        sql += " AND trade_date <= ?"
        params.append(end_date.isoformat())
    sql += " ORDER BY trade_date"
    rows = conn.execute(sql, params).fetchall()
    return [datetime.strptime(row["trade_date"], "%Y-%m-%d").date() for row in rows]


def _load_db_stocks(conn) -> Dict[str, Stock]:
    rows = conn.execute(
        """SELECT code, name, market FROM stocks
           WHERE is_deleted = 0 ORDER BY code"""
    ).fetchall()
    return {
        row["code"]: Stock(code=row["code"], name=row["name"], market=row["market"])
        for row in rows
    }


def validate_trading_days(conn, report: ValidationReport) -> None:
    """用 Tushare get_all_trading_days 校验库内 trading_days 是否一致。"""
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
        sample = only_remote[:10]
        report.add(Issue(
            "market", "*", "trading_days",
            f"DB 缺失 {len(only_remote)} 个交易日，示例: {sample}",
        ))
    if only_db:
        sample = only_db[:10]
        report.add(Issue(
            "market", "*", "trading_days",
            f"DB 多余 {len(only_db)} 个交易日，示例: {sample}",
        ))


def validate_stocks(conn, report: ValidationReport) -> None:
    """用 Tushare get_all_stocks 校验库内 stocks 是否一致。"""
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


def validate_flow_coverage(conn, report: ValidationReport) -> None:
    """按库内 trading_days，校验每个交易日个股 money_flows 有数据率（排除北交所）。"""
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
    validation_end = _validation_end_date()
    trading_days = _load_all_trading_days(
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
        + (
            f"；最低 {min_rate:.2%} @ {min_rate_date}"
            if min_rate_date is not None else ""
        )
    )


def _load_stock_agg_end_dates(
    conn, codes: Sequence[str],
) -> Dict[str, Dict]:
    """加载个股聚合 end_date：{code: {accumulation: set[date], sliding: {w: set[date]}}}"""
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
            WHERE type = ?
              AND code IN ({placeholders})""",
        [AggregationType.STOCK.value, *codes],
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


def _report_date_gaps(
    report: ValidationReport,
    code: str,
    category: str,
    expected: Sequence[date],
    actual: Set[date],
    sample_limit: int = 5,
) -> int:
    """校验 actual 相对 expected 是否齐全；返回缺失天数。"""
    expected_set = set(expected)
    missing = sorted(expected_set - actual)
    extra = sorted(actual - expected_set)
    if missing:
        report.add(Issue(
            "stock", code, category,
            f"缺失 {len(missing)} 天，示例: {missing[:sample_limit]}",
        ))
    if extra:
        report.add(Issue(
            "stock", code, category,
            f"多余 {len(extra)} 天（无对应 money_flows），示例: {extra[:sample_limit]}",
        ))
    return len(missing)


def validate_stock_agg_completeness(
    conn,
    report: ValidationReport,
    codes: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> None:
    """
        按个股 money_flows 交易日校验 accumulation / sliding(3/5/10/20) 齐全且连续。
        以 flow 日期为基准，天然兼容停牌日（无 flow 的日期不要求有聚合）。
    """
    validation_start = get_market_earliest_date()
    validation_end = _validation_end_date()

    stock_codes = _load_stocks(conn)
    bj_codes = {
        row["code"]
        for row in conn.execute(
            "SELECT code FROM stocks WHERE is_deleted = 0 AND market = 'BJ'"
        ).fetchall()
    }
    stock_codes = [c for c in stock_codes if c not in bj_codes]

    if codes:
        stock_codes = [c for c in stock_codes if c in set(codes)]
    if limit is not None:
        stock_codes = stock_codes[:limit]

    flows_by_code = _load_flows(conn, stock_codes)
    aggs = _load_stock_agg_end_dates(conn, stock_codes)
    no_flow = 0
    missing_acc = 0
    incomplete_acc = 0
    incomplete_slide = 0

    for code in stock_codes:
        flow_dates = sorted(
            d for d, _, _ in flows_by_code.get(code, [])
            if validation_start <= d <= validation_end
        )
        data = aggs.get(code) or {"accumulation": set(), "sliding": {w: set() for w in WINDOWS}}
        acc_dates: Set[date] = {
            d for d in data["accumulation"]
            if validation_start <= d <= validation_end
        }

        if not flow_dates:
            no_flow += 1
            if acc_dates or any(data["sliding"][w] for w in WINDOWS):
                report.add(Issue(
                    "stock", code, "accumulation",
                    f"{validation_start} ~ {validation_end} 无 money_flows 但存在聚合数据",
                ))
            continue

        if not acc_dates:
            missing_acc += 1
            report.add(Issue(
                "stock", code, "accumulation",
                f"有 {len(flow_dates)} 天 money_flows，但无 accumulation 数据",
            ))
            continue

        if _report_date_gaps(report, code, "accumulation", flow_dates, acc_dates):
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
                        f"money_flows 仅 {len(flow_dates)} 天，不应存在 sliding 数据",
                    ))
                continue
            if not slide_dates:
                incomplete_slide += 1
                report.add(Issue(
                    "stock", code, f"sliding({window}日)",
                    "有足够 money_flows 但无 sliding 数据",
                ))
                continue

            # 以 flow 序列滑动：从第 window 个有 flow 的交易日起，每日都应有 end=该日的 sliding
            expected_slide = flow_dates[window - 1 :]
            if _report_date_gaps(
                report, code, f"sliding({window}日)", expected_slide, slide_dates,
            ):
                incomplete_slide += 1

    print(
        f"聚合齐全/连续检查（排除北交所，自 {validation_start}，按 money_flows）："
        f"股票 {len(stock_codes)} 只，无 flow {no_flow}，无 accumulation {missing_acc}，"
        f"accumulation 不齐 {incomplete_acc}，sliding 不齐 {incomplete_slide}"
    )


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
    members: List[str],
    sector_version: int,
    change_logs: Sequence[SectorChangeLog],
    flows_by_code: Dict[str, List[FlowRow]],
    stock_aggs: Dict[str, Dict],
    sector_aggs: Dict,
    report: ValidationReport,
) -> None:
    if not members:
        return

    member_history = _build_member_history(members, sector_version, change_logs)
    all_members = _all_members_from_history(member_history)
    member_flows_exist = any(flows_by_code.get(m) for m in all_members)
    if not member_flows_exist:
        if sector_aggs["accumulation"] or any(sector_aggs["sliding"][w] for w in WINDOWS):
            report.add(Issue("sector", code, "accumulation", "成分股无 flow 但存在聚合数据"))
        return

    stock_accumulations = {
        m: stock_aggs.get(m, {}).get("accumulation", [])
        for m in all_members
    }
    _validate_sector_accumulation(
        code, member_history, change_logs, stock_accumulations,
        sector_aggs["accumulation"], report,
    )

    for window in WINDOWS:
        _validate_sector_sliding(
            code, member_history, change_logs, flows_by_code,
            sector_aggs["sliding"][window], window, report,
        )


def run_validation(
    scope: str = "all",
    codes: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> ValidationReport:
    report = ValidationReport()

    with get_db() as conn:
        if scope in ("all", "calendar"):
            validate_trading_days(conn, report)

        if scope in ("all", "stocks"):
            validate_stocks(conn, report)

        if scope in ("all", "coverage", "stock"):
            validate_flow_coverage(conn, report)

        if scope in ("all", "completeness"):
            validate_stock_agg_completeness(conn, report, codes=codes, limit=limit)

        if scope in ("calendar", "stocks", "coverage", "completeness"):
            return report

        all_stocks = _load_stocks(conn)
        all_sectors = _load_sectors(conn)
        sector_change_logs = _load_sector_change_logs(conn, all_sectors.keys())

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
            for sc in sector_codes:
                members, ver = all_sectors.get(sc, ([], 0))
                history = _build_member_history(
                    members, ver, sector_change_logs.get(sc, []),
                )
                member_codes |= _all_members_from_history(history)

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
                members, sector_version = all_sectors.get(code, ([], 0))
                validate_sector(
                    code,
                    members,
                    sector_version,
                    sector_change_logs.get(code, []),
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
    parser = argparse.ArgumentParser(description="验证基础数据与 aggregation 完整性/正确性")
    parser.add_argument(
        "--scope",
        choices=["all", "calendar", "stocks", "coverage", "completeness", "stock", "sector"],
        default="all",
        help=(
            "检查范围：all=1~4+数值比对；calendar=交易日；stocks=股票列表；"
            "coverage=flow 覆盖率；completeness=聚合齐全连续；"
            "stock/sector=数值比对"
        ),
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

    print("开始验证...")
    report = run_validation(scope=args.scope, codes=args.code, limit=args.limit)
    _print_report(report, verbose=args.verbose)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
