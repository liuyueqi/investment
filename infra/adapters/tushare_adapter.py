import tushare as ts
from datetime import date, datetime
from typing import List, Optional
from domain.money_flow import MoneyFlow
from .stock_data_adapter import StockDataAdapter


class TushareAdapter(StockDataAdapter):
    """基于 Tushare Pro 的数据适配器"""

    _TOKEN = '807a3e2496925bfdcb03c2d9011efcae6f51c056cc7a08d76948f5f4'

    def __init__(self):
        """
        初始化 Tushare 适配器
        Args:
            token: Tushare Pro 接口 Token（在 https://tushare.pro 注册获取）
        """
        ts.set_token(self._TOKEN)
        self._pro = ts.pro_api()

    # ========== 资金流向（核心） ==========

    def get_daily_flow(
        self,
        code: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[MoneyFlow]:
        """
        获取个股日级资金流向
        接口: moneyflow（2000积分）
        数据源: 同花顺

        Args:
            code: 可选的股票代码（6位纯数字）
            start_date: 查询起始日期
            end_date: 查询结束日期
        """
        try:
            ts_code = code and self._to_ts_code(code)
            params = {'ts_code': ts_code}
            if start_date is not None:
                params['start_date'] = start_date.strftime('%Y%m%d')
            if end_date is not None:
                params['end_date'] = end_date.strftime('%Y%m%d')
            df = self._pro.moneyflow(**params)

            if df is None or df.empty:
                return []

            flows = []
            for _, row in df.iterrows():
                dt = datetime.strptime(row['trade_date'], '%Y%m%d')
                flow = MoneyFlow.daily(
                    code=row.get('ts_code', code),
                    date=dt,
                    main_net=row.get('net_amount', 0.0),           # 主力净流入（万元）
                    main_net_pct=row.get('net_amount_rate', 0.0),  # 占比
                    net_amount=row.get('net_amount', 0.0),
                    # moneyflow 接口返回的字段与 moneyflow_dc 略有不同
                    # 根据实际返回字段调整映射
                )
                flows.append(flow)
            return flows
        except Exception as e:
            print(f"获取股票 {code} 资金流向失败: {e}")
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