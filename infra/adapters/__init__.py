from .efinance_adapter import EfinanceAdapter

# 业务层默认使用的适配器实例
default_adapter = EfinanceAdapter()

__all__ = ['default_adapter', 'EfinanceAdapter']