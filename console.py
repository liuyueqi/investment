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
                elif cmd == "help":
                    self._show_help()
                elif cmd == "download":
                    self._downloader.download_all()
                    print("\n✅ download 完成")
                elif cmd == "aggregate":
                    self._aggregator.aggregate_all()
                    print("\n✅ aggregate 完成")
                else:
                    print(f"未知命令: {cmd}。输入 help 查看可用命令。")

            except KeyboardInterrupt:
                print("\n再见！")
                break
            except Exception as e:
                print(f"执行出错: {e}")

    # ── 帮助 / 横幅 ───────────────────────────────────────────

    @staticmethod
    def _print_banner() -> None:
        print("=" * 60)
        print("  投资数据系统控制台")
        print("=" * 60)
        Console._show_help()

    @staticmethod
    def _show_help() -> None:
        print("可用命令:")
        print("  help                          - 显示帮助信息")
        print("  download                      - 下载股票 + 板块 + 资金流向 + 聚合")
        print("  aggregate                     - 仅执行数据聚合")
        print("  quit / exit                   - 退出控制台")
        print("-" * 60)
