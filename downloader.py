"""数据下载器：从外部接口下载股票 / 板块 / 资金流向数据到本地数据库"""

import time
from typing import List, Optional

from infra.container import container
from infra.database.schema import init_db
from infra.log import logger

SEPARATOR = "=" * 50


class Downloader:
    """数据下载器，通过 IoC 容器获取 Repository 执行数据下载"""

    def __init__(self):
        self._stock_repo = container.stock_repo()
        self._sector_repo = container.sector_repo()
        self._money_flow_repo = container.money_flow_repo()
        self._aggregator = container.money_flow_aggregator()

    # ── 数据库初始化 ──────────────────────────────────────────

    @staticmethod
    def init_database() -> None:
        init_db()
        logger.info("数据库初始化完成!")

    # ── 步骤方法 ──────────────────────────────────────────────

    def download_stocks(self) -> List:
        """下载股票数据到数据库"""
        logger.info(f"\n{SEPARATOR}")
        logger.info("1. 下载股票数据")
        logger.info(SEPARATOR)

        self._stock_repo.refresh(force=True)
        stocks = self._stock_repo.find_all()
        logger.info(f"\n共下载 {len(stocks)} 只股票")
        if stocks:
            logger.info("前5只股票：")
            for stock in stocks[:5]:
                logger.info(f"  {stock.code} - {stock.name} ({stock.market})")
        return stocks

    def download_sectors(self, stock_codes: Optional[List[str]] = None) -> None:
        """下载板块数据到数据库"""
        logger.info(f"\n{SEPARATOR}")
        logger.info("2. 下载板块数据")
        logger.info(SEPARATOR)

        try:
            self._sector_repo.refresh(stock_codes, force=True)
            sectors = self._sector_repo.find_all()
            logger.info(f"共下载 {len(sectors)} 个板块")
            if sectors:
                logger.info("前5个板块：")
                for sector in sectors[:5]:
                    member_count = len(sector.members)
                    logger.info(
                        f"  {sector.code} - {sector.name} ({sector.type.value}) - "
                        f"{member_count} 只成分股"
                    )
        except ValueError as e:
            logger.error(f"下载板块数据失败: {e}")

    def download_money_flows(self, stock_codes: Optional[List[str]] = None) -> None:
        """下载资金流向数据到数据库"""
        logger.info(f"\n{SEPARATOR}")
        logger.info("3. 下载资金流向数据")
        logger.info(SEPARATOR)

        self._money_flow_repo.refresh(stock_codes, force=True)

    def aggregate_money_flows(self, stock_codes: Optional[List[str]] = None) -> None:
        """聚合资金流向数据（计算累计净流入）"""
        logger.info(f"\n{SEPARATOR}")
        logger.info("4. 聚合资金流向数据")
        logger.info(SEPARATOR)

        self._aggregator.aggregate_all(stock_codes)

    # ── 完整流程 ──────────────────────────────────────────────

    def download_all(self, stock_codes: Optional[List[str]] = None) -> List:
        """执行完整数据下载流程

        Args:
            stock_codes: 可选的股票代码列表，为 None 则全量下载

        Returns:
            下载完成后的股票列表
        """
        start_time = time.time()

        self.init_database()

        # 第1步：下载股票
        stocks = self.download_stocks()
        codes = stock_codes or [stock.code for stock in stocks]

        # 第2步：下载板块
        self.download_sectors(codes)

        # 第3步：下载资金流向
        self.download_money_flows(codes)

        # 第4步：聚合资金流向
        self.aggregate_money_flows(codes)

        elapsed = time.time() - start_time
        logger.info(f"\n{SEPARATOR}")
        logger.info(f"数据下载完成，耗时 {elapsed:.2f} 秒")

        return stocks


# 便捷函数
_default_downloader: Optional[Downloader] = None


def download_all(stock_codes: Optional[List[str]] = None) -> List:
    """使用默认 Downloader 执行数据下载"""
    global _default_downloader
    if _default_downloader is None:
        _default_downloader = Downloader()
    return _default_downloader.download_all(stock_codes)
