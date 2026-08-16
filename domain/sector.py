from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from typing import List, Optional


class SectorType(Enum):
    """板块类型"""
    INDUSTRY = "行业"
    CONCEPT = "概念"
    REGION = "地区"
    STYLE = "风格"
    UNKNOWN = "UNKNOWN"


class SectorChangeAction(Enum):
    """板块变更类型"""
    MODIFY_NAME = "modify_name"
    MODIFY_TYPE = "modify_type"
    ADD_MEMBER = "add_member"
    REMOVE_MEMBER = "remove_member"


@dataclass
class Sector:
    """板块信息"""
    code: str
    name: str
    type: SectorType
    version: int = 0
    members: List[str] = field(default_factory=list)
    _sign: str = field(default="", repr=False, compare=False)

    def add_member(self, stock_code: str) -> None:
        """添加成分股代码（去重）"""
        if stock_code not in self.members:
            self.members.append(stock_code)
            self._sign = ""

    def copy(self) -> "Sector":
        """浅拷贝板块（members 列表独立）。"""
        return Sector(
            code=self.code,
            name=self.name,
            type=self.type,
            version=self.version,
            members=list(self.members),
        )

    @property
    def sign(self) -> str:
        """
            成分股集合签名，成员变动时变化
        """

        if self._sign:
            return self._sign

        payload = ",".join(sorted(self.members))
        payload = f"{self.code}|{self.name}|{self.type}|{payload}"
        self._sign = sha256(payload.encode()).hexdigest()
        return self._sign

    def __str__(self) -> str:
        return f"{self.name}（{self.code}）"


@dataclass
class SectorChangeLog:
    """
        板块变更记录
    """

    sector_code: str
    action: SectorChangeAction
    old_value: str = ""
    new_value: str = ""
    version: int = 0
    id: Optional[int] = None
    changed_at: Optional[datetime] = None  # 板块实际变更时间
    created_at: Optional[datetime] = None  # 数据库插入时间

    def __str__(self) -> str:
        return (
            f"{self.sector_code} v{self.version} "
            f"{self.action.value}: {self.old_value!r} -> {self.new_value!r}"
        )


@dataclass
class DCSectorData:
    """
        东财概念板块行情（Tushare dc_index）
        接口文档：https://tushare.pro/document/2?doc_id=362
    """

    ts_code: str            # 概念代码
    trade_date: date        # 交易日期
    name: str               # 概念名称
    leading: str            # 领涨股票名称
    leading_code: str       # 领涨股票代码
    pct_change: float       # 涨跌幅
    leading_pct: float      # 领涨股票涨跌幅
    total_mv: float         # 总市值（万元）
    turnover_rate: float    # 换手率
    up_num: int             # 上涨家数
    down_num: int           # 下降家数
    idx_type: str           # 板块类型(行业板块、概念板块、地域板块)
    level: str              # 行业层级


@dataclass
class DCSectorMemberData:
    """
        东财概念板块成分（Tushare dc_member）
        接口文档：https://tushare.pro/document/2?doc_id=363
    """
    trade_date: date        # 交易日期
    ts_code: str            # 概念代码
    con_code: str           # 成分代码
    name: str               # 成分股名称
