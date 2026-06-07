"""
Database - SQLite (FREE, no setup needed)
SQLite works perfectly for personal trading bots
For production scale, upgrade to Supabase free tier
"""
import sqlite3
import os
import json
from datetime import datetime, timedelta


DB_PATH = os.environ.get("DB_PATH", "cryptoos.db")


class Database:
    def __init__(self):
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                amount REAL,
                price REAL,
                exit_price REAL,
                pnl REAL DEFAULT 0,
                strategy TEXT,
                status TEXT DEFAULT 'open',
                paper INTEGER DEFAULT 1,
                order_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                closed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT,
                module TEXT DEFAULT 'system',
                message TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_usdt REAL,
                btc_balance REAL DEFAULT 0,
                eth_balance REAL DEFAULT 0,
                pnl_today REAL DEFAULT 0,
                recorded_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS staking_positions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                asset           TEXT NOT NULL,
                amount_staked   REAL DEFAULT 0,
                apr             REAL DEFAULT 0,
                rewards_earned  REAL DEFAULT 0,
                status          TEXT DEFAULT 'active',
                started_at      TEXT DEFAULT (datetime('now')),
                unlock_at       TEXT,
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
        conn.close()

    def save_trade(self, trade: dict) -> int:
        conn = self._conn()
        cur = conn.execute(
            """INSERT INTO trades (symbol, side, amount, price, pnl, strategy, status, paper, order_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade["symbol"], trade["side"], trade["amount"], trade["price"],
                trade.get("pnl", 0), trade.get("strategy", ""),
                trade.get("status", "open"), 1 if trade.get("paper", True) else 0,
                trade.get("order_id", ""),
            )
        )
        conn.commit()
        trade_id = cur.lastrowid
        conn.close()
        return trade_id

    def close_trade(self, trade_id: int, exit_price: float, pnl: float):
        conn = self._conn()
        conn.execute(
            "UPDATE trades SET status='closed', exit_price=?, pnl=?, closed_at=datetime('now') WHERE id=?",
            (exit_price, pnl, trade_id)
        )
        conn.commit()
        conn.close()

    def get_trades(self, limit: int = 50) -> list:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_open_trades(self) -> list:
        conn = self._conn()
        rows = conn.execute("SELECT * FROM trades WHERE status='open'").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        conn = self._conn()
        total = conn.execute("SELECT COUNT(*) as c FROM trades WHERE status='closed'").fetchone()["c"]
        wins = conn.execute("SELECT COUNT(*) as c FROM trades WHERE status='closed' AND pnl > 0").fetchone()["c"]
        total_pnl = conn.execute("SELECT SUM(pnl) as s FROM trades WHERE status='closed'").fetchone()["s"] or 0
        open_count = conn.execute("SELECT COUNT(*) as c FROM trades WHERE status='open'").fetchone()["c"]
        conn.close()
        win_rate = round((wins / total * 100) if total > 0 else 0, 1)
        return {
            "total_trades": total,
            "open_trades": open_count,
            "wins": wins,
            "losses": total - wins,
            "win_rate": win_rate,
            "total_pnl": round(total_pnl, 4),
        }

    def get_portfolio_history(self, days: int = 30) -> list:
        conn = self._conn()
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE recorded_at > ? ORDER BY recorded_at",
            (since,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def save_portfolio_snapshot(self, data: dict):
        conn = self._conn()
        conn.execute(
            "INSERT INTO portfolio_snapshots (total_usdt, btc_balance, eth_balance, pnl_today) VALUES (?,?,?,?)",
            (data.get("total_usdt", 0), data.get("btc", 0), data.get("eth", 0), data.get("pnl_today", 0))
        )
        conn.commit()
        conn.close()

    def log(self, level: str, message: str, module: str = "system"):
        conn = self._conn()
        conn.execute("INSERT INTO logs (level, module, message) VALUES (?,?,?)", (level, module, message))
        conn.commit()
        conn.close()
        print(f"[{level.upper()}][{module}] {message}")

    def get_logs(self, limit: int = 100) -> list:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
