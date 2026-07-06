# main.py
from domain.stock_repository import StockRepository
from domain.sector_repository import SectorRepository
from domain.stock_flow_repository import StockFlowRepository

def main():
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
    
    # 检查缓存文件是否存在
    cache_path = stock_repo._cache_path
    if cache_path.exists():
        print(f"\n缓存文件已保存到: {cache_path}")
        print(f"文件大小: {cache_path.stat().st_size} 字节")
    else:
        print("\n缓存文件未找到，请检查保存逻辑。")

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
    stock_flow_repo = StockFlowRepository()
    stock_flow_repo.refresh([stock.code for stock in stocks])

if __name__ == "__main__":
    main()