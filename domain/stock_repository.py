import csv
import time
from pathlib import Path
from typing import Dict, List, Optional

from context import CACHE_DIR
from domain.stock import Stock
from infra.adapters import default_adapter

class StockRepository:

    CACHE_FILE = "stocks.csv"
    CACHE_TTL_SECONDS = 24 * 60 * 60  # 1 天
    
    def __init__(self):
        self._adapter = default_adapter
        self._cache_dir = CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path = self._cache_dir / self.CACHE_FILE
        self._stocks: Optional[Dict[str, Stock]] = None
    
    def _loaded(self) -> bool:
        return self._stocks is not None

    def _latest(self) -> bool:
        if not self._cache_path.exists():
            return False
        return (time.time() - self._cache_path.stat().st_mtime) < self.CACHE_TTL_SECONDS

    def refresh(self, sync: bool = False) -> None:
        """同步外部数据到本地 CSV，并将结果加载到内存"""
        if not sync and self._latest():
            self._load_from_csv()

        self._update_from_adapter()

    def _load_from_csv(self) -> None:
        if not self._cache_path.exists():
            return

        try:
            with open(self._cache_path, 'r', encoding='utf-8', newline='') as f:
                reader = csv.reader(f)
                rows = list(reader)
                if len(rows) < 2:
                    print("CSV 缓存文件为空或格式不正确，将重新构建")
                    return

                stock_map: Dict[str, Stock] = {}
                for row in rows[1:]:
                    if len(row) >= 3:
                        stock = Stock(code=row[0], name=row[1], market=row[2])
                        stock_map[stock.code] = stock
                    else:
                        print(f"CSV 行格式不正确，跳过: {row}")

                self._stocks = stock_map
                print(f"从 CSV 缓存加载了 {len(stock_map)} 只股票")
        except Exception as e:
            print(f"读取股票缓存失败: {e}")

    def _update_from_adapter(self) -> None:
        stocks = self._adapter.get_all_stock_info()
        if not stocks:
            print("获取股票列表失败，保留现有缓存数据")
            if self._stocks is None:
                self._stocks = {}
            return

        self._stocks = {stock.code: stock for stock in stocks}
        self._save_to_csv(stocks)
        print(f"从适配器获取到 {len(self._stocks)} 只股票，并已保存缓存")

    def _save_to_csv(self, stocks: List[Stock]) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        with open(self._cache_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['stock_code', 'stock_name', 'market'])
            for stock in stocks:
                writer.writerow([stock.code, stock.name, stock.market])

        print(f"股票缓存已保存到 {self._cache_path}")

    def find_all(self) -> List[Stock]:
        if not self._loaded():
            self.refresh()
        return list((self._stocks or {}).values())

    def find_by_code(self, code: str) -> Optional[Stock]:
        if not self._loaded():
            self.refresh()
        return (self._stocks or {}).get(code)

    def get_all_codes(self) -> List[str]:
        return list((self._stocks or {}).keys())