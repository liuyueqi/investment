import math
import re
from collections import defaultdict
from pathlib import Path
from time import sleep

import tushare as ts
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from common.date_range_util import iter_day_ranges, iter_fortnight_ranges, iter_week_ranges
from domain.basket import Basket, BasketType
from domain.sector import (
    Constituent,
    DCSectorData,
    DCSectorMemberData,
    Sector,
    SectorType,
)
from domain.stock import Stock
from domain.ts_code_util import infer_stock_market, normalize_code, to_stock_ts_code
from domain.daily_quote import DailyQuote
from domain.money_flow import MoneyFlow
from infra.config import get_market_earliest_date
from infra.log import logger

# 仅保留：① 6位数字.CSI  ⑤ 字母前缀+数字.CSI（排除币种变体）
_CSI_PRIMARY_TS_CODE = re.compile(r'^(\d{6}|[A-Z]+\d+)\.CSI$')
_DC_IDX_TYPE_MAP = {
    "概念板块": SectorType.CONCEPT,
    "行业板块": SectorType.INDUSTRY,
    "地域板块": SectorType.REGION,
    "风格板块": SectorType.STYLE,
}
_EXCHANGE_TO_MARKET = {
    "SSE": "SH",
    "SZSE": "SZ",
    "BSE": "BJ",
}

