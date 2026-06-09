"""
CryptoOS - Futures Trading Bot
Trades: BTC/USDT perpetual futures
Strategy: RSI momentum + MA trend confirmation
Safety: Hard 3x leverage cap, liquidation buffer, auto-close on stop
WARNING: Only enable after spot bot is profitable for 2+ weeks
"""
import asyncio
import ccxt
from datetime import datetime
from config import Config
from database import Database
from control_engine import ControlEngine
from risk_manager import RiskManager
from market_data import MarketData


def _rsi(prices: list, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    gains  = [d for d in recent if d > 0]
    losses = [abs(d) for d in recent if d < 0]
    avg_g  = sum(gains)  / period if gains  else 0.0001
    avg_l  = sum(losses) / period if losses else 0.0001
    return round(100 - (100 / (1 + avg_g / avg_l)), 2)


def _ma(prices: list, period: int):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


class FuturesBot:
    def __init__(self, control: ControlEngine, db: Database,
                 risk: RiskManager, market: MarketData):
        self.control = control
        self.db      = db
        self.risk    = risk
        self.market  = market
        self._running       = False
        self._price_history = []
        self._open_position = None

        self.MAX_LEVERAGE        = 3
        self.LIQUIDATION_BUFFER  = 0.15
        self.MIN_ORDER_USDT      = 6.0

    def _get_exchange(self):
        params = {
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
        key    = Config.get_api_key()
        secret = Config.get_api_secret()
        if key and secret:
            params["apiKey"] = key
            params["secret"] = secret
        ExchangeClass = getattr(ccxt, Config.get("exchange", "binance"))
        return ExchangeClass(params)

    def _get_leverage(self) -> int:
        return min(int(Config.get("max_leverage", 2)), self.MAX_LEVERAGE)

    def _get_trade_size(self) -> float:
        capital = float(Config.get("capital_usdt", 7.69))
        pct     = float(Config.get("trade_size_pct", 100.0)) / 100
        raw     = capital * pct
        size    = max(raw, self.MIN_ORDER_USDT)
        return round(min(size, capital), 4)

    # ── Signal ────────────────────────────────────────────────────────────────
    # Long:  RSI < 45 (momentum dipping) + price above MA (trend still up)
    # Short: RSI > 55 (momentum rising)  + price below MA (trend down)
    # Wider thresholds than extreme RSI so signals fire regularly

    def _get_signal(self, prices: list) -> str:
        if len(prices) < 15:
            return "hold"
        rsi_val  = _rsi(prices, 14)
        ma10     = _ma(prices, 10)
        ma5      = _ma(prices, 5)
        current  = prices[-1]
        if ma10 is None or ma5 is None:
            return "hold"

        long_signal  = rsi_val < 45 and current > ma10 and ma5 > ma10
        short_signal = rsi_val > 55 and current < ma10 and ma5 < ma10

        if long_signal:
            return "long"
        if short_signal:
            return "short"
        return "hold"

    # ── Exit ──────────────────────────────────────────────────────────────────

    def _check_exit(self, current_price: float):
        if not self._open_position:
            return None
        entry    = self._open_position["price"]
        side     = self._open_position["side"]
        leverage = self._open_position["leverage"]
        sl_pct   = float(Config.get("stop_loss_pct",  2.0)) / 100
        tp_pct   = float(Config.get("take_profit_pct", 3.0)) / 100

        if side == "long":
            pnl_pct   = (current_price - entry) / entry
            liq_price = entry * (1 - (1/leverage) + self.LIQUIDATION_BUFFER/leverage)
            if current_price <= liq_price: return "liquidation_protection"
            if pnl_pct <= -sl_pct:         return "stop_loss"
            if pnl_pct >= tp_pct:          return "take_profit"
        elif side == "short":
            pnl_pct   = (entry - current_price) / entry
            liq_price = entry * (1 + (1/leverage) - self.LIQUIDATION_BUFFER/leverage)
            if current_price >= liq_price: return "liquidation_protection"
            if pnl_pct <= -sl_pct:         return "stop_loss"
            if pnl_pct >= tp_pct:          return "take_profit"
        return None

    def _calc_pnl(self, entry, exit_price, amount, side, leverage) -> float:
        raw = (exit_price - entry) * amount if side == "long" \
              else (entry - exit_price) * amount
        return round(raw * leverage, 6)

    def _unrealised_pnl(self, current_price: float) -> float:
        if not self._open_position:
            return 0.0
        pos = self._open_position
        return self._calc_pnl(
            pos["price"], current_price,
            pos["amount"], pos["side"], pos["leverage"]
        )

    # ── Open long ─────────────────────────────────────────────────────────────

    async def _open_long(self, symbol: str, price: float):
        leverage  = self._get_leverage()
        size_usdt = self._get_trade_size()
        qty       = round(size_usdt / price, 6)
        paper     = self.control.is_paper_mode()
        sl_price  = round(price * (1 - float(Config.get("stop_loss_pct",  2)) / 100), 2)
        tp_price  = round(price * (1 + float(Config.get("take_profit_pct", 3)) / 100), 2)

        trade_data = {
            "symbol": symbol, "side": "long", "amount": qty,
            "price": price, "pnl": 0.0, "strategy": "rsi_ma_futures",
            "module": "futures", "status": "open", "paper": paper,
            "leverage": leverage, "stop_loss": sl_price, "take_profit": tp_price,
        }

        if not paper:
            try:
                ex = self._get_exchange()
                ex.set_leverage(leverage, symbol)
                ex.create_market_buy_order(symbol, qty, {"type": "future"})
            except Exception as e:
                self.db.log("error", f"Long order failed: {e}", module="futures")
                return

        trade_id = self.db.save_trade(trade_data)
        self._open_position = {
            "side": "long", "price": price, "amount": qty,
            "leverage": leverage, "trade_id": trade_id,
        }
        mode = "PAPER" if paper else "LIVE"
        self.db.log("info",
            f"[{mode}] ▶ LONG  {qty:.6f} {symbol} @ ${price:,.2f} "
            f"| {leverage}x | SL: ${sl_price:,.2f} | TP: ${tp_price:,.2f}",
            module="futures")

    # ── Open short ────────────────────────────────────────────────────────────

    async def _open_short(self, symbol: str, price: float):
        leverage  = self._get_leverage()
        size_usdt = self._get_trade_size()
        qty       = round(size_usdt / price, 6)
        paper     = self.control.is_paper_mode()
        sl_price  = round(price * (1 + float(Config.get("stop_loss_pct",  2)) / 100), 2)
        tp_price  = round(price * (1 - float(Config.get("take_profit_pct", 3)) / 100), 2)

        trade_data = {
            "symbol": symbol, "side": "short", "amount": qty,
            "price": price, "pnl": 0.0, "strategy": "rsi_ma_futures",
            "module": "futures", "status": "open", "paper": paper,
            "leverage": leverage, "stop_loss": sl_price, "take_profit": tp_price,
        }

        if not paper:
            try:
                ex = self._get_exchange()
                ex.set_leverage(leverage, symbol)
                ex.create_market_sell_order(symbol, qty, {"type": "future"})
            except Exception as e:
                self.db.log("error", f"Short order failed: {e}", module="futures")
                return

        trade_id = self.db.save_trade(trade_data)
        self._open_position = {
            "side": "short", "price": price, "amount": qty,
            "leverage": leverage, "trade_id": trade_id,
        }
        mode = "PAPER" if paper else "LIVE"
        self.db.log("info",
            f"[{mode}] ▼ SHORT {qty:.6f} {symbol} @ ${price:,.2f} "
            f"| {leverage}x | SL: ${sl_price:,.2f} | TP: ${tp_price:,.2f}",
            module="futures")

    # ── Close position ────────────────────────────────────────────────────────

    async def _close_position(self, symbol: str, price: float, reason: str):
        if not self._open_position:
            return
        paper    = self.control.is_paper_mode()
        pos      = self._open_position
        pnl      = self._calc_pnl(pos["price"], price, pos["amount"],
                                   pos["side"], pos["leverage"])
        trade_id = pos.get("trade_id")

        if not paper:
            try:
                ex   = self._get_exchange()
                side = pos["side"]
                if side == "long":
                    ex.create_market_sell_order(
                        symbol, pos["amount"],
                        {"type": "future", "reduceOnly": True})
                else:
                    ex.create_market_buy_order(
                        symbol, pos["amount"],
                        {"type": "future", "reduceOnly": True})
            except Exception as e:
                self.db.log("error", f"Close failed: {e}", module="futures")
                return

        self.risk.record_trade_result(pnl)
        if trade_id:
            self.db.close_trade(trade_id, price, pnl)

        entry  = pos["price"]
        pct    = round(((price - entry) / entry) * 100 * pos["leverage"], 3)
        emoji  = "✅" if pnl > 0 else "❌"
        mode   = "PAPER" if paper else "LIVE"
        self.db.log("info",
            f"[{mode}] {emoji} CLOSE {pos['side'].upper()} @ ${price:,.2f} "
            f"| PnL: ${pnl:+.6f} ({pct:+.3f}%) | {reason}",
            module="futures")
        self._open_position = None

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        if self._running:
            return
        self._running = True
        symbol = Config.get("default_symbol", "BTC/USDT")
        self.db.log("warn",
            f"Futures bot started | Symbol: {symbol} | "
            f"Mode: {'PAPER' if self.control.is_paper_mode() else 'LIVE'} | "
            f"Max leverage: {self._get_leverage()}x",
            module="futures")

        cycle = 0
        while self.control.is_enabled("futures"):
            try:
                price = await self.market.get_price_async(symbol)
                self._price_history.append(price)
                if len(self._price_history) > 100:
                    self._price_history.pop(0)

                cycle += 1
                can_trade, block_reason = self.risk.can_trade()

                exit_reason = self._check_exit(price)
                if exit_reason and self._open_position:
                    await self._close_position(symbol, price, exit_reason)

                elif not self._open_position and can_trade:
                    signal = self._get_signal(self._price_history)
                    if signal == "long":
                        await self._open_long(symbol, price)
                    elif signal == "short":
                        await self._open_short(symbol, price)
                    elif cycle % 20 == 0:
                        rsi_val = _rsi(self._price_history, 14)
                        self.db.log("info",
                            f"Monitoring | ${price:,.2f} | "
                            f"RSI: {rsi_val:.1f} | History: {len(self._price_history)} pts",
                            module="futures")

                elif not can_trade:
                    self.db.log("warn", f"Trade blocked: {block_reason}",
                                module="futures")

                # Log unrealised PnL every 60 seconds
                if self._open_position and cycle % 4 == 0:
                    upnl  = self._unrealised_pnl(price)
                    entry = self._open_position["price"]
                    pct   = round(((price - entry) / entry) * 100, 3)
                    emoji = "📈" if upnl >= 0 else "📉"
                    self.db.log("info",
                        f"{emoji} Holding {self._open_position['side'].upper()} | "
                        f"Current: ${price:,.2f} | Entry: ${entry:,.2f} | "
                        f"Unrealised PnL: ${upnl:+.6f} ({pct:+.3f}%)",
                        module="futures")

            except Exception as e:
                self.db.log("error", f"Loop error: {e}", module="futures")

            await asyncio.sleep(15)

        if self._open_position:
            price = await self.market.get_price_async(symbol)
            await self._close_position(symbol, price, "bot_stopped")

        self._running = False
        self.db.log("info", "Futures bot stopped", module="futures")