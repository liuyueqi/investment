"""A 股 / 指数代码与 Tushare ts_code 转换工具"""

import re

_LETTER_PREFIX_INDEX_CODE = re.compile(r"^[A-Z]+\d+$")


def normalize_code(code: str) -> str:
    return code.zfill(6)


def to_stock_ts_code(code: str) -> str:
    """将纯数字股票代码转换为 Tushare ts_code（code.SH / code.SZ / code.BJ）。"""
    code = normalize_code(code)
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("8", "4")) or code.startswith("92"):
        return f"{code}.BJ"
    return f"{code}.SZ"


def infer_stock_market(code: str) -> str:
    """根据股票代码推断交易所（SH / SZ / BJ）"""
    return to_stock_ts_code(code).rsplit(".", 1)[1]
