# main.py
import sys
import time
from typing import List, Optional

from domain.stock_repository import StockRepository
from domain.sector_repository import SectorRepository
from domain.money_flow_repository import MoneyFlowRepository
from infra.database.schema import init_db


def init_database() -> None:
    """初始化数据库"""
    init_db()
    print("数据库初始化完成!")


def load_stocks(stock_repo: StockRepository) -> List:
    """加载股票数据"""
    print("=" * 50)
    print("1. 加载股票数据")
    print("=" * 50)

    stock_repo.refresh(force=True)
    
    stocks = stock_repo.find_all()
    print(f"\n共加载 {len(stocks)} 只股票")
    if stocks:
        print("前5只股票：")
        for stock in stocks[:5]:
            print(f"  {stock.code} - {stock.name} ({stock.market})")
    
    return stocks


def load_sectors(sector_repo: SectorRepository, stock_codes: Optional[List[str]] = None) -> None:
    """加载板块数据"""
    print("\n" + "=" * 50)
    print("2. 加载板块数据")
    print("=" * 50)

    try:
        sector_repo.refresh(stock_codes, force=True)
        sectors = sector_repo.find_all()
        print(f"共加载 {len(sectors)} 个板块")
        if sectors:
            print("前5个板块：")
            for sector in sectors[:5]:
                member_count = len(sector.members)
                print(f"  {sector.code} - {sector.name} ({sector.type.value}) - {member_count} 只成分股")
    except ValueError as e:
        print(f"加载板块数据失败: {e}")


def load_money_flows(money_flow_repo: MoneyFlowRepository, stock_codes: Optional[List[str]] = None) -> None:
    """加载资金流向数据"""
    print("\n" + "=" * 50)
    print("3. 加载资金流向数据")
    print("=" * 50)

    money_flow_repo.refresh(stock_codes, force=True)


def query_examples(stock_repo: StockRepository, sector_repo: SectorRepository, money_flow_repo: MoneyFlowRepository) -> None:
    """查询示例"""
    print("\n" + "=" * 50)
    print("4. 查询示例")
    print("=" * 50)

    # 查询指定股票
    code = "000001"
    stock = stock_repo.find_by_code(code)
    if stock:
        print(f"\n查询股票 {code}: {stock.name} ({stock.market})")
    else:
        print(f"\n股票 {code} 未找到")

    # 查询指定板块
    sectors = sector_repo.find_all()
    if sectors:
        sample_sector = sectors[0]
        sector = sector_repo.find_by_code(sample_sector.code)
        if sector:
            print(f"\n查询板块 {sample_sector.code}: {sector.name}")
            print(f"  类型: {sector.type.value}")
            print(f"  成分股数量: {len(sector.members)}")

    # 查询资金流向
    flows = money_flow_repo.find_by_code(code)
    if flows:
        print(f"\n查询资金流向 {code}: 共 {len(flows)} 条记录")
        for flow in flows[:3]:
            print(f"  {flow.time.date()} - 主力净流入: {flow.main_net:.2f} 万元")


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
    limited_codes = stock_codes
    load_money_flows(money_flow_repo, limited_codes)

    # 第4步：查询示例
    query_examples(stock_repo, sector_repo, money_flow_repo)

    elapsed = time.time() - start_time
    print(f"\n{'=' * 50}")
    print(f"执行完成，耗时 {elapsed:.2f} 秒")


if __name__ == "__main__":
    main()