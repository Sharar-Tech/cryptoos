"""
Risk Manager - Your safety firewall
Prevents blowing your account
"""
from datetime import datetime, date
from config import Config


class RiskManager:
    def __init__(self, db):
        self.db = db
        self._daily_loss = 0.0
        self._daily_trades = 0
        self._last_reset = date.today()

    def _reset_if_new_day(self):
        today = date.today()
        if today != self._last_reset:
            self._daily_loss = 0.0
            self._daily_trades = 0
            self._last_reset = today

    def record_trade_result(self, pnl: float):
        self._reset_if_new_day()
        if pnl < 0:
            self._daily_loss += abs(pnl)
        self._daily_trades += 1

    def daily_loss_exceeded(self) -> bool:
        self._reset_if_new_day()
        limit = Config.get("daily_loss_limit_pct", 5.0)
        capital = Config.get("capital_usdt", 10.0)
        max_loss = capital * (limit / 100)
        return self._daily_loss >= max_loss

    def max_trades_exceeded(self) -> bool:
        self._reset_if_new_day()
        max_trades = Config.get("max_daily_trades", 20)
        return self._daily_trades >= max_trades

    def get_trade_size(self) -> float:
        """How much USDT to use per trade — safe sizing"""
        capital = Config.get("capital_usdt", 10.0)
        pct = Config.get("trade_size_pct", 10.0)  # 10% per trade by default
        return round(capital * (pct / 100), 2)

    def can_trade(self) -> tuple[bool, str]:
        if self.daily_loss_exceeded():
            return False, "Daily loss limit reached"
        if self.max_trades_exceeded():
            return False, "Max daily trades reached"
        return True, "ok"

    def get_daily_summary(self) -> dict:
        self._reset_if_new_day()
        capital = Config.get("capital_usdt", 10.0)
        limit = Config.get("daily_loss_limit_pct", 5.0)
        return {
            "daily_loss_usd": round(self._daily_loss, 4),
            "daily_loss_limit_usd": round(capital * (limit / 100), 4),
            "daily_trades": self._daily_trades,
            "max_daily_trades": Config.get("max_daily_trades", 20),
            "trade_size_usd": self.get_trade_size(),
            "capital_usd": capital,
            "safe_to_trade": not self.daily_loss_exceeded(),
        }
