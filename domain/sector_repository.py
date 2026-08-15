import time
# from collections import defaultdict  # UNUSED
from datetime import date, datetime
from typing import Dict, List, Optional

from common.date_range_util import iter_day_ranges, iter_week_ranges
from domain.sector import (
    DCSectorData,
    DCSectorMemberData,
    Sector,
    # SectorChangeAction,  # UNUSED
    # SectorChangeLog,  # UNUSED
    SectorType,
)
# from domain.sector_history import SectorHistory  # UNUSED
from infra.adapters.tushare_adapter import TushareAdapter
from infra.config import get_market_earliest_date
from infra.database.connection import get_db
from infra.log import logger


class SectorRepository:
    """板块数据仓库，管理 sectors 表和 sector_members 表"""

    _CACHE_TTL_SECONDS = 24 * 60 * 60  # 缓存有效期：1 天
    _DC_MEMBER_ROW_LIMIT = 8000

    def __init__(self, adapter: TushareAdapter):
        self._adapter = adapter

    def refresh(self, force: bool = False) -> None:
        """同步外部板块快照到数据库，并写入变更日志。"""
        if not force and self._latest():
            logger.info("数据库缓存有效，跳过刷新")
            return
        # self._update_from_adapter()
        self._update_sector_data()
        self._update_sector_members_data()

    def _latest(self) -> bool:
        """检查数据库中是否有在缓存有效期内的数据"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS cnt, MAX(updated_at) AS max_updated
                   FROM sectors WHERE is_deleted = 0"""
            ).fetchone()
            count = row["cnt"]
            if count == 0:
                return False
            max_updated = row["max_updated"]
            if not max_updated:
                return False
            updated_dt = datetime.strptime(max_updated, "%Y-%m-%d %H:%M:%S")
            return (time.time() - updated_dt.timestamp()) < self._CACHE_TTL_SECONDS

