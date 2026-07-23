"""
验证资金流聚合数据的正确性

验证内容：
  1. 单只股票 accumulation 的递推正确性
     后一天 accumulation = 前一天 accumulation + 当天原始 flow 的 main_net
  2. 单只股票滚动 N 天净流入 = 连续 N 天原始 flow 的 main_net 之和
  3. 板块滚动 N 天净流入 = 各成分股同日滑动净流入之和
"""

import random
from typing import List

from infra.database.connection import get_db


def get_all_stock_codes() -> List[str]:
    with get_db() as conn:
        rows = conn.execute("SELECT code FROM stocks WHERE is_deleted = 0").fetchall()
        return [r["code"] for r in rows]


def get_sector_codes() -> List[str]:
    with get_db() as conn:
        rows = conn.execute("SELECT code FROM sectors").fetchall()
        return [r["code"] for r in rows]


def get_sector_members(sector_code: str) -> List[str]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT stock_code FROM sector_members WHERE sector_code = ?", (sector_code,)
        ).fetchall()
        return [r["stock_code"] for r in rows]


def get_main_net_sum(code: str, from_date: str, to_date: str) -> float:
    """获取原始 flow 在指定日期范围内的 main_net 总和"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT SUM(main_net) as total FROM money_flows WHERE code = ? AND trade_date BETWEEN ? AND ? AND period = 'day' AND is_deleted = 0",
            (code, from_date, to_date),
        ).fetchone()
        return row["total"] or 0.0


# ════════════════════════════════════════════════════════════════
#  验证 1：accumulation = 前一天 accumulation + 当日 main_net
# ════════════════════════════════════════════════════════════════

def verify_stock_accumulation_progression():
    """
    验证 accumulation 的递推正确性：
    accumulation[end_date] = accumulation[prev_end_date] + flow[prev_end_date+1].main_net + ... + flow[end_date].main_net
    """
    stocks = get_all_stock_codes()
    sample = random.sample(stocks, min(20, len(stocks)))

    print("=" * 70)
    print("验证 1：单只股票 accumulation 递推正确性")
    print(f"抽样 {len(sample)} 只股票，逐日验证相邻 accumulation 的差值")
    print("=" * 70)

    total_check = 0
    failed = 0

    for code in sample:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT end_date, main_net
                   FROM money_flow_aggregation
                   WHERE code = ? AND type = 'stock' AND is_accumulative = 1
                   ORDER BY end_date""",
                (code,),
            ).fetchall()

        if len(rows) < 2:
            continue

        # 逐对验证相邻 accumulation
        for i in range(1, len(rows)):
            prev = rows[i - 1]
            curr = rows[i]

            prev_date = prev["end_date"]
            curr_date = curr["end_date"]
            prev_main = prev["main_net"] or 0.0
            curr_main = curr["main_net"] or 0.0

            # 差值应该等于 prev_date+1 到 curr_date 的 main_net 之和
            diff = curr_main - prev_main

            # 从原始 flow 中计算这段时间的 main_net 之和
            # 注意：prev_date 那天的 main_net 已经算在 prev 里了
            # 所以要从 prev_date 的下一天算起
            from_date = prev_date  # 但需要排除 prev_date 本身
            with get_db() as conn:
                flow_sum_row = conn.execute(
                    """SELECT SUM(main_net) as total FROM money_flows
                       WHERE code = ? AND trade_date > ? AND trade_date <= ?
                         AND period = 'day' AND is_deleted = 0""",
                    (code, prev_date, curr_date),
                ).fetchone()
            expected_diff = flow_sum_row["total"] or 0.0

            total_check += 1
            if abs(diff - expected_diff) > 0.01:
                print(f"  ❌ {code} [{prev_date} -> {curr_date}]: diff={diff:.2f} vs expected={expected_diff:.2f}")
                failed += 1

    if failed == 0:
        print(f"  ✅ 全部通过 ({total_check} 次校验)")
    else:
        print(f"  ❌ {failed}/{total_check} 次校验失败")

    return failed == 0


# ════════════════════════════════════════════════════════════════
#  验证 2：单只股票滚动 N 天净流入 = 连续 N 天 main_net 之和
# ════════════════════════════════════════════════════════════════

