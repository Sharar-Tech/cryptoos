"""
Futures Trading Bot
Supports: Long / Short positions
Strategy: RSI + MA confirmation
Safety: Hard leverage cap, liquidation buffer, auto-close on loss
WARNING: Only activate after you're profitable on spot first
"""
import asyncio
import ccxt
from datetime import datetime
from config import Config
from database import Database
from control_engine import ControlEngine
from risk_manager import RiskManager
from market_data import MarketData


# ─── RSI Calculation ──────────────────────────────────────────────────────────
def calculate_rsi(prices: list, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0  # Neutral if not enough data

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d for d in deltas[-period:] if d > 0]
    losses = [abs(d) for d in deltas[-period:] if d < 0]

    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.0001

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def moving_average(prices: list, period: int) -> float | None:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


# ─── Futures Bot ──────────────────────────────────────────────────────────────
class FuturesBot:
    def __init__(self, control: ControlEngine, db: Database, risk: RiskManager, market: MarketData):
        self.control = control
        self.db = db
        self.risk = risk
        self.market = market
        self._running = False
        self._price_history = []
        self._open_position = None  # {"side": "long"/"short", "price": X, "amount": Y, "leverage": N}

        # Hard safety caps — never exceeded
        self.MAX_LEVERAGE = 3        # Never go above 3x
        self.LIQUIDATION_BUFFER = 0.15  # Close before liquidation (15% buffer)

    def _get_exchange(self):
        params = {
            "enableRateLimit": True,
            "options": {"defaultType": "future"},  # Futures mode
        }
        api_key = Config.get_api_key()
        api_secret = Config.get_api_secret()
        if api_key:
            params["apiKey"] = api_key
            params["secret"] = api_secret
        exchange_name = Config.get("exchange", "binance")
        ExchangeClass = getattr(ccxt, exchange_name)
        return ExchangeClass(params)

    def _get_leverage(self) -> int:
        configured = int(Config.get("max_leverage", 2))
        return min(configured, self.MAX_LEVERAGE)

    def _get_signal(self, prices: list) -> str:
        """
        Signal logic:
        - RSI < 30 + price above short MA → LONG (oversold bounce)
        - RSI > 70 + price below short MA → SHORT (overbought drop)
        - Otherwise → HOLD
        """
        if len(prices) < 20:
            return "hold"

        rsi = calculate_rsi(prices, 14)
        ma10 = moving_average(prices, 10)
        current = prices[-1]

        if ma10 is None:
            return "hold"

        if rsi < 30 and current > ma10:
            return "long"
        if rsi > 70 and current < ma10:
            return "short"
        return "hold"

    def _check_exit(self, current_price: float) -> str | None:
        """
        Exit conditions:
        1. Stop loss hit
        2. Take profit hit
        3. Approaching liquidation price (safety buffer)
        """
        if not self._open_position:
            return None

        entry = self._open_position["price"]
        side = self._open_position["side"]
        leverage = self._open_position["leverage"]
        sl_pct = Config.get("stop_loss_pct", 2.0) / 100
        tp_pct = Config.get("take_profit_pct", 3.0) / 100

        if side == "long":
            pnl_pct = (current_price - entry) / entry
            liq_price = entry * (1 - (1 / leverage) + self.LIQUIDATION_BUFFER / leverage)
            if current_price <= liq_price:
                return "liquidation_protection"
            if pnl_pct <= -sl_pct:
                return "stop_loss"
            if pnl_pct >= tp_pct:
                return "take_profit"

        elif side == "short":
            pnl_pct = (entry - current_price) / entry
            liq_price = entry * (1 + (1 / leverage) - self.LIQUIDATION_BUFFER / leverage)
            if current_price >= liq_price:
                return "liquidation_protection"
            if pnl_pct <= -sl_pct:
                return "stop_loss"
            if pnl_pct >= tp_pct:
                return "take_profit"

        return None

    def _calc_pnl(self, entry: float, exit_price: float, amount: float, side: str, leverage: int) -> float:
        if side == "long":
            raw = (exit_price - entry) * amount
        else:
            raw = (entry - exit_price) * amount
        return round(raw * leverage, 6)

    async def _open_long(self, symbol: str, price: float):
        leverage = self._get_leverage()
        size_usdt = self.risk.get_trade_size() * leverage
        qty = round(size_usdt / price, 6)
        paper = self.control.is_paper_mode()

        trade_data = {
            "symbol": symbol, "side": "long", "amount": qty,
            "price": price, "pnl": 0.0, "strategy": "rsi_ma",
            "module": "futures", "status": "open", "paper": paper,
            "leverage": leverage,
            "stop_loss": round(price * (1 - Config.get("stop_loss_pct", 2) / 100), 4),
            "take_profit": round(price * (1 + Config.get("take_profit_pct", 3) / 100), 4),
        }

        if not paper:
            try:
                ex = self._get_exchange()
                ex.set_leverage(leverage, symbol)
                ex.create_market_buy_order(symbol, qty, {"type": "future"})
            except Exception as e:
                self.db.log("error", f"[FUTURES] Long order failed: {e}", module="futures")
                return

        self._open_position = {"side": "long", "price": price, "amount": qty, "leverage": leverage}
        trade_id = self.db.save_trade(trade_data)
        self._open_position["trade_id"] = trade_id
        mode = "PAPER" if paper else "LIVE"
        self.db.log("info", f"[{mode}][FUTURES] LONG {qty} {symbol} @ ${price:.2f} | {leverage}x leverage", module="futures")

    async def _open_short(self, symbol: str, price: float):
        leverage = self._get_leverage()
        size_usdt = self.risk.get_trade_size() * leverage
        qty = round(size_usdt / price, 6)
        paper = self.control.is_paper_mode()

        trade_data = {
            "symbol": symbol, "side": "short", "amount": qty,
            "price": price, "pnl": 0.0, "strategy": "rsi_ma",
            "module": "futures", "status": "open", "paper": paper,
            "leverage": leverage,
            "stop_loss": round(price * (1 + Config.get("stop_loss_pct", 2) / 100), 4),
            "take_profit": round(price * (1 - Config.get("take_profit_pct", 3) / 100), 4),
        }

        if not paper:
            try:
                ex = self._get_exchange()
                ex.set_leverage(leverage, symbol)
                ex.create_market_sell_order(symbol, qty, {"type": "future"})
            except Exception as e:
                self.db.log("error", f"[FUTURES] Short order failed: {e}", module="futures")
                return

        self._open_position = {"side": "short", "price": price, "amount": qty, "leverage": leverage}
        trade_id = self.db.save_trade(trade_data)
        self._open_position["trade_id"] = trade_id
        mode = "PAPER" if paper else "LIVE"
        self.db.log("info", f"[{mode}][FUTURES] SHORT {qty} {symbol} @ ${price:.2f} | {leverage}x leverage", module="futures")

    async def _close_position(self, symbol: str, current_price: float, reason: str):
        if not self._open_position:
            return

        paper = self.control.is_paper_mode()
        pos = self._open_position
        pnl = self._calc_pnl(pos["price"], current_price, pos["amount"], pos["side"], pos["leverage"])
        trade_id = pos.get("trade_id")

        if not paper:
            try:
                ex = self._get_exchange()
                # Close opposite side
                if pos["side"] == "long":
                    ex.create_market_sell_order(symbol, pos["amount"], {"type": "future", "reduceOnly": True})
                else:
                    ex.create_market_buy_order(symbol, pos["amount"], {"type": "future", "reduceOnly": True})
            except Exception as e:
                self.db.log("error", f"[FUTURES] Close failed: {e}", module="futures")
                return

        self.risk.record_trade_result(pnl)
        if trade_id:
            self.db.close_trade(trade_id, current_price, pnl)

        mode = "PAPER" if paper else "LIVE"
        emoji = "✅" if pnl > 0 else "❌"
        self.db.log(
            "info",
            f"[{mode}][FUTURES] {emoji} CLOSE {pos['side'].upper()} @ ${current_price:.2f} | PnL: ${pnl:.4f} | {reason}",
            module="futures"
        )
        self._open_position = None

    async def run(self):
        if self._running:
            return
        self._running = True
        symbol = Config.get("default_symbol", "BTC/USDT")
        self.db.log("warn", f"⚠️ Futures bot started | Symbol: {symbol} | Paper: {self.control.is_paper_mode()}", module="futures")

        while self.control.is_enabled("futures"):
            try:
                price = await self.market.get_price_async(symbol)
                self._price_history.append(price)
                if len(self._price_history) > 200:
                    self._price_history.pop(0)

                can_trade, reason = self.risk.can_trade()

                # Always check exits first
                exit_reason = self._check_exit(price)
                if exit_reason and self._open_position:
                    await self._close_position(symbol, price, exit_reason)

                elif not self._open_position and can_trade:
                    signal = self._get_signal(self._price_history)
                    if signal == "long":
                        await self._open_long(symbol, price)
                    elif signal == "short":
                        await self._open_short(symbol, price)

                elif not can_trade:
                    self.db.log("warn", f"[FUTURES] Trade blocked: {reason}", module="futures")

            except Exception as e:
                self.db.log("error", f"[FUTURES] Loop error: {e}", module="futures")

            await asyncio.sleep(15)  # Futures checks every 15 seconds

        # Close any open position when bot is stopped
        if self._open_position:
            price = await self.market.get_price_async(symbol)
            await self._close_position(symbol, price, "bot_stopped")

        self._running = False
        self.db.log("info", "Futures bot stopped", module="futures")
