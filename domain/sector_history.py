from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import List, Optional, Tuple

from domain.sector import Sector
from domain.sector_change_log import SectorChangeAction, SectorChangeLog


@dataclass
class SectorHistory:
    """板块按交易日的快照序列（每个元素为某日的一个板块）"""
    _entries: List[Tuple[date, Sector]] = field(default_factory=list)

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
        return cls(_entries=entries)

    def get_change_logs(self) -> List[SectorChangeLog]:
        """
            对压缩后相邻快照做 diff，生成变更记录。
            第一条为基线；第 k 次变迁 version=k，created_at 取新快照日期。
        """
        logs: List[SectorChangeLog] = []
        for i in range(1, len(self._entries)):
            trade_date, curr = self._entries[i]
            _, prev = self._entries[i - 1]
            created_at = datetime.combine(trade_date, time.min)
            for log in self.compute_change_logs(prev, curr, version=i):
                log.created_at = created_at
                logs.append(log)
        return logs

    @staticmethod
    def compute_change_logs(
        old: Sector,
        new: Sector,
        version: int,
    ) -> List[SectorChangeLog]:
        """比较两个板块快照，生成变更记录。"""
        logs: List[SectorChangeLog] = []

        if old.name != new.name:
            logs.append(SectorChangeLog(
                sector_code=new.code,
                action=SectorChangeAction.MODIFY_NAME,
                old_value=old.name,
                new_value=new.name,
                version=version,
            ))
        if old.type != new.type:
            logs.append(SectorChangeLog(
                sector_code=new.code,
                action=SectorChangeAction.MODIFY_TYPE,
                old_value=old.type.value,
                new_value=new.type.value,
                version=version,
            ))

        old_members = set(old.members)
        new_members = set(new.members)
        for stock_code in sorted(new_members - old_members):
            logs.append(SectorChangeLog(
                sector_code=new.code,
                action=SectorChangeAction.ADD_MEMBER,
                new_value=stock_code,
                version=version,
            ))
        for stock_code in sorted(old_members - new_members):
            logs.append(SectorChangeLog(
                sector_code=new.code,
                action=SectorChangeAction.REMOVE_MEMBER,
                old_value=stock_code,
                version=version,
            ))
        return logs
