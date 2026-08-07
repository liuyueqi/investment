from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

from domain.sector import Sector


@dataclass
class SectorHistory:
    """板块按交易日的快照序列（每个元素为某日的一个板块）"""
    entries: List[Tuple[date, Sector]] = field(default_factory=list)

    @classmethod
    def from_snapshots(
        cls, snapshots: List[Tuple[date, Sector]]
    ) -> "SectorHistory":
        """
            按 sign 压缩快照：连续未变时只保留最早一条。
            默认入参为同一板块 code 的时间序列。
        """
        
        entries: List[Tuple[date, Sector]] = []
        last_sign: Optional[str] = None
        expected_code: Optional[str] = None
        
        sorted_snapshots = sorted(snapshots, key=lambda item: item[0])
        for trade_d, sector in sorted_snapshots:
            if expected_code is None:
                expected_code = sector.code
            elif sector.code != expected_code:
                raise ValueError(
                    f"from_snapshots 要求同一板块 code，"
                    f"期望 {expected_code}，实际 {sector.code}（date={trade_d}）"
                )
                
            sign = sector.sign
            if last_sign == sign:
                continue
            last_sign = sign
            entries.append((trade_d, sector))
        return cls(entries=entries)
