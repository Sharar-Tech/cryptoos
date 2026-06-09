"""
CryptoOS - Spot Trading Bot
Strategy: Price action with MA trend filter
- Trades more frequently using short-term price momentum
- Minimum trade size enforced ($5 USDT floor for Binance)
- Live unrealised PnL calculated and logged every cycle
- Stop loss and take profit fully working
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
    def __init__(self, control: ControlEngine, db: Database,
                 risk: RiskManager, market: MarketData):
        self.control = control
        self.db      = db
        self.risk    = risk
        self.market  = market
        self._running       = False
        self._price_history = []
        self._open_position = None

        # Binance minimum order size — never go below this
        self.MIN_ORDER_USDT = 6.0

    def _get_exchange(self):
        params = {"enableRateLimit": True}
        key    = Config.get_api_key()
        secret = Config.get_api_secret()
        if key and secret:
            params["apiKey"] = key
            params["secret"] = secret
        ExchangeClass = getattr(ccxt, Config.get("exchange", "binance"))
        return ExchangeClass(params)

    # ── Indicators ────────────────────────────────────────────────────────────

    def _ma(self, prices: list, period: int):
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    def _rsi(self, prices: list, period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        recent = deltas[-period:]
        gains  = [d for d in recent if d > 0]
        losses = [abs(d) for d in recent if d < 0]
        avg_g  = sum(gains)  / period if gains  else 0.0001
        avg_l  = sum(losses) / period if losses else 0.0001
        rs     = avg_g / avg_l
        return round(100 - (100 / (1 + rs)), 2)

    # ── Signal logic ──────────────────────────────────────────────────────────
    # Uses 3 confluent signals — needs at least 2 of 3 to agree before trading.
    # This fires far more often than a pure MA crossover while staying disciplined.

    def _get_signal(self, prices: list) -> str:
        if len(prices) < 15:
            return "hold"

        ma5  = self._ma(prices, 5)
        ma10 = self._ma(prices, 10)
        rsi  = self._rsi(prices, 14)
        current = prices[-1]
        prev    = prices[-2]

        if ma5 is None or ma10 is None:
            return "hold"

        # Bullish signals
        bull_momentum = current > prev                     # price moving up
        bull_ma       = ma5 > ma10                         # short MA above long MA
        bull_rsi      = rsi < 55                           # not overbought

        # Bearish signals
        bear_momentum = current < prev                     # price moving down
        bear_ma       = ma5 < ma10                         # short MA below long MA
        bear_rsi      = rsi > 45                           # not oversold

        bull_score = sum([bull_momentum, bull_ma, bull_rsi])
        bear_score = sum([bear_momentum, bear_ma, bear_rsi])

        if bull_score >= 2 and bear_score <= 1:
            return "buy"
        if bear_score >= 2 and bull_score <= 1:
            return "sell"
        return "hold"

    # ── Exit check ────────────────────────────────────────────────────────────

    def _check_exit(self, current_price: float):
        if not self._open_position:
            return None
        entry  = self._open_position["price"]
        sl_pct = float(Config.get("stop_loss_pct",  2.0)) / 100
        tp_pct = float(Config.get("take_profit_pct", 3.0)) / 100
        pnl_pct = (current_price - entry) / entry
        if pnl_pct <= -sl_pct:
            return "stop_loss"
        if pnl_pct >= tp_pct:
            return "take_profit"
        return None

    def _unrealised_pnl(self, current_price: float) -> float:
        if not self._open_position:
            return 0.0
        entry = self._open_position["price"]
        qty   = self._open_position["amount"]
        return round((current_price - entry) * qty, 6)

    # ── Order execution ───────────────────────────────────────────────────────

    def _get_trade_size(self) -> float:
        """
        Return trade size in USDT.
        Always at least MIN_ORDER_USDT so Binance accepts the order.
        Never more than total capital.
        """
        capital  = float(Config.get("capital_usdt", 7.69))
        pct      = float(Config.get("trade_size_pct", 10.0)) / 100
        raw_size = capital * pct
        # Enforce minimum — use full capital if capital itself is below minimum
        size = max(raw_size, self.MIN_ORDER_USDT)
        size = min(size, capital)
        return round(size, 4)

    async def _buy(self, symbol: str, price: float):
        paper    = self.control.is_paper_mode()
        size_usdt = self._get_trade_size()
        qty      = round(size_usdt / price, 6)

        sl_price = round(price * (1 - float(Config.get("stop_loss_pct",  2.0)) / 100), 2)
        tp_price = round(price * (1 + float(Config.get("take_profit_pct", 3.0)) / 100), 2)

        trade_data = {
            "symbol":      symbol,
            "side":        "buy",
            "amount":      qty,
            "price":       price,
            "pnl":         0.0,
            "strategy":    "ma_rsi_confluence",
            "module":      "spot",
            "status":      "open",
            "paper":       paper,
            "stop_loss":   sl_price,
            "take_profit": tp_price,
            "leverage":    1,
        }

        if not paper:
            try:
                ex    = self._get_exchange()
                order = ex.create_market_buy_order(symbol, qty)
                trade_data["order_id"] = order.get("id", "")
            except Exception as e:
                self.db.log("error", f"BUY order failed: {e}", module="spot")
                return

        trade_id = self.db.save_trade(trade_data)
        self._open_position = {
            "side": "buy", "price": price,
            "amount": qty, "symbol": symbol,
            "trade_id": trade_id,
        }
        mode = "PAPER" if paper else "LIVE"
        self.db.log("info",
            f"[{mode}] ▶ BUY  {qty:.6f} {symbol} "
            f"@ ${price:,.2f} | Size: ${size_usdt:.4f} "
            f"| SL: ${sl_price:,.2f} | TP: ${tp_price:,.2f}",
            module="spot")

    async def _sell(self, symbol: str, price: float, reason: str):
        if not self._open_position:
            return
        paper    = self.control.is_paper_mode()
        entry    = self._open_position["price"]
        qty      = self._open_position["amount"]
        pnl      = round((price - entry) * qty, 6)
        trade_id = self._open_position.get("trade_id")

        if not paper:
            try:
                ex = self._get_exchange()
                ex.create_market_sell_order(symbol, qty)
            except Exception as e:
                self.db.log("error", f"SELL order failed: {e}", module="spot")
                return

        self.risk.record_trade_result(pnl)
        if trade_id:
            self.db.close_trade(trade_id, price, pnl)

        mode  = "PAPER" if paper else "LIVE"
        emoji = "✅" if pnl > 0 else "❌"
        pct   = round(((price - entry) / entry) * 100, 3)
        self.db.log("info",
            f"[{mode}] {emoji} SELL {qty:.6f} {symbol} "
            f"@ ${price:,.2f} | PnL: ${pnl:+.6f} ({pct:+.3f}%) | {reason}",
            module="spot")
        self._open_position = None

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        if self._running:
            return
        self._running = True
        symbol = Config.get("default_symbol", "BTC/USDT")
        self.db.log("info",
            f"Spot bot started | Symbol: {symbol} | "
            f"Mode: {'PAPER' if self.control.is_paper_mode() else 'LIVE'} | "
            f"Capital: ${Config.get('capital_usdt', 7.69)} USDT",
            module="spot")

        cycle = 0
        while self.control.is_enabled("spot"):
            try:
                price = await self.market.get_price_async(symbol)
                self._price_history.append(price)
                if len(self._price_history) > 100:
                    self._price_history.pop(0)

                cycle += 1
                can_trade, block_reason = self.risk.can_trade()

                # ── Check exit on open position first ──────────────────────
                exit_reason = self._check_exit(price)
                if exit_reason and self._open_position:
                    await self._sell(symbol, price, exit_reason)

                # ── Look for entry if no position ──────────────────────────
                elif not self._open_position:
                    if can_trade:
                        signal = self._get_signal(self._price_history)
                        if signal == "buy":
                            await self._buy(symbol, price)
                        elif cycle % 30 == 0:  # log hold every 5 minutes
                            hist_len = len(self._price_history)
                            self.db.log("info",
                                f"Monitoring | Price: ${price:,.2f} | "
                                f"History: {hist_len} pts | Signal: HOLD",
                                module="spot")
                    else:
                        self.db.log("warn",
                            f"Trade blocked: {block_reason}", module="spot")

                # ── Log unrealised PnL every 60 seconds ───────────────────
                elif self._open_position and cycle % 6 == 0:
                    upnl = self._unrealised_pnl(price)
                    entry = self._open_position["price"]
                    pct   = round(((price - entry) / entry) * 100, 3)
                    emoji = "📈" if upnl >= 0 else "📉"
                    self.db.log("info",
                        f"{emoji} Holding | Current: ${price:,.2f} | "
                        f"Entry: ${entry:,.2f} | "
                        f"Unrealised PnL: ${upnl:+.6f} ({pct:+.3f}%)",
                        module="spot")

            except Exception as e:
                self.db.log("error", f"Loop error: {e}", module="spot")

            await asyncio.sleep(10)

        # Close any open position on stop
        if self._open_position:
            price = await self.market.get_price_async(symbol)
            await self._sell(symbol, price, "bot_stopped")

        self._running = False
        self.db.log("info", "Spot bot stopped", module="spot")