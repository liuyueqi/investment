"""
Streamlit 数据看板：展示个股/板块的 accumulation / sliding，
并可叠加日线收盘价对比观察。

使用方式：
    streamlit run endpoint/dashboard.py
    或通过 Console 输入 dashboard 命令
"""

import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import plotly.graph_objects as go
import streamlit as st
from datetime import date

from domain.daily_quote import DailyQuote
from domain.money_flow_aggregation import MoneyFlowAggregation
from infra.container import container

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
        if Dashboard._process and Dashboard._process.poll() is None:
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
            webbrowser.open(local_url)
        else:
            print("请在浏览器中访问: http://localhost:8501")
            print(f"若仍无法访问，请查看日志: {_STREAMLIT_LOG}")

    @staticmethod
    def stop() -> None:
        """停止 Streamlit 看板子进程"""
        if Dashboard._process:
            Dashboard._process.terminate()
            try:
                Dashboard._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                Dashboard._process.kill()
                Dashboard._process.wait()
        Dashboard._process = None

    @staticmethod
    def _filter_quotes(
        quotes: Sequence[DailyQuote],
        start: Optional[date],
        end: Optional[date],
    ) -> Tuple[List[date], List[float]]:
        filtered = [
            q for q in quotes
            if (not start or q.date >= start) and (not end or q.date <= end)
        ]
        return [q.date for q in filtered], [q.close for q in filtered]

    @staticmethod
    def _add_quote_trace(
        fig: go.Figure,
        quotes: Sequence[DailyQuote],
        start: Optional[date],
        end: Optional[date],
    ) -> bool:
        """叠加收盘价到次坐标轴；有数据时返回 True"""
        dates, closes = Dashboard._filter_quotes(quotes, start, end)
        if not dates:
            return False
        fig.add_trace(go.Scatter(
            x=dates,
            y=closes,
            mode='lines',
            name='收盘价',
            line=dict(color='#5c6bc0', width=1.5),
            yaxis='y2',
        ))
        return True

    @staticmethod
    def _apply_dual_axis_layout(
        fig: go.Figure,
        title: str,
        y_title: str,
        height: int,
        has_quote: bool,
        x_title: str = '日期',
    ) -> None:
        layout = dict(
            title=title,
            xaxis_title=x_title,
            yaxis_title=y_title,
            hovermode='x unified',
            template='plotly_white',
            height=height,
            margin=dict(l=40, r=60 if has_quote else 40, t=60, b=40),
            legend=dict(orientation='h', yanchor='bottom', y=1.02, x=0),
        )
        if has_quote:
            layout['yaxis2'] = dict(
                title='收盘价（元）',
                overlaying='y',
                side='right',
                showgrid=False,
            )
        fig.update_layout(**layout)

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
        _quote_repo = container.daily_quote_repo()

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

        def _plot_accumulation(
            aggs: List[MoneyFlowAggregation],
            title: str,
            quotes: Optional[List[DailyQuote]] = None,
        ) -> None:
            """绘制累计净流入走势图，可选叠加收盘价"""
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

            has_quote = False
            if quotes:
                has_quote = Dashboard._add_quote_trace(
                    fig, quotes, dates[0], dates[-1],
                )
            Dashboard._apply_dual_axis_layout(
                fig, title, '累计净流入（万元）', 600, has_quote,
            )
            st.plotly_chart(fig, use_container_width=True)

        def _plot_sliding(
            sliding: List[MoneyFlowAggregation],
            title: str,
            window: int,
            quotes: Optional[List[DailyQuote]] = None,
        ) -> None:
            sliding = sorted(sliding, key=lambda a: a.end_date)
            dates = [a.end_date for a in sliding]
            values = [a.main_net for a in sliding]

            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=dates,
                y=values,
                name=f'{window}日净流入',
                marker_color=['#ef5350' if v < 0 else '#26a69a' for v in values],
            ))
            fig.add_hline(y=0, line_dash='dash', line_color='gray', opacity=0.5)

            has_quote = False
            if quotes:
                has_quote = Dashboard._add_quote_trace(
                    fig, quotes, dates[0], dates[-1],
                )
            Dashboard._apply_dual_axis_layout(
                fig, title, '净流入（万元）', 400, has_quote, x_title='结束日期',
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
        show_quote = False
        if entity_type == "个股":
            show_quote = st.sidebar.checkbox("叠加日线收盘价对比", value=True)

        # ── 主界面 ─────────────────────────────────────────────────
        st.title(f"{entity_type}：{selected_name}（{selected_code}）")
        st.markdown("---")

        quotes: List[DailyQuote] = []
        if show_quote:
            quotes = _quote_repo.find_by_code(selected_code, force=True)

        accumulations = _get_accumulation_data(selected_code)

        if accumulations:
            latest = accumulations[-1]
            cols = st.columns(5 if quotes else 4)
            cols[0].metric("统计区间", f"{accumulations[0].end_date} ~ {latest.end_date}")
            cols[1].metric("交易天数", f"{latest.trading_days} 天")
            prev = accumulations[-2].main_net if len(accumulations) > 1 else 0
            cols[2].metric(
                "累计净流入", f"{latest.main_net:,.0f} 万元",
                delta=f"{latest.main_net - prev:,.0f}",
            )
            cols[3].metric("累计笔数", f"{latest.main_cnt:,}")
            if quotes:
                latest_quote = quotes[-1]
                cols[4].metric(
                    "最新收盘", f"{latest_quote.close:.2f} 元",
                    delta=f"{latest_quote.pct_chg:.2f}%",
                )
            st.markdown("---")

            quote_for_plot = quotes if show_quote else None
            _plot_accumulation(
                accumulations,
                f"{selected_name} 累计净流入走势",
                quotes=quote_for_plot,
            )
            if show_quote and not quotes:
                st.info("暂无日线行情数据，请先执行 download 下载。")

            if show_sliding:
                st.markdown("---")
                st.subheader("滑动窗口净流入（3/5/10/20 日）")

                windows = [3, 5, 10, 20]
                tabs = st.tabs([f"{w}日" for w in windows])

                for tab, window in zip(tabs, windows):
                    with tab:
                        sliding = _agg_repo.find_by_trading_days(selected_code, window)
                        if sliding:
                            _plot_sliding(
                                sliding,
                                f'{selected_name} {window}日净流入',
                                window,
                                quotes=quote_for_plot,
                            )
                        else:
                            st.info(f"暂无 {window}日 滑动窗口数据，请先执行 aggregate 命令。")
        else:
            st.warning("暂无累计净流入数据。请先在控制台执行 `download` 和 `aggregate` 命令。")

        st.markdown("---")
        st.caption("数据来源：Tushare / Efinance | 更新频率：手动触发")


# ── streamlit run 入口 ──────────────────────────────────────
if __name__ == "__main__":
    Dashboard._render()
