import time
from datetime import date, datetime
from typing import Dict, List, Optional

from common.date_range_util import iter_day_ranges, iter_week_ranges
from domain.sector import (
    DCSectorData,
    DCSectorMemberData,
    Sector,
    SectorType,
)
from domain.ts_code_util import code_from_ts_code
from infra.adapters.tushare_adapter import TushareAdapter
from infra.config import get_market_earliest_date
from infra.database.connection import get_db
from infra.log import logger


class SectorRepository:
    """板块数据仓库：dc_* 增量同步，sectors 快照查询"""

    _CACHE_TTL_SECONDS = 24 * 60 * 60  # 缓存有效期：1 天
    _DC_MEMBER_ROW_LIMIT = 8000

    def __init__(self, adapter: TushareAdapter):
        self._adapter = adapter

    def refresh(self, force: bool = False) -> None:
        """同步外部板块快照到数据库。"""
        if not force and self._latest():
            logger.info("数据库缓存有效，跳过刷新")
            return
        self._update_sector_data()
        self._update_sector_members_data()

    def _latest(self) -> bool:
        """检查数据库中是否有在缓存有效期内的数据"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS cnt, MAX(updated_at) AS max_updated
                   FROM dc_sectors WHERE is_deleted = 0"""
            ).fetchone()
            count = row["cnt"]
            if count == 0:
                return False
            max_updated = row["max_updated"]
            if not max_updated:
                return False
            updated_dt = datetime.strptime(max_updated, "%Y-%m-%d %H:%M:%S")
            return (time.time() - updated_dt.timestamp()) < self._CACHE_TTL_SECONDS

    def _update_sector_data(self) -> None:
        """增量同步东财板块行情到 dc_sectors，并去重写入 sectors。"""
        latest_date = self._load_latest_dc_sector_date()
        start_date = latest_date if latest_date else get_market_earliest_date()
        end_date = date.today()
        if start_date >= end_date:
            logger.info(f"dc_sectors 已覆盖至 {start_date}，无需拉取")
            self._sync_sectors_from_dc()
            return

        logger.info(f"按日拉取 dc_index: {start_date} ~ {end_date}")
        total = 0
        for day, _ in iter_day_ranges(start_date, end_date):
            day_rows = self._adapter.get_sector_data(day)
            inserted = self._save_dc_sectors(day_rows) if day_rows else 0
            logger.info(
                f"dc_index 分日拉取完成: {day}, 拉取 {len(day_rows)} 条, 写入 {inserted} 条"
            )
            total += inserted
            time.sleep(0.1)
        logger.info(f"dc_sectors 增量写入完成: {start_date} ~ {end_date}, 共写入 {total} 条")
        self._sync_sectors_from_dc()

    def _load_latest_dc_sector_date(self) -> Optional[date]:
        with get_db() as conn:
            row = conn.execute(
                """SELECT MAX(trade_date) AS max_date
                   FROM dc_sectors
                   WHERE is_deleted = 0"""
            ).fetchone()
        if not row or not row["max_date"]:
            return None
        return datetime.strptime(row["max_date"], "%Y-%m-%d").date()

    def _sync_sectors_from_dc(self) -> None:
        """将 dc_sectors 按 code 去重（取最新交易日）写入 sectors，已存在则跳过。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            before = conn.total_changes
            conn.execute(
                """INSERT INTO sectors (code, name, type, created_at, updated_at)
                   SELECT d.code,
                          d.name,
                          CASE d.idx_type
                              WHEN '行业板块' THEN '行业'
                              WHEN '概念板块' THEN '概念'
                              WHEN '地域板块' THEN '地区'
                              ELSE 'UNKNOWN'
                          END,
                          ?,
                          ?
                   FROM dc_sectors d
                   INNER JOIN (
                       SELECT code, MAX(trade_date) AS max_date
                       FROM dc_sectors
                       WHERE is_deleted = 0
                       GROUP BY code
                   ) t ON d.code = t.code AND d.trade_date = t.max_date
                   WHERE d.is_deleted = 0
                   ON CONFLICT(code) DO NOTHING""",
                (now, now),
            )
            inserted = conn.total_changes - before
        logger.info(f"sectors 同步完成: 新增 {inserted} 条")

    def _save_dc_sectors(self, rows: List[DCSectorData]) -> int:
        if not rows:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT INTO dc_sectors (
                       ts_code, code, trade_date, name, leading, leading_code,
                       pct_change, leading_pct, total_mv, turnover_rate,
                       up_num, down_num, idx_type, level,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ts_code, trade_date) DO NOTHING""",
                [
                    (
                        item.ts_code,
                        code_from_ts_code(item.ts_code),
                        item.trade_date.isoformat(),
                        item.name,
                        item.leading,
                        item.leading_code,
                        item.pct_change,
                        item.leading_pct,
                        item.total_mv,
                        item.turnover_rate,
                        item.up_num,
                        item.down_num,
                        item.idx_type,
                        item.level,
                        now,
                        now,
                    )
                    for item in rows
                ],
            )
            return conn.total_changes - before

    def _update_sector_members_data(self) -> None:
        """
            增量同步东财板块成分到 dc_sector_members。
        """
        logger.info("查询 dc_sectors 日期范围，用于确定需要拉取的板块成分")
        sectors_date_range = self.find_dc_sectors_date_range()
        if not sectors_date_range:
            logger.warning("dc_sectors 无数据，跳过成分拉取")
            return

        member_dates = self._load_latest_dc_member_dates()
        logger.info(f"按周拉取 dc_member: 共 {len(sectors_date_range)} 个板块")

        total = 0
        for seq, (code, (ts_code, _, sector_max_date)) in enumerate(sectors_date_range.items()):
            member_max_date = member_dates.get(code)
            if member_max_date and member_max_date >= sector_max_date:
                logger.info(
                    f"{seq}: {code} 成分已覆盖至 {member_max_date} "
                    f"(>= 板块 {sector_max_date})，跳过"
                )
                continue

            start_date = member_max_date or get_market_earliest_date()
            end_date = sector_max_date
            if start_date > end_date:
                continue

            logger.info(f"{seq}: 按周拉取板块成分: {code}, {start_date} ~ {end_date}")
            for week_start, week_end in iter_week_ranges(start_date, end_date):
                week_rows = self._adapter.get_sector_members_data(ts_code, week_start, week_end)
                if len(week_rows) >= self._DC_MEMBER_ROW_LIMIT:
                    logger.warning(f"dc_member 分周结果达到上限 {len(week_rows)} 条，改为按日重拉: {code}, {week_start} ~ {week_end}")
                    for day_start, day_end in iter_day_ranges(week_start, week_end):
                        day_rows = self._adapter.get_sector_members_data(ts_code, day_start, day_end)
                        inserted = self._save_dc_sector_members(day_rows) if day_rows else 0
                        logger.info(
                            f"dc_member 分日写入: {code}, {day_start}, "
                            f"拉取 {len(day_rows)} 条, 写入 {inserted} 条"
                        )
                        total += inserted
                        time.sleep(0.1)
                    continue
                inserted = self._save_dc_sector_members(week_rows) if week_rows else 0
                logger.info(
                    f"dc_member 分周写入: {code}, {week_start} ~ {week_end}, "
                    f"拉取 {len(week_rows)} 条, 写入 {inserted} 条"
                )
                total += inserted
                time.sleep(0.1)
        logger.info(f"dc_sector_members 增量写入完成: 共写入 {total} 条")

    def _load_latest_dc_member_dates(self) -> Dict[str, date]:
        """查询 dc_sector_members，按 code 分组取各板块最新 trade_date。"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, MAX(trade_date) AS max_date
                   FROM dc_sector_members
                   WHERE is_deleted = 0
                   GROUP BY code"""
            ).fetchall()
        result: Dict[str, date] = {}
        for row in rows:
            code = row["code"]
            if not code or not row["max_date"]:
                continue
            result[code] = datetime.strptime(row["max_date"], "%Y-%m-%d").date()
        return result

    def _save_dc_sector_members(self, rows: List[DCSectorMemberData]) -> int:
        if not rows:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT INTO dc_sector_members (
                       trade_date, ts_code, code, con_code, name,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(trade_date, ts_code, con_code) DO NOTHING""",
                [
                    (
                        item.trade_date.isoformat(),
                        item.ts_code,
                        code_from_ts_code(item.ts_code),
                        item.con_code,
                        item.name,
                        now,
                        now,
                    )
                    for item in rows
                ],
            )
            return conn.total_changes - before

    def find_by_code(self, code: str) -> Optional[Sector]:
        """根据板块代码查询 sectors。"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT code, name, type, version
                   FROM sectors
                   WHERE code = ? AND is_deleted = 0""",
                (code,),
            ).fetchone()
            if not row:
                return None
            return Sector(
                code=row["code"],
                name=row["name"],
                type=SectorType(row["type"]),
                version=row["version"],
            )

    def find_by_codes(self, codes: Optional[List[str]]) -> List[Sector]:
        """根据一组板块代码批量查询 sectors。"""
        if not codes:
            return []
        placeholders = ",".join("?" * len(codes))
        with get_db() as conn:
            rows = conn.execute(
                f"""SELECT code, name, type, version
                    FROM sectors
                    WHERE code IN ({placeholders}) AND is_deleted = 0
                    ORDER BY code""",
                codes,
            ).fetchall()
            return [
                Sector(
                    code=row["code"],
                    name=row["name"],
                    type=SectorType(row["type"]),
                    version=row["version"],
                )
                for row in rows
            ]

    def find_all(self) -> List[Sector]:
        """获取 sectors 全部板块。"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, name, type, version
                   FROM sectors
                   WHERE is_deleted = 0
                   ORDER BY code"""
            ).fetchall()
            return [
                Sector(
                    code=row["code"],
                    name=row["name"],
                    type=SectorType(row["type"]),
                    version=row["version"],
                )
                for row in rows
            ]

    def find_dc_sectors_date_range(
        self,
        codes: Optional[List[str]] = None,
    ) -> Dict[str, tuple[str, date, date]]:
        """查询 dc_sectors，按 code 分组返回 (ts_code, 最早, 最晚) trade_date。

        Args:
            codes: 可选板块代码列表；为空则查询全部。
        """
        with get_db() as conn:
            if codes is not None:
                if not codes:
                    return {}
                placeholders = ",".join("?" * len(codes))
                rows = conn.execute(
                    f"""SELECT code, ts_code,
                               MIN(trade_date) AS min_date,
                               MAX(trade_date) AS max_date
                        FROM dc_sectors
                        WHERE is_deleted = 0
                          AND code IN ({placeholders})
                        GROUP BY code, ts_code
                        ORDER BY code""",
                    codes,
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT code, ts_code,
                              MIN(trade_date) AS min_date,
                              MAX(trade_date) AS max_date
                       FROM dc_sectors
                       WHERE is_deleted = 0
                       GROUP BY code, ts_code
                       ORDER BY code"""
                ).fetchall()
        result: Dict[str, tuple[str, date, date]] = {}
        for row in rows:
            code = row["code"]
            ts_code = row["ts_code"]
            if not code or not ts_code or not row["min_date"] or not row["max_date"]:
                continue
            result[code] = (
                ts_code,
                datetime.strptime(row["min_date"], "%Y-%m-%d").date(),
                datetime.strptime(row["max_date"], "%Y-%m-%d").date(),
            )
        return result

    def find_dc_members_by_date(
        self,
        sector_code: str,
        trade_date: date,
    ) -> List[str]:
        """查询指定板块在某交易日的成分股代码列表（con_code）。"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT con_code
                   FROM dc_sector_members
                   WHERE is_deleted = 0
                     AND code = ?
                     AND trade_date = ?
                   ORDER BY con_code""",
                (sector_code, trade_date.isoformat()),
            ).fetchall()
        return [row["con_code"] for row in rows]
