# main.py
from domain.stock_repository import StockRepository
from domain.sector_repository import SectorRepository
from domain.money_flow_repository import MoneyFlowRepository
from infra.database.schema import init_db

def main():
    
    _init_db()  # 初始化数据库

    print("初始化 StockRepository 并加载数据...")
    stock_repo = StockRepository()
    stock_repo.refresh()  # 强制刷新，验证从适配器获取并持久化
    
    # 打印一些统计信息
    stocks = stock_repo.find_all()
    print(f"\n共加载 {len(stocks)} 只股票")
    if stocks:
        print("前5只股票：")
        for stock in stocks[:5]:
            print(f"  {stock.code} - {stock.name} ({stock.market})")
    
    print("\n加载板块数据...")
    sector_repo = SectorRepository()
    try:
        sector_repo.refresh([stock.code for stock in stocks])
        sectors = sector_repo.find_all()
        print(f"共加载 {len(sectors)} 个板块")
        if sectors:
            print("前5个板块：")
            for sector in sectors[:5]:
                print(f"  {sector.code} - {sector.name} ({sector.type})")
    except ValueError as e:
        print(f"加载板块数据失败: {e}")

    print("\n加载资金流入数据...")
    stock_flow_repo = MoneyFlowRepository()
    stock_flow_repo.refresh(force=True)  # 强制刷新，验证从适配器获取并持久化

def _init_db():
    """初始化数据库"""
    init_db()
    print("数据库初始化完成!")

if __name__ == "__main__":
    main()