"""IoC 容器 — 基于 dependency-injector"""

from dependency_injector import containers, providers

from infra.adapters.efinance_adapter import EfinanceAdapter
from infra.adapters.tushare_adapter import TushareAdapter
from domain.stock_repository import StockRepository
from domain.sector_repository import SectorRepository
from domain.money_flow_repository import MoneyFlowRepository


class AppContainer(containers.DeclarativeContainer):
    """应用容器：管理所有组件的生命周期和依赖"""

    # ── 适配器（单例） ────────────────────────────────────────
    efinance_adapter = providers.Singleton(EfinanceAdapter)
    tushare_adapter = providers.Singleton(TushareAdapter)

    # ── Repository（单例，自动注入 adapter） ─────────────────
    stock_repo = providers.Singleton(
        StockRepository,
        adapter=efinance_adapter,
    )

    sector_repo = providers.Singleton(
        SectorRepository,
        adapter=efinance_adapter,
    )

    money_flow_repo = providers.Singleton(
        MoneyFlowRepository,
        stock_adapter=efinance_adapter,
        flow_adapter=tushare_adapter,
    )


# 全局容器实例
container = AppContainer()