# UNUSED: _update_from_adapter 不可达（refresh 中已注释调用）
#     def _update_from_adapter(self) -> None:
#         start_date = self._min_sector_updated_date() or get_market_earliest_date()
#         logger.info(f"开始拉取板块快照，start_date={start_date}")
#
#         snapshots = self._adapter.get_all_sectors(start_date=start_date)
#         if not snapshots:
#             logger.warning("未获取到板块快照，跳过保存")
#             return
#
#         by_code: Dict[str, List[tuple[date, Sector]]] = defaultdict(list)
#         for trade_date, sector in snapshots:
#             by_code[sector.code].append((trade_date, sector))
#
#         histories = [
#             SectorHistory.from_snapshots(group)
#             for group in by_code.values()
#         ]
#         self._save_histories(histories)

    def _update_sector_data(self) -> None:
        """增量同步东财板块行情到 dc_sectors。"""
        latest_date = self._load_latest_dc_sector_date()
        start_date = latest_date if latest_date else get_market_earliest_date()
        end_date = date.today()
        if start_date >= end_date:
            logger.info(f"dc_sectors 已覆盖至 {start_date}，无需拉取")
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

    def _save_dc_sectors(self, rows: List[DCSectorData]) -> int:
        if not rows:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT INTO dc_sectors (
                       ts_code, trade_date, name, leading, leading_code,
                       pct_change, leading_pct, total_mv, turnover_rate,
                       up_num, down_num, idx_type, level,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ts_code, trade_date) DO NOTHING""",
                [
                    (
                        item.ts_code,
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
        """增量同步东财板块成分到 dc_sector_members。"""
        sectors_date_range = self.find_dc_sectors_date_range()
        if not sectors_date_range:
            logger.warning("dc_sectors 无数据，跳过成分拉取")
            return

        member_dates = self._load_latest_dc_member_dates()
        logger.info(f"按周拉取 dc_member: 共 {len(sectors_date_range)} 个板块")

        total = 0
        for seq, (ts_code, (_, sector_max_date)) in enumerate(sectors_date_range.items()):
            member_max_date = member_dates.get(ts_code)
            if member_max_date and member_max_date >= sector_max_date:
                logger.info(
                    f"{seq}: {ts_code} 成分已覆盖至 {member_max_date} "
                    f"(>= 板块 {sector_max_date})，跳过"
                )
                continue

            start_date = member_max_date or get_market_earliest_date()
            end_date = sector_max_date
            if start_date > end_date:
                continue

            logger.info(f"{seq}: 按周拉取板块成分: {ts_code}, {start_date} ~ {end_date}")
            for week_start, week_end in iter_week_ranges(start_date, end_date):
                week_rows = self._adapter.get_sector_members_data(ts_code, week_start, week_end)
                if len(week_rows) >= self._DC_MEMBER_ROW_LIMIT:
                    logger.warning(f"dc_member 分周结果达到上限 {len(week_rows)} 条，改为按日重拉: {ts_code}, {week_start} ~ {week_end}")
                    for day_start, day_end in iter_day_ranges(week_start, week_end):
                        day_rows = self._adapter.get_sector_members_data(ts_code, day_start, day_end)
                        inserted = self._save_dc_sector_members(day_rows) if day_rows else 0
                        logger.info(
                            f"dc_member 分日写入: {ts_code}, {day_start}, "
                            f"拉取 {len(day_rows)} 条, 写入 {inserted} 条"
                        )
                        total += inserted
                        time.sleep(0.1)
                    continue
                inserted = self._save_dc_sector_members(week_rows) if week_rows else 0
                logger.info(
                    f"dc_member 分周写入: {ts_code}, {week_start} ~ {week_end}, "
                    f"拉取 {len(week_rows)} 条, 写入 {inserted} 条"
                )
                total += inserted
                time.sleep(0.1)
        logger.info(f"dc_sector_members 增量写入完成: 共写入 {total} 条")

    def find_dc_sectors_date_range(
        self,
        codes: Optional[List[str]] = None,
    ) -> Dict[str, tuple[date, date]]:
        """查询 dc_sectors，按 ts_code 分组返回各板块 (最早, 最晚) trade_date。

        Args:
            codes: 可选板块代码列表；为空则查询全部。
        """
        with get_db() as conn:
            if codes is not None:
                if not codes:
                    return {}
                placeholders = ",".join("?" * len(codes))
                rows = conn.execute(
                    f"""SELECT ts_code,
                               MIN(trade_date) AS min_date,
                               MAX(trade_date) AS max_date
                        FROM dc_sectors
                        WHERE is_deleted = 0
                          AND ts_code IN ({placeholders})
                        GROUP BY ts_code
                        ORDER BY ts_code""",
                    codes,
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT ts_code,
                              MIN(trade_date) AS min_date,
                              MAX(trade_date) AS max_date
                       FROM dc_sectors
                       WHERE is_deleted = 0
                       GROUP BY ts_code
                       ORDER BY ts_code"""
                ).fetchall()
        result: Dict[str, tuple[date, date]] = {}
        for row in rows:
            if not row["min_date"] or not row["max_date"]:
                continue
            result[row["ts_code"]] = (
                datetime.strptime(row["min_date"], "%Y-%m-%d").date(),
                datetime.strptime(row["max_date"], "%Y-%m-%d").date(),
            )
        return result

    def find_dc_members_by_date(
        self,
        sector_code: str,
        trade_date: date,
    ) -> List[str]:
        """查询指定板块在某交易日的成分股代码列表。"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT con_code
                   FROM dc_sector_members
                   WHERE is_deleted = 0
                     AND ts_code = ?
                     AND trade_date = ?
                   ORDER BY con_code""",
                (sector_code, trade_date.isoformat()),
            ).fetchall()
        return [row["con_code"] for row in rows]

    def _load_latest_dc_member_dates(self) -> Dict[str, date]:
        """查询 dc_sector_members，按 ts_code 分组取各板块最新 trade_date。"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT ts_code, MAX(trade_date) AS max_date
                   FROM dc_sector_members
                   WHERE is_deleted = 0
                   GROUP BY ts_code"""
            ).fetchall()
        result: Dict[str, date] = {}
        for row in rows:
            if not row["max_date"]:
                continue
            result[row["ts_code"]] = datetime.strptime(row["max_date"], "%Y-%m-%d").date()
        return result

    # def _load_dc_sector_codes(self) -> List[str]:
    #     """查询 dc_sectors 中去重后的 ts_code。"""
    #     with get_db() as conn:
    #         rows = conn.execute(
    #             """SELECT DISTINCT ts_code
    #                FROM dc_sectors
    #                WHERE is_deleted = 0
    #                ORDER BY ts_code"""
    #         ).fetchall()
    #     return [row["ts_code"] for row in rows]

    def _save_dc_sector_members(self, rows: List[DCSectorMemberData]) -> int:
        if not rows:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT INTO dc_sector_members (
                       trade_date, ts_code, con_code, name,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(trade_date, ts_code, con_code) DO NOTHING""",
                [
                    (
                        item.trade_date.isoformat(),
                        item.ts_code,
                        item.con_code,
                        item.name,
                        now,
                        now,
                    )
                    for item in rows
                ],
            )
            return conn.total_changes - before

# UNUSED: 旧 sectors 快照写入链，仅被 _update_from_adapter 使用
#     def _min_sector_updated_date(self) -> Optional[date]:
#         """各板块最新更新时间中的最早值；无数据返回 None。"""
#         with get_db() as conn:
#             row = conn.execute(
#                 """SELECT MIN(updated_at) AS min_updated
#                    FROM sectors WHERE is_deleted = 0"""
#             ).fetchone()
#             min_updated = row["min_updated"] if row else None
#             if not min_updated:
#                 return None
#             return datetime.strptime(min_updated[:19], "%Y-%m-%d %H:%M:%S").date()
#
#     def _save_histories(self, histories: List[SectorHistory]) -> None:
#         if not histories:
#             logger.warning("警告：没有板块数据可保存")
#             return
#
#         now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#         codes = [h.latest[1].code for h in histories if h.latest]
#         replace_codes: List[str] = []
#         pending: List[Sector] = []
#         all_logs: List[SectorChangeLog] = []
#         skipped = 0
#
#         with get_db() as conn:
#             db_sectors = self._fetch_sectors_by_codes(conn, codes)
#
#             for history in histories:
#                 latest_entry = history.latest
#                 first_entry = history.first
#                 if not latest_entry or not first_entry:
#                     skipped += 1
#                     continue
#
#                 since, _ = first_entry
#                 _, latest = latest_entry
#                 db_sector = db_sectors.get(latest.code)
#                 records = history.get_records_since(since)
#
#                 # 终点 sign 相同且窗口内无中间变迁时才跳过，避免 A→B→A 被漏记
#                 if db_sector and db_sector.sign == latest.sign and len(records) <= 1:
#                     skipped += 1
#                     continue
#
#                 base = db_sector.version if db_sector else 0
#                 version_by_date = {
#                     trade_date: base + i
#                     for i, (trade_date, _) in enumerate(records)
#                 }
#                 latest.version = version_by_date[records[-1][0]]
#
#                 logs = history.get_change_logs(start_date=since)
#                 for log in logs:
#                     if log.changed_at:
#                         log.version = version_by_date[log.changed_at.date()]
#
#                 if db_sector:
#                     replace_codes.append(latest.code)
#                 pending.append(latest)
#                 all_logs.extend(logs)
#
#             self._delete_sectors_and_members(conn, replace_codes)
#             self._insert_sectors(conn, pending, now)
#             self._insert_change_logs(conn, all_logs, now)
#
#         logger.info(
#             f"板块数据已保存到数据库，新增/更新 {len(pending)} 个，"
#             f"跳过 {skipped} 个，添加变更记录 {len(all_logs)} 条"
#         )
#
#     def _fetch_sectors_by_codes(self, conn, codes: List[str]) -> Dict[str, Sector]:
#         if not codes:
#             return {}
#
#         placeholders = ",".join("?" * len(codes))
#         rows = conn.execute(
#             f"""SELECT code, name, type, sign, version
#                 FROM sectors
#                 WHERE code IN ({placeholders}) AND is_deleted = 0""",
#             codes,
#         ).fetchall()
#         if not rows:
#             return {}
#
#         member_rows = conn.execute(
#             f"""SELECT sector_code, stock_code FROM sector_members
#                 WHERE sector_code IN ({placeholders}) AND is_deleted = 0
#                 ORDER BY sector_code, stock_code""",
#             codes,
#         ).fetchall()
#         members_by_code: Dict[str, List[str]] = defaultdict(list)
#         for row in member_rows:
#             members_by_code[row["sector_code"]].append(row["stock_code"])
#
#         sectors: Dict[str, Sector] = {}
#         for row in rows:
#             sector = Sector(
#                 code=row["code"],
#                 name=row["name"],
#                 type=SectorType(row["type"]),
#                 version=row["version"],
#                 members=members_by_code[row["code"]],
#             )
#             sector._sign = row["sign"]
#             sectors[row["code"]] = sector
#         return sectors
#
#     def _delete_sectors_and_members(self, conn, codes: List[str]) -> None:
#         if not codes:
#             return
#         placeholders = ",".join("?" * len(codes))
#         conn.execute(
#             f"DELETE FROM sector_members WHERE sector_code IN ({placeholders})",
#             codes,
#         )
#         conn.execute(
#             f"DELETE FROM sectors WHERE code IN ({placeholders})",
#             codes,
#         )
#
#     def _insert_sectors(self, conn, sectors: List[Sector], now: str) -> None:
#         if not sectors:
#             return
#         conn.executemany(
#             """INSERT INTO sectors
#                (code, name, type, sign, version, created_at, updated_at)
#                VALUES (?, ?, ?, ?, ?, ?, ?)""",
#             [
#                 (
#                     sector.code,
#                     sector.name,
#                     sector.type.value,
#                     sector.sign,
#                     sector.version,
#                     now,
#                     now,
#                 )
#                 for sector in sectors
#             ],
#         )
#         member_rows = [
#             (sector.code, member_code, now, now)
#             for sector in sectors
#             for member_code in sector.members
#         ]
#         if member_rows:
#             conn.executemany(
#                 """INSERT INTO sector_members
#                    (sector_code, stock_code, created_at, updated_at)
#                    VALUES (?, ?, ?, ?)""",
#                 member_rows,
#             )
#
#     def _insert_change_logs(self, conn, logs: List[SectorChangeLog], now: str) -> None:
#         if not logs:
#             return
#         conn.executemany(
#             """INSERT INTO sector_change_logs
#                (sector_code, action, old_value, new_value, version,
#                 changed_at, created_at)
#                VALUES (?, ?, ?, ?, ?, ?, ?)""",
#             [
#                 (
#                     log.sector_code,
#                     log.action.value,
#                     log.old_value,
#                     log.new_value,
#                     log.version,
#                     (
#                         log.changed_at.strftime("%Y-%m-%d %H:%M:%S")
#                         if log.changed_at
#                         else now
#                     ),
#                     now,
#                 )
#                 for log in logs
#             ],
#         )

    def find_by_code(self, code: str) -> Optional[Sector]:
        """根据板块代码查询板块信息及成分股"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT code, name, type, version
                   FROM sectors
                   WHERE code = ? AND is_deleted = 0""",
                (code,),
            ).fetchone()
            if not row:
                return None

            member_rows = conn.execute(
                """SELECT stock_code
                   FROM sector_members
                   WHERE sector_code = ? AND is_deleted = 0
                   ORDER BY stock_code""",
                (code,),
            ).fetchall()
            members = [r["stock_code"] for r in member_rows]

            return Sector(
                code=row["code"],
                name=row["name"],
                type=SectorType(row["type"]),
                version=row["version"],
                members=members,
            )

    def find_by_codes(self, codes: Optional[List[str]]) -> List[Sector]:
        """根据一组板块代码批量查询板块信息及成分股"""

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

            sectors = []
            for row in rows:
                member_rows = conn.execute(
                    """SELECT stock_code
                       FROM sector_members
                       WHERE sector_code = ? AND is_deleted = 0
                       ORDER BY stock_code""",
                    (row["code"],),
                ).fetchall()
                members = [r["stock_code"] for r in member_rows]

                sectors.append(Sector(
                    code=row["code"],
                    name=row["name"],
                    type=SectorType(row["type"]),
                    version=row["version"],
                    members=members,
                ))

            return sectors

    def find_all(self) -> List[Sector]:
        """获取所有板块信息及成分股"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, name, type, version
                   FROM sectors
                   WHERE is_deleted = 0
                   ORDER BY code"""
            ).fetchall()

            sectors = []
            for row in rows:
                member_rows = conn.execute(
                    """SELECT stock_code
                       FROM sector_members
                       WHERE sector_code = ? AND is_deleted = 0
                       ORDER BY stock_code""",
                    (row["code"],),
                ).fetchall()
                members = [r["stock_code"] for r in member_rows]

                sectors.append(Sector(
                    code=row["code"],
                    name=row["name"],
                    type=SectorType(row["type"]),
                    version=row["version"],
                    members=members,
                ))

            return sectors

