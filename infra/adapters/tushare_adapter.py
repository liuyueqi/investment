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
            params = {}
            if code is not None:
                params['ts_code'] = self._to_ts_code(code)
            if start_date is not None:
                params['start_date'] = start_date.strftime('%Y%m%d')
            if end_date is not None:
                params['end_date'] = end_date.strftime('%Y%m%d')
            
            results = self._pro.moneyflow(**params)

            if results is None or results.empty:
                return []

            money_flows = []
            for _, row in results.iterrows():
                
                ts_code = row.get('ts_code')
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
                    code = ts_code.split('.')[0],  # 提取纯数字代码
                    date = trade_date,
                    main_cnt = net_mf_vol,             # 主力笔数
                    main_net = net_mf_amount,           # 主力净流入（万元）
                    super_large_cnt = buy_elg_vol - sell_elg_vol,  # 超大单笔数
                    super_large_net = buy_elg_amount - sell_elg_amount,  # 超大单净流入（万元）
                    large_cnt = buy_lg_vol - sell_lg_vol,          # 大单笔数
                    large_net = buy_lg_amount - sell_lg_amount,          # 大单净流入（万元）
                    medium_cnt = buy_md_vol - sell_md_vol,         # 中单笔数
                    medium_net = buy_md_amount - sell_md_amount,         # 中单净流入（万元）
                    small_cnt = buy_sm_vol - sell_sm_vol,           # 小单笔数
                    small_net = buy_sm_amount - sell_sm_amount          # 小单净流入（万元）
                )
                money_flows.append(money_flow)
            return money_flows
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