import csv
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

from context import CACHE_DIR
from domain.sector import Sector, SectorType
from infra.adapters import default_adapter


class SectorRepository:

    CACHE_FILE = "sectors.csv"
    CACHE_TTL_SECONDS = 24 * 60 * 60  # 1 天
    # 并发加载配置
    CHUNK_SIZE = 500  # 每个线程处理的股票数量（可调整）
    MAX_WORKERS = 8   # 最大并发线程数

    def __init__(self):
        self._adapter = default_adapter
        self._cache_dir = CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path = self._cache_dir / self.CACHE_FILE
        self._sectors: Optional[Dict[str, Sector]] = None
        # 保护对 _sectors 并发写入的锁
        self._lock = threading.Lock()

    def _loaded(self) -> bool:
        return self._sectors is not None

    def _latest(self) -> bool:
        if not self._cache_path.exists():
            return False
        return (time.time() - self._cache_path.stat().st_mtime) < self.CACHE_TTL_SECONDS

    def refresh(self, stock_codes: Optional[List[str]] = None, sync: bool = False) -> None:
        """同步外部数据到本地 CSV，并将数据加载到内存"""
        if not sync and self._latest():
            self._load_from_csv()

        self._update_from_adapter(stock_codes)

    def _load_from_csv(self) -> None:
        if not self._cache_path.exists():
            return

        try:
            with open(self._cache_path, 'r', encoding='utf-8', newline='') as f:
                reader = csv.reader(f)
                rows = list(reader)
                if len(rows) < 2:
                    print("CSV 缓存文件为空或格式不正确，将重新构建")

                sector_map: Dict[str, Sector] = {}
                for row in rows[1:]:
                    if len(row) >= 4:
                        code = row[0]
                        name = row[1]
                        type_str = row[2]
                        constituents_str = row[3]
                        constituents = [c for c in constituents_str.split(',') if c]
                        sector_map[code] = Sector(
                            code=code,
                            name=name,
                            type=SectorType(type_str),
                            members=constituents,
                        )
                    else:
                        print(f"CSV 行格式不正确，跳过: {row}")

                self._sectors = sector_map
                print(f"从 CSV 缓存加载了 {len(sector_map)} 个板块")
        except Exception as e:
            print(f"读取板块缓存失败: {e}")

    def _update_from_adapter(self, stock_codes: Optional[List[str]] = None) -> None:
        # 如果外部未传入股票列表，则尝试从适配器获取全部股票信息
        if stock_codes is None:
            stocks = self._adapter.get_all_stock_info()
            stock_codes = [stock.code for stock in stocks]

        if not stock_codes:
            print("获取股票代码列表失败，无法构建板块数据")
            self._sectors = self._sectors or {}
            return

        # 并发加载：把 stock_codes 切分为块，每个 worker 调用 adapter.get_stock_boards
        total = len(stock_codes)
        if total == 0:
            print("股票代码列表为空，无法构建板块数据")
            self._sectors = self._sectors or {}
            return

        sectors: Dict[str, Sector] = {}
        chunks: List[List[str]] = [stock_codes[i:i + self.CHUNK_SIZE] for i in range(0, total, self.CHUNK_SIZE)]
        max_workers = min(self.MAX_WORKERS, len(chunks))

        with ThreadPoolExecutor(max_workers=max_workers) as exc:
            futures = {exc.submit(self._process_chunk, chunk): chunk for chunk in chunks}
            for fut in as_completed(futures):
                try:
                    local_map = fut.result()
                except Exception as e:
                    print(f"线程处理异常: {e}")
                    continue

                # 合并到共享 sectors
                with self._lock:
                    for code, sector in local_map.items():
                        if code not in sectors:
                            sectors[code] = sector
                        else:
                            for c in sector.members:
                                sectors[code].add_constituent(c)

        self._save_to_csv(sectors)
        self._sectors = sectors
        print(f"构建完成，共 {len(self._sectors or {})} 个板块，已保存到缓存")

    def _build_from_stocks(self, stock_codes: List[str]) -> None:
        # 保留顺序处理方法（用于小规模或测试）
        sector_map: Dict[str, Sector] = {}
        for i, stock_code in enumerate(stock_codes):
            boards = self._adapter.get_stock_sectors(stock_code)
            for board in boards:
                code = board['code']
                name = board['name']
                if code not in sector_map:
                    sector_map[code] = Sector(
                        code=code,
                        name=name,
                        type=self._infer_type(name)
                    )
                sector_map[code].add_constituent(stock_code)

            if (i + 1) % 500 == 0:
                print(f"已处理 {i+1}/{len(stock_codes)} 只股票")

        self._sectors = sector_map

    def _process_chunk(self, chunk: List[str]) -> Dict[str, Sector]:
        """处理一个股票代码块，返回本地的 sector_map。"""
        local_map: Dict[str, Sector] = {}
        for stock_code in chunk:
            boards = self._adapter.get_stock_sectors(stock_code)
            for board in boards:
                code = board['code']
                name = board['name']
                if code not in local_map:
                    local_map[code] = Sector(
                        code=code,
                        name=name,
                        type=self._infer_type(name)
                    )
                local_map[code].add_constituent(stock_code)
        return local_map

    def _save_to_csv(self, sectors: Dict[str, Sector]) -> None:
        if not sectors:
            print("警告：没有板块数据可保存")
            return

        with open(self._cache_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['sector_code', 'sector_name', 'sector_type', 'constituents'])
            for sector in sectors.values():
                constituents_str = ','.join(sector.members)
                writer.writerow([
                    sector.code,
                    sector.name,
                    sector.type.value,
                    constituents_str,
                ])

        print(f"板块缓存已保存到 {self._cache_path}")

    def find_by_code(self, code: str) -> Optional[Sector]:
        if not self._loaded():
            self.refresh()
        return (self._sectors or {}).get(code)

    def find_all(self) -> List[Sector]:
        if not self._loaded():
            self.refresh()
        return list((self._sectors or {}).values())

    @staticmethod
    def _infer_type(name: str) -> SectorType:
        if '行业' in name or '制造' in name:
            return SectorType.INDUSTRY
        elif '概念' in name or '主题' in name:
            return SectorType.CONCEPT
        elif '地区' in name:
            return SectorType.REGION
        return SectorType.CONCEPT
