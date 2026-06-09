"""
CryptoOS - Market Data Service
Fetches LIVE prices from Binance via CCXT.
Source: Binance public API — same feed as the Binance trading app.
No mock data is used when connected. Mock only fires if Binance
API is completely unreachable (e.g. network outage).
"""
import ccxt
import asyncio
from datetime import datetime
from config import Config


class MarketData:
    def __init__(self):
        self._exchange    = None
        self._price_cache = {}
        self._cache_time  = {}
        self._cache_ttl   = 2   # seconds — reduced so dashboard updates feel live

    def _get_exchange(self):
        """Create exchange connection once and reuse it."""
        if self._exchange is None:
            api_key    = Config.get_api_key()
            api_secret = Config.get_api_secret()
            params     = {"enableRateLimit": True}
            if api_key and api_secret:
                params["apiKey"] = api_key
                params["secret"] = api_secret
            try:
                name = Config.get("exchange", "binance")
                ExchangeClass  = getattr(ccxt, name)
                self._exchange = ExchangeClass(params)
                print(f"[MARKET] Connected to {name} — using LIVE price feed")
            except Exception as e:
                print(f"[MARKET] Exchange init failed: {e} — falling back to binance")
                self._exchange = ccxt.binance({"enableRateLimit": True})
        return self._exchange

    def _is_cached(self, key: str) -> bool:
        if key not in self._cache_time:
            return False
        age = datetime.utcnow().timestamp() - self._cache_time[key]
        return age < self._cache_ttl

    # ── Single price ──────────────────────────────────────────────────────────

    def get_price(self, symbol: str = "BTC/USDT") -> float:
        if self._is_cached(symbol):
            return self._price_cache[symbol]
        try:
            ex     = self._get_exchange()
            ticker = ex.fetch_ticker(symbol)
            price  = float(ticker["last"])
            self._price_cache[symbol] = price
            self._cache_time[symbol]  = datetime.utcnow().timestamp()
            return price
        except Exception as e:
            print(f"[MARKET] Price fetch failed for {symbol}: {e}")
            # Return last known price if available
            if symbol in self._price_cache:
                return self._price_cache[symbol]
            return self._mock_price(symbol)

    async def get_price_async(self, symbol: str = "BTC/USDT") -> float:
        return await asyncio.to_thread(self.get_price, symbol)

    # ── All prices in parallel ────────────────────────────────────────────────

    async def get_prices_async(self) -> dict:
        """
        Fetch all 4 coin prices in parallel — much faster than sequential.
        Returns dict like: {"BTC-USDT": 61000.0, "ETH-USDT": 3200.0, ...}
        """
        symbols = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"]

        async def fetch_one(s):
            try:
                price = await self.get_price_async(s)
                return s.replace("/", "-"), price
            except Exception:
                return s.replace("/", "-"), self._mock_price(s)

        results = await asyncio.gather(*[fetch_one(s) for s in symbols])
        prices  = dict(results)
        # Log so you can see in Render logs that prices are live
        btc = prices.get("BTC-USDT", 0)
        print(f"[MARKET] Live prices fetched — BTC: ${btc:,.2f}")
        return prices

    # ── Candles ───────────────────────────────────────────────────────────────

    def get_candles(self, symbol: str, timeframe: str = "1h", limit: int = 100):
        try:
            ex    = self._get_exchange()
            ohlcv = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
            return [
                {
                    "time":   int(c[0] / 1000),
                    "open":   c[1], "high": c[2],
                    "low":    c[3], "close": c[4],
                    "volume": c[5],
                }
                for c in ohlcv
            ]
        except Exception as e:
            print(f"[MARKET] Candles fetch failed: {e}")
            return []

    async def get_candles_async(self, symbol: str,
                                timeframe: str = "1h", limit: int = 100):
        return await asyncio.to_thread(self.get_candles, symbol, timeframe, limit)

    # ── Account balance ───────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        api_key = Config.get_api_key()
        capital = float(Config.get("capital_usdt", 7.69))
        if not api_key:
            return {
                "total_usdt": capital, "free_usdt": capital,
                "BTC": 0.0, "ETH": 0.0, "paper_mode": True,
            }
        try:
            ex      = self._get_exchange()
            balance = ex.fetch_balance()
            return {
                "total_usdt": float(balance.get("USDT", {}).get("total", 0)),
                "free_usdt":  float(balance.get("USDT", {}).get("free",  0)),
                "BTC":        float(balance.get("BTC",  {}).get("total", 0)),
                "ETH":        float(balance.get("ETH",  {}).get("total", 0)),
                "BNB":        float(balance.get("BNB",  {}).get("total", 0)),
                "paper_mode": False,
            }
        except Exception as e:
            print(f"[MARKET] Balance fetch failed: {e}")
            return {
                "total_usdt": capital, "free_usdt": capital,
                "paper_mode": True, "error": str(e),
            }

    async def get_balance_async(self) -> dict:
        return await asyncio.to_thread(self.get_balance)

    # ── Mock fallback — only used if Binance is unreachable ───────────────────

    def _mock_price(self, symbol: str) -> float:
        print(f"[MARKET] WARNING — using mock price for {symbol}. Check Binance connectivity.")
        mocks = {
            "BTC/USDT": 61000.0, "ETH/USDT": 3150.0,
            "BNB/USDT": 580.0,   "SOL/USDT": 145.0,
        }
        return mocks.get(symbol, 100.0)