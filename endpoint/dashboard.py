"""
Streamlit 数据看板：展示个股/板块的 accumulation 走势图

使用方式：
    streamlit run endpoint/dashboard.py
"""

import streamlit as st
import plotly.graph_objects as go
from datetime import date, timedelta
from typing import List, Optional

from infra.container import container
from infra.log import logger
from domain.money_flow_aggregation import MoneyFlowAggregation, AggregationType

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


def _get_accumulation_data(code: str, type: str) -> List[MoneyFlowAggregation]:
    """获取实体的所有累计净流入数据（is_accumulative=1）"""
    return _agg_repo.find_accumulations_by_code(code, type, since=None, force=True)


def _plot_accumulation(aggs: List[MoneyFlowAggregation], title: str) -> None:
    """绘制累计净流入走势图"""
    if not aggs:
        st.info("没有数据可展示，请先通过控制台执行 download 和 aggregate 命令。")
        return

    # 提取日期和主力净流入（万元）
    dates = [a.end_date for a in aggs]
    values = [a.main_net for a in aggs]

    fig = go.Figure()

    # 填充区域图，上涨绿色下跌红色
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

    # 零轴线
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

# 选择类型
entity_type = st.sidebar.radio(
    "选择数据类型",
    ["个股", "板块"],
    index=0,
)

# 根据类型加载选项
if entity_type == "个股":
    options = _load_stocks()
else:
    options = _load_sectors()

if not options:
    st.sidebar.warning("请先通过控制台执行 download 下载数据。")
    st.stop()

# 选择具体实体
selected_label = st.sidebar.selectbox(
    f"选择{entity_type}",
    options,
    index=0,
)

# 解析 code 和 name
selected_code = selected_label.split(" - ")[0].strip()
selected_name = selected_label.split(" - ")[1].strip()

# 可选：展示窗口滑动数据
show_sliding = st.sidebar.checkbox("同时展示滑动窗口（3/5/10/20日）", value=False)

# ── 主界面 ─────────────────────────────────────────────────
st.title(f"{entity_type}：{selected_name}（{selected_code}）")
st.markdown("---")

# 加载累计数据
type_str = "stock" if entity_type == "个股" else "sector"
accumulations = _get_accumulation_data(selected_code, type_str)

if accumulations:
    # 显示最新累计值摘要
    latest = accumulations[-1]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("统计区间", f"{accumulations[0].end_date} ~ {latest.end_date}")
    col2.metric("交易天数", f"{latest.trading_days} 天")
    col3.metric("累计净流入", f"{latest.main_net:,.0f} 万元",
                delta=f"{latest.main_net - (accumulations[-2].main_net if len(accumulations) > 1 else 0):,.0f}")
    col4.metric("累计笔数", f"{latest.main_cnt:,}")
    st.markdown("---")

    # 主走势图
    _plot_accumulation(accumulations, f"{selected_name} 累计净流入走势")

    # 可选：展示滑动窗口数据
    if show_sliding:
        st.markdown("---")
        st.subheader("滑动窗口净流入（3/5/10/20 日）")

        windows = [3, 5, 10, 20]
        tabs = st.tabs([f"{w}日" for w in windows])

        for tab, window in zip(tabs, windows):
            with tab:
                sliding = _agg_repo.find_by_trading_days(
                    selected_code, type_str, window, since=None, force=True
                )
                if sliding:
                    # 按 start_date 排序
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

# ── 底部信息 ───────────────────────────────────────────────
st.markdown("---")
st.caption("数据来源：Tushare / Efinance | 更新频率：手动触发")
```