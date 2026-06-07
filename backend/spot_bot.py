"""
Spot Trading Bot
Strategy: Moving Average Crossover (50 MA vs 200 MA)
With stop-loss and take-profit
"""
import asyncio
import ccxt
from datetime import datetime
from config import Config
from database import Database
from control_engine import ControlEngine
from risk_manager import RiskManager
from market_data import MarketData


class SpotBot:
    def __init__(self, control: ControlEngine, db: Database, risk: RiskManager, market: MarketData):
        self.control = control
        self.db = db
        self.risk = risk
        self.market = market
        self._running = False
        self._price_history = []
        self._open_position = None  # {"side": "buy", "price": X, "amount": Y, "symbol": Z}

    def _get_exchange(self):
        api_key = Config.get_api_key()
        api_secret = Config.get_api_secret()
        exchange_name = Config.get("exchange", "binance")
        params = {"enableRateLimit": True}
        if api_key and api_secret:
            params["apiKey"] = api_key
            params["secret"] = api_secret
        ExchangeClass = getattr(ccxt, exchange_name)
        return ExchangeClass(params)

    def _moving_average(self, prices: list, period: int) -> float:
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    def _check_signal(self, prices: list) -> str:
        """Returns 'buy', 'sell', or 'hold'"""
        if len(prices) < 20:
            return "hold"

        short_ma = self._moving_average(prices, min(10, len(prices)))
        long_ma = self._moving_average(prices, min(20, len(prices)))

        if short_ma is None or long_ma is None:
            return "hold"

        prev_short = self._moving_average(prices[:-1], min(10, len(prices) - 1))
        prev_long = self._moving_average(prices[:-1], min(20, len(prices) - 1))

        if prev_short and prev_long:
            # Golden cross: short MA crosses above long MA → BUY
            if prev_short <= prev_long and short_ma > long_ma:
                return "buy"
            # Death cross: short MA crosses below long MA → SELL
            if prev_short >= prev_long and short_ma < long_ma:
                return "sell"

        return "hold"

    def _check_exit(self, current_price: float) -> str:
        """Check if we should exit current position"""
        if not self._open_position:
            return "hold"

        entry = self._open_position["price"]
        side = self._open_position["side"]
        sl_pct = Config.get("stop_loss_pct", 2.0) / 100
        tp_pct = Config.get("take_profit_pct", 3.0) / 100

        if side == "buy":
            pnl_pct = (current_price - entry) / entry
            if pnl_pct <= -sl_pct:
                return "stop_loss"
            if pnl_pct >= tp_pct:
                return "take_profit"
        return "hold"

    async def _execute_buy(self, symbol: str, amount_usdt: float, price: float):
        paper = self.control.is_paper_mode()
        qty = round(amount_usdt / price, 6)
        trade_data = {
            "symbol": symbol,
            "side": "buy",
            "amount": qty,
            "price": price,
            "pnl": 0.0,
            "strategy": "ma_crossover",
            "status": "open",
            "paper": paper,
        }

        if not paper:
            try:
                ex = self._get_exchange()
                order = ex.create_market_buy_order(symbol, qty)
                trade_data["order_id"] = order.get("id", "")
            except Exception as e:
                self.db.log("error", f"Buy order failed: {e}")
                return

        self._open_position = {"side": "buy", "price": price, "amount": qty, "symbol": symbol}
        trade_id = self.db.save_trade(trade_data)
        self._open_position["trade_id"] = trade_id
        mode = "PAPER" if paper else "LIVE"
        self.db.log("info", f"[{mode}] BUY {qty} {symbol} @ ${price:.2f} | Size: ${amount_usdt:.2f}")

    async def _execute_sell(self, symbol: str, reason: str, current_price: float):
        if not self._open_position:
            return

        paper = self.control.is_paper_mode()
        entry = self._open_position["price"]
        qty = self._open_position["amount"]
        pnl = (current_price - entry) * qty
        trade_id = self._open_position.get("trade_id")

        if not paper:
            try:
                ex = self._get_exchange()
                ex.create_market_sell_order(symbol, qty)
            except Exception as e:
                self.db.log("error", f"Sell order failed: {e}")
                return

        self.risk.record_trade_result(pnl)
        if trade_id:
            self.db.close_trade(trade_id, current_price, pnl)

        mode = "PAPER" if paper else "LIVE"
        emoji = "✅" if pnl > 0 else "❌"
        self.db.log("info", f"[{mode}] {emoji} SELL {qty} {symbol} @ ${current_price:.2f} | PnL: ${pnl:.4f} | Reason: {reason}")
        self._open_position = None

    async def run(self):
        if self._running:
            return
        self._running = True
        symbol = Config.get("default_symbol", "BTC/USDT")
        self.db.log("info", f"Spot bot started | Symbol: {symbol} | Paper: {self.control.is_paper_mode()}")

        while self.control.is_enabled("spot"):
            try:
                price = await self.market.get_price_async(symbol)
                self._price_history.append(price)

                # Keep last 200 prices
                if len(self._price_history) > 200:
                    self._price_history.pop(0)

                # Check safety
                can_trade, reason = self.risk.can_trade()

                # Check exit first
                exit_signal = self._check_exit(price)
                if exit_signal in ("stop_loss", "take_profit") and self._open_position:
                    await self._execute_sell(symbol, exit_signal, price)

                # Check entry
                elif not self._open_position and can_trade:
                    signal = self._check_signal(self._price_history)
                    if signal == "buy":
                        size = self.risk.get_trade_size()
                        await self._execute_buy(symbol, size, price)
                    elif signal == "sell" and self._open_position:
                        await self._execute_sell(symbol, "ma_signal", price)

                elif not can_trade and not self._open_position:
                    self.db.log("warn", f"Trade blocked: {reason}")

            except Exception as e:
                self.db.log("error", f"Spot bot error: {e}")

            await asyncio.sleep(10)  # Check every 10 seconds

        # Close open position when bot stops
        if self._open_position:
            price = await self.market.get_price_async(symbol)
            await self._execute_sell(symbol, "bot_stopped", price)

        self._running = False
        self.db.log("info", "Spot bot stopped")
