from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class SectorChangeAction(Enum):
    """板块变更类型"""
    MODIFY_NAME = "modify_name"
    MODIFY_TYPE = "modify_type"
    ADD_MEMBER = "add_member"
    REMOVE_MEMBER = "remove_member"


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
