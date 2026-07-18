"""轻量级 IoC 容器，负责组件生命周期和依赖注入"""

import inspect
from typing import Dict, Type, TypeVar, Optional, get_type_hints

T = TypeVar("T")


class Container:
    """依赖注入容器

    用法：
        container = Container()
        container.register(StockRepository, StockRepository)
        repo = container.resolve(StockRepository)
    """

    def __init__(self):
        self._registry: Dict[Type, dict] = {}
        self._singletons: Dict[Type, object] = {}

    def register(
        self,
        abstract: Type,
        concrete: Optional[Type] = None,
        singleton: bool = True,
    ) -> "Container":
        if concrete is None:
            concrete = abstract
        self._registry[abstract] = {"class": concrete, "singleton": singleton}
        return self

    def resolve(self, abstract: Type[T]) -> T:
        """解析依赖，返回实例（自动注入构造函数参数）"""
        if abstract in self._singletons:
            return self._singletons[abstract]

        info = self._registry.get(abstract)
        if info is None:
            raise KeyError(
                f"组件未注册: {abstract.__name__}\n"
                f"已注册: {', '.join(t.__name__ for t in self._registry)}"
            )

        concrete_cls = info["class"]
        instance = self._build(concrete_cls)

        if info["singleton"]:
            self._singletons[abstract] = instance

        return instance

    def _find_registered(self, param_type: Type) -> Optional[Type]:
        """查找注册表中与参数类型匹配的组件（支持子类匹配）"""
        # 精确匹配
        if param_type in self._registry:
            return param_type

        # 子类匹配：查找 param_type 的子类中已注册的
        for registered_type in self._registry:
            if issubclass(registered_type, param_type):
                return registered_type

        return None

    def _build(self, cls: Type) -> object:
        """创建实例，通过 __init__ 类型标注自动注入依赖"""
        try:
            hints = get_type_hints(cls.__init__)
        except Exception:
            hints = {}

        sig = inspect.signature(cls.__init__)
        kwargs = {}

        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            if param_name in ("args", "kwargs"):
                continue
            if param.default is not inspect.Parameter.empty:
                continue

            param_type = hints.get(param_name)
            if param_type is None:
                raise ValueError(
                    f"无法自动注入 '{cls.__name__}.__init__({param_name})': "
                    f"缺少类型标注"
                )

            matched = self._find_registered(param_type)
            if matched is None:
                raise KeyError(
                    f"无法自动注入 '{cls.__name__}.__init__({param_name})': "
                    f"类型 {param_type.__name__} 未注册"
                )

            kwargs[param_name] = self.resolve(matched)

        return cls(**kwargs)


# ── 全局容器实例 ────────────────────────────────────────────

_container: Optional[Container] = None


def get_container() -> Container:
    """获取全局容器实例（延迟初始化）"""
    global _container
    if _container is None:
        from infra.adapters.efinance_adapter import EfinanceAdapter
        from infra.adapters.tushare_adapter import TushareAdapter

        _container = Container()
        _container.register(EfinanceAdapter, singleton=True)
        _container.register(TushareAdapter, singleton=True)
    return _container
