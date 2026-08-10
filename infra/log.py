"""日志配置模块"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def get_logger(name: str = __name__) -> logging.Logger:
    """获取 logger 实例（统一格式：日期时间 [级别] [线程] 消息；日志按日分文件）。"""
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(logging.INFO)

        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 控制台输出
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # 文件输出：按自然日切分，轮转为 investment.log.YYYY-MM-DD
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            log_dir / "investment.log",
            when="midnight",
            interval=1,
            encoding="utf-8",
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# 便捷引用
logger = get_logger("investment")
