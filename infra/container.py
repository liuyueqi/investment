"""IoC 容器 — 基于 dependency-injector"""

from concurrent.futures import ThreadPoolExecutor
from dependency_injector import containers, providers

from infra.adapters.efinance_adapter import EfinanceAdapter
from infra.adapters.tushare_adapter import TushareAdapter
from domain.stock_repository import StockRepository
from domain.sector_repository import SectorRepository
from domain.money_flow_repository import MoneyFlowRepository
from domain.money_flow_aggregation_repository import MoneyFlowAggregationRepository
from domain.money_flow_aggregator import MoneyFlowAggregator
from downloader import Downloader


class AppContainer(containers.DeclarativeContainer):
    """应用容器：管理所有组件的生命周期和依赖"""

    # ── 适配器（单例） ────────────────────────────────────────
    efinance_adapter = providers.Singleton(EfinanceAdapter)
    tushare_adapter = providers.Singleton(TushareAdapter)

    # ── 线程池（单例） ──────────────────────────────────────
    default_pool = providers.Singleton(
        ThreadPoolExecutor,
        max_workers=10,
        thread_name_prefix="DefaultPool",
    )

    sector_aggr_pool = providers.Singleton(
        ThreadPoolExecutor,
        max_workers=4,
        thread_name_prefix="SectorAggrPool",
    )

    sector_calc_pool = providers.Singleton(
        ThreadPoolExecutor,
        max_workers=8,
        thread_name_prefix="SectorCalcPool",
    )

    # ── Repository（单例，自动注入 adapter） ─────────────────
    stock_repo = providers.Singleton(
        StockRepository,
        adapter=efinance_adapter,
    )

    sector_repo = providers.Singleton(
        SectorRepository,
        adapter=efinance_adapter,
        build_pool=default_pool,
    )

    money_flow_repo = providers.Singleton(
        MoneyFlowRepository,
        stock_adapter=efinance_adapter,
        flow_adapter=tushare_adapter,
    )

    money_flow_aggregation_repo = providers.Singleton(
        MoneyFlowAggregationRepository,
    )

    # ── 聚合器（单例，自动注入 Repository） ──────────────────
    money_flow_aggregator = providers.Singleton(
        MoneyFlowAggregator,
        stock_repo=stock_repo,
        sector_repo=sector_repo,
        money_flow_repo=money_flow_repo,
        agg_repo=money_flow_aggregation_repo,
        default_pool=default_pool,
        sector_aggr_pool=sector_aggr_pool,
        calc_pool=sector_calc_pool,
    )

    # ── 下载器（单例，注入依赖） ─────────────────────────────
    downloader = providers.Singleton(
        Downloader,
        stock_repo=stock_repo,
        sector_repo=sector_repo,
        money_flow_repo=money_flow_repo,
    )


# 全局容器实例
container = AppContainer()
