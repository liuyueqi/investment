from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Tuple

from domain.sector import Sector


@dataclass
class SectorHistory:
    """板块按交易日的快照序列（每个元素为某日的一个板块）"""
    entries: List[Tuple[date, Sector]] = field(default_factory=list)

    @classmethod
    def from_by_date(cls, by_date: Dict[date, List[Sector]]) -> "SectorHistory":
        entries: List[Tuple[date, Sector]] = []
        for trade_d in sorted(by_date):
            for sector in by_date[trade_d]:
                entries.append((trade_d, sector))
        return cls(entries=entries)