class TushareAdapter:
    """基于 Tushare Pro 的数据适配器"""

    _TOKEN_FILE_ENV = 'TUSHARE_TOKEN_FILE'
    _DEFAULT_TOKEN_FILE = Path(".tushare_token")
    _DC_INDEX_ROW_LIMIT = 5000
    _DC_MEMBER_ROW_LIMIT = 8000

    def __init__(self):
        """初始化 Tushare 适配器"""
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
            end_date = date(date.today().year + 1, 12, 31)
            df = self._pro.trade_cal(
                exchange="SSE",
                start_date="19900101",
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

    def get_all_sectors(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[tuple[date, Sector]]:
        """
            按 dc_index 的 trade_date 返回板块快照；成分由 dc_member 串行拉取
            （接口限频约 500 次/分钟）。

            成分接口: dc_member（https://tushare.pro/document/2?doc_id=363）
            板块列表: dc_index（https://tushare.pro/document/2?doc_id=362）
            start_date 为空时取 market.earliest_date，end_date 为空时取今天。
        """
        try:
            if start_date is None:
                start_date = get_market_earliest_date()
            if end_date is None:
                end_date = date.today()
            if start_date > end_date:
                logger.warning(f"板块查询 start_date {start_date} > end_date {end_date}，返回空")
                return []

            logger.info(f"拉取板块列表: start_date: {start_date}, end_date: {end_date}")
            
            rows: List[tuple[date, Sector]] = []
            for week_start, week_end in iter_week_ranges(start_date, end_date):
                
                logger.info(f"分周拉取板块列表: {week_start} ~ {week_end}")
                week_rows = self._fetch_sectors(week_start, week_end)

                if len(week_rows) >= self._DC_INDEX_ROW_LIMIT:
                    logger.warning(f"分周结果达到上限 {len(week_rows)} 条，改为按日重拉: {week_start} ~ {week_end}")
                    week_rows = []
                    for day_start, day_end in iter_day_ranges(week_start, week_end):
                        day_rows = self._fetch_sectors(day_start, day_end)
                        logger.info(f"分日拉取完成: {day_start}, 当日 {len(day_rows)} 条")
                        week_rows.extend(day_rows)
                logger.info(f"分周拉取完成: {week_start} ~ {week_end}, 本周 {len(week_rows)} 条")
                rows.extend(week_rows)

            rows.sort(key=lambda item: (item[0], item[1].code))

            if not rows:
                logger.warning(f"拉取板块列表无数据: start_date: {start_date}, end_date: {end_date}")
                return []
            logger.info(
                f"拉取板块列表完成: start_date: {start_date}, end_date: {end_date}, "
                f"共 {len(rows)} 条"
            )

            member_start = rows[0][0]
            member_end = rows[-1][0]
            unique_codes = {sector.code for _, sector in rows}

            members_by_code: Dict[str, Dict[date, set[str]]] = {}
            for seq, code in enumerate(sorted(unique_codes)):
                try:
                    logger.info(f"{seq}: 按双周拉取板块成分: {code}, {member_start} ~ {member_end}")
                    member_rows: List[tuple[date, str]] = []
                    for batch_start, batch_end in iter_fortnight_ranges(member_start, member_end):
                        batch_rows = self._fetch_sector_members(code, batch_start, batch_end)
                        if len(batch_rows) >= self._DC_MEMBER_ROW_LIMIT:
                            logger.warning(
                                f"dc_member 双周结果达到上限 {len(batch_rows)} 条，改为按日重拉: {code}, {batch_start} ~ {batch_end}"
                            )
                            batch_rows = []
                            for day_start, day_end in iter_day_ranges(batch_start, batch_end):
                                day_rows = self._fetch_sector_members(code, day_start, day_end)
                                logger.info(f"分日拉取成分完成: {code}, {day_start}, 当日 {len(day_rows)} 只")
                                batch_rows.extend(day_rows)
                                sleep(0.1)
                        else:
                            sleep(0.1)
                        member_rows.extend(batch_rows)

                    by_date: Dict[date, set[str]] = defaultdict(set)
                    for trade_date, stock_code in member_rows:
                        by_date[trade_date].add(stock_code)
                    members_by_code[code] = dict(by_date)
                except Exception as e:
                    logger.error(f"dc_member 拉取失败 sector={code}: {e}", exc_info=True)

            result: List[tuple[date, Sector]] = []
            for trade_date, sector in rows:
                members = members_by_code.get(sector.code, {}).get(trade_date)
                if members:
                    sector.members = list(members)
                result.append((trade_date, sector))
            return result
        except Exception as e:
            logger.error(f"获取东财板块失败: {e}", exc_info=True)
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

    def _fetch_sectors(
        self,
        start_date: date,
        end_date: date,
    ) -> List[tuple[date, Sector]]:
        """
            东财 dc_index 按日期范围拉取全部板块，按 trade_date 升序返回。
        """

        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        
        df = self._pro.dc_index(start_date=start_str, end_date=end_str)
        if df is None or df.empty:
            return []

        rows: List[tuple[date, Sector]] = []
        for _, row in df.iterrows():
            trade_date_raw = str(row.get("trade_date", "") or "").strip()
            ts_code = str(row.get("ts_code", "") or "").strip()
            name = str(row.get("name", "") or "").strip()
            if not trade_date_raw or not ts_code or not name:
                continue
       
            trade_date = datetime.strptime(trade_date_raw[:8], "%Y%m%d").date()
            idx_type_raw = str(row.get("idx_type", "") or "").strip()
            sector_type = _DC_IDX_TYPE_MAP.get(idx_type_raw, SectorType.UNKNOWN)
            rows.append((
                trade_date,
                Sector(
                    code=self._normalize_dc_sector_code(ts_code),
                    name=name,
                    type=sector_type,
                ),
            ))
        return rows

    def _fetch_sector_members(
        self,
        sector_code: str,
        start_date: date,
        end_date: date,
    ) -> List[tuple[date, str]]:
        """东财 dc_member 按日期范围拉取成分，返回 (trade_date, stock_code) 列表。"""
        ts_code = f"{sector_code}.DC"
        df = self._pro.dc_member(
            ts_code=ts_code,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            logger.warning(f"dc_member 无数据: ts_code={ts_code}, {start_date} ~ {end_date}")
            return []

        rows: List[tuple[date, str]] = []
        for _, row in df.iterrows():
            trade_date_raw = str(row.get("trade_date", "") or "").strip()
            if not trade_date_raw:
                continue
            trade_date = datetime.strptime(trade_date_raw[:8], "%Y%m%d").date()
            stock_code = normalize_code(str(row.get("con_code", "") or "").split(".")[0])
            if stock_code:
                rows.append((trade_date, stock_code))

        logger.info(f"dc_member 拉取: ts_code={ts_code}, {start_date} ~ {end_date}, 共 {len(rows)} 条")
        return rows

    def _normalize_dc_sector_code(self, ts_code: str) -> str:
        """BK1184.DC -> BK1184"""
        return ts_code.split(".", 1)[0].strip()

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
                stock_code = normalize_code(str(row.get("con_code", "") or "").split(".")[0])
                if not stock_code:
                    continue

                weight = self._to_float(row.get("weight"))
                trade_date = datetime.strptime(str(row["trade_date"]), "%Y%m%d").date()
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
