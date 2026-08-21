# infra/database/schema.py

from .connection import get_connection, DB_PATH
from infra.log import logger

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
    sign        TEXT NOT NULL DEFAULT '', -- 成分股集合签名
    version     INTEGER NOT NULL DEFAULT 0, -- 变更版本
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
    PRIMARY KEY (sector_code, stock_code)
);
"""

CREATE_SECTOR_CHANGE_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS sector_change_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sector_code     TEXT NOT NULL,                   -- 板块代码
    action          TEXT NOT NULL,                   -- 变更类型: modify_name/modify_type/add_member/remove_member
    old_value       TEXT NOT NULL DEFAULT '',        -- 变更前值
    new_value       TEXT NOT NULL DEFAULT '',        -- 变更后值
    version         INTEGER NOT NULL DEFAULT 0,      -- 变更版本
    changed_at      TEXT NOT NULL DEFAULT '',        -- 板块实际变更时间
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))  -- 数据库插入时间
);
"""

CREATE_MONEY_FLOWS_TABLE = """
CREATE TABLE IF NOT EXISTS money_flows (
    code            TEXT NOT NULL,        -- 股票代码
    trade_date      TEXT NOT NULL,        -- '2025-06-08'
    period          TEXT NOT NULL DEFAULT 'day',  -- 粒度: day/week/month
    main_cnt        INTEGER DEFAULT 0,   -- 净流入量(手)
    main_net        REAL DEFAULT 0.0,    -- 净流入额(万元)

    -- 特大单(Huge): >= 100万
    huge_buy_cnt    INTEGER,             -- 特大单成交买方笔数
    huge_buy_net    REAL,                -- 特大单成交买方金额(万元)
    huge_sell_cnt   INTEGER,             -- 特大单成交卖方笔数
    huge_sell_net   REAL,                -- 特大单成交卖方金额(万元)

    -- 大单(Large): 20万 ~ 100万
    large_buy_cnt   INTEGER,
    large_buy_net   REAL,
    large_sell_cnt  INTEGER,
    large_sell_net  REAL,

    -- 中单(Medium): 5万 ~ 20万
    medium_buy_cnt  INTEGER,
    medium_buy_net  REAL,
    medium_sell_cnt INTEGER,
    medium_sell_net REAL,

    -- 小单(Small): 5万以下
    small_buy_cnt   INTEGER,
    small_buy_net   REAL,
    small_sell_cnt  INTEGER,
    small_sell_net  REAL,

    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (code, trade_date, period)  -- 同一天同一粒度只有一条
);
"""

