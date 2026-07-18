"""日志配置模块"""

import logging
import sys


def get_logger(name: str = __name__) -> logging.Logger:
    """获取 logger 实例（统一格式：时间 [级别] 消息）"""
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(handler)
    
    return logger


# 便捷引用
logger = get_logger("investment")
