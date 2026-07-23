"""交互式控制台：通过命令执行数据下载和聚合"""

import shlex

from infra.container import container


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

                parts = shlex.split(raw)
                cmd = parts[0]

                if cmd in ("quit", "exit"):
                    print("再见！")
                    break
                elif cmd in ("help"):
                    self._show_help()
                elif cmd in ("download", "sync") :
                    self._downloader.download_all()
                    self._aggregator.aggregate(None)
                    print("\n✅ download 完成")
                elif cmd in ("aggregate", "aggr"):
                    scope = parts[1] if len(parts) > 1 else None
                    self._aggregator.aggregate(scope)
                    print("\n✅ aggregate 完成")
                else:
                    print(f"未知命令: {cmd}。输入 help 查看可用命令。")

            except KeyboardInterrupt:
                print("\n再见！")
                break
            except Exception as e:
                print(f"执行出错: {e}")

    # ── 帮助 / 横幅 ───────────────────────────────────────────

    def _print_banner(self) -> None:
        print("=" * 60)
        print("  投资数据系统控制台")
        print("=" * 60)
        self._show_help()

    def _show_help(self) -> None:
        print("可用命令:")
        print("  help                               - 显示帮助信息")
        print("  download / sync                    - 下载股票 + 板块 + 资金流向 + 聚合")
        print("  aggregate / aggr                   - 仅执行数据聚合")
        print("    aggregate stock / aggr stock     - 仅执行股票数据聚合")
        print("    aggregate sector / aggr sector   - 仅执行板块数据聚合")
        print("  quit / exit                        - 退出控制台")
        print("-" * 60)
