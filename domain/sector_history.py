from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import List, Optional, Tuple

from domain.sector import Sector
from domain.sector_change_log import SectorChangeAction, SectorChangeLog


@dataclass
class SectorHistory:
    """板块按交易日的快照序列（每个元素为某日的一个板块）"""
    _entries: List[Tuple[date, Sector]] = field(default_factory=list)
    _change_logs: Optional[List[SectorChangeLog]] = field(
        default=None, repr=False, compare=False,
    )

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
        for trade_date, sector in sorted_snapshots:
            if expected_code is None:
                expected_code = sector.code
            elif sector.code != expected_code:
                raise ValueError(
                    f"from_snapshots 要求同一板块 code，"
                    f"期望 {expected_code}，实际 {sector.code}（date={trade_date}）"
                )

            sign = sector.sign
            if last_sign == sign:
                continue
            last_sign = sign
            entries.append((trade_date, sector))
        return cls(_entries=entries)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def first(self) -> Optional[Tuple[date, Sector]]:
        if not self._entries:
            return None
        return self._entries[0]

    @property
    def latest(self) -> Optional[Tuple[date, Sector]]:
        if not self._entries:
            return None
        return self._entries[-1]

    def get_records_since(
        self,
        since: date,
        include: bool = True,
    ) -> List[Tuple[date, Sector]]:
        """
            返回 since 之后的压缩快照；include=True 时包含 since 当天。
        """
        if include:
            return [entry for entry in self._entries if entry[0] >= since]
        return [entry for entry in self._entries if entry[0] > since]

    def get_record_at(self, at: date) -> Optional[Sector]:
        """
            返回 at 当日有效的板块（trade_date <= at 的最后一条）；无则 None。
        """
        result: Optional[Sector] = None
        for trade_date, sector in self._entries:
            if trade_date > at:
                break
            result = sector
        return result

    def get_change_logs(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[SectorChangeLog]:
        """
            对压缩后相邻快照做 diff，生成变更记录。
            第一条为基线；第 k 次变迁 version=k，changed_at 取新快照日期。
            可通过 start_date / end_date（含边界）筛选 changed_at。
        """
        if self._change_logs is None:
            logs: List[SectorChangeLog] = []
            for i in range(1, len(self._entries)):
                _, prev = self._entries[i - 1]
                logs.extend(
                    self.compute_change_logs(prev, self._entries[i], version=i)
                )
            self._change_logs = logs

        result: List[SectorChangeLog] = []
        for log in self._change_logs:
            if not log.changed_at:
                continue
            changed_date = log.changed_at.date()
            if start_date and changed_date < start_date:
                continue
            if end_date and changed_date > end_date:
                continue
            result.append(log)
        return result

        
    @staticmethod
    def compute_change_logs(
        old: Sector,
        new: Tuple[date, Sector],
        version: int,
    ) -> List[SectorChangeLog]:
        """比较两个板块快照，生成变更记录；changed_at 取 new 的交易日。"""
        trade_date, new_sector = new
        changed_at = datetime.combine(trade_date, time.min)
        logs: List[SectorChangeLog] = []

        if old.name != new_sector.name:
            logs.append(SectorChangeLog(
                sector_code=new_sector.code,
                action=SectorChangeAction.MODIFY_NAME,
                old_value=old.name,
                new_value=new_sector.name,
                version=version,
                changed_at=changed_at,
            ))
        if old.type != new_sector.type:
            logs.append(SectorChangeLog(
                sector_code=new_sector.code,
                action=SectorChangeAction.MODIFY_TYPE,
                old_value=old.type.value,
                new_value=new_sector.type.value,
                version=version,
                changed_at=changed_at,
            ))

        old_members = set(old.members)
        new_members = set(new_sector.members)
        for stock_code in sorted(new_members - old_members):
            logs.append(SectorChangeLog(
                sector_code=new_sector.code,
                action=SectorChangeAction.ADD_MEMBER,
                new_value=stock_code,
                version=version,
                changed_at=changed_at,
            ))
        for stock_code in sorted(old_members - new_members):
            logs.append(SectorChangeLog(
                sector_code=new_sector.code,
                action=SectorChangeAction.REMOVE_MEMBER,
                old_value=stock_code,
                version=version,
                changed_at=changed_at,
            ))
        return logs
