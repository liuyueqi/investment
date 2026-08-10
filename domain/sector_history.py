from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Dict, List, Optional, Tuple

from domain.sector import Sector, SectorChangeAction, SectorChangeLog, SectorType
from infra.config import get_market_earliest_date


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

    @classmethod
    def from_change_logs(
        cls,
        latest: Sector,
        change_logs: Optional[List[SectorChangeLog]],
    ) -> "SectorHistory":
        """
            根据最新板块与变更日志回溯历史。
            日志 version 表示变迁后的版本；逆序回放得到各版本快照。
            各版本生效日取该 version 首条日志的 changed_at；
            无则用 market.earliest_date。无变更日志时仅包含 latest 一条。
        """
        if not change_logs:
            return cls(
                _entries=[(get_market_earliest_date(), latest.copy())],
            )

        for log in change_logs:
            if log.sector_code != latest.code:
                raise ValueError(
                    f"from_change_logs 要求同一板块 code，"
                    f"期望 {latest.code}，实际 {log.sector_code}"
                )

        logs_by_version: Dict[int, List[SectorChangeLog]] = defaultdict(list)
        for log in change_logs:
            logs_by_version[log.version].append(log)

        states: Dict[int, Sector] = {latest.version: latest.copy()}
        for version in sorted(logs_by_version.keys(), reverse=True):
            if version > latest.version:
                raise ValueError(
                    f"变更日志 version={version} 大于最新板块 version={latest.version}"
                )
            if version not in states:
                raise ValueError(
                    f"变更日志 version 不连续：缺少 version={version} 的板块状态"
                )
            prev = cls._apply_inverse(states[version], logs_by_version[version])
            states[prev.version] = prev

        entries: List[Tuple[date, Sector]] = []
        for version in sorted(states):
            version_logs = logs_by_version.get(version, [])
            trade_date = (
                version_logs[0].changed_at.date()
                if version_logs and version_logs[0].changed_at
                else get_market_earliest_date()
            )
            entries.append((trade_date, states[version]))

        return cls(_entries=entries)

    @staticmethod
    def _apply_inverse(
        sector: Sector,
        logs: List[SectorChangeLog],
    ) -> Sector:
        """对 sector 逆序应用 logs，返回上一版本快照（不修改入参）。"""
        result = sector.copy()
        for log in logs:
            if log.action == SectorChangeAction.ADD_MEMBER:
                if log.new_value in result.members:
                    result.members.remove(log.new_value)
            elif log.action == SectorChangeAction.REMOVE_MEMBER:
                if log.old_value not in result.members:
                    result.members.append(log.old_value)
            elif log.action == SectorChangeAction.MODIFY_NAME:
                result.name = log.old_value
            elif log.action == SectorChangeAction.MODIFY_TYPE:
                result.type = SectorType(log.old_value)
        result.version = sector.version - 1
        result._sign = ""
        return result

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

    def __len__(self) -> int:
        return len(self._entries)
        
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
