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
    
    def get_daily_flow(
        self,
        code: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[MoneyFlow]:
        """
        获取日级资金流向数据
        """
        # 1. 从 efinance 获取原始数据
        logger.info(f"正在获取 {code} 的资金流向数据...")
        df = ef.stock.get_history_bill(code)
        logger.info(f"获取到 {len(df)} 条资金流向数据")
        
        # 2. 将日期列转换为 date 对象
        df['日期'] = pd.to_datetime(df['日期']).dt.date
        
        # 3. 按日期范围过滤（可选）
        if start_date is not None or end_date is not None:
            mask = True
            if start_date is not None:
                mask = mask & (df['日期'] >= start_date)
            if end_date is not None:
                mask = mask & (df['日期'] <= end_date)
            filtered_df = df.loc[mask]
        else:
            filtered_df = df
        
        # 4. 转换为 MoneyFlow 对象列表
        results: List[MoneyFlow] = []
        for _, row in filtered_df.iterrows():
            mf = MoneyFlow.daily(
                code=code,
                date=row['日期'],
                # 元 → 万元
                main_net=row['主力净流入'] / 10000,
                main_net_pct=row['主力净流入占比'],
                # efinance 暂无此字段
                net_amount=0.0,
                huge_net=row['超大单净流入'] / 10000,
                large_net=row['大单净流入'] / 10000,
                medium_net=row['中单净流入'] / 10000,
                small_net=row['小单净流入'] / 10000
            )
            results.append(mf)
        
        return results

    @staticmethod
    def _infer_market(code: str) -> str:
        if code.startswith('6'):
            return 'SH'
        elif code.startswith('0') or code.startswith('3'):
            return 'SZ'
        elif code.startswith('8') or code.startswith('4'):
            return 'BJ'
        return 'UNKNOWN'