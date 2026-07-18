"""数据下载器：从外部接口下载股票 / 板块 / 资金流向数据到本地数据库"""

import time
from typing import List, Optional

from domain.stock_repository import StockRepository
from domain.sector_repository import SectorRepository
from domain.money_flow_repository import MoneyFlowRepository
from infra.container import get_container
from infra.database.schema import init_db
from infra.log import logger

SEPARATOR = "=" * 50


class Downloader:
    """数据下载器，协调 Repository 从外部接口下载数据到本地数据库"""

    def __init__(self):
        """通过 IoC 容器自动获取各 Repository 实例"""
        container = get_container()

        # 注册 Repository 到容器（消费 adapter 依赖）
        container.register(StockRepository, singleton=True)
        container.register(SectorRepository, singleton=True)
        container.register(MoneyFlowRepository, singleton=True)

        self._stock_repo = container.resolve(StockRepository)
        self._sector_repo = container.resolve(SectorRepository)
        self._money_flow_repo = container.resolve(MoneyFlowRepository)

    # ── 数据库初始化 ──────────────────────────────────────────

    @staticmethod
    def init_database() -> None:
        """初始化数据库（幂等安全，多次运行不会重复创建）"""
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

        elapsed = time.time() - start_time
        logger.info(f"\n{SEPARATOR}")
        logger.info(f"数据下载完成，耗时 {elapsed:.2f} 秒")

        return stocks


# 便捷函数，兼容旧调用方式
_default_downloader: Optional[Downloader] = None


def download_all(stock_codes: Optional[List[str]] = None) -> List:
    """便捷函数：使用默认 Downloader 执行完整数据下载

    Args:
        stock_codes: 可选的股票代码列表，为 None 则全量下载

    Returns:
        下载完成后的股票列表
    """
    global _default_downloader
    if _default_downloader is None:
        _default_downloader = Downloader()
    return _default_downloader.download_all(stock_codes)
