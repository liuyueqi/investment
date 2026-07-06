from datetime import date
from typing import List
from zzshare.client import DataApi

from domain.stock import Stock
from domain.money_flow import MoneyFlow
from .stock_data_adapter import StockDataAdapter

class ZzshareAdapter(StockDataAdapter):
    """zzshare 数据源适配器"""
    
    def __init__(self):
        self._api = DataApi(token='0a60bd670f1bbd495221610f8594c504db2a70661a1dea08ffe31e32fbcb84e5')
    
    def get_all_stock_info(self) -> List[Stock]:
        """获取全市场股票信息列表"""
        # TODO: 根据 zzshare API 实现全市场股票列表获取
        return []

    def get_stock_sectors(self, stock_code: str) -> List[dict]:
        """获取股票所属板块列表"""
        # TODO: 根据 zzshare API 实现个股所属板块查询
        return []
    
    def get_daily_flow(self, code: str, start_date: date, end_date: date) -> List[MoneyFlow]:
        """获取日级资金流向"""
        raw_data = self._api.stock_moneyflow(stock_id=code)
        return self._parse_moneyflow_data(code, raw_data)
    
    def get_minute_flow(self, code: str, dt: date) -> List[MoneyFlow]:
        """获取分钟级资金流向"""
        # 分钟级接口可能也需要调整参数名
        raw_data = self._api.market_mf(
            stock=code,
            date=dt.strftime('%Y%m%d')
        )
        return self._parse_moneyflow_data(code, raw_data)

    def get_daily_quote(self, code: str, start_date: date, end_date: date):
        """获取日线行情数据"""
        # TODO: 根据 zzshare API 实现日线行情获取
        return []
    
    def _parse_moneyflow_data(self, code: str, df) -> List[MoneyFlow]:
        """解析资金流向数据"""
        results = []
        
        if hasattr(df, 'iterrows'):
            for _, row in df.iterrows():
                mf = self._row_to_moneyflow(code, row)
                if mf:
                    results.append(mf)
        return results
    
    def _row_to_moneyflow(self, code: str, row) -> MoneyFlow:
        """将 DataFrame 行转换为 MoneyFlow"""
        # 字段名需要根据实际返回确认
        return MoneyFlow.daily(
            code=code,
            date=row.get('trade_date'),
            main_net=row.get('main_net_inflow', 0.0),
            main_net_pct=row.get('main_net_inflow_pct', 0.0),
            net_amount=row.get('net_amount', 0.0),
            super_large_net=row.get('super_large_net'),
            large_net=row.get('large_net'),
            medium_net=row.get('medium_net'),
            small_net=row.get('small_net')
        )