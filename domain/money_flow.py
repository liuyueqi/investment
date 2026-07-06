from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class MoneyFlow:
    """
    资金流向数据（支持任意时间粒度）
    
    粒度由 time 字段和 period 字段共同决定：
    - period = "1min"  + time = "2025-06-08 09:31:00"  → 分钟级
    - period = "day"   + time = "2025-06-08 00:00:00"  → 日级
    - period = "week"  + time = "2025-06-02 00:00:00"  → 周级（周一日期）
    - period = "month" + time = "2025-06-01 00:00:00"  → 月级（月首日期）
    """
    code: str                     # 股票代码
    time: datetime                # 时间点（分钟/日/周/月 的起始时刻）
    period: str                   # 粒度: "1min", "5min", "day", "week", "month"
    main_net: float               # 主力资金净流入（万元）
    main_net_pct: float = 0.0     # 主力净流入占比（%）
    net_amount: float = 0.0       # 净主动买入额（万元）
    
    # 可选明细（如果API提供）
    super_large_net: Optional[float] = None
    large_net: Optional[float] = None
    medium_net: Optional[float] = None
    small_net: Optional[float] = None

    # ========== 核心工厂方法 ==========

    @classmethod
    def daily(cls, code: str, date: datetime, main_net: float, **kwargs):
        """创建日级资金流向"""
        return cls(
            code=code,
            time=datetime(date.year, date.month, date.day),
            period="day",
            main_net=main_net,
            **kwargs
        )

    @classmethod
    def minute(cls, code: str, dt: datetime, main_net: float, **kwargs):
        """创建分钟级资金流向"""
        return cls(
            code=code,
            time=dt.replace(second=0, microsecond=0),
            period="1min",
            main_net=main_net,
            **kwargs
        )

    @classmethod
    def weekly(cls, code: str, date: datetime, main_net: float, **kwargs):
        """创建周级资金流向（自动归一化到周一）"""
        # 计算所在周的周一日期
        days_to_monday = date.weekday()  # 周一=0, 周日=6
        monday = datetime(date.year, date.month, date.day - days_to_monday)
        return cls(
            code=code,
            time=monday,
            period="week",
            main_net=main_net,
            **kwargs
        )

    @classmethod
    def monthly(cls, code: str, date: datetime, main_net: float, **kwargs):
        """创建月级资金流向（自动归一化到月首）"""
        month_start = datetime(date.year, date.month, 1)
        return cls(
            code=code,
            time=month_start,
            period="month",
            main_net=main_net,
            **kwargs
        )

    # ========== 基于当前时间的便捷方法 ==========

    @classmethod
    def today(cls, code: str, main_net: float, **kwargs):
        """创建今日日级资金流向（用于盘后分析）"""
        return cls.daily(
            code=code,
            date=datetime.now(),
            main_net=main_net,
            **kwargs
        )

    @classmethod
    def this_week(cls, code: str, main_net: float, **kwargs):
        """创建本周周级资金流向"""
        return cls.weekly(
            code=code,
            date=datetime.now(),
            main_net=main_net,
            **kwargs
        )

    @classmethod
    def this_month(cls, code: str, main_net: float, **kwargs):
        """创建本月月级资金流向"""
        return cls.monthly(
            code=code,
            date=datetime.now(),
            main_net=main_net,
            **kwargs
        )

    @classmethod
    def current_minute(cls, code: str, main_net: float, **kwargs):
        """创建当前分钟的资金流向（用于盘中实时监控）"""
        return cls.minute(
            code=code,
            dt=datetime.now(),
            main_net=main_net,
            **kwargs
        )

    @classmethod
    def last_n_minutes(cls, code: str, minutes_ago: int, main_net: float, **kwargs):
        """创建过去第N分钟的资金流向
        
        Args:
            code: 股票代码
            minutes_ago: 几分钟前（1=上一分钟，5=5分钟前）
            main_net: 主力净流入
            **kwargs: 其他字段
        """
        target_time = datetime.now() - timedelta(minutes=minutes_ago)
        return cls.minute(
            code=code,
            dt=target_time,
            main_net=main_net,
            **kwargs
        )

    # ========== 辅助方法 ==========

    def is_inflow(self) -> bool:
        """主力是否净流入"""
        return self.main_net > 0

    def is_outflow(self) -> bool:
        """主力是否净流出"""
        return self.main_net < 0

    def inflow_level(self) -> str:
        """流入强度级别"""
        abs_net = abs(self.main_net)
        if abs_net >= 10000:
            return "巨量"
        elif abs_net >= 5000:
            return "大量"
        elif abs_net >= 1000:
            return "中量"
        elif abs_net > 0:
            return "少量"
        else:
            return "无"