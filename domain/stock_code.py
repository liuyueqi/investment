"""A 股代码与市场推断（面向 000985 成分股）"""


def normalize_code(code: str) -> str:
    return code.zfill(6)


def to_ts_code(code: str) -> str:
    """将纯数字代码转换为 Tushare ts_code（code.SH / code.SZ / code.BJ）。"""
    code = normalize_code(code)
    if code.startswith('6'):
        return f"{code}.SH"
    if code.startswith(('8', '4')) or code.startswith('92'):
        return f"{code}.BJ"
    return f"{code}.SZ"


def infer_market(code: str) -> str:
    """根据代码推断交易所（SH / SZ / BJ）"""
    return to_ts_code(code).rsplit('.', 1)[1]
