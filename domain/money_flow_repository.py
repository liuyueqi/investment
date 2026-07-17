import time
from typing import Dict, List, Optional
from datetime import date, datetime, timedelta

from domain.money_flow import MoneyFlow
from infra.adapters import efinance_adapter, tushare_adapter
from infra.database.connection import get_db


class MoneyFlowRepository:

    _REQUEST_INTERVAL_SECONDS = 0.3
    _DEFAULT_START_DAYS = 90
    _CACHE_TTL_SECONDS = 24 * 60 * 60

    def __init__(self):
        self._efinance_adapter = efinance_adapter
        self._tushare_adapter = tushare_adapter

    def refresh(self, stock_codes: Optional[List[str]] = None, force: bool = True) -> None:
        if not force and self._latest():
            print("数据库缓存有效，跳过刷新")
            return
        self._update_from_adapter(stock_codes)

    def _latest(self) -> bool:
        with get_db() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS cnt, MAX(updated_at) AS max_updated
                   FROM money_flows WHERE is_deleted = 0"""
            ).fetchone()
            count = row["cnt"]
            if count == 0:
                return False
            max_updated = row["max_updated"]
            if max_updated is None:
                return False
            updated_dt = datetime.strptime(max_updated, "%Y-%m-%d %H:%M:%S")
            return (time.time() - updated_dt.timestamp()) < self._CACHE_TTL_SECONDS

    def _update_from_adapter(self, stock_codes: Optional[List[str]] = None) -> None:
        if stock_codes is None:
            stocks = self._efinance_adapter.get_all_stock_info()
            stock_codes = [stock.code for stock in stocks]

        if not stock_codes:
            print("未提供股票代码列表，且无法从适配器获取股票信息，无法更新资金流向数据")
            return

        print(f"开始更新资金流向数据，共 {len(stock_codes)} 只股票")

        last_flow_dates = self._load_last_flow_dates()
        total_saved = 0
        index = 0
        for code in stock_codes:
            last_date = last_flow_dates.get(code)
            if self._is_up_to_date(last_date):
                continue

            if last_date:
                start_date = last_date + timedelta(days=1)
            else:
                start_date = date.today() - timedelta(days=self._DEFAULT_START_DAYS)

            today = date.today()
            if start_date > today:
                continue

            index += 1
            print(f"{index}: 正在获取股票 {code} 从 {start_date} 到 {today} 的资金流向数据...")
            flows = self._tushare_adapter.get_daily_flow(code, start_date, today)
            time.sleep(self._REQUEST_INTERVAL_SECONDS)

            if flows:
                self._save_flows_to_db(flows)
                total_saved += len(flows)

        print(f"资金流向数据更新完成，共保存 {total_saved} 条新记录")

    def _load_last_flow_dates(self) -> Dict[str, Optional[date]]:
        result: Dict[str, Optional[date]] = {}
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, MAX(trade_date) AS max_date
                   FROM money_flows
                   WHERE period = 'day' AND is_deleted = 0 GROUP BY code"""
            ).fetchall()
            for row in rows:
                max_date_str = row["max_date"]
                if max_date_str:
                    result[row["code"]] = datetime.strptime(max_date_str, "%Y-%m-%d").date()
                else:
                    result[row["code"]] = None
        return result

    def _is_up_to_date(self, last_date: Optional[date]) -> bool:
        if last_date is None:
            return False
        last_trading_day = self._get_last_trading_day()
        return last_date >= last_trading_day

    def _get_last_trading_day(self) -> date:
        today = date.today()
        weekday = today.weekday()
        if weekday == 5:
            return today - timedelta(days=1)
        elif weekday == 6:
            return today - timedelta(days=2)
        return today

    def _save_flows_to_db(self, money_flows: List[MoneyFlow]) -> None:
        if not money_flows:
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            for mf in money_flows:
                trade_date = mf.time.strftime("%Y-%m-%d")
                conn.execute(
                    """INSERT INTO money_flows (
                           code, trade_date, period,
                           main_cnt, main_net, net_amount,
                           huge_net, large_net, medium_net, small_net,
                           huge_cnt, large_cnt, medium_cnt, small_cnt,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        mf.code, trade_date, mf.period,
                        mf.main_cnt, mf.main_net, mf.net_amount,
                        mf.huge_net, mf.large_net, mf.medium_net, mf.small_net,
                        mf.huge_cnt, mf.large_cnt, mf.medium_cnt, mf.small_cnt,
                        now, now,
                    ),
                )

    def find_by_code(self, code: str) -> List[MoneyFlow]:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, trade_date, period,
                          main_cnt, main_net, net_amount,
                          huge_net, large_net, medium_net, small_net,
                          huge_cnt, large_cnt, medium_cnt, small_cnt
                   FROM money_flows
                   WHERE code = ? AND period = 'day' AND is_deleted = 0
                   ORDER BY trade_date""",
                (code,),
            ).fetchall()

            return [self._row_to_money_flow(row) for row in rows]

    def find_by_code_and_date_range(self, code: str, start_date: date, end_date: date) -> List[MoneyFlow]:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, trade_date, period,
                          main_cnt, main_net, net_amount,
                          huge_net, large_net, medium_net, small_net,
                          huge_cnt, large_cnt, medium_cnt, small_cnt
                   FROM money_flows
                   WHERE code = ? AND period = 'day' AND is_deleted = 0
                     AND trade_date >= ? AND trade_date <= ?
                   ORDER BY trade_date""",
                (code, start_date.isoformat(), end_date.isoformat()),
            ).fetchall()

            return [self._row_to_money_flow(row) for row in rows]

    @staticmethod
    def _row_to_money_flow(row) -> MoneyFlow:
        trade_date = datetime.strptime(row["trade_date"], "%Y-%m-%d")
        return MoneyFlow.daily(
            code=row["code"],
            date=trade_date,
            main_cnt=row["main_cnt"] or 0,
            main_net=row["main_net"] or 0.0,
            net_amount=row["net_amount"] or 0.0,
            huge_net=row["huge_net"],
            large_net=row["large_net"],
            medium_net=row["medium_net"],
            small_net=row["small_net"],
            huge_cnt=row["huge_cnt"],
            large_cnt=row["large_cnt"],
            medium_cnt=row["medium_cnt"],
            small_cnt=row["small_cnt"],
        )
