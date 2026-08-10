from datetime import date, timedelta
from typing import Iterator, Tuple


def iter_week_ranges(
    start_date: date,
    end_date: date,
) -> Iterator[Tuple[date, date]]:
    """将 [start_date, end_date] 按自然周（周一至周日）切分为闭区间。"""
    # weekday(): Mon=0 ... Sun=6
    cursor = start_date - timedelta(days=start_date.weekday())
    while cursor <= end_date:
        week_end = cursor + timedelta(days=6)
        range_start = max(start_date, cursor)
        range_end = min(end_date, week_end)
        if range_start <= range_end:
            yield range_start, range_end
        cursor = week_end + timedelta(days=1)


def iter_day_ranges(
    start_date: date,
    end_date: date,
) -> Iterator[Tuple[date, date]]:
    """将 [start_date, end_date] 按自然日切分为闭区间（每日一段）。"""
    cursor = start_date
    while cursor <= end_date:
        yield cursor, cursor
        cursor += timedelta(days=1)


def iter_fortnight_ranges(
    start_date: date,
    end_date: date,
) -> Iterator[Tuple[date, date]]:
    """将 [start_date, end_date] 按双周（连续两个自然周，周一至周日）切分为闭区间。"""
    cursor = start_date - timedelta(days=start_date.weekday())
    while cursor <= end_date:
        fortnight_end = cursor + timedelta(days=13)
        range_start = max(start_date, cursor)
        range_end = min(end_date, fortnight_end)
        if range_start <= range_end:
            yield range_start, range_end
        cursor = fortnight_end + timedelta(days=1)
