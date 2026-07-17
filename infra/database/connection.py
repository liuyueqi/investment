import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Generator

# 数据库文件路径（与现有 DATA_DIR 保持一致）
from context import DATA_DIR

DB_PATH = DATA_DIR / "investment.db"

def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""PRAGMA journal_mode=WAL""")      # 读写并发安全
    conn.execute("""PRAGMA foreign_keys=ON""")        # 外键约束
    conn.row_factory = sqlite3.Row               # 支持按列名访问
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """上下文管理器，自动提交/回滚"""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()