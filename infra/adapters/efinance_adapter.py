import efinance as ef
from datetime import date, datetime
from typing import List, Dict, Optional
import pandas as pd

from domain.stock import Stock
from domain.money_flow import MoneyFlow
from domain.daily_quote import DailyQuote
from .stock_data_adapter import StockDataAdapter
from infra.log import logger

class EfinanceAdapter(StockDataAdapter):
    """基于 efinance 的数据适配器"""
    
    def get_all_stock_info(self) -> List[Stock]:
        """通过上证指数 + 深证综指获取沪深全部股票信息"""
        stock_map = {}  # 用字典去重（代码 -> Stock）
        
        index_codes = ['000985']
        for idx in index_codes:
            try:
                df = ef.stock.get_members(idx)
                if df is None or df.empty:
                    logger.warning(f"获取指数 {idx} 成分股失败")
                    continue
                
                # 确定列名
                code_col = '股票代码' if '股票代码' in df.columns else '代码'
                name_col = '股票名称' if '股票名称' in df.columns else '名称'
                
                for _, row in df.iterrows():
                    code = str(row[code_col]).zfill(6)
                    # 如果已经存在，跳过（去重）
                    if code in stock_map:
                        continue
                    name = row[name_col]
                    market = self._infer_market(code)
                    stock_map[code] = Stock(code=code, name=name, market=market)
                    
            except Exception as e:
                logger.error(f"获取指数 {idx} 成分股异常: {e}")
                continue
        
        return list(stock_map.values())
    
    def get_stock_sectors(self, stock_code: str) -> List[Dict[str, str]]:
        try:
            df = ef.stock.get_belong_board(stock_code)
            if df is None or df.empty:
                return []
            return [
                {'code': row['板块代码'], 'name': row['板块名称']}
                for _, row in df.iterrows()
            ]
        except Exception as e:
            logger.warning(f"获取 {stock_code} 所属板块失败: {e}")
            return []
    
    @staticmethod
    def _infer_market(code: str) -> str:
        if code.startswith('6'):
            return 'SH'
        elif code.startswith(('0', '3')):
            return 'SZ'
        elif code.startswith(('8', '4')):
            return 'BJ'
        return 'UNKNOWN'