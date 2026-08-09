import time
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional

from domain.sector import Sector, SectorType
from domain.sector_change_log import SectorChangeAction, SectorChangeLog
from domain.sector_history import SectorHistory
from infra.adapters.tushare_adapter import TushareAdapter
from infra.config import get_market_earliest_date
from infra.database.connection import get_db
from infra.log import logger


class SectorRepository:
    """板块数据仓库，管理 sectors 表和 sector_members 表"""

    _CACHE_TTL_SECONDS = 24 * 60 * 60  # 缓存有效期：1 天

    def __init__(self, adapter: TushareAdapter):
        self._adapter = adapter

    def refresh(self, force: bool = False) -> None:
        """同步外部板块快照到数据库，并写入变更日志。"""
        if not force and self._latest():
            logger.info("数据库缓存有效，跳过刷新")
            return
        self._update_from_adapter()

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
            if max_updated is None:
                return False
            updated_dt = datetime.strptime(max_updated, "%Y-%m-%d %H:%M:%S")
            return (time.time() - updated_dt.timestamp()) < self._CACHE_TTL_SECONDS

    def _update_from_adapter(self) -> None:
        start_date = self._min_sector_updated_date() or get_market_earliest_date()
        logger.info(f"开始拉取板块快照，start_date={start_date}")

        snapshots = self._adapter.get_all_sectors(start_date=start_date)
        if not snapshots:
            logger.warning("未获取到板块快照，跳过保存")
            return

        by_code: Dict[str, List[tuple[date, Sector]]] = defaultdict(list)
        for trade_date, sector in snapshots:
            by_code[sector.code].append((trade_date, sector))

        histories = [
            SectorHistory.from_snapshots(group)
            for group in by_code.values()
        ]
        self._save_histories(histories)

    def _min_sector_updated_date(self) -> Optional[date]:
        """各板块最新更新时间中的最早值；无数据返回 None。"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT MIN(updated_at) AS min_updated
                   FROM sectors WHERE is_deleted = 0"""
            ).fetchone()
            min_updated = row["min_updated"] if row else None
            if not min_updated:
                return None
            return datetime.strptime(min_updated[:19], "%Y-%m-%d %H:%M:%S").date()

    def _save_histories(self, histories: List[SectorHistory]) -> None:
        if not histories:
            logger.warning("警告：没有板块数据可保存")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        codes = [h.latest[1].code for h in histories if h.latest]
        replace_codes: List[str] = []
        pending: List[Sector] = []
        all_logs: List[SectorChangeLog] = []
        skipped = 0

        with get_db() as conn:
            db_sectors = self._fetch_sectors_by_codes(conn, codes)

            for history in histories:
                latest_entry = history.latest
                first_entry = history.first
                if not latest_entry or not first_entry:
                    skipped += 1
                    continue

                since, _ = first_entry
                _, latest = latest_entry
                db_sector = db_sectors.get(latest.code)
                records = history.get_records_since(since)

                # 终点 sign 相同且窗口内无中间变迁时才跳过，避免 A→B→A 被漏记
                if db_sector and db_sector.sign == latest.sign and len(records) <= 1:
                    skipped += 1
                    continue

                base = db_sector.version if db_sector else 0
                version_by_date = {
                    trade_date: base + i
                    for i, (trade_date, _) in enumerate(records)
                }
                latest.version = version_by_date[records[-1][0]]

                logs = history.get_change_logs(start_date=since)
                for log in logs:
                    if log.changed_at:
                        log.version = version_by_date[log.changed_at.date()]

                if db_sector:
                    replace_codes.append(latest.code)
                pending.append(latest)
                all_logs.extend(logs)

            self._delete_sectors_and_members(conn, replace_codes)
            self._insert_sectors(conn, pending, now)
            self._insert_change_logs(conn, all_logs, now)

        logger.info(
            f"板块数据已保存到数据库，新增/更新 {len(pending)} 个，"
            f"跳过 {skipped} 个，添加变更记录 {len(all_logs)} 条"
        )

    def _fetch_sectors_by_codes(self, conn, codes: List[str]) -> Dict[str, Sector]:
        if not codes:
            return {}

        placeholders = ",".join("?" * len(codes))
        rows = conn.execute(
            f"""SELECT code, name, type, sign, version
                FROM sectors
                WHERE code IN ({placeholders}) AND is_deleted = 0""",
            codes,
        ).fetchall()
        if not rows:
            return {}

        member_rows = conn.execute(
            f"""SELECT sector_code, stock_code FROM sector_members
                WHERE sector_code IN ({placeholders}) AND is_deleted = 0
                ORDER BY sector_code, stock_code""",
            codes,
        ).fetchall()
        members_by_code: Dict[str, List[str]] = defaultdict(list)
        for row in member_rows:
            members_by_code[row["sector_code"]].append(row["stock_code"])

        sectors: Dict[str, Sector] = {}
        for row in rows:
            sector = Sector(
                code=row["code"],
                name=row["name"],
                type=SectorType(row["type"]),
                version=row["version"],
                members=members_by_code[row["code"]],
            )
            sector._sign = row["sign"]
            sectors[row["code"]] = sector
        return sectors

    def _delete_sectors_and_members(self, conn, codes: List[str]) -> None:
        if not codes:
            return
        placeholders = ",".join("?" * len(codes))
        conn.execute(
            f"DELETE FROM sector_members WHERE sector_code IN ({placeholders})",
            codes,
        )
        conn.execute(
            f"DELETE FROM sectors WHERE code IN ({placeholders})",
            codes,
        )

    def _insert_sectors(self, conn, sectors: List[Sector], now: str) -> None:
        if not sectors:
            return
        conn.executemany(
            """INSERT INTO sectors
               (code, name, type, sign, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    sector.code,
                    sector.name,
                    sector.type.value,
                    sector.sign,
                    sector.version,
                    now,
                    now,
                )
                for sector in sectors
            ],
        )
        member_rows = [
            (sector.code, member_code, now, now)
            for sector in sectors
            for member_code in sector.members
        ]
        if member_rows:
            conn.executemany(
                """INSERT INTO sector_members
                   (sector_code, stock_code, created_at, updated_at)
                   VALUES (?, ?, ?, ?)""",
                member_rows,
            )

    def _insert_change_logs(self, conn, logs: List[SectorChangeLog], now: str) -> None:
        if not logs:
            return
        conn.executemany(
            """INSERT INTO sector_change_logs
               (sector_code, action, old_value, new_value, version,
                changed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    log.sector_code,
                    log.action.value,
                    log.old_value,
                    log.new_value,
                    log.version,
                    (
                        log.changed_at.strftime("%Y-%m-%d %H:%M:%S")
                        if log.changed_at
                        else now
                    ),
                    now,
                )
                for log in logs
            ],
        )

    def find_by_code(self, code: str) -> Optional[Sector]:
        """根据板块代码查询板块信息及成分股"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT code, name, type, version
                   FROM sectors
                   WHERE code = ? AND is_deleted = 0""",
                (code,),
            ).fetchone()
            if row is None:
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

    def find_sector_histories(
        self,
        codes: Optional[List[str]] = None,
    ) -> Dict[str, SectorHistory]:
        """
            按板块代码查询最新 Sector 与 change logs，组装为 SectorHistory。
            codes 为空时查询全部板块。
        """
        with get_db() as conn:
            if codes is None:
                rows = conn.execute(
                    """SELECT code FROM sectors
                       WHERE is_deleted = 0
                       ORDER BY code"""
                ).fetchall()
                codes = [row["code"] for row in rows]
            if not codes:
                return {}

            sectors = self._fetch_sectors_by_codes(conn, codes)
            logs_by_code = self._fetch_change_logs_by_codes(conn, list(sectors.keys()))

        return {
            code: SectorHistory.from_change_logs(
                sector, logs_by_code.get(code, []),
            )
            for code, sector in sectors.items()
        }

    def _fetch_change_logs_by_codes(
        self,
        conn,
        codes: List[str],
    ) -> Dict[str, List[SectorChangeLog]]:
        if not codes:
            return {}

        placeholders = ",".join("?" * len(codes))
        rows = conn.execute(
            f"""SELECT sector_code, action, old_value, new_value, version,
                       changed_at, created_at
                FROM sector_change_logs
                WHERE sector_code IN ({placeholders})
                ORDER BY sector_code, version, id""",
            codes,
        ).fetchall()

        logs_by_code: Dict[str, List[SectorChangeLog]] = defaultdict(list)
        for row in rows:
            changed_raw = row["changed_at"] or row["created_at"]
            logs_by_code[row["sector_code"]].append(SectorChangeLog(
                sector_code=row["sector_code"],
                action=SectorChangeAction(row["action"]),
                old_value=row["old_value"] or "",
                new_value=row["new_value"] or "",
                version=row["version"],
                changed_at=(
                    datetime.strptime(changed_raw[:19], "%Y-%m-%d %H:%M:%S")
                    if changed_raw
                    else None
                ),
                created_at=(
                    datetime.strptime(row["created_at"][:19], "%Y-%m-%d %H:%M:%S")
                    if row["created_at"]
                    else None
                ),
            ))
        return logs_by_code
