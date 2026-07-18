import time
from datetime import datetime
from typing import Dict, List, Optional

from domain.stock import Stock
from infra.adapters import efinance_adapter
from infra.database.connection import get_db


class StockRepository:
    """股票数据仓库，管理 stocks 表"""

    _CACHE_TTL_SECONDS = 24 * 60 * 60  # 缓存有效期：1 天
    
    def __init__(self):
        self._adapter = efinance_adapter

    def refresh(self, force: bool = False) -> None:
        """同步外部数据到数据库
        
        Args:
            force: 是否强制刷新（无视缓存有效期）
        """
        if not force and self._latest():
            print("数据库缓存有效，跳过刷新")
            return
        self._update_from_adapter()

    def _latest(self) -> bool:
        """检查数据库中是否有在缓存有效期内的数据"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS cnt, MAX(updated_at) AS max_updated
                   FROM stocks WHERE is_deleted = 0"""
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
        """从适配器获取全市场股票，增量更新到数据库"""
        stocks = self._adapter.get_all_stock_info()
        if not stocks:
            print("获取股票列表失败")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            # 逐条判断：有则更新（恢复 is_deleted = 0），无则插入
            for stock in stocks:
                existing = conn.execute(
                    """SELECT 1 FROM stocks WHERE code = ?""",
                    (stock.code,),
                ).fetchone()
                if existing:
                    conn.execute(
                        """UPDATE stocks
                           SET name = ?, market = ?, updated_at = ?, is_deleted = 0
                           WHERE code = ?""",
                        (stock.name, stock.market, now, stock.code),
                    )
                else:
                    conn.execute(
                        """INSERT INTO stocks (code, name, market, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (stock.code, stock.name, stock.market, now, now),
                    )
        print(f"从适配器获取到 {len(stocks)} 只股票，并已存入数据库")

    def find_all(self) -> List[Stock]:
        """查询所有未删除的股票"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, name, market
                   FROM stocks
                   WHERE is_deleted = 0
                   ORDER BY code"""
            ).fetchall()
            return [Stock(code=row["code"], name=row["name"], market=row["market"]) for row in rows]

    def find_by_code(self, code: str) -> Optional[Stock]:
        """根据股票代码查询"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT code, name, market
                   FROM stocks
                   WHERE code = ? AND is_deleted = 0""",
                (code,),
            ).fetchone()
            if row is None:
                return None
            return Stock(code=row["code"], name=row["name"], market=row["market"])

    def get_all_codes(self) -> List[str]:
        """获取所有股票代码列表"""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code FROM stocks
                   WHERE is_deleted = 0
                   ORDER BY code"""
            ).fetchall()
            return [row["code"] for row in rows]
