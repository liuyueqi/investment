from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import List, Optional


class BasketType(Enum):
    """
        篮子类型
            INDEX: 指数
            SECTOR: 板块
    """
    INDEX = "INDEX"
    SECTOR = "SECTOR"


@dataclass
class Basket:
    """
        指数/板块等成分篮子的统一抽象
    """

    code: str
    name: str
    type: BasketType
    category: Optional[str] = None
    version: int = 0
    constituents: List[str] = field(default_factory=list)
    _sign: str = field(default="", repr=False, compare=False)

    def add_constituent(self, stock_code: str) -> None:
        """添加成分股代码（去重）"""
        if stock_code not in self.constituents:
            self.constituents.append(stock_code)
            self._sign = ""

    @property
    def sign(self) -> str:
        """成分股集合签名，成员变动时变化"""
        if self._sign:
            return self._sign

        payload = ",".join(sorted(self.constituents))
        payload = (
            f"{self.code}|{self.name}|{self.type.value}|{self.category}|{payload}"
        )
        self._sign = sha256(payload.encode()).hexdigest()
        return self._sign

    def __str__(self) -> str:
        return f"{self.name}（{self.code}）"
