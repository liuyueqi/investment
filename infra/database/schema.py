# infra/database/schema.py
from .connection import get_connection, DB_PATH

# ============================================================
# 表结构定义
# ============================================================

CREATE_STOCKS_TABLE = """
CREATE TABLE IF NOT EXISTS stocks (
    code        TEXT PRIMARY KEY,        -- '000001'
    name        TEXT NOT NULL,           -- '平安银行'
    market      TEXT NOT NULL,           -- '主板'
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted  INTEGER NOT NULL DEFAULT 0
);
"""

CREATE_SECTORS_TABLE = """
CREATE TABLE IF NOT EXISTS sectors (
    code        TEXT PRIMARY KEY,        -- 'BK0477'
    name        TEXT NOT NULL,           -- '超级品牌'
    type        TEXT NOT NULL,           -- '行业' / '概念' / '地区' / '风格'
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted  INTEGER NOT NULL DEFAULT 0
);
"""

# 板块成分股（多对多关系，正规化处理，不再用逗号拼接）
CREATE_SECTOR_MEMBERS_TABLE = """
CREATE TABLE IF NOT EXISTS sector_members (
    sector_code TEXT NOT NULL,
    stock_code  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (sector_code, stock_code),
    FOREIGN KEY (sector_code) REFERENCES sectors(code),
    FOREIGN KEY (stock_code)  REFERENCES stocks(code)
);
"""

CREATE_MONEY_FLOWS_TABLE = """
CREATE TABLE IF NOT EXISTS money_flows (
    code            TEXT NOT NULL,        -- 股票代码
    trade_date      TEXT NOT NULL,        -- '2025-06-08'
    period          TEXT NOT NULL DEFAULT 'day',  -- 粒度: day/week/month
    main_cnt        INTEGER DEFAULT 0,   -- 主力笔数
    main_net        REAL DEFAULT 0.0,    -- 主力净流入(万元)
    net_amount      REAL DEFAULT 0.0,    -- 净主动买入额(万元)
    huge_net        REAL,                -- 超大单净流入(万元)
    large_net       REAL,                -- 大单净流入(万元)
    medium_net      REAL,                -- 中单净流入(万元)
    small_net       REAL,                -- 小单净流入(万元)
    huge_cnt        INTEGER,
    large_cnt       INTEGER,
    medium_cnt      INTEGER,
    small_cnt       INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (code, trade_date, period)  -- 同一天同一粒度只有一条
);
"""

# 每日行情（可选，目前 DailyQuote 模型已定义但未使用）
CREATE_DAILY_QUOTES_TABLE = """
CREATE TABLE IF NOT EXISTS daily_quotes (
    code        TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      INTEGER NOT NULL,       -- 成交量(手)
    amount      REAL NOT NULL,          -- 成交额(万元)
    pct_chg     REAL NOT NULL,          -- 涨跌幅(%)
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (code, trade_date)
);
"""

# 索引（加速查询）
CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_money_flows_date ON money_flows(trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_money_flows_code ON money_flows(code);",
    "CREATE INDEX IF NOT EXISTS idx_daily_quotes_code ON daily_quotes(code);",
    "CREATE INDEX IF NOT EXISTS idx_daily_quotes_date ON daily_quotes(trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_sector_members_stock ON sector_members(stock_code);",
]


def _ensure_column(conn, table: str, column_def: str) -> None:
    column_name = column_def.split()[0]
    current_columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column_name not in current_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def init_db() -> None:
    """初始化数据库：创建所有表（幂等，多次运行安全）"""
    conn = get_connection()
    try:
        conn.execute(CREATE_STOCKS_TABLE)
        conn.execute(CREATE_SECTORS_TABLE)
        conn.execute(CREATE_SECTOR_MEMBERS_TABLE)
        conn.execute(CREATE_MONEY_FLOWS_TABLE)
        conn.execute(CREATE_DAILY_QUOTES_TABLE)
        for table, column in [
            ("stocks", "ts_code TEXT"),
            ("stocks", "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))"),
            ("stocks", "is_deleted INTEGER NOT NULL DEFAULT 0"),
            ("sectors", "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))"),
            ("sectors", "is_deleted INTEGER NOT NULL DEFAULT 0"),
            ("sector_members", "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))"),
            ("sector_members", "is_deleted INTEGER NOT NULL DEFAULT 0"),
            ("money_flows", "huge_net REAL"),
            ("money_flows", "huge_cnt INTEGER"),
            ("money_flows", "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))"),
            ("money_flows", "is_deleted INTEGER NOT NULL DEFAULT 0"),
            ("daily_quotes", "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))"),
            ("daily_quotes", "is_deleted INTEGER NOT NULL DEFAULT 0"),
        ]:
            _ensure_column(conn, table, column)
        for idx in CREATE_INDEXES:
            conn.execute(idx)
        conn.commit()
        print(f"数据库初始化完成: {conn.execute('PRAGMA database_list').fetchone()}")
        print(f"数据库文件路径: {DB_PATH}")
    finally:
        conn.close()
