from enum import Enum


class Source(str, Enum):
    """
        数据来源
    """
    
    DC = "DC"   # 东方财富
    TL = "TL"   # 通联
