"""
Market Data Service
Fetches prices, candles, and account balance via CCXT
Falls back to mock data if no API keys configured
"""
import ccxt
import asyncio
from datetime import datetime
from config import Config


class MarketData:
    def __init__(self):
        self._exchange = None
        self._price_cache = {}
        self._cache_time = {}
        self._cache_ttl = 5  # seconds

    def _get_exchange(self):
        if self._exchange is None:
            api_key = Config.get_api_key()
            api_secret = Config.get_api_secret()
            exchange_name = Config.get("exchange", "binance")

            params = {"enableRateLimit": True}
            if api_key and api_secret:
                params["apiKey"] = api_key
                params["secret"] = api_secret

            try:
                ExchangeClass = getattr(ccxt, exchange_name)
                self._exchange = ExchangeClass(params)
            except Exception:
                self._exchange = ccxt.binance({"enableRateLimit": True})

        return self._exchange

    def _is_cached(self, key: str) -> bool:
        if key not in self._cache_time:
            return False
        return (datetime.utcnow().timestamp() - self._cache_time[key]) < self._cache_ttl

    async def get_price_async(self, symbol: str = "BTC/USDT") -> float:
        return await asyncio.to_thread(self.get_price, symbol)

    def get_price(self, symbol: str = "BTC/USDT") -> float:
        if self._is_cached(symbol):
            return self._price_cache[symbol]
        try:
            ex = self._get_exchange()
            ticker = ex.fetch_ticker(symbol)
            price = ticker["last"]
            self._price_cache[symbol] = price
            self._cache_time[symbol] = datetime.utcnow().timestamp()
            return price
        except Exception as e:
            # Return cached or mock if API fails
            if symbol in self._price_cache:
                return self._price_cache[symbol]
            return self._mock_price(symbol)

    def _mock_price(self, symbol: str) -> float:
        mocks = {
            "BTC/USDT": 67500.0,
            "ETH/USDT": 3200.0,
            "BNB/USDT": 580.0,
            "SOL/USDT": 145.0,
        }
        return mocks.get(symbol, 100.0)

    async def get_prices_async(self) -> dict:
        symbols = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"]
        prices = {}
        for s in symbols:
            try:
                prices[s.replace("/", "-")] = await self.get_price_async(s)
            except Exception:
                prices[s.replace("/", "-")] = self._mock_price(s)
        return prices

    async def get_candles_async(self, symbol: str, timeframe: str = "1h", limit: int = 100):
        return await asyncio.to_thread(self.get_candles, symbol, timeframe, limit)

    def get_candles(self, symbol: str, timeframe: str = "1h", limit: int = 100):
        try:
            ex = self._get_exchange()
            ohlcv = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
            return [
                {
                    "time": int(c[0] / 1000),
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5],
                }
                for c in ohlcv
            ]
        except Exception:
            return self._mock_candles(limit)

    def _mock_candles(self, limit: int) -> list:
        import random
        candles = []
        price = 67000.0
        ts = int(datetime.utcnow().timestamp()) - (limit * 3600)
        for i in range(limit):
            change = random.uniform(-0.02, 0.02)
            open_p = price
            close_p = price * (1 + change)
            high_p = max(open_p, close_p) * random.uniform(1.0, 1.01)
            low_p = min(open_p, close_p) * random.uniform(0.99, 1.0)
            candles.append({
                "time": ts + (i * 3600),
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "volume": round(random.uniform(10, 100), 4),
            })
            price = close_p
        return candles

    async def get_balance_async(self) -> dict:
        return await asyncio.to_thread(self.get_balance)

    def get_balance(self) -> dict:
        api_key = Config.get_api_key()
        if not api_key:
            # Paper mode mock balance
            capital = Config.get("capital_usdt", 10.0)
            return {
                "total_usdt": capital,
                "free_usdt": capital,
                "BTC": 0.0,
                "ETH": 0.0,
                "paper_mode": True,
            }
        try:
            ex = self._get_exchange()
            balance = ex.fetch_balance()
            return {
                "total_usdt": balance["USDT"]["total"] if "USDT" in balance else 0,
                "free_usdt": balance["USDT"]["free"] if "USDT" in balance else 0,
                "BTC": balance.get("BTC", {}).get("total", 0),
                "ETH": balance.get("ETH", {}).get("total", 0),
                "paper_mode": False,
            }
        except Exception as e:
            capital = Config.get("capital_usdt", 10.0)
            return {"total_usdt": capital, "free_usdt": capital, "paper_mode": True, "error": str(e)}
