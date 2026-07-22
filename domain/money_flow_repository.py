import time
from typing import Dict, List, Optional
from datetime import date, datetime, timedelta

from domain.money_flow import MoneyFlow
from infra.adapters.efinance_adapter import EfinanceAdapter
from infra.adapters.tushare_adapter import TushareAdapter
from infra.database.connection import get_db
from infra.log import logger


class MoneyFlowRepository:
    """资金流向数据仓库，管理 money_flows 表"""

    _REQUEST_INTERVAL_SECONDS = 0.3      # 每次接口请求间隔（秒）
    _DEFAULT_START_DAYS = 360            # 默认拉取最近 360 天数据
    _CACHE_TTL_SECONDS = 24 * 60 * 60    # 缓存有效期：1 天

    def __init__(
        self,
        stock_adapter: EfinanceAdapter,
        flow_adapter: TushareAdapter,
    ):
        """
        Args:
            stock_adapter: 用于获取股票列表的适配器
            flow_adapter: 用于获取资金流向数据的适配器
        """
        self._stock_adapter = stock_adapter
        self._flow_adapter = flow_adapter
        self._find_by_code_cache: Dict[str, List[MoneyFlow]] = {}

    def refresh(self, stock_codes: Optional[List[str]] = None,
                force: bool = True) -> None:
        """同步资金流向数据到数据库
        
        Args:
            stock_codes: 股票代码列表，为 None 则自动获取全市场
            force: 是否强制刷新
        """
        if not force and self._latest():
            logger.info("数据库缓存有效，跳过刷新")
            return
        self._update_from_adapter(stock_codes)

    def _latest(self) -> bool:
        """检查数据库中是否有在缓存有效期内的数据"""
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
        """从适配器获取资金流向数据，增量更新到数据库"""
        if stock_codes is None:
            stocks = self._stock_adapter.get_all_stock_info()
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
                continue

            if last_date:
                start_date = last_date + timedelta(days=1)
            else:
                start_date = date.today() - timedelta(days=self._DEFAULT_START_DAYS)

            today = date.today()
            if start_date > today:
                continue

            index += 1
            logger.info(f"{index}: 正在获取股票 {code} 资金流向数据 "
                  f"[{start_date} -> {today}]...")
            flows = self._flow_adapter.get_daily_flow(code, start_date, today)
            time.sleep(self._REQUEST_INTERVAL_SECONDS)

            if flows:
                self._save_flows_to_db(flows)
                total_saved += len(flows)

        logger.info(f"资金流向数据更新完成，共保存 {total_saved} 条新记录")

    def _load_last_flow_dates(self) -> Dict[str, Optional[date]]:
        """查询每只股票已有的最后交易日期"""
        result: Dict[str, Optional[date]] = {}
        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, MAX(trade_date) AS max_date
                   FROM money_flows
                   WHERE period = 'day' AND is_deleted = 0
                   GROUP BY code"""
            ).fetchall()
            for row in rows:
                max_date_str = row["max_date"]
                if max_date_str:
                    result[row["code"]] = datetime.strptime(
                        max_date_str, "%Y-%m-%d"
                    ).date()
                else:
                    result[row["code"]] = None
        return result

    def _is_up_to_date(self, last_date: Optional[date]) -> bool:
        """判断股票数据是否需要更新"""
        if last_date is None:
            return False
        last_trading_day = self._get_last_trading_day()
        return last_date >= last_trading_day

    @staticmethod
    def _get_last_trading_day() -> date:
        """获取最近一个交易日"""
        today = date.today()
        weekday = today.weekday()
        if weekday == 5:
            return today - timedelta(days=1)
        elif weekday == 6:
            return today - timedelta(days=2)
        return today

    def _save_flows_to_db(self, money_flows: List[MoneyFlow]) -> None:
        """将资金流向数据写入数据库（UPSERT，幂等安全）"""
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
                           huge_buy_cnt, huge_buy_net,
                           huge_sell_cnt, huge_sell_net,
                           huge_cnt, huge_net,
                           large_buy_cnt, large_buy_net,
                           large_sell_cnt, large_sell_net,
                           large_cnt, large_net,
                           medium_buy_cnt, medium_buy_net,
                           medium_sell_cnt, medium_sell_net,
                           medium_cnt, medium_net,
                           small_buy_cnt, small_buy_net,
                           small_sell_cnt, small_sell_net,
                           small_cnt, small_net,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?,
                                 ?, ?, ?, ?,
                                 ?, ?,
                                 ?, ?, ?, ?,
                                 ?, ?,
                                 ?, ?, ?, ?,
                                 ?, ?,
                                 ?, ?, ?, ?,
                                 ?, ?,
                                 ?, ?)""",
                    (
                        mf.code, trade_date, mf.period,
                        mf.main_cnt, mf.main_net, mf.net_amount,
                        mf.huge_buy_cnt, mf.huge_buy_net,
                        mf.huge_sell_cnt, mf.huge_sell_net,
                        mf.huge_cnt, mf.huge_net,
                        mf.large_buy_cnt, mf.large_buy_net,
                        mf.large_sell_cnt, mf.large_sell_net,
                        mf.large_cnt, mf.large_net,
                        mf.medium_buy_cnt, mf.medium_buy_net,
                        mf.medium_sell_cnt, mf.medium_sell_net,
                        mf.medium_cnt, mf.medium_net,
                        mf.small_buy_cnt, mf.small_buy_net,
                        mf.small_sell_cnt, mf.small_sell_net,
                        mf.small_cnt, mf.small_net,
                        now, now,
                    ),
                )

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
        if not force and code in self._find_by_code_cache:
            return self._find_by_code_cache[code]

        with get_db() as conn:
            rows = conn.execute(
                """SELECT code, trade_date, period,
                          main_cnt, main_net, net_amount,
                          huge_buy_cnt, huge_buy_net,
                          huge_sell_cnt, huge_sell_net,
                          huge_cnt, huge_net,
                          large_buy_cnt, large_buy_net,
                          large_sell_cnt, large_sell_net,
                          large_cnt, large_net,
                          medium_buy_cnt, medium_buy_net,
                          medium_sell_cnt, medium_sell_net,
                          medium_cnt, medium_net,
                          small_buy_cnt, small_buy_net,
                          small_sell_cnt, small_sell_net,
                          small_cnt, small_net
                   FROM money_flows
                   WHERE code = ? AND period = 'day' AND is_deleted = 0
                   ORDER BY trade_date""",
                (code,),
            ).fetchall()
            result = [self._row_to_money_flow(row) for row in rows]
            self._find_by_code_cache[code] = result
            return result

    def find_by_code_and_date_range(
        self, code: str, start_date: date, end_date: date, force: bool = False
    ) -> List[MoneyFlow]:
        """
            按股票代码和日期范围查询资金流向记录。
            优先走 find_by_code 的缓存，在内存中过滤日期范围。

            Args:
                code (str):       股票代码
                start_date (date): 起始日期（含）
                end_date (date):   结束日期（含）
                force (bool):     是否强制从数据库读取并更新缓存

            Returns:
                符合条件的资金流向记录列表
        """
        all_flows = self.find_by_code(code, force=force)
        return [
            f for f in all_flows
            if f.time.date() >= start_date and f.time.date() <= end_date
        ]
        
    _BATCH_SIZE = 500

    def get_date_range(self, *codes: str) -> tuple[Optional[date], Optional[date]]:
        """获取指定股票的最早和最晚的资金流向记录（按 code 分批查询，返回所有 code 的合并范围）"""
        
        if not codes:
            return (None, None)

        earliest_date: Optional[date] = None
        latest_date: Optional[date] = None

        for i in range(0, len(codes), self._BATCH_SIZE):
            
            batch = codes[i:i + self._BATCH_SIZE]
            placeholders = ','.join(['?' for _ in batch])
            sql = f"""SELECT
                               MIN(trade_date) as start_date,
                               MAX(trade_date) as end_date
                          FROM money_flows
                         WHERE code IN ({placeholders})
                           AND period = 'day'
                           AND is_deleted = 0"""
            
            with get_db() as conn:
                row = conn.execute(sql, batch).fetchone()
                if row and row['start_date']:
                    start_date = datetime.strptime(row['start_date'], '%Y-%m-%d').date()
                    end_date = datetime.strptime(row['end_date'], '%Y-%m-%d').date()
                    if earliest_date is None or start_date < earliest_date:
                        earliest_date = start_date
                    if latest_date is None or end_date > latest_date:
                        latest_date = end_date
                        
        return (earliest_date, latest_date)

        for i in range(0, len(codes), self._BATCH_SIZE):
            batch = codes[i:i + self._BATCH_SIZE]
            placeholders = ','.join(['?' for _ in batch])
            sql = f"""SELECT code,
                               MIN(trade_date) as start_date,
                               MAX(trade_date) as end_date
                          FROM money_flows
                         WHERE code IN ({placeholders})
                           AND period = 'day'
                           AND is_deleted = 0
                         GROUP BY code"""
            with get_db() as conn:
                rows = conn.execute(sql, batch).fetchall()
                for row in rows:
                    result[row['code']] = (row['start_date'], row['end_date'])
        return result
    def _row_to_money_flow(self, row) -> MoneyFlow:
        """将数据库行记录转换为 MoneyFlow 实体"""
        trade_date = datetime.strptime(row["trade_date"], "%Y-%m-%d")
        return MoneyFlow.daily(
            code=row["code"],
            date=trade_date,
            main_cnt=row["main_cnt"] or 0,
            main_net=row["main_net"] or 0.0,
            net_amount=row["net_amount"] or 0.0,
            huge_buy_cnt=row["huge_buy_cnt"],
            huge_buy_net=row["huge_buy_net"],
            huge_sell_cnt=row["huge_sell_cnt"],
            huge_sell_net=row["huge_sell_net"],
            huge_cnt=row["huge_cnt"],
            huge_net=row["huge_net"],
            large_buy_cnt=row["large_buy_cnt"],
            large_buy_net=row["large_buy_net"],
            large_sell_cnt=row["large_sell_cnt"],
            large_sell_net=row["large_sell_net"],
            large_cnt=row["large_cnt"],
            large_net=row["large_net"],
            medium_buy_cnt=row["medium_buy_cnt"],
            medium_buy_net=row["medium_buy_net"],
            medium_sell_cnt=row["medium_sell_cnt"],
            medium_sell_net=row["medium_sell_net"],
            medium_cnt=row["medium_cnt"],
            medium_net=row["medium_net"],
            small_buy_cnt=row["small_buy_cnt"],
            small_buy_net=row["small_buy_net"],
            small_sell_cnt=row["small_sell_cnt"],
            small_sell_net=row["small_sell_net"],
            small_cnt=row["small_cnt"],
            small_net=row["small_net"],
        )
