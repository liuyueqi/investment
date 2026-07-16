from dataclasses import dataclass, field
from typing import List
from enum import Enum

class SectorType(Enum):
    INDUSTRY = "行业"
    CONCEPT = "概念"
    REGION = "地区"
    STYLE = "风格"

@dataclass
class Sector:
    code: str
    name: str
    type: SectorType
    members: List[str] = field(default_factory=list)
    
    # 只保留最基本的添加成分股方法（便于构建）
    def add_member(self, stock_code: str) -> None:
        if stock_code not in self.members:
            self.members.append(stock_code)