from .efinance_adapter import EfinanceAdapter
from .tushare_adapter import TushareAdapter

# 业务层默认使用的适配器实例
efinance_adapter = EfinanceAdapter()
tushare_adapter = TushareAdapter()

__all__ = ['efinance_adapter', 'EfinanceAdapter', 'tushare_adapter', 'TushareAdapter']