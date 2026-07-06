import tushare as ts
import pandas as pd
from datetime import date, datetime
from typing import List, Dict, Optional
from domain.stock import Stock
from domain.money_flow import MoneyFlow
from domain.daily_quote import DailyQuote
from stock_data_adapter import StockDataAdapter


class TushareAdapter(StockDataAdapter):
    """基于 Tushare Pro 的数据适配器"""

    def __init__(self, token: str):
        """
        初始化 Tushare 适配器
        Args:
            token: Tushare Pro 接口 Token（在 https://tushare.pro 注册获取）
        """
        ts.set_token(token)
        self._pro = ts.pro_api()

    # ========== 1. 获取全市场股票信息 ==========

    def get_all_stock_info(self) -> List[Stock]:
        """
        获取全市场股票信息
        接口: stock_basic
        积分要求: 2000
        """
        try:
            df = self._pro.stock_basic(
                exchange='',
                list_status='L',  # 仅上市状态
                fields='ts_code,symbol,name,market,exchange'
            )
            if df is None or df.empty:
                return []

            stocks = []
            for _, row in df.iterrows():
                # ts_code 格式如 '000001.SZ'，提取纯数字代码
                code = row['symbol']
                market = self._infer_market(code)
                stocks.append(Stock(
                    code=code,
                    name=row['name'],
                    market=market,
                    # Tushare 的 stock_basic 不直接返回 index_code，可留空或后续补充
                ))
            return stocks
        except Exception as e:
            print(f"获取股票列表失败: {e}")
            return []

    def _infer_market(self, code: str) -> str:
        """根据股票代码推断市场"""
        if code.startswith('6'):
            return 'SH'
        elif code.startswith('0') or code.startswith('3'):
            return 'SZ'
        elif code.startswith('8') or code.startswith('4'):
            return 'BJ'
        return 'UNKNOWN'

    # ========== 2. 获取板块成分股 ==========

    def get_stock_sectors(self, stock_code: str) -> List[Dict[str, str]]:
        """
        获取股票所属板块列表
        注意：Tushare 的 tdx_member 接口需要传入板块代码，无法通过个股反查。
        因此此方法返回空列表，建议通过 get_sector_constituents 按板块查询。
        积分要求: 6000
        """
        # Tushare 暂不支持通过个股反查所属板块
        # 如需此功能，可考虑使用 dc_member 或 tdx_member 遍历所有板块
        return []

    def get_sector_members(self, sector_code: str, trade_date: str) -> List[str]:
        """
        获取指定板块的成分股列表
        接口: tdx_member
        积分要求: 6000
        Args:
            sector_code: 板块代码，如 '880728.TDX'
            trade_date: 交易日期，格式 'YYYYMMDD'
        """
        try:
            df = self._pro.tdx_member(
                ts_code=sector_code,
                trade_date=trade_date,
                fields='con_code'
            )
            if df is None or df.empty:
                return []
            return df['con_code'].tolist()
        except Exception as e:
            print(f"获取板块 {sector_code} 成分股失败: {e}")
            return []

    # ========== 3. 获取日级资金流向 ==========

    def get_daily_flow(self, code: str, start_date: date, end_date: date) -> List[MoneyFlow]:
        """
        获取个股日级资金流向
        接口: moneyflow_dc
        积分要求: 5000
        数据开始于 2023-09-11[reference:6]
        """
        try:
            df = self._pro.moneyflow_dc(
                ts_code=self._to_ts_code(code),
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d')
            )
            if df is None or df.empty:
                return []

            flows = []
            for _, row in df.iterrows():
                # 解析日期
                dt = datetime.strptime(row['trade_date'], '%Y%m%d')
                flow = MoneyFlow.daily(
                    code=code,
                    date=dt,
                    main_net=row.get('net_amount', 0.0),           # 主力净流入（万元）[reference:7]
                    main_net_pct=row.get('net_amount_rate', 0.0),  # 主力净流入占比[reference:8]
                    net_amount=row.get('net_amount', 0.0),         # 净主动买入额
                    super_large_net=row.get('buy_elg_amount'),     # 超大单净流入[reference:9]
                    large_net=row.get('buy_lg_amount'),            # 大单净流入[reference:10]
                    medium_net=row.get('buy_md_amount'),           # 中单净流入[reference:11]
                    small_net=row.get('buy_sm_amount')             # 小单净流入[reference:12]
                )
                flows.append(flow)
            return flows
        except Exception as e:
            print(f"获取股票 {code} 资金流向失败: {e}")
            return []

    # ========== 4. 获取分钟级资金流向 ==========

    def get_minute_flow(self, code: str, dt: date) -> List[MoneyFlow]:
        """
        获取分钟级资金流向
        注意：Tushare 的 rt_min 接口仅返回 OHLCV 数据[reference:13]，
        不直接提供资金流向统计。如需分钟级资金流向，
        需要配合逐笔数据或 Level2 数据自行计算。
        接口: rt_min（独立付费权限）[reference:14]
        """
        # Tushare 的 rt_min 返回的是分钟 K 线，不是资金流向
        # 如需分钟级资金流向，建议使用 tick 或逐笔数据自行计算
        print(f"警告: Tushare 的 rt_min 接口不提供资金流向统计，仅返回 OHLCV 数据")
        return []

    # ========== 5. 获取日线行情 ==========

    def get_daily_quote(self, code: str, start_date: date, end_date: date) -> List[DailyQuote]:
        """
        获取个股日线行情
        接口: daily
        积分要求: 120[reference:15]
        """
        try:
            df = self._pro.daily(
                ts_code=self._to_ts_code(code),
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d')
            )
            if df is None or df.empty:
                return []

            quotes = []
            for _, row in df.iterrows():
                dt = datetime.strptime(row['trade_date'], '%Y%m%d').date()
                quote = DailyQuote(
                    code=code,
                    date=dt,
                    open=row.get('open', 0.0),
                    high=row.get('high', 0.0),
                    low=row.get('low', 0.0),
                    close=row.get('close', 0.0),
                    volume=row.get('vol', 0),      # 成交量（手）[reference:16]
                    amount=row.get('amount', 0.0), # 成交额（千元）[reference:17]
                    pct_chg=row.get('pct_chg', 0.0)# 涨跌幅[reference:18]
                )
                quotes.append(quote)
            return quotes
        except Exception as e:
            print(f"获取股票 {code} 日线行情失败: {e}")
            return []

    # ========== 辅助方法 ==========

    @staticmethod
    def _to_ts_code(code: str) -> str:
        """
        将纯数字代码转换为 Tushare 格式
        e.g. '000001' -> '000001.SZ'
        """
        code = code.zfill(6)
        if code.startswith('6'):
            return f"{code}.SH"
        elif code.startswith('0') or code.startswith('3'):
            return f"{code}.SZ"
        elif code.startswith('8') or code.startswith('4'):
            return f"{code}.BJ"
        return code