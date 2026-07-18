# main.py
# 主程序入口：初始化数据库，加载股票 / 板块 / 资金流向数据，并执行查询示例

import time
from typing import List, Optional

from domain.stock_repository import StockRepository
from domain.sector_repository import SectorRepository
from domain.money_flow_repository import MoneyFlowRepository
from infra.database.schema import init_db
from infra.log import logger

SEPARATOR = "=" * 50


def init_database() -> None:
    """初始化数据库（幂等安全，多次运行不会重复创建）"""
    init_db()
    logger.info("数据库初始化完成!")


def load_stocks(stock_repo: StockRepository) -> List:
    """加载股票数据"""
    logger.info(f"\n{SEPARATOR}")
    logger.info("1. 加载股票数据")
    logger.info(SEPARATOR)

    stock_repo.refresh(force=True)
    
    stocks = stock_repo.find_all()
    logger.info(f"\n共加载 {len(stocks)} 只股票")
    if stocks:
        logger.info("前5只股票：")
        for stock in stocks[:5]:
            logger.info(f"  {stock.code} - {stock.name} ({stock.market})")
    
    return stocks


def load_sectors(
    sector_repo: SectorRepository,
    stock_codes: Optional[List[str]] = None,
) -> None:
    """加载板块数据"""
    logger.info(f"\n{SEPARATOR}")
    logger.info("2. 加载板块数据")
    logger.info(SEPARATOR)

    try:
        sector_repo.refresh(stock_codes, force=True)
        sectors = sector_repo.find_all()
        logger.info(f"共加载 {len(sectors)} 个板块")
        if sectors:
            logger.info("前5个板块：")
            for sector in sectors[:5]:
                member_count = len(sector.members)
                logger.info(f"  {sector.code} - {sector.name} ({sector.type.value}) - "
                      f"{member_count} 只成分股")
    except ValueError as e:
        logger.error(f"加载板块数据失败: {e}")


def load_money_flows(
    money_flow_repo: MoneyFlowRepository,
    stock_codes: Optional[List[str]] = None,
) -> None:
    """加载资金流向数据"""
    logger.info(f"\n{SEPARATOR}")
    logger.info("3. 加载资金流向数据")
    logger.info(SEPARATOR)

    money_flow_repo.refresh(stock_codes, force=True)


def query_examples(
    stock_repo: StockRepository,
    sector_repo: SectorRepository,
    money_flow_repo: MoneyFlowRepository,
) -> None:
    """查询示例"""
    logger.info(f"\n{SEPARATOR}")
    logger.info("4. 查询示例")
    logger.info(SEPARATOR)

    # 查询指定股票
    code = "000001"
    stock = stock_repo.find_by_code(code)
    if stock:
        logger.info(f"\n查询股票 {code}: {stock.name} ({stock.market})")
    else:
        logger.warning(f"\n股票 {code} 未找到")

    # 查询指定板块
    sectors = sector_repo.find_all()
    if sectors:
        sample_sector = sectors[0]
        sector = sector_repo.find_by_code(sample_sector.code)
        if sector:
            logger.info(f"\n查询板块 {sample_sector.code}: {sector.name}")
            logger.info(f"  类型: {sector.type.value}")
            logger.info(f"  成分股数量: {len(sector.members)}")

    # 查询资金流向
    flows = money_flow_repo.find_by_code(code)
    if flows:
        logger.info(f"\n查询资金流向 {code}: 共 {len(flows)} 条记录")
        for flow in flows[:3]:
            logger.info(f"  {flow.time.date()} - 主力净流入: {flow.main_net:.2f} 万元")


def main():
    """主程序入口"""
    start_time = time.time()

    init_database()

    stock_repo = StockRepository()
    sector_repo = SectorRepository()
    money_flow_repo = MoneyFlowRepository()

    # 第1步：加载股票
    stocks = load_stocks(stock_repo)
    stock_codes = [stock.code for stock in stocks]

    # 第2步：加载板块
    load_sectors(sector_repo, stock_codes)

    # 第3步：加载资金流向
    load_money_flows(money_flow_repo, stock_codes)

    # 第4步：查询示例
    query_examples(stock_repo, sector_repo, money_flow_repo)

    elapsed = time.time() - start_time
    logger.info(f"\n{SEPARATOR}")
    logger.info(f"执行完成，耗时 {elapsed:.2f} 秒")


if __name__ == "__main__":
    main()
