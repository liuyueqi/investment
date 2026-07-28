import akshare as ak
from typing import List

from domain.etf import ETF

class AkshareAdapter:
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
            print(f"获取 ETF 列表失败: {e}")
            return []
    
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