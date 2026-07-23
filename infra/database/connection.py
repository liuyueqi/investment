import sqlite3
from datetime import date
from pathlib import Path
from contextlib import contextmanager
from typing import Generator


def _adapt_date(val: date) -> str:
    """将 date 对象转换为 ISO 格式字符串（Python 3.12+ 需要显式注册）"""
    return val.isoformat()


sqlite3.register_adapter(date, _adapt_date)

from context import DATA_DIR

DB_PATH = DATA_DIR / "investment.db"


def get_connection() -> sqlite3.Connection:
    """获取数据库连接（配置 WAL 模式、外键约束、行工厂）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""PRAGMA page_size=8192""")          # 2GB 以上数据库支持
    conn.execute("""PRAGMA journal_mode=WAL""")       # 读写并发安全
    conn.execute("""PRAGMA foreign_keys=ON""")         # 外键约束
    conn.row_factory = sqlite3.Row                        # 支持按列名访问
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """上下文管理器：自动提交 / 回滚 / 关闭连接"""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