# UNUSED: find_sector_histories / _fetch_change_logs_by_codes 不可达
#     def find_sector_histories(
#         self,
#         codes: Optional[List[str]] = None,
#     ) -> Dict[str, SectorHistory]:
#         """
#             按板块代码查询最新 Sector 与 change logs，组装为 SectorHistory。
#             codes 为空时查询全部板块。
#         """
#         with get_db() as conn:
#             if codes is None:
#                 rows = conn.execute(
#                     """SELECT code FROM sectors
#                        WHERE is_deleted = 0
#                        ORDER BY code"""
#                 ).fetchall()
#                 codes = [row["code"] for row in rows]
#             if not codes:
#                 return {}
#
#             sectors = self._fetch_sectors_by_codes(conn, codes)
#             logs_by_code = self._fetch_change_logs_by_codes(conn, list(sectors.keys()))
#
#         return {
#             code: SectorHistory.from_change_logs(
#                 sector, logs_by_code.get(code, []),
#             )
#             for code, sector in sectors.items()
#         }
#
#     def _fetch_change_logs_by_codes(
#         self,
#         conn,
#         codes: List[str],
#     ) -> Dict[str, List[SectorChangeLog]]:
#         if not codes:
#             return {}
#
#         placeholders = ",".join("?" * len(codes))
#         rows = conn.execute(
#             f"""SELECT sector_code, action, old_value, new_value, version,
#                        changed_at, created_at
#                 FROM sector_change_logs
#                 WHERE sector_code IN ({placeholders})
#                 ORDER BY sector_code, version, id""",
#             codes,
#         ).fetchall()
#
#         logs_by_code: Dict[str, List[SectorChangeLog]] = defaultdict(list)
#         for row in rows:
#             changed_raw = row["changed_at"] or row["created_at"]
#             logs_by_code[row["sector_code"]].append(SectorChangeLog(
#                 sector_code=row["sector_code"],
#                 action=SectorChangeAction(row["action"]),
#                 old_value=row["old_value"] or "",
#                 new_value=row["new_value"] or "",
#                 version=row["version"],
#                 changed_at=(
#                     datetime.strptime(changed_raw[:19], "%Y-%m-%d %H:%M:%S")
#                     if changed_raw
#                     else None
#                 ),
#                 created_at=(
#                     datetime.strptime(row["created_at"][:19], "%Y-%m-%d %H:%M:%S")
#                     if row["created_at"]
#                     else None
#                 ),
#             ))
#         return logs_by_code
