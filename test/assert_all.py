"""依次执行全部数据校验。

顺序：calendar → stocks → coverage → aggregation

用法：
  python test/assert_all.py
  python test/assert_all.py -v
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_TEST_DIR = Path(__file__).resolve().parent

_STEPS = (
    ("calendar", "assert_calendar.py"),
    ("stocks", "assert_stocks.py"),
    ("coverage", "assert_flow_coverage.py"),
    ("aggregation", "assert_flow_aggregation.py"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="依次执行全部数据校验")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出全部问题详情")
    args = parser.parse_args()

    extra = ["-v"] if args.verbose else []
    failed: list[str] = []

    for name, script in _STEPS:
        print(f"\n========== {name} ==========")
        cmd = [sys.executable, str(_TEST_DIR / script), *extra]
        rc = subprocess.call(cmd)
        if rc != 0:
            failed.append(name)

    print("\n========== 汇总 ==========")
    if failed:
        print(f"❌ 失败步骤: {', '.join(failed)}")
        return 1
    print("✅ 全部步骤通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
