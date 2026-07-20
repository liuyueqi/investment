from dataclasses import dataclass, field
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
    members: List[str] = field(default_factory=list)

    def add_member(self, stock_code: str) -> None:
        """添加成分股代码（去重）"""
        if stock_code not in self.members:
            self.members.append(stock_code)

    def __str__(self) -> str:
        return f"{self.name}（{self.code}）"