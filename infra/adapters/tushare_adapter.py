import math
from pathlib import Path

import tushare as ts
from datetime import date, datetime
from typing import List, Optional

from domain.sector import (
    DCSectorData,
    DCSectorMemberData,
)
from domain.stock import Stock
from domain.ts_code_util import infer_stock_market, normalize_code, to_stock_ts_code
from domain.daily_quote import DailyQuote
from domain.money_flow import MoneyFlow
from infra.config import get_market_earliest_date
from infra.log import logger

_EXCHANGE_TO_MARKET = {
    "SSE": "SH",
    "SZSE": "SZ",
    "BSE": "BJ",
}

class TushareAdapter:
    """基于 Tushare Pro 的数据适配器"""

    _TOKEN_FILE_ENV = 'TUSHARE_TOKEN_FILE'
    _DEFAULT_TOKEN_FILE = Path(".tushare_token")

    def __init__(self):
        """初始化 Tushare 适配器"""
        token = self._load_token()
        ts.set_token(token)
        self._pro = ts.pro_api()

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

    def get_all_stocks(self) -> List[Stock]:
        """
            获取上市股票基础信息列表。

            接口文档: https://tushare.pro/document/2?doc_id=25
            接口: stock_basic(list_status='L')
        """
        try:
            df = self._pro.stock_basic(
                list_status="L",
                fields="ts_code,symbol,name,exchange,market",
            )
            if df is None or df.empty:
                return []

            stocks: List[Stock] = []
            for _, row in df.iterrows():
                symbol = str(row.get("symbol", "") or "").strip()
                name = str(row.get("name", "") or "").strip()
                if not symbol or not name:
                    continue
                code = normalize_code(symbol)
                exchange = str(row.get("exchange", "") or "").strip().upper()
                market = _EXCHANGE_TO_MARKET.get(exchange) or infer_stock_market(code)
                stocks.append(Stock(code=code, name=name, market=market))
            return stocks
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}", exc_info=True)
            return []

    def get_all_trading_days(self) -> List[date]:
        """
            获取 A 股交易日历（仅交易日）。

            接口文档: https://tushare.pro/document/2?doc_id=26
            接口: trade_cal(is_open='1')
        """
        try:
            start_date = get_market_earliest_date()
            end_date = date.today()
            df = self._pro.trade_cal(
                exchange="SSE",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                is_open="1",
            )
            if df is None or df.empty:
                return []

            trading_days: List[date] = []
            for _, row in df.iterrows():
                raw = str(row.get("cal_date", "") or "").strip()
                if not raw:
                    continue
                trading_days.append(datetime.strptime(raw[:8], "%Y%m%d").date())
            trading_days.sort()
            return trading_days
        except Exception as e:
            logger.error(f"获取交易日历失败: {e}", exc_info=True)
            return []

    def get_sector_data(self, trade_date: date) -> List[DCSectorData]:
        """
            按单日拉取东财板块行情（dc_index）。

            接口文档: https://tushare.pro/document/2?doc_id=362
        """
        df = self._pro.dc_index(trade_date=trade_date.strftime("%Y%m%d"))
        if df is None or df.empty:
            return []

        rows: List[DCSectorData] = []
        for _, row in df.iterrows():
            trade_date_raw = str(row.get("trade_date", "") or "").strip()
            ts_code = str(row.get("ts_code", "") or "").strip()
            name = str(row.get("name", "") or "").strip()
            if not trade_date_raw or not ts_code or not name:
                continue
            rows.append(DCSectorData(
                ts_code=ts_code,
                trade_date=datetime.strptime(trade_date_raw[:8], "%Y%m%d").date(),
                name=name,
                leading=str(row.get("leading", "") or "").strip(),
                leading_code=str(row.get("leading_code", "") or "").strip(),
                pct_change=self._to_float(row.get("pct_change")),
                leading_pct=self._to_float(row.get("leading_pct")),
                total_mv=self._to_float(row.get("total_mv")),
                turnover_rate=self._to_float(row.get("turnover_rate")),
                up_num=int(self._to_float(row.get("up_num"))),
                down_num=int(self._to_float(row.get("down_num"))),
                idx_type=str(row.get("idx_type", "") or "").strip(),
                level=str(row.get("level", "") or "").strip(),
            ))
        return rows

    def get_sector_members_data(
        self,
        ts_code: str,
        start_date: date,
        end_date: date,
    ) -> List[DCSectorMemberData]:
        """
            按区间拉取东财板块成分（dc_member）。

            接口文档: https://tushare.pro/document/2?doc_id=363
        """
        df = self._pro.dc_member(
            ts_code=ts_code,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            logger.warning(f"dc_member 无数据: ts_code={ts_code}, {start_date} ~ {end_date}")
            return []

        rows: List[DCSectorMemberData] = []
        for _, row in df.iterrows():
            trade_date_raw = str(row.get("trade_date", "") or "").strip()
            con_code = str(row.get("con_code", "") or "").strip()
            name = str(row.get("name", "") or "").strip()
            if not trade_date_raw or not con_code:
                continue
            rows.append(DCSectorMemberData(
                trade_date=datetime.strptime(trade_date_raw[:8], "%Y%m%d").date(),
                ts_code=str(row.get("ts_code", "") or ts_code).strip() or ts_code,
                con_code=con_code,
                name=name,
            ))
        logger.info(f"dc_member 拉取: ts_code={ts_code}, {start_date} ~ {end_date}, 共 {len(rows)} 条")
        return rows

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
            if code:
                params['ts_code'] = to_stock_ts_code(code)
            if start_date:
                params['start_date'] = start_date.strftime('%Y%m%d')
            if end_date:
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
