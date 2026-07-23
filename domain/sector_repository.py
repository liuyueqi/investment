import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional

from domain.sector import Sector, SectorType
from infra.adapters.stock_data_adapter import StockDataAdapter
from infra.database.connection import get_db
from infra.log import logger


class SectorRepository:
    """板块数据仓库，管理 sectors 表和 sector_members 表"""

    _CACHE_TTL_SECONDS = 24 * 60 * 60  # 缓存有效期：1 天
    _CHUNK_SIZE = 100   # 每个线程处理的股票数量

    def __init__(self, adapter: StockDataAdapter, build_pool: ThreadPoolExecutor):
        self._adapter = adapter
        self._lock = threading.Lock()
        self._build_pool = build_pool

    def refresh(self, stock_codes: Optional[List[str]] = None, force: bool = False) -> None:
        """同步外部数据到数据库
        
        Args:
            stock_codes: 股票代码列表，为 None 则自动获取全市场
            force: 是否强制刷新
        """
        if not force and self._latest():
            logger.info("数据库缓存有效，跳过刷新")
            return
        self._update_from_adapter(stock_codes)

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

    def _update_from_adapter(self, stock_codes: Optional[List[str]] = None) -> None:
        """从适配器获取板块数据，并发处理各股票所属板块"""
        if stock_codes is None:
            stocks = self._adapter.get_all_stock_info()
            stock_codes = [stock.code for stock in stocks]

        if not stock_codes:
            logger.warning("获取股票代码列表失败，无法构建板块数据")
            return

        total = len(stock_codes)
        if total == 0:
            logger.warning("股票代码列表为空，无法构建板块数据")
            return

        # 并发加载：将股票代码分块，每个线程处理一块
        sectors: Dict[str, Sector] = {}
        chunks = [stock_codes[i:i + self._CHUNK_SIZE] for i in range(0, total, self._CHUNK_SIZE)]

        logger.info(f"开始构建板块数据，共 {total} 只股票，分为 {len(chunks)} 个块")
        futures = {self._build_pool.submit(self._process_chunk, chunk): chunk for chunk in chunks}
        for fut in as_completed(futures):
            try:
                local_map = fut.result()
            except Exception as e:
                logger.error(f"线程处理异常: {e}")
                continue

            with self._lock:
                for code, sector in local_map.items():
                    if code not in sectors:
                        sectors[code] = sector
                    else:
                        for c in sector.members:
                            sectors[code].add_member(c)

        self._save_to_db(sectors)
        logger.info(f"构建完成，共 {len(sectors)} 个板块，已保存到数据库")

    def _process_chunk(self, chunk: List[str]) -> Dict[str, Sector]:
        """处理一个股票代码块，返回该块构建的板块映射"""
        logger.info(f"线程 {threading.current_thread().name} 开始处理 {len(chunk)} 只股票")
        local_map: Dict[str, Sector] = {}
        for stock_code in chunk:
            sectors = self._adapter.get_stock_sectors(stock_code)
            for sector in sectors:
                code = sector['code']
                name = sector['name']
                if code not in local_map:
                    local_map[code] = Sector(
                        code=code,
                        name=name,
                        type=self._infer_type(name),
                    )
                local_map[code].add_member(stock_code)
        return local_map

    def _save_to_db(self, sectors: Dict[str, Sector]) -> None:
        """将板块数据写入数据库（sectors 表 + sector_members 表）"""
        if not sectors:
            logger.warning("警告：没有板块数据可保存")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            for sector in sectors.values():
                existing = conn.execute(
                    """SELECT 1 FROM sectors WHERE code = ?""",
                    (sector.code,),
                ).fetchone()

                if existing:
                    conn.execute(
                        """UPDATE sectors
                           SET name = ?, type = ?, updated_at = ?, is_deleted = 0
                           WHERE code = ?""",
                        (sector.name, sector.type.value, now, sector.code),
                    )
                else:
                    conn.execute(
                        """INSERT INTO sectors (code, name, type, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (sector.code, sector.name, sector.type.value, now, now),
                    )

                for member_code in sector.members:
                    existing = conn.execute(
                        """SELECT 1 FROM sector_members
                           WHERE sector_code = ? AND stock_code = ?""",
                        (sector.code, member_code),
                    ).fetchone()

                    if existing:
                        conn.execute(
                            """UPDATE sector_members
                               SET is_deleted = 0, updated_at = ?
                               WHERE sector_code = ? AND stock_code = ?""",
                            (now, sector.code, member_code),
                        )
                    else:
                        conn.execute(
                            """INSERT INTO sector_members (sector_code, stock_code, created_at, updated_at)
                               VALUES (?, ?, ?, ?)""",
                            (sector.code, member_code, now, now),
                        )

        logger.info(f"板块数据已保存到数据库，共 {len(sectors)} 个板块")

    def find_by_code(self, code: str) -> Optional[Sector]:
        """根据板块代码查询板块信息及成分股"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT code, name, type
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
                members=members,
            )

    def find_all(self) -> List[Sector]:
        """获取所有板块信息及成分股"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, name, type
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
                    members=members,
                ))

            return sectors

    @staticmethod
    def _infer_type(name: str) -> SectorType:
        """根据板块名称推断板块类型"""
        if '行业' in name or '制造' in name:
            return SectorType.INDUSTRY
        elif '概念' in name or '主题' in name:
            return SectorType.CONCEPT
        elif '地区' in name:
            return SectorType.REGION
        return SectorType.CONCEPT
