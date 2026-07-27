"""
Streamlit 数据看板：展示个股/板块的 accumulation 走势图

使用方式：
    streamlit run endpoint/dashboard.py
    或通过 Console 输入 dashboard 命令
"""

import subprocess
import sys
import time
from pathlib import Path
import streamlit as st
import plotly.graph_objects as go
from datetime import date, timedelta
from typing import List, Optional

from infra.container import container
from infra.log import logger
from domain.money_flow_aggregation import MoneyFlowAggregation, AggregationType

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STREAMLIT_LOG = _PROJECT_ROOT / "logs" / "streamlit.log"


class Dashboard:
    """Streamlit 数据看板，封装 UI 逻辑并通过 launch() 启动"""

    _process: Optional[subprocess.Popen] = None

    @staticmethod
    def _resolve_python() -> str:
        """优先使用项目虚拟环境中的 Python（依赖安装在此）"""
        candidates = [
            _PROJECT_ROOT / ".venv" / "bin" / "python",
            _PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return sys.executable

    @staticmethod
    def _ensure_streamlit(python: str) -> bool:
        """检查目标 Python 是否已安装 streamlit"""
        result = subprocess.run(
            [python, "-m", "streamlit", "--version"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True

        print("\n❌ 启动失败：当前 Python 未安装 streamlit")
        print(f"   Python: {python}")
        if result.stderr.strip():
            print(f"   错误: {result.stderr.strip()}")
        print("\n请使用项目虚拟环境运行控制台，例如：")
        print("   .venv/bin/python main.py")
        return False

    @staticmethod
    def _read_local_url(since_marker: str) -> Optional[str]:
        """从日志中读取本次启动后的 Local URL"""
        if not _STREAMLIT_LOG.exists():
            return None

        text = _STREAMLIT_LOG.read_text(encoding="utf-8")
        if since_marker not in text:
            return None

        tail = text.split(since_marker, 1)[1]
        for line in reversed(tail.splitlines()):
            if "Local URL:" in line:
                return line.split("Local URL:", 1)[1].strip()
        return None

    @staticmethod
    def launch() -> None:
        """启动 Streamlit 看板子进程"""
        if Dashboard._process is not None and Dashboard._process.poll() is None:
            print("\n数据看板已在运行中")
            print("请在浏览器中访问: http://localhost:8501")
            return

        python = Dashboard._resolve_python()
        if not Dashboard._ensure_streamlit(python):
            return

        print("\n正在启动数据看板...")
        _STREAMLIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        launch_marker = f"--- launch at {time.strftime('%Y-%m-%d %H:%M:%S')} ---"
        log_file = _STREAMLIT_LOG.open("a", encoding="utf-8")
        log_file.write(f"\n{launch_marker}\n")
        log_file.flush()

        # 必须持有 Popen 引用，否则 Python 3.12+ 会在 GC 时发出 ResourceWarning
        Dashboard._process = subprocess.Popen(
            [
                python, "-m", "streamlit", "run", __file__,
                "--server.headless", "true",
                "--server.port", "8501",
            ],
            cwd=str(_PROJECT_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        log_file.close()

        # 等待 Streamlit 完成启动；若进程已退出则提示查看日志
        local_url = None
        for _ in range(20):
            if Dashboard._process.poll() is not None:
                print(f"\n❌ 看板启动失败（退出码 {Dashboard._process.returncode}）")
                print(f"   详细日志: {_STREAMLIT_LOG}")
                Dashboard._process = None
                return

            local_url = Dashboard._read_local_url(launch_marker)
            if local_url:
                break
            time.sleep(0.5)

        if local_url:
            print(f"请在浏览器中访问: {local_url}")
        else:
            print("请在浏览器中访问: http://localhost:8501")
            print(f"若仍无法访问，请查看日志: {_STREAMLIT_LOG}")

    @staticmethod
    def _render() -> None:
        """Streamlit UI 入口（由 streamlit run 调用）"""

        # ── 页面配置 ───────────────────────────────────────────────
        st.set_page_config(
            page_title="资金流向看板",
            page_icon="📊",
            layout="wide",
        )

        # ── 获取容器中的仓库 ─────────────────────────────────────
        _agg_repo = container.money_flow_aggregation_repo()
        _stock_repo = container.stock_repo()
        _sector_repo = container.sector_repo()

        def _load_stocks() -> List[str]:
            """获取所有股票代码列表（用于选择）"""
            stocks = _stock_repo.find_all()
            return [f"{s.code} - {s.name}" for s in stocks]

        def _load_sectors() -> List[str]:
            """获取所有板块代码列表"""
            sectors = _sector_repo.find_all()
            return [f"{s.code} - {s.name}" for s in sectors]

        def _get_accumulation_data(code: str) -> List[MoneyFlowAggregation]:
            """获取实体的所有累计净流入数据（is_accumulative=1）"""
            return _agg_repo.find_accumulations_by_code(code, since=None, force=True)

        def _plot_accumulation(aggs: List[MoneyFlowAggregation], title: str) -> None:
            """绘制累计净流入走势图"""
            if not aggs:
                st.info("没有数据可展示，请先通过控制台执行 download 和 aggregate 命令。")
                return

            dates = [a.end_date for a in aggs]
            values = [a.main_net for a in aggs]

            fig = go.Figure()
            colors = ['#ef5350' if v < 0 else '#26a69a' for v in values]
            fig.add_trace(go.Scatter(
                x=dates,
                y=values,
                mode='lines+markers',
                name='累计净流入',
                line=dict(color='#26a69a', width=2),
                marker=dict(size=4, color=colors),
                fill='tozeroy',
                fillcolor='rgba(38, 166, 154, 0.2)',
            ))
            fig.add_hline(y=0, line_dash='dash', line_color='gray', opacity=0.5)
            fig.update_layout(
                title=title,
                xaxis_title='日期',
                yaxis_title='累计净流入（万元）',
                hovermode='x unified',
                template='plotly_white',
                height=600,
                margin=dict(l=40, r=40, t=60, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)

        # ── 侧边栏 ─────────────────────────────────────────────────
        st.sidebar.title("📊 资金流向看板")
        st.sidebar.markdown("---")

        entity_type = st.sidebar.radio(
            "选择数据类型",
            ["个股", "板块"],
            index=0,
        )

        if entity_type == "个股":
            options = _load_stocks()
        else:
            options = _load_sectors()

        if not options:
            st.sidebar.warning("请先通过控制台执行 download 下载数据。")
            st.stop()

        selected_label = st.sidebar.selectbox(
            f"选择{entity_type}",
            options,
            index=0,
        )

        selected_code = selected_label.split(" - ")[0].strip()
        selected_name = selected_label.split(" - ")[1].strip()

        show_sliding = st.sidebar.checkbox("同时展示滑动窗口（3/5/10/20日）", value=False)

        # ── 主界面 ─────────────────────────────────────────────────
        st.title(f"{entity_type}：{selected_name}（{selected_code}）")
        st.markdown("---")

        accumulations = _get_accumulation_data(selected_code)

        if accumulations:
            latest = accumulations[-1]
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("统计区间", f"{accumulations[0].end_date} ~ {latest.end_date}")
            col2.metric("交易天数", f"{latest.trading_days} 天")
            prev = accumulations[-2].main_net if len(accumulations) > 1 else 0
            col3.metric(
                "累计净流入", f"{latest.main_net:,.0f} 万元",
                delta=f"{latest.main_net - prev:,.0f}",
            )
            col4.metric("累计笔数", f"{latest.main_cnt:,}")
            st.markdown("---")

            _plot_accumulation(accumulations, f"{selected_name} 累计净流入走势")

            if show_sliding:
                st.markdown("---")
                st.subheader("滑动窗口净流入（3/5/10/20 日）")

                windows = [3, 5, 10, 20]
                tabs = st.tabs([f"{w}日" for w in windows])

                for tab, window in zip(tabs, windows):
                    with tab:
                        sliding = _agg_repo.find_by_trading_days(
                            selected_code, window, since=None, force=True,
                        )
                        if sliding:
                            sliding.sort(key=lambda a: a.start_date)
                            dates = [a.start_date for a in sliding]
                            values = [a.main_net for a in sliding]

                            fig = go.Figure()
                            fig.add_trace(go.Bar(
                                x=dates,
                                y=values,
                                name=f'{window}日净流入',
                                marker_color=['#ef5350' if v < 0 else '#26a69a' for v in values],
                            ))
                            fig.add_hline(y=0, line_dash='dash', line_color='gray', opacity=0.5)
                            fig.update_layout(
                                title=f'{selected_name} {window}日净流入',
                                xaxis_title='起始日期',
                                yaxis_title='净流入（万元）',
                                hovermode='x unified',
                                template='plotly_white',
                                height=400,
                            )
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.info(f"暂无 {window}日 滑动窗口数据，请先执行 aggregate 命令。")
        else:
            st.warning("暂无累计净流入数据。请先在控制台执行 `download` 和 `aggregate` 命令。")

        st.markdown("---")
        st.caption("数据来源：Tushare / Efinance | 更新频率：手动触发")


# ── streamlit run 入口 ──────────────────────────────────────
if __name__ == "__main__":
    Dashboard._render()
