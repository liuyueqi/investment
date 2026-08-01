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


def get_market_earliest_date() -> date:
    """读取历史数据首次拉取的起始日期（资金流向、日线行情等共用）"""
    raw = _load_config()["market"]["earliest_date"]
    if isinstance(raw, date):
        return raw
    return datetime.strptime(str(raw), "%Y-%m-%d").date()


def reload_config() -> None:
    """重新加载配置文件（主要用于测试）"""
    _load_config.cache_clear()
    logger.info(f"已重新加载配置文件: {CONFIG_PATH}")
