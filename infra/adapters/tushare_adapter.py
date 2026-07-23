import os
from pathlib import Path

import tushare as ts
from datetime import date, datetime
from typing import List, Optional
from domain.money_flow import MoneyFlow
from .stock_data_adapter import StockDataAdapter
from context import TUSHARE_TOKEN_FILE
from infra.log import logger

class TushareAdapter(StockDataAdapter):
    """基于 Tushare Pro 的数据适配器"""

    _TOKEN_FILE_ENV = 'TUSHARE_TOKEN_FILE'
    _DEFAULT_TOKEN_FILE = TUSHARE_TOKEN_FILE

    def __init__(self):
        """
        初始化 Tushare 适配器
        Args:
            token_file: 可选的 Tushare Token 文件路径。优先级：参数 > 环境变量 TUSHARE_TOKEN_FILE > 项目根目录下 .tushare_token
        """
        token = self._load_token()
        ts.set_token(token)
        self._pro = ts.pro_api()

    def _load_token(self) -> str:

        token_file = TUSHARE_TOKEN_FILE.resolve()
        if not token_file.exists():
            raise FileNotFoundError(
                f'Tushare token file not found: {token_file}.\n'
                '请在项目根目录创建 .tushare_token 或通过环境变量 TUSHARE_TOKEN_FILE 指定路径。'
            )

        token = token_file.read_text(encoding='utf-8').strip()
        if not token:
            raise ValueError(f'Tushare token file is empty: {token_file}')
        return token

    # ========== 资金流向（核心） ==========

    def get_daily_flow(
        self,
        code: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[MoneyFlow]:
        """
            获取个股日级资金流向
            
            接口文档: https://tushare.pro/document/2?doc_id=170

            Args:
                code: 可选的股票代码（6位纯数字）
                start_date: 查询起始日期
                end_date: 查询结束日期
        """
        try:
            params = {}
            if code is not None:
                params['ts_code'] = self._to_ts_code(code)
            if start_date is not None:
                params['start_date'] = start_date.strftime('%Y%m%d')
            if end_date is not None:
                params['end_date'] = end_date.strftime('%Y%m%d')
            
            results = self._pro.moneyflow(**params)

            if results is None or results.empty:
                return []

            money_flows = []
            for _, row in results.iterrows():
                
                ts_code = row.get('ts_code', '')
                trade_date = datetime.strptime(row['trade_date'], '%Y%m%d')
                buy_sm_vol = row.get('buy_sm_vol', 0.0)
                buy_sm_amount = row.get('buy_sm_amount', 0.0)
                sell_sm_vol = row.get('sell_sm_vol', 0.0)
                sell_sm_amount = row.get('sell_sm_amount', 0.0)
                buy_md_vol = row.get('buy_md_vol', 0.0)
                buy_md_amount = row.get('buy_md_amount', 0.0)
                sell_md_vol = row.get('sell_md_vol', 0.0)
                sell_md_amount = row.get('sell_md_amount', 0.0)
                buy_lg_vol = row.get('buy_lg_vol', 0.0)
                buy_lg_amount = row.get('buy_lg_amount', 0.0)
                sell_lg_vol = row.get('sell_lg_vol', 0.0)
                sell_lg_amount = row.get('sell_lg_amount', 0.0)
                buy_elg_vol = row.get('buy_elg_vol', 0.0)
                buy_elg_amount = row.get('buy_elg_amount', 0.0)
                sell_elg_vol = row.get('sell_elg_vol', 0.0)
                sell_elg_amount = row.get('sell_elg_amount', 0.0)
                net_mf_vol = row.get('net_mf_vol', 0.0)
                net_mf_amount = row.get('net_mf_amount', 0.0)

                money_flow = MoneyFlow.daily(
                    code = ts_code.split('.')[0],
                    date = trade_date,

                    main_cnt = net_mf_vol,                      # 净流入量(手)
                    main_net = net_mf_amount,                   # 净流入额(万元)
                    
                    # 逐笔成交分类统计（特大单 >= 100万）
                    huge_buy_cnt = buy_elg_vol,                 # 特大单成交买方笔数
                    huge_buy_net = buy_elg_amount,              # 特大单成交买方金额(万元)
                    huge_sell_cnt = sell_elg_vol,               # 特大单成交卖方笔数
                    huge_sell_net = sell_elg_amount,            # 特大单成交卖方金额(万元)
                    
                    # 大单 20万 ~ 100万
                    large_buy_cnt = buy_lg_vol,
                    large_buy_net = buy_lg_amount,
                    large_sell_cnt = sell_lg_vol,
                    large_sell_net = sell_lg_amount,
                    
                    # 中单 5万 ~ 20万
                    medium_buy_cnt = buy_md_vol,
                    medium_buy_net = buy_md_amount,
                    medium_sell_cnt = sell_md_vol,
                    medium_sell_net = sell_md_amount,
                    
                    # 小单 5万以下
                    small_buy_cnt = buy_sm_vol,
                    small_buy_net = buy_sm_amount,
                    small_sell_cnt = sell_sm_vol,
                    small_sell_net = sell_sm_amount,
                )
                money_flows.append(money_flow)
            return money_flows
        except Exception as e:
            logger.error(f"获取股票 {code} 资金流向失败: {e}")
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
        elif code.startswith(('0', '3')):
            return f"{code}.SZ"
        elif code.startswith(('8', '4')):
            return f"{code}.BJ"
        return code