def verify_stock_sliding_window():
    """抽查多只股票的多个滑动窗口，验证是否正确"""
    stocks = get_all_stock_codes()
    sample = random.sample(stocks, min(20, len(stocks)))
    windows = [3, 5, 10, 20]

    print()
    print("=" * 70)
    print("验证 2：单只股票滚动 N 天净流入（sliding window）")
    print(f"抽样 {len(sample)} 只股票，窗口: {windows}")
    print("=" * 70)

    total_check = 0
    failed = 0

    for code in sample:
        for window in windows:
            with get_db() as conn:
                rows = conn.execute(
                    """SELECT start_date, end_date, main_net
                       FROM money_flow_aggregation
                       WHERE code = ? AND type = 'stock'
                         AND is_accumulative = 0 AND trading_days = ?
                       ORDER BY start_date""",
                    (code, window),
                ).fetchall()

            if not rows:
                continue

            # 抽查 3 条
            if len(rows) <= 3:
                check_rows = rows
            else:
                check_rows = [rows[0], rows[len(rows) // 2], rows[-1]]

            for row in check_rows:
                start_date = row["start_date"]
                end_date = row["end_date"]
                agg_main_net = row["main_net"] or 0.0

                # 原始 flow 中 start_date ~ end_date 的 main_net 总和
                orig_sum = get_main_net_sum(code, start_date, end_date)

                total_check += 1
                if abs(agg_main_net - orig_sum) > 0.01:
                    print(f"  ❌ {code} {window}d [{start_date}~{end_date}]: agg={agg_main_net:.2f} vs sum={orig_sum:.2f}")
                    failed += 1

    if failed == 0:
        print(f"  ✅ 全部通过 ({total_check} 次校验)")
    else:
        print(f"  ❌ {failed}/{total_check} 次校验失败")

    return failed == 0


# ════════════════════════════════════════════════════════════════
#  验证 3：板块滚动 N 天净流入 = 成分股同日滑动净流入之和
# ════════════════════════════════════════════════════════════════

def verify_sector_sliding_window():
    """抽查多个板块的滑动窗口，验证是否符合成分股的合并值"""
    sectors = get_sector_codes()
    sample = random.sample(sectors, min(10, len(sectors)))
    windows = [3, 5, 10, 20]

    print()
    print("=" * 70)
    print("验证 3：板块滚动 N 天净流入 = 成分股滑动净流入之和")
    print(f"抽样 {len(sample)} 个板块，窗口: {windows}")
    print("=" * 70)

    total_check = 0
    failed = 0

    for s_code in sample:
        members = get_sector_members(s_code)
        if not members:
            continue

        # 获取板块名称
        with get_db() as conn:
            s_name = conn.execute("SELECT name FROM sectors WHERE code=?", (s_code,)).fetchone()
            s_name = s_name["name"] if s_name else s_code

        for window in windows:
            with get_db() as conn:
                s_rows = conn.execute(
                    """SELECT start_date, end_date, main_net, huge_net, large_net, medium_net, small_net
                       FROM money_flow_aggregation
                       WHERE code = ? AND type = 'sector'
                         AND is_accumulative = 0 AND trading_days = ?
                       ORDER BY start_date""",
                    (s_code, window),
                ).fetchall()

            if not s_rows:
                continue

            # 抽查 3 条
            if len(s_rows) <= 3:
                check_rows = s_rows
            else:
                check_rows = [s_rows[0], s_rows[len(s_rows) // 2], s_rows[-1]]

            for s_row in check_rows:
                start_date = s_row["start_date"]
                end_date = s_row["end_date"]
                s_main_net = s_row["main_net"] or 0.0
                s_huge_net = s_row["huge_net"] or 0.0
                s_large_net = s_row["large_net"] or 0.0
                s_medium_net = s_row["medium_net"] or 0.0
                s_small_net = s_row["small_net"] or 0.0

                # 计算所有成分股同一起始日期的净流入之和
                total_main = 0.0
                total_huge = 0.0
                total_large = 0.0
                total_medium = 0.0
                total_small = 0.0
                member_count = 0

                for member in members:
                    with get_db() as conn:
                        m_row = conn.execute(
                            """SELECT main_net, huge_net, large_net, medium_net, small_net
                               FROM money_flow_aggregation
                               WHERE code = ? AND type = 'stock'
                                 AND is_accumulative = 0 AND trading_days = ?
                                 AND start_date = ? AND end_date = ?""",
                            (member, window, start_date, end_date),
                        ).fetchone()

                    if m_row:
                        total_main += m_row["main_net"] or 0.0
                        total_huge += m_row["huge_net"] or 0.0
                        total_large += m_row["large_net"] or 0.0
                        total_medium += m_row["medium_net"] or 0.0
                        total_small += m_row["small_net"] or 0.0
                        member_count += 1

                if member_count == 0:
                    continue

                total_check += 1

                main_ok = abs(s_main_net - total_main) <= 0.01
                huge_ok = abs(s_huge_net - total_huge) <= 0.01
                large_ok = abs(s_large_net - total_large) <= 0.01
                medium_ok = abs(s_medium_net - total_medium) <= 0.01
                small_ok = abs(s_small_net - total_small) <= 0.01

                if not (main_ok and huge_ok and large_ok and medium_ok and small_ok):
                    print(f"  ❌ 板块 {s_code}({s_name}) {window}d [{start_date}~{end_date}]")
                    print(f"     main:  sector={s_main_net:.2f} vs members_sum={total_main:.2f} {'✅' if main_ok else '❌'}")
                    print(f"     huge:  sector={s_huge_net:.2f} vs members_sum={total_huge:.2f} {'✅' if huge_ok else '❌'}")
                    print(f"     large: sector={s_large_net:.2f} vs members_sum={total_large:.2f} {'✅' if large_ok else '❌'}")
                    print(f"     medium:sector={s_medium_net:.2f} vs members_sum={total_medium:.2f} {'✅' if medium_ok else '❌'}")
                    print(f"     small: sector={s_small_net:.2f} vs members_sum={total_small:.2f} {'✅' if small_ok else '❌'}")
                    print(f"     (成分股数: {member_count}/{len(members)})")
                    failed += 1

    if failed == 0:
        print(f"  ✅ 全部通过 ({total_check} 次校验)")
    else:
        print(f"  ❌ {failed}/{total_check} 次校验失败")

    return failed == 0


if __name__ == "__main__":
    r1 = verify_stock_accumulation_progression()
    r2 = verify_stock_sliding_window()
    r3 = verify_sector_sliding_window()

    print()
    print("=" * 70)
    print("汇总：")
    print(f"  验证 1（个股 accumulation 递推）: {'✅ 通过' if r1 else '❌ 失败'}")
    print(f"  验证 2（个股滑动窗口）:         {'✅ 通过' if r2 else '❌ 失败'}")
    print(f"  验证 3（板块滑动窗口）:         {'✅ 通过' if r3 else '❌ 失败'}")
