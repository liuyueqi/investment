import time
from datetime import datetime
from typing import List, Optional

from domain.stock import Stock
from infra.adapters.tushare_adapter import TushareAdapter
from infra.database.connection import get_db
from infra.log import logger


class StockRepository:
    """股票数据仓库，管理 stocks 表"""

    _CACHE_TTL_SECONDS = 24 * 60 * 60  # 缓存有效期：1 天
    
    def __init__(self, adapter: TushareAdapter):
        self._adapter = adapter

    def refresh(self, force: bool = False) -> None:
        """同步外部数据到数据库
        
        Args:
            force: 是否强制刷新（无视缓存有效期）
        """
        if not force and self._latest():
            logger.info("数据库缓存有效，跳过刷新")
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
            if not max_updated:
                return False
            updated_dt = datetime.strptime(max_updated, "%Y-%m-%d %H:%M:%S")
            return (time.time() - updated_dt.timestamp()) < self._CACHE_TTL_SECONDS

    def _update_from_adapter(self) -> None:
        """从适配器获取全市场股票，增量更新到数据库"""
        stocks = self._adapter.get_all_stocks()
        if not stocks:
            logger.warning("获取股票列表失败")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT INTO stocks (code, name, market, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(code) DO UPDATE SET
                       name = excluded.name,
                       market = excluded.market,
                       updated_at = excluded.updated_at,
                       is_deleted = 0""",
                [
                    (stock.code, stock.name, stock.market, now, now)
                    for stock in stocks
                ],
            )
            changed = conn.total_changes - before
        logger.info(
            f"从适配器获取到 {len(stocks)} 只股票，写入/更新 {changed} 条"
        )
        self._mark_delisted([stock.code for stock in stocks])

    def _mark_delisted(self, listed_codes: List[str]) -> None:
        """将 Tushare 退市列表中、且当前仍有效的股票标记为删除。"""
        delisted_codes = self._adapter.get_delisted_stock_codes()
        if delisted_codes is None:
            logger.warning("未获取到退市股票列表，跳过删除")
            return

        listed = set(listed_codes)
        codes = [code for code in delisted_codes if code not in listed]
        if not codes:
            logger.info("没有需要删除的退市股票")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        deleted = 0
        with get_db() as conn:
            for i in range(0, len(codes), 400):
                batch = codes[i:i + 400]
                placeholders = ",".join("?" * len(batch))
                before = conn.total_changes
                conn.execute(
                    f"""UPDATE stocks
                        SET is_deleted = 1, updated_at = ?
                        WHERE is_deleted = 0 AND code IN ({placeholders})""",
                    [now, *batch],
                )
                deleted += conn.total_changes - before
        logger.info(f"退市股票标记删除 {deleted} 只")

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

    def find_by_codes(self, codes: Optional[List[str]]) -> List[Stock]:
        """根据一组股票代码批量查询"""
        
        if not codes:
            return []
        placeholders = ",".join("?" * len(codes))
        with get_db() as conn:
            rows = conn.execute(
                f"""SELECT code, name, market
                    FROM stocks
                    WHERE code IN ({placeholders}) AND is_deleted = 0
                    ORDER BY code""",
                codes,
            ).fetchall()
            return [Stock(code=row["code"], name=row["name"], market=row["market"]) for row in rows]

