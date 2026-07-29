from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import tomllib

from infra.log import logger

CONFIG_PATH = Path("config.toml")


@lru_cache
def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}")
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def get_money_flow_earliest_date() -> date:
    """读取 money flow 数据的最早起始日期"""
    raw = _load_config()["money_flow"]["earliest_date"]
    if isinstance(raw, date):
        return raw
    return datetime.strptime(str(raw), "%Y-%m-%d").date()


def reload_config() -> None:
    """重新加载配置文件（主要用于测试）"""
    _load_config.cache_clear()
    logger.info(f"已重新加载配置文件: {CONFIG_PATH}")
