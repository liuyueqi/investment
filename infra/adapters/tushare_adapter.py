import math
import re
from pathlib import Path

import tushare as ts
from datetime import date, datetime
from typing import Dict, List, Optional

from domain.basket import Basket, BasketType
from domain.ts_code_util import normalize_code, to_stock_ts_code
from domain.constituent import Constituent
from domain.daily_quote import DailyQuote
from domain.money_flow import MoneyFlow
from infra.config import get_market_earliest_date
from infra.log import logger
from .external_data_adapter import ExternalDataAdapter

# 仅保留：① 6位数字.CSI  ⑤ 字母前缀+数字.CSI（排除币种变体）
_CSI_PRIMARY_TS_CODE = re.compile(r'^(\d{6}|[A-Z]+\d+)\.CSI$')

class TushareAdapter(ExternalDataAdapter):
    """基于 Tushare Pro 的数据适配器"""

    _TOKEN_FILE_ENV = 'TUSHARE_TOKEN_FILE'
    _DEFAULT_TOKEN_FILE = Path(".tushare_token")

    def __init__(self):
        """
        初始化 Tushare 适配器
        Args:
            token_file: 可选的 Tushare Token 文件路径。优先级：参数 > 环境变量 TUSHARE_TOKEN_FILE > 项目根目录下 .tushare_token
        """
        token = self._load_token()
        ts.set_token(token)
        self._pro = ts.pro_api()
        self._index_ts_code_cache: Dict[str, str] = {}

    def _load_token(self) -> str:

        token_file = Path(".tushare_token").resolve()
        if not token_file.exists():
            raise FileNotFoundError(
                f'Tushare token file not found: {token_file}.\n'
                '请在项目根目录创建 .tushare_token 或通过环境变量 TUSHARE_TOKEN_FILE 指定路径。'
            )

        token = token_file.read_text(encoding='utf-8').strip()
        if not token:
            raise ValueError(f'Tushare token file is empty: {token_file}')
        return token

    def get_all_indexes(self) -> List[Basket]:
        """
        获取中证（CSI）全部指数。

        接口文档: https://tushare.pro/document/2?doc_id=94
        接口: index_basic(market='CSI')

        仅保留主代码：
          - 6位数字.CSI（如 000300.CSI）
          - 字母前缀+数字.CSI（如 H00009.CSI / CU0007.CSI）
        带币种的衍生代码（如 000300CNY030.CSI）会被过滤。
        """
        try:
            df = self._pro.index_basic(market="CSI")
            if df is None or df.empty:
                return []

            baskets: List[Basket] = []
            for _, row in df.iterrows():
                ts_code = str(row.get("ts_code", "") or "").strip()
                name = str(row.get("name", "") or "").strip()
                if not ts_code or not name:
                    continue
                if not _CSI_PRIMARY_TS_CODE.match(ts_code):
                    continue
                code = normalize_code(ts_code.split(".")[0])
                category = str(row.get("category", "") or "").strip()
                baskets.append(Basket(
                    code=code,
                    name=name,
                    type=BasketType.INDEX,
                    category=category,
                ))
            return baskets
        except Exception as e:
            logger.error(f"获取中证指数列表失败: {e}", exc_info=True)
            return []

    def get_constituents_history(
        self,
        code: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Constituent]:
        """
        获取指数历史成分股（平铺为 Constituent 列表）。

        接口文档: https://tushare.pro/document/2?doc_id=96
        接口: index_weight
        start_date 为空时取 config market.earliest_date，end_date 为空时取今天。
        """
        try:
            if start_date is None:
                start_date = get_market_earliest_date()
            if end_date is None:
                end_date = date.today()

            index_code = self._resolve_index_ts_code(code)
            df = self._pro.index_weight(
                index_code=index_code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )
            if df is None or df.empty:
                return []

            constituents: List[Constituent] = []
            for _, row in df.iterrows():
                
                stock_code = normalize_code(
                    str(row.get("con_code", "") or "").split(".")[0]
                )
                if not stock_code:
                    continue

                weight = self._to_float(row.get("weight"))
                trade_date = datetime.strptime(
                    str(row["trade_date"]), "%Y%m%d"
                ).date()
                constituents.append(Constituent(
                    stock_code=stock_code,
                    weight=weight,
                    trade_date=trade_date,
                ))

            constituents.sort(key=lambda c: (c.trade_date, c.stock_code))
            return constituents
        except Exception as e:
            logger.error(f"获取指数 {code} 历史成分失败: {e}", exc_info=True)
            return []

    def _resolve_index_ts_code(self, code: str) -> str:
        """
        通过 index_basic(symbol=...) 解析指数 ts_code，结果缓存在实例内。
        code 为 6 位数字格式。

        接口文档: https://tushare.pro/document/2?doc_id=94
        """
        if code in self._index_ts_code_cache:
            return self._index_ts_code_cache[code]

        df = self._pro.index_basic(symbol=code)
        if df is None or df.empty:
            raise ValueError(f"index_basic 未找到指数: {code}")

        ts_code = str(df.iloc[0].get("ts_code", "") or "").strip()
        if not ts_code:
            raise ValueError(f"index_basic 返回空 ts_code: {code}")

        self._index_ts_code_cache[code] = ts_code
        return ts_code

    # ========== 资金流向（核心） ==========

    def get_daily_flow(
        self,
        code: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
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
                params['ts_code'] = to_stock_ts_code(code)
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
                    code = normalize_code(ts_code.split('.')[0] if ts_code else (code or '')),
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
            logger.error(f"获取股票 {code} 资金流向失败", e)
            return []

    # ========== 日线行情 ==========

    def get_daily_quote(
        self, code: str, start_date: date, end_date: date,
    ) -> List[DailyQuote]:
        """
        获取个股前复权日线行情。

        接口文档: https://tushare.pro/document/2?doc_id=146

        Args:
            code: 股票代码（6位纯数字）
            start_date: 查询起始日期
            end_date: 查询结束日期
        """
        try:
            results = ts.pro_bar(
                ts_code=to_stock_ts_code(code),
                api=self._pro,
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d'),
                asset='E',
                adj='qfq',
                freq='D',
            )
            if results is None or results.empty:
                return []

            quotes = []
            for _, row in results.iterrows():
                ts_code = str(row.get('ts_code', '') or '')
                trade_date = datetime.strptime(str(row['trade_date']), '%Y%m%d').date()
                if trade_date < start_date or trade_date > end_date:
                    continue
                # Tushare amount 单位为千元，DailyQuote 使用万元
                amount = self._to_float(row.get('amount')) / 10.0
                quotes.append(DailyQuote(
                    code=normalize_code(ts_code.split('.')[0] if ts_code else code),
                    date=trade_date,
                    open=self._to_float(row.get('open')),
                    high=self._to_float(row.get('high')),
                    low=self._to_float(row.get('low')),
                    close=self._to_float(row.get('close')),
                    volume=int(self._to_float(row.get('vol'))),
                    amount=amount,
                    change=self._to_float(row.get('change')),
                    pct_chg=self._to_float(row.get('pct_chg')),
                ))
            return quotes
        except Exception as e:
            logger.error(f"获取股票 {code} 日线行情失败", e)
            return []

    def _to_float(self, value) -> float:
        if value is None:
            return 0.0
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if math.isnan(number) else number
