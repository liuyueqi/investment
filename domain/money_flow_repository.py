import csv
import time
from pathlib import Path
from typing import Dict, List, Optional
from datetime import date, datetime, timedelta

from context import CACHE_DIR
from domain.money_flow import MoneyFlow
from infra.adapters import efinance_adapter, tushare_adapter


class MoneyFlowRepository:

    _CACHE_FILE = "money_flows.csv"
    _REQUEST_INTERVAL_SECONDS = 0.3  # 请求间隔，避免频繁请求被封禁
    _DEFAULT_START_DAYS = 90  # 默认获取最近90天的数据

    def __init__(self):
        self._efinance_adapter = efinance_adapter
        self._tushare_adapter = tushare_adapter
        self._cache_dir = CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path = self._cache_dir / self._CACHE_FILE
        self._money_flows: Optional[Dict[str, Dict[date, MoneyFlow]]] = None

    def _loaded(self) -> bool:
        return self._money_flows is not None

    def refresh(self, stock_codes: Optional[List[str]] = None, sync: bool = True) -> None:
        """同步外部数据与本地 CSV 缓存，并将结果加载到内存。"""
        if not sync:
            self._load_from_csv()
            return
        self._update_from_adapter(stock_codes)

    def _load_from_csv(self) -> None:
        if not self._cache_path.exists():
            return

        try:
            with open(self._cache_path, 'r', encoding='utf-8', newline='') as f:
                reader = csv.reader(f)
                rows = list(reader)
                if len(rows) < 2:
                    print("CSV 缓存文件为空或格式不正确，将重新构建")
                    return

                money_flows: Dict[str, Dict[date, MoneyFlow]] = {}
                for row in rows[1:]:
                    if len(row) >= 4:
                        code = row[0]
                        date_obj = datetime.strptime(row[1], '%Y-%m-%d')
                        money_flow = MoneyFlow.daily(
                            code=code,
                            date=date_obj,
                            main_net=float(row[2]) if row[2] else 0.0,
                            main_net_pct=float(row[3]) if len(row) > 3 and row[3] else 0.0,
                            net_amount=float(row[4]) if len(row) > 4 and row[4] else 0.0,
                            super_large_net=float(row[5]) if len(row) > 5 and row[5] else None,
                            large_net=float(row[6]) if len(row) > 6 and row[6] else None,
                            medium_net=float(row[7]) if len(row) > 7 and row[7] else None,
                            small_net=float(row[8]) if len(row) > 8 and row[8] else None,
                        )
                        if code not in money_flows:
                            money_flows[code] = {}
                        money_flows[code][date_obj] = money_flow
                    else:
                        print(f"CSV 行格式不正确，跳过: {row}")

                self._money_flows = money_flows
                print(f"从 CSV 缓存加载了 {len(money_flows)} 个股票的资金流向数据")
        except Exception as e:
            print(f"读取资金流向缓存失败: {e}")

    def _update_from_adapter(self, stock_codes: Optional[List[str]] = None) -> None:
        # 如果外部未传入股票列表，则尝试从适配器获取全部股票信息
        if stock_codes is None:
            stocks = self._efinance_adapter.get_all_stock_info()
            stock_codes = [stock.code for stock in stocks]

        if not stock_codes:
            print("未提供股票代码列表，且无法从适配器获取股票信息，无法更新资金流向数据")
            self._money_flows = self._money_flows or {}
            return
        
        last_money_flows: Dict[str, MoneyFlow] = self._load_last_money_flows()
        latest_money_flows: Dict[str, List[MoneyFlow]] = {}

        print(f"开始更新资金流向数据，共 {len(stock_codes)} 只股票")
        index = 0
        for code in stock_codes:
            money_flow: Optional[MoneyFlow] = last_money_flows.get(code)
            if self._latest_money_flow(money_flow):
                continue

            if money_flow:
                start_date = money_flow.time.date() + timedelta(days=1)
            else:
                start_date = date.today() - timedelta(days=self._DEFAULT_START_DAYS)  # 默认获取最近90天的数据

            index = index + 1
            print(f"{index}: 正在获取股票 {code} 从 {start_date} 到 {date.today()} 的资金流向数据...")
            latest_stock_flows = self._tushare_adapter.get_daily_flow(code, start_date, date.today())
            time.sleep(self._REQUEST_INTERVAL_SECONDS)
            latest_money_flows[code] = latest_stock_flows

        for code, flows in latest_money_flows.items():
            if flows:
                self._merge_flows(code, flows)

        list_of_money_flows = [val for values in latest_money_flows.values() for val in values]
        self._save_to_csv(list_of_money_flows)

    def _load_last_money_flows(self) -> Dict[str, MoneyFlow]:
        if not self._cache_path.exists():
            return {}
        
        try:
            with open(self._cache_path, 'r', encoding='utf-8', newline='') as f:
                reader = csv.reader(f)
                rows = list(reader)
                if len(rows) < 2:
                    print("CSV 缓存文件为空或格式不正确，将重新构建")

                money_flows: Dict[str, MoneyFlow] = {}
                for row in rows[1:]:
                    if len(row) >= 4:
                        code = row[0]
                        date_obj = datetime.strptime(row[1], '%Y-%m-%d')
                        money_flow = money_flows.get(code)
                        if not money_flow or money_flow.time < date_obj:
                            money_flow = MoneyFlow.daily(
                                code=code,
                                date=date_obj,
                                main_net=float(row[2]) if row[2] else 0.0,
                                main_net_pct=float(row[3]) if len(row) > 3 and row[3] else 0.0,
                                net_amount=float(row[4]) if len(row) > 4 and row[4] else 0.0,
                                super_large_net=float(row[5]) if len(row) > 5 and row[5] else None,
                                large_net=float(row[6]) if len(row) > 6 and row[6] else None,
                                medium_net=float(row[7]) if len(row) > 7 and row[7] else None,
                                small_net=float(row[8]) if len(row) > 8 and row[8] else None,
                            )
                            money_flows[code] = money_flow
                    else:
                        print(f"CSV 行格式不正确，跳过: {row}")

                print(f"从 CSV 缓存加载了 {len(money_flows)} 个板块")
                return money_flows
        except Exception as e:
            print(f"读取板块缓存失败: {e}")
            return {}
        
    def _latest_money_flow(self, money_flow: Optional[MoneyFlow]) -> bool:
        if not money_flow:
            return False
        
        last_trading_day = datetime.now().date()
        if (last_trading_day.weekday() >= 5):  # 周末
            last_trading_day -= timedelta(days=last_trading_day.weekday() - 4)

        return money_flow.time.date() >= last_trading_day

    def _merge_flows(self, code: str, money_flows: List[MoneyFlow]) -> None:
        if self._money_flows is None:
            self._money_flows = {}

        if code not in self._money_flows:
            self._money_flows[code] = {}

        for money_flow in money_flows:
            self._money_flows[code][money_flow.time.date()] = money_flow

    def _save_to_csv(self, money_flows: Optional[List[MoneyFlow]]) -> None:
        if money_flows is None:
            print("没有资金流向数据可保存")
            return

        print(f"保存资金流向缓存到 {self._cache_path}，共 {len(money_flows)} 条记录")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        with open(self._cache_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['code', 'date', 'main_net', 'main_net_pct', 'net_amount',
                             'super_large_net', 'large_net', 'medium_net', 'small_net'])
            for money_flow in money_flows:
                writer.writerow([
                    money_flow.code,
                    money_flow.time.date().isoformat(),
                    money_flow.main_net,
                    # money_flow.main_net_pct,
                    money_flow.net_amount,
                    money_flow.super_large_net,
                    money_flow.large_net,
                    money_flow.medium_net,
                    money_flow.small_net,
                ])
