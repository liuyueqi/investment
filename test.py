import tushare as ts
from datetime import date, datetime
from typing import List, Optional
from domain.money_flow import MoneyFlow
from infra.adapters import tushare_adapter

money_flows = tushare_adapter.get_daily_flow(code='000001', start_date=date(2026, 4, 5), end_date=date(2026, 7, 15))
for flow in money_flows:
    print(f"股票 {flow.code} 在 {flow.time.date()} 的资金流向数据: 主力净流入 {flow.main_net} 万元")