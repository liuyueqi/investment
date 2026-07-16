from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import List, Dict, Optional

from domain.stock import Stock
from domain.money_flow import MoneyFlow
from domain.daily_quote import DailyQuote

class StockDataAdapter(ABC):
    """股票数据提供者接口（所有适配器必须实现）"""
    
    def get_all_stock_info(self) -> List[Stock]:
        """获取全市场股票信息列表"""
        raise NotImplementedError("get_all_stock_info 方法未实现")
    
    def get_stock_sectors(self, stock_code: str) -> List[Dict[str, str]]:
        """获取股票所属板块列表"""
        raise NotImplementedError("get_stock_sectors 方法未实现")
    
    def get_sector_members(self, sector_code: str, trade_date: str) -> List[str]:
        """获取板块成员股票代码列表"""
        raise NotImplementedError("get_sector_members 方法未实现")

    def get_daily_flow(
        self,
        code: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[MoneyFlow]:
        """获取日级资金流向（已转换为 MoneyFlow 对象）"""
        raise NotImplementedError("get_daily_flow 方法未实现")
    
    def get_minute_flow(self, code: str, dt: date) -> List[MoneyFlow]:
        """获取分钟级资金流向（已转换为 MoneyFlow 对象）"""
        raise NotImplementedError("get_minute_flow 方法未实现")

    def get_daily_quote(self, code: str, start_date: date, end_date: date) -> List[DailyQuote]:
        """获取日级行情数据"""
        raise NotImplementedError("get_daily_quote 方法未实现")