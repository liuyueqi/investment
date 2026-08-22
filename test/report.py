"""校验问题与报告输出。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Issue:
    entity_type: str
    code: str
    category: str
    message: str

    def __str__(self) -> str:
        return f"[{self.entity_type}:{self.code}] {self.category}: {self.message}"


@dataclass
class ValidationReport:
    issues: List[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)


def print_report(report: ValidationReport, verbose: bool = False) -> None:
    if report.ok:
        print("✅ 全部检查通过")
        return

    print(f"❌ 发现 {len(report.issues)} 个问题\n")
    by_category: Dict[str, int] = defaultdict(int)
    for issue in report.issues:
        by_category[f"{issue.entity_type}/{issue.category}"] += 1

    print("问题汇总：")
    for key in sorted(by_category):
        print(f"  {key}: {by_category[key]}")
    print()

    if verbose:
        for issue in report.issues:
            print(issue)
    else:
        print("加 --verbose 查看全部详情。前 20 条：")
        for issue in report.issues[:20]:
            print(issue)
