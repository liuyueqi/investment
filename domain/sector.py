from dataclasses import dataclass, field
from hashlib import sha256
from typing import List
from enum import Enum


class SectorType(Enum):
    """板块类型"""
    INDUSTRY = "行业"
    CONCEPT = "概念"
    REGION = "地区"
    STYLE = "风格"


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