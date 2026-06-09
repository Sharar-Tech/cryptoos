"""
CryptoOS - Risk Manager
Safety firewall — enforces daily loss limits, trade sizing,
and minimum order sizes compatible with Binance.
"""
from datetime import datetime, date
from config import Config


class RiskManager:
    def __init__(self, db):
        self.db          = db
        self._daily_loss  = 0.0
        self._daily_trades = 0
        self._last_reset  = date.today()
        self.MIN_ORDER_USDT = 6.0  # Binance minimum

    def _reset_if_new_day(self):
        today = date.today()
        if today != self._last_reset:
            self._daily_loss   = 0.0
            self._daily_trades = 0
            self._last_reset   = today

    def record_trade_result(self, pnl: float):
        self._reset_if_new_day()
        if pnl < 0:
            self._daily_loss += abs(pnl)
        self._daily_trades += 1

    def daily_loss_exceeded(self) -> bool:
        self._reset_if_new_day()
        limit   = float(Config.get("daily_loss_limit_pct", 5.0))
        capital = float(Config.get("capital_usdt", 7.69))
        max_loss = capital * (limit / 100)
        return self._daily_loss >= max_loss

    def max_trades_exceeded(self) -> bool:
        self._reset_if_new_day()
        return self._daily_trades >= int(Config.get("max_daily_trades", 20))

    def get_trade_size(self) -> float:
        """
        Returns USDT amount for one trade.
        Enforces Binance minimum of $6 USDT.
        """
        capital = float(Config.get("capital_usdt", 7.69))
        pct     = float(Config.get("trade_size_pct", 10.0)) / 100
        raw     = capital * pct
        size    = max(raw, self.MIN_ORDER_USDT)
        size    = min(size, capital)
        return round(size, 4)

    def can_trade(self) -> tuple:
        if self.daily_loss_exceeded():
            return False, "Daily loss limit reached — resets midnight"
        if self.max_trades_exceeded():
            return False, "Max daily trades reached"
        return True, "ok"

    def get_daily_summary(self) -> dict:
        self._reset_if_new_day()
        capital = float(Config.get("capital_usdt", 7.69))
        limit   = float(Config.get("daily_loss_limit_pct", 5.0))
        return {
            "daily_loss_usd":       round(self._daily_loss, 6),
            "daily_loss_limit_usd": round(capital * (limit / 100), 6),
            "daily_trades":         self._daily_trades,
            "max_daily_trades":     int(Config.get("max_daily_trades", 20)),
            "trade_size_usd":       self.get_trade_size(),
            "capital_usd":          capital,
            "safe_to_trade":        not self.daily_loss_exceeded(),
        }