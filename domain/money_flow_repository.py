import bisect
import threading
import time
from typing import Dict, List, Optional, Tuple
from datetime import date, datetime, timedelta

from domain.money_flow import MoneyFlow, TsMoneyFlowData
from domain.trading_day_repository import TradingDayRepository
from domain.ts_code_util import normalize_code
from infra.adapters.tushare_adapter import TushareAdapter
from infra.config import get_market_earliest_date
from infra.database.connection import get_db
from infra.log import logger


class MoneyFlowRepository:
    """资金流向数据仓库，管理 ts_money_flows 表（通联 moneyflow）"""

    _REQUEST_INTERVAL_SECONDS = 0.3      # 每次接口请求间隔（秒）
    _CACHE_TTL_SECONDS = 24 * 60 * 60    # 缓存有效期：1 天

    def __init__(
        self,
        stock_adapter: TushareAdapter,
        flow_adapter: TushareAdapter,
        trading_day_repo: TradingDayRepository,
    ):
        """
        Args:
            stock_adapter: 用于获取股票列表的适配器
            flow_adapter: 用于获取资金流向数据的适配器
            trading_day_repo: 交易日历仓库
        """
        self._stock_adapter = stock_adapter
        self._flow_adapter = flow_adapter
        self._trading_day_repo = trading_day_repo
        # 按 code 的不可变快照：(升序 flows, date -> flow)；一写多读
        self._cache: Dict[str, Tuple[Tuple[MoneyFlow, ...], Dict[date, MoneyFlow]]] = {}
        self._cache_lock = threading.RLock()

    def refresh(
        self,
        stock_codes: Optional[List[str]] = None,
        force: bool = True,
    ) -> None:
        """同步资金流向数据到数据库
        
        Args:
            stock_codes: 股票代码列表，为 None 则自动获取全市场
            force: 是否强制刷新
        """
        if not force and self._latest():
            logger.info("数据库缓存有效，跳过刷新")
            return
        self._update_from_adapter(stock_codes)
        self.clear_cache()

    def _latest(self) -> bool:
        """检查数据库中是否有在缓存有效期内的数据"""
        with get_db() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS cnt, MAX(updated_at) AS max_updated
                   FROM ts_money_flows WHERE is_deleted = 0"""
            ).fetchone()
            count = row["cnt"]
            if count == 0:
                return False
            max_updated = row["max_updated"]
            if not max_updated:
                return False
            updated_dt = datetime.strptime(max_updated, "%Y-%m-%d %H:%M:%S")
            return (time.time() - updated_dt.timestamp()) < self._CACHE_TTL_SECONDS

    def _update_from_adapter(self, stock_codes: Optional[List[str]] = None) -> None:
        """从适配器获取资金流向原始数据，增量写入 ts_money_flows"""
        if stock_codes is None:
            stocks = self._stock_adapter.get_all_stocks()
            stock_codes = [stock.code for stock in stocks]

        if not stock_codes:
            logger.warning("未提供股票代码列表，且无法从适配器获取股票信息，无法更新资金流向数据")
            return

        logger.info(f"开始更新资金流向数据，共 {len(stock_codes)} 只股票")
        last_flow_dates = self._load_last_flow_dates()
        total_saved = 0
        index = 0

        for code in stock_codes:
            last_date = last_flow_dates.get(code)
            if self._is_up_to_date(last_date):
                logger.info(f"股票 {code} 最新数据日期 {last_date} 已同步到最新交易日，跳过")
                continue

            if last_date:
                start_date = last_date + timedelta(days=1)
            else:
                start_date = get_market_earliest_date()

            today = date.today()
            if start_date > today:
                logger.warning(f"股票 {code} 最新数据日期 {start_date} 大于今天 {today}，跳过")
                continue

            index += 1
            logger.info(f"{index}: 正在获取股票 {code} 资金流向数据 "
                  f"[{start_date} -> {today}]...")
            rows = self._flow_adapter.get_daily_flow(code, start_date, today)
            if rows:
                total_saved += self._save_rows_to_db(rows)

            time.sleep(self._REQUEST_INTERVAL_SECONDS)

        logger.info(f"资金流向数据更新完成，共保存 {total_saved} 条新记录")

    def _load_last_flow_dates(self) -> Dict[str, Optional[date]]:
        """查询每只股票已有的最后交易日期（key 为 6 位 code）"""
        result: Dict[str, Optional[date]] = {}
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, MAX(trade_date) AS max_date
                   FROM ts_money_flows
                   WHERE is_deleted = 0
                   GROUP BY code"""
            ).fetchall()
            for row in rows:
                code = row["code"]
                if not code:
                    continue
                max_date_str = row["max_date"]
                if max_date_str:
                    result[code] = datetime.strptime(max_date_str, "%Y-%m-%d").date()
                else:
                    result[code] = None
        return result

    def _is_up_to_date(self, last_date: Optional[date]) -> bool:
        """判断股票数据是否需要更新"""
        if not last_date:
            return False
        last_trading_day = self._get_last_trading_day()
        return last_date >= last_trading_day

    def _get_last_trading_day(self) -> date:
        """获取最近一个交易日"""
        last = self._trading_day_repo.find_latest_trading_day()
        if not last:
            raise ValueError("交易日历为空，无法获取最近交易日")
        return last

    def _save_rows_to_db(self, rows: List[TsMoneyFlowData]) -> int:
        """将通联 moneyflow 原始行写入 ts_money_flows"""
        if not rows:
            return 0

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT INTO ts_money_flows (
                       ts_code, code, trade_date,
                       buy_sm_vol, buy_sm_amount, sell_sm_vol, sell_sm_amount,
                       buy_md_vol, buy_md_amount, sell_md_vol, sell_md_amount,
                       buy_lg_vol, buy_lg_amount, sell_lg_vol, sell_lg_amount,
                       buy_elg_vol, buy_elg_amount, sell_elg_vol, sell_elg_amount,
                       net_mf_vol, net_mf_amount,
                       created_at, updated_at
                   ) VALUES (?, ?, ?,
                             ?, ?, ?, ?,
                             ?, ?, ?, ?,
                             ?, ?, ?, ?,
                             ?, ?, ?, ?,
                             ?, ?,
                             ?, ?)
                   ON CONFLICT(ts_code, trade_date) DO NOTHING""",
                [
                    (
                        item.ts_code,
                        normalize_code(item.ts_code.split(".", 1)[0]),
                        item.trade_date.isoformat(),
                        item.buy_sm_vol,
                        item.buy_sm_amount,
                        item.sell_sm_vol,
                        item.sell_sm_amount,
                        item.buy_md_vol,
                        item.buy_md_amount,
                        item.sell_md_vol,
                        item.sell_md_amount,
                        item.buy_lg_vol,
                        item.buy_lg_amount,
                        item.sell_lg_vol,
                        item.sell_lg_amount,
                        item.buy_elg_vol,
                        item.buy_elg_amount,
                        item.sell_elg_vol,
                        item.sell_elg_amount,
                        item.net_mf_vol,
                        item.net_mf_amount,
                        now,
                        now,
                    )
                    for item in rows
                ],
            )
            return conn.total_changes - before

    def find_by_code(self, code: str, force: bool = False) -> List[MoneyFlow]:
        """
            根据股票代码查询所有资金流向记录。
            结果会缓存在内存中，优先从缓存读取。

            Args:
                code (str):  股票代码
                force (bool): 是否强制从数据库读取并更新缓存

            Returns:
                资金流向记录列表
        """
        flows, _ = self._get_cache(code, force)
        return list(flows)

    def find_by_code_and_date(
        self,
        code: str,
        trade_date: date,
        force: bool = False,
    ) -> Optional[MoneyFlow]:
        """按股票代码和交易日查询单条资金流向记录。"""
        _, by_date = self._get_cache(code, force)
        return by_date.get(trade_date)

    def find_by_code_and_date_range(
        self,
        code: str,
        start_date: date,
        end_date: date,
        force: bool = False,
    ) -> List[MoneyFlow]:
        """
            按股票代码和日期范围查询资金流向记录。
            与 find_by_code / find_by_code_and_date 共用同一缓存。

            Args:
                code (str):       股票代码
                start_date (date): 起始日期（含）
                end_date (date):   结束日期（含）
                force (bool):     是否强制从数据库读取并更新缓存

            Returns:
                符合条件的资金流向记录列表
        """
        flows, _ = self._get_cache(code, force)
        lo = bisect.bisect_left(flows, start_date, key=lambda f: f.time.date())
        hi = bisect.bisect_right(flows, end_date, key=lambda f: f.time.date())
        return list(flows[lo:hi])

    def _get_cache(
        self, code: str, force: bool = False,
    ) -> Tuple[Tuple[MoneyFlow, ...], Dict[date, MoneyFlow]]:
        if not force:
            cached = self._cache.get(code)
            if cached is not None:
                return cached
        with self._cache_lock:
            if not force:
                cached = self._cache.get(code)
                if cached is not None:
                    return cached
            flows = tuple(self._load_flows_from_db(code))
            by_date = {flow.time.date(): flow for flow in flows}
            snapshot = (flows, by_date)
            self._cache[code] = snapshot
            return snapshot

    def _load_flows_from_db(self, code: str) -> List[MoneyFlow]:
        code = normalize_code(code)
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, trade_date,
                          buy_sm_vol, buy_sm_amount, sell_sm_vol, sell_sm_amount,
                          buy_md_vol, buy_md_amount, sell_md_vol, sell_md_amount,
                          buy_lg_vol, buy_lg_amount, sell_lg_vol, sell_lg_amount,
                          buy_elg_vol, buy_elg_amount, sell_elg_vol, sell_elg_amount,
                          net_mf_vol, net_mf_amount
                   FROM ts_money_flows
                   WHERE code = ? AND is_deleted = 0
                   ORDER BY trade_date""",
                (code,),
            ).fetchall()
        return [self._row_to_money_flow(row) for row in rows]

    def _row_to_money_flow(self, row) -> MoneyFlow:
        """将 ts_money_flows 行映射为 MoneyFlow 实体"""
        trade_date = datetime.strptime(row["trade_date"], "%Y-%m-%d")
        return MoneyFlow.daily(
            code=row["code"],
            date=trade_date,
            main_cnt=row["net_mf_vol"] or 0,
            main_net=row["net_mf_amount"] or 0.0,
            huge_buy_cnt=row["buy_elg_vol"],
            huge_buy_net=row["buy_elg_amount"],
            huge_sell_cnt=row["sell_elg_vol"],
            huge_sell_net=row["sell_elg_amount"],
            large_buy_cnt=row["buy_lg_vol"],
            large_buy_net=row["buy_lg_amount"],
            large_sell_cnt=row["sell_lg_vol"],
            large_sell_net=row["sell_lg_amount"],
            medium_buy_cnt=row["buy_md_vol"],
            medium_buy_net=row["buy_md_amount"],
            medium_sell_cnt=row["sell_md_vol"],
            medium_sell_net=row["sell_md_amount"],
            small_buy_cnt=row["buy_sm_vol"],
            small_buy_net=row["buy_sm_amount"],
            small_sell_cnt=row["sell_sm_vol"],
            small_sell_net=row["sell_sm_amount"],
        )

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()