# 每日行情（前复权日线）
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
    change      REAL NOT NULL DEFAULT 0.0,  -- 涨跌额
    pct_chg     REAL NOT NULL,          -- 涨跌幅(%)
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (code, trade_date)
);
"""

# 交易日历
CREATE_TRADING_DAYS_TABLE = """
CREATE TABLE IF NOT EXISTS trading_days (
    trade_date  TEXT PRIMARY KEY,       -- '2025-06-08'
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted  INTEGER NOT NULL DEFAULT 0
);
"""

# 东财概念板块行情（Tushare dc_index / DCSectorData）
CREATE_DC_SECTORS_TABLE = """
CREATE TABLE IF NOT EXISTS dc_sectors (
    ts_code         TEXT NOT NULL,      -- 概念代码（如 BK0145.DC）
    code            TEXT NOT NULL,      -- 板块代码（由 ts_code 计算，如 BK0145）
    trade_date      TEXT NOT NULL,      -- 交易日期
    name            TEXT NOT NULL,      -- 概念名称
    leading         TEXT NOT NULL DEFAULT '',  -- 领涨股票名称
    leading_code    TEXT NOT NULL DEFAULT '',  -- 领涨股票代码
    pct_change      REAL NOT NULL DEFAULT 0.0, -- 涨跌幅
    leading_pct     REAL NOT NULL DEFAULT 0.0, -- 领涨股票涨跌幅
    total_mv        REAL NOT NULL DEFAULT 0.0, -- 总市值（万元）
    turnover_rate   REAL NOT NULL DEFAULT 0.0, -- 换手率
    up_num          INTEGER NOT NULL DEFAULT 0, -- 上涨家数
    down_num        INTEGER NOT NULL DEFAULT 0, -- 下降家数
    idx_type        TEXT NOT NULL DEFAULT '',  -- 板块类型
    level           TEXT NOT NULL DEFAULT '',  -- 行业层级
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ts_code, trade_date)
);
"""

# 东财概念板块成分（Tushare dc_member / DCSectorMemberData）
CREATE_DC_SECTOR_MEMBERS_TABLE = """
CREATE TABLE IF NOT EXISTS dc_sector_members (
    trade_date  TEXT NOT NULL,          -- 交易日期
    ts_code     TEXT NOT NULL,          -- 概念代码（如 BK0145.DC）
    code        TEXT NOT NULL,          -- 板块代码（由 ts_code 计算，如 BK0145）
    con_code    TEXT NOT NULL,          -- 成分代码
    name        TEXT NOT NULL DEFAULT '', -- 成分股名称
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trade_date, ts_code, con_code)
);
"""

# 东财个股资金流向（Tushare moneyflow_dc）
# 接口文档: https://tushare.pro/document/2?doc_id=349
CREATE_DC_MONEY_FLOWS_TABLE = """
CREATE TABLE IF NOT EXISTS dc_money_flows (
    trade_date              TEXT NOT NULL,      -- 交易日期
    ts_code                 TEXT NOT NULL,      -- 股票代码（如 000001.SZ）
    code                    TEXT NOT NULL,      -- 6 位股票代码（由 ts_code 计算）
    name                    TEXT NOT NULL DEFAULT '',  -- 股票名称
    pct_change              REAL NOT NULL DEFAULT 0.0, -- 涨跌幅
    close                   REAL NOT NULL DEFAULT 0.0, -- 最新价
    net_amount              REAL NOT NULL DEFAULT 0.0, -- 今日主力净流入额（万元）
    net_amount_rate         REAL NOT NULL DEFAULT 0.0, -- 今日主力净流入净占比（%）
    buy_elg_amount          REAL NOT NULL DEFAULT 0.0, -- 今日超大单净流入额（万元）
    buy_elg_amount_rate     REAL NOT NULL DEFAULT 0.0, -- 今日超大单净流入占比（%）
    buy_lg_amount           REAL NOT NULL DEFAULT 0.0, -- 今日大单净流入额（万元）
    buy_lg_amount_rate      REAL NOT NULL DEFAULT 0.0, -- 今日大单净流入占比（%）
    buy_md_amount           REAL NOT NULL DEFAULT 0.0, -- 今日中单净流入额（万元）
    buy_md_amount_rate      REAL NOT NULL DEFAULT 0.0, -- 今日中单净流入占比（%）
    buy_sm_amount           REAL NOT NULL DEFAULT 0.0, -- 今日小单净流入额（万元）
    buy_sm_amount_rate      REAL NOT NULL DEFAULT 0.0, -- 今日小单净流入占比（%）
    created_at              TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted              INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ts_code, trade_date)
);
"""

# 通联个股资金流向（Tushare moneyflow）
# 接口文档: https://tushare.pro/document/2?doc_id=170
CREATE_TS_MONEY_FLOWS_TABLE = """
CREATE TABLE IF NOT EXISTS ts_money_flows (
    ts_code             TEXT NOT NULL,      -- TS代码（如 000001.SZ）
    code                TEXT NOT NULL,      -- 6 位股票代码（由 ts_code 计算）
    trade_date          TEXT NOT NULL,      -- 交易日期
    buy_sm_vol          INTEGER NOT NULL DEFAULT 0,  -- 小单买入量（手）
    buy_sm_amount       REAL NOT NULL DEFAULT 0.0,   -- 小单买入金额（万元）
    sell_sm_vol         INTEGER NOT NULL DEFAULT 0,  -- 小单卖出量（手）
    sell_sm_amount      REAL NOT NULL DEFAULT 0.0,   -- 小单卖出金额（万元）
    buy_md_vol          INTEGER NOT NULL DEFAULT 0,  -- 中单买入量（手）
    buy_md_amount       REAL NOT NULL DEFAULT 0.0,   -- 中单买入金额（万元）
    sell_md_vol         INTEGER NOT NULL DEFAULT 0,  -- 中单卖出量（手）
    sell_md_amount      REAL NOT NULL DEFAULT 0.0,   -- 中单卖出金额（万元）
    buy_lg_vol          INTEGER NOT NULL DEFAULT 0,  -- 大单买入量（手）
    buy_lg_amount       REAL NOT NULL DEFAULT 0.0,   -- 大单买入金额（万元）
    sell_lg_vol         INTEGER NOT NULL DEFAULT 0,  -- 大单卖出量（手）
    sell_lg_amount      REAL NOT NULL DEFAULT 0.0,   -- 大单卖出金额（万元）
    buy_elg_vol         INTEGER NOT NULL DEFAULT 0,  -- 特大单买入量（手）
    buy_elg_amount      REAL NOT NULL DEFAULT 0.0,   -- 特大单买入金额（万元）
    sell_elg_vol        INTEGER NOT NULL DEFAULT 0,  -- 特大单卖出量（手）
    sell_elg_amount     REAL NOT NULL DEFAULT 0.0,   -- 特大单卖出金额（万元）
    net_mf_vol          INTEGER NOT NULL DEFAULT 0,  -- 净流入量（手）
    net_mf_amount       REAL NOT NULL DEFAULT 0.0,   -- 净流入额（万元）
    created_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_deleted          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ts_code, trade_date)
);
"""

# 资金流聚合数据（与原始 money_flows 数据分离，按 code + start_date + end_date + is_accumulative 唯一标识）
CREATE_MONEY_FLOW_AGGREGATION_TABLE = """
CREATE TABLE IF NOT EXISTS money_flow_aggregation (
    code                            TEXT NOT NULL,      -- 股票代码 / 板块代码
    type                            TEXT NOT NULL,      -- 'stock' / 'sector'
    start_date                      TEXT NOT NULL,      -- 统计期的起始日期
    end_date                        TEXT NOT NULL,      -- 统计期的结束日期
    trading_days                    INTEGER DEFAULT 1,
    is_accumulative                 INTEGER NOT NULL,   -- 是否为资金流累计总和

    -- 累计主要指标
    main_net             REAL DEFAULT 0.0,
    main_cnt             INTEGER DEFAULT 0,

    -- 超大单累计
    huge_buy_net         REAL,
    huge_sell_net        REAL,
    huge_buy_cnt         INTEGER,
    huge_sell_cnt        INTEGER,

    -- 大单累计
    large_buy_net        REAL,
    large_sell_net       REAL,
    large_buy_cnt        INTEGER,
    large_sell_cnt       INTEGER,

    -- 中单累计
    medium_buy_net       REAL,
    medium_sell_net      REAL,
    medium_buy_cnt       INTEGER,
    medium_sell_cnt      INTEGER,

    -- 小单累计
    small_buy_net        REAL,
    small_sell_net       REAL,
    small_buy_cnt        INTEGER,
    small_sell_cnt       INTEGER,

    -- 元数据
    created_at                      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at                      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),

    PRIMARY KEY (code, start_date, end_date, is_accumulative)
);
"""

# 索引（加速查询）
CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_money_flows_date ON money_flows(trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_money_flows_code ON money_flows(code);",
    "CREATE INDEX IF NOT EXISTS idx_daily_quotes_code ON daily_quotes(code);",
    "CREATE INDEX IF NOT EXISTS idx_daily_quotes_date ON daily_quotes(trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_trading_days_date ON trading_days(trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_sector_members_stock ON sector_members(stock_code);",
    "CREATE INDEX IF NOT EXISTS idx_sectors_sign ON sectors(sign);",
    "CREATE INDEX IF NOT EXISTS idx_sector_change_logs_sector_version ON sector_change_logs(sector_code, version);",
    "CREATE INDEX IF NOT EXISTS idx_money_flow_agg_code_tra ON money_flow_aggregation(code, trading_days, is_accumulative);",
    "CREATE INDEX IF NOT EXISTS idx_dc_sectors_code_date ON dc_sectors(code, trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_dc_sectors_type ON dc_sectors(idx_type);",
    "CREATE INDEX IF NOT EXISTS idx_dc_sector_members_code_date ON dc_sector_members(code, trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_dc_sector_members_con ON dc_sector_members(con_code);",
    "CREATE INDEX IF NOT EXISTS idx_dc_money_flows_code_date ON dc_money_flows(code, trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_ts_money_flows_code_date ON ts_money_flows(code, trade_date);",
]

def init_db() -> None:
    """初始化数据库：创建所有表（幂等，多次运行安全）"""
    conn = get_connection()
    try:
        conn.execute("PRAGMA page_size=8192")
        conn.execute(CREATE_STOCKS_TABLE)
        conn.execute(CREATE_SECTORS_TABLE)
        conn.execute(CREATE_SECTOR_MEMBERS_TABLE)
        conn.execute(CREATE_SECTOR_CHANGE_LOGS_TABLE)
        conn.execute(CREATE_MONEY_FLOWS_TABLE)
        conn.execute(CREATE_DAILY_QUOTES_TABLE)
        conn.execute(CREATE_TRADING_DAYS_TABLE)
        conn.execute(CREATE_DC_SECTORS_TABLE)
        conn.execute(CREATE_DC_SECTOR_MEMBERS_TABLE)
        conn.execute(CREATE_DC_MONEY_FLOWS_TABLE)
        conn.execute(CREATE_TS_MONEY_FLOWS_TABLE)
        conn.execute(CREATE_MONEY_FLOW_AGGREGATION_TABLE)

        for idx in CREATE_INDEXES:
            conn.execute(idx)

        conn.commit()
        logger.info(f"数据库初始化完成: {conn.execute('PRAGMA database_list').fetchone()}")
        logger.info(f"数据库文件路径: {DB_PATH}")
    finally:
        conn.close()
