"""交互式控制台：通过命令执行数据下载、聚合和启动看板"""

import shlex
import sys

from infra.log import logger
from infra.container import container
from endpoint.dashboard import Dashboard


class Console:
    """交互式控制台"""

    def __init__(self):
        self._downloader = container.downloader()
        self._aggregator = container.money_flow_aggregator()

    def run(self) -> None:
        """启动控制台，循环等待用户输入"""

        self._print_banner()

        while True:
            try:
                raw = input(">>> ").strip()
                if not raw:
                    continue
                if not self._execute_line(raw):
                    break
            except KeyboardInterrupt:
                print("\n再见！")
                break
            except Exception as e:
                print(f"执行出错: {e}")

    def _execute_line(self, raw: str) -> bool:
        """按 & 拆分并顺序执行。返回 False 表示退出控制台。"""
        for segment in raw.split("&"):
            segment = segment.strip()
            if not segment:
                continue
            try:
                if not self._execute(segment):
                    return False
            except Exception as e:
                print(f"执行出错: {segment}: {e}")
        return True

    def _execute(self, raw: str) -> bool:
        """执行单条命令。返回 False 表示退出控制台。"""
        parts = shlex.split(raw)
        if not parts:
            return True
        cmd = parts[0]

        if cmd in ("quit", "exit"):
            Dashboard.stop()
            print("再见！")
            return False
        if cmd in ("help",):
            self._show_help()
        elif cmd in ("download", "sync"):
            scopes = parts[1:] or None
            self._downloader.download(scopes)
            print("\n✅ download 完成")
        elif cmd in ("aggregate", "aggr"):
            args_cnt = len(parts) - 1
            scope = parts[1] if args_cnt > 0 else None
            if scope and args_cnt > 1:
                codes = parts[2:] if args_cnt > 1 else None
            else:
                codes = None
            self._aggregator.aggregate(scope, codes)
            print("\n✅ aggregate 完成")
        elif cmd == "dashboard":
            Dashboard.launch()
        else:
            print(f"未知命令: {cmd}。输入 help 查看可用命令。")
        return True

    # ── 帮助 / 横幅 ───────────────────────────────────────────

    def _print_banner(self) -> None:
        print("=" * 60)
        print("  投资数据系统控制台")
        print("=" * 60)
        self._show_help()

    def _show_help(self) -> None:
        print("可用命令:")
        print("  help                                                       - 显示帮助信息")
        print("  download / sync                                            - 依次下载全部数据（含交易日历）")
        print("    download stock sector quote flow                         - 按 scope 多选下载（仍会刷新交易日历）")
        print("  aggregate / aggr                                           - 仅执行数据聚合")
        print("    aggregate stock [code ...] / aggr stock [code ...]       - 仅执行股票数据聚合")
        print("    aggregate sector [code ...] / aggr sector [code ...]     - 仅执行板块数据聚合")
        print("  dashboard                                                  - 启动数据看板")
        print("  cmd1 & cmd2 & ...                                          - 按顺序执行多个命令")
        print("  quit / exit                                                - 退出控制台")
        print("-" * 60)
