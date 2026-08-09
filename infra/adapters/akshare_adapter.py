from datetime import date, datetime
from typing import List, Optional

import akshare as ak
import requests

from domain.basket import Basket, BasketType
from domain.etf import ETF
from infra.log import logger


class AkshareAdapter:
    """基于 akshare 的数据适配器"""

    def get_all_indexes(self) -> List[Basket]:
        """
        获取国证全部指数。

        接口文档: https://akshare.akfamily.xyz/data/index/index.html#id25
        对应 ak.index_all_cni；因上游字段增减导致 akshare 列映射报错，
        这里直连同一 URL，按字段名解析。
        """
        try:
            rows = self._fetch_index_all_cni()
            baskets: List[Basket] = []
            for item in rows:
                code = str(item.get("indexcode", "")).strip()
                name = str(item.get("indexname", "")).strip()
                if not code or not name:
                    continue
                baskets.append(Basket(
                    code=code,
                    name=name,
                    type=BasketType.INDEX,
                ))
            return baskets
        except Exception as e:
            logger.error(f"获取全部指数失败: {e}", exc_info=True)
            return []

    def _fetch_index_all_cni(self) -> list:
        """直连国证 indexList（与 ak.index_all_cni 同源）。"""
        url = "https://www.cnindex.com.cn/index/indexList"
        params = {
            "channelCode": "-1",
            "rows": "2000",
            "pageNum": "1",
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()["data"]["rows"]

    def get_all_trading_days(self) -> List[date]:
        """
        获取股票交易日历。

        接口文档: https://akshare.akfamily.xyz/data/tool/tool.html#id1
        """
        try:
            df = ak.tool_trade_date_hist_sina()
            if df is None or df.empty:
                return []

            trading_days: List[date] = []
            for value in df["trade_date"]:
                trade_date = self._parse_trade_date(value)
                if trade_date is not None:
                    trading_days.append(trade_date)
            return trading_days
        except Exception as e:
            logger.error(f"获取交易日历失败: {e}", exc_info=True)
            return []

    def get_all_etf_info(self) -> List[ETF]:
        try:
            # 使用 symbol="ETF基金" 获取全量上市基金（含 ETF 和 LOF）
            df = ak.fund_etf_category_sina(symbol="ETF基金")
            if df is None or df.empty:
                return []

            etfs = []
            for _, row in df.iterrows():
                code_raw = str(row['代码'])  # 如 'sz159998' 或 'sh510300'
                # 去除市场前缀
                code = code_raw.replace('sh', '').replace('sz', '').replace('bj', '')
                code = code.zfill(6)

                # 过滤出真正的 ETF（仅包含沪市特定前缀和深市 159xxx）
                if not self._is_etf_code(code):
                    continue

                name = row['名称']
                # 推断市场
                if code.startswith('5'):
                    market = 'SH'
                elif code.startswith('1'):
                    market = 'SZ'
                else:
                    market = 'UNKNOWN'
                etfs.append(ETF(
                    code=code,
                    name=name,
                    market=market
                ))
            return etfs
        except Exception as e:
            logger.error(f"获取 ETF 列表失败: {e}", exc_info=True)
            return []

    def _parse_trade_date(self, value) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date()
        return datetime.strptime(text[:10], "%Y-%m-%d").date()

    def _is_etf_code(self, code: str) -> bool:
        """判断股票代码是否为真正的 ETF（基于代码范围）"""
        # 沪市 ETF 代码前缀
        sh_prefixes = ['510', '511', '512', '513', '515', '516', '517', '518',
                       '560', '561', '562', '563', '588']
        # 深市 ETF 代码前缀
        sz_prefixes = ['159']
        # 注意：16xxxx 是 LOF，不是 ETF，排除
        # 注意：501xxx 是 LOF，不是 ETF，排除
        if any(code.startswith(p) for p in sh_prefixes) or any(code.startswith(p) for p in sz_prefixes):
            return True
        return False
