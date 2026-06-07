-- ============================================================
-- CryptoOS Database Schema
-- Engine: SQLite (free, no server needed)
-- Auto-created by database.py on first run
-- Run manually: sqlite3 cryptoos.db < schema.sql
-- ============================================================

PRAGMA journal_mode=WAL;  -- Better concurrent read performance
PRAGMA foreign_keys=ON;

-- ── BOT CONTROL STATE ────────────────────────────────────────
-- Single row that tracks which modules are ON/OFF
CREATE TABLE IF NOT EXISTS bot_control (
    id              INTEGER PRIMARY KEY DEFAULT 1,
    spot_enabled    INTEGER DEFAULT 0,
    futures_enabled INTEGER DEFAULT 0,
    staking_enabled INTEGER DEFAULT 0,
    paper_mode      INTEGER DEFAULT 1,  -- 1 = simulation, 0 = real money
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- Seed the single control row
INSERT OR IGNORE INTO bot_control (id) VALUES (1);


-- ── TRADES ───────────────────────────────────────────────────
-- Every buy/sell action the bot takes gets recorded here
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,           -- e.g. BTC/USDT
    side        TEXT    NOT NULL,           -- buy | sell | long | short
    amount      REAL    NOT NULL,           -- quantity of coin
    price       REAL    NOT NULL,           -- entry price in USDT
    exit_price  REAL,                       -- filled when trade closes
    pnl         REAL    DEFAULT 0,          -- profit or loss in USDT
    strategy    TEXT    DEFAULT 'manual',   -- ma_crossover | rsi | manual
    module      TEXT    DEFAULT 'spot',     -- spot | futures | staking
    status      TEXT    DEFAULT 'open',     -- open | closed | cancelled
    paper       INTEGER DEFAULT 1,          -- 1=paper trade, 0=real
    order_id    TEXT    DEFAULT '',         -- exchange order ID
    stop_loss   REAL,                       -- stop loss price
    take_profit REAL,                       -- take profit price
    leverage    INTEGER DEFAULT 1,          -- for futures only
    created_at  TEXT    DEFAULT (datetime('now')),
    closed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_status   ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol   ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_created  ON trades(created_at);


-- ── BOT LOGS ─────────────────────────────────────────────────
-- Every decision, signal, error the bot generates
CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    level       TEXT DEFAULT 'info',   -- info | warn | error
    module      TEXT DEFAULT 'system', -- spot | futures | staking | system
    message     TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_logs_level   ON logs(level);
CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at);


-- ── PORTFOLIO SNAPSHOTS ───────────────────────────────────────
-- Recorded every hour — used to draw the PnL chart
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    total_usdt   REAL DEFAULT 0,
    free_usdt    REAL DEFAULT 0,
    btc_balance  REAL DEFAULT 0,
    eth_balance  REAL DEFAULT 0,
    bnb_balance  REAL DEFAULT 0,
    pnl_today    REAL DEFAULT 0,
    pnl_total    REAL DEFAULT 0,
    recorded_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_portfolio_recorded ON portfolio_snapshots(recorded_at);


-- ── STAKING POSITIONS ────────────────────────────────────────
-- Tracks what's staked, APR, and accumulated rewards
CREATE TABLE IF NOT EXISTS staking_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asset           TEXT NOT NULL,       -- BTC, ETH, BNB, etc.
    amount_staked   REAL DEFAULT 0,
    apr             REAL DEFAULT 0,      -- annual percentage rate %
    rewards_earned  REAL DEFAULT 0,
    status          TEXT DEFAULT 'active', -- active | unlocked | expired
    started_at      TEXT DEFAULT (datetime('now')),
    unlock_at       TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);


-- ── SETTINGS ─────────────────────────────────────────────────
-- Runtime settings saved between restarts
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- Default settings
INSERT OR IGNORE INTO settings (key, value) VALUES
    ('capital_usdt',         '10'),
    ('trade_size_pct',       '10'),
    ('daily_loss_limit_pct', '5'),
    ('max_daily_trades',     '20'),
    ('stop_loss_pct',        '2'),
    ('take_profit_pct',      '3'),
    ('default_symbol',       'BTC/USDT'),
    ('timeframe',            '5m'),
    ('exchange',             'binance'),
    ('max_leverage',         '3');


-- ── VIEWS (useful queries pre-built) ─────────────────────────

-- Closed trades with PnL summary
CREATE VIEW IF NOT EXISTS v_trade_summary AS
SELECT
    module,
    strategy,
    COUNT(*)                        AS total_trades,
    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
    ROUND(SUM(pnl), 6)              AS total_pnl,
    ROUND(AVG(pnl), 6)              AS avg_pnl,
    ROUND(
        100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1
    )                               AS win_rate_pct
FROM trades
WHERE status = 'closed'
GROUP BY module, strategy;

-- Today's activity
CREATE VIEW IF NOT EXISTS v_today AS
SELECT
    COUNT(*)                        AS trades_today,
    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins_today,
    ROUND(SUM(pnl), 6)              AS pnl_today,
    ROUND(SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END), 6) AS loss_today
FROM trades
WHERE status = 'closed'
  AND date(closed_at) = date('now');
