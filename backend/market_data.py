"""
CryptoOS - Market Data Service
Primary price source: CoinGecko API (free, no IP restrictions, no key needed)
Fallback: Binance via CCXT (works locally, blocked on some cloud hosts)
This ensures live prices always work on Render.com free tier.
"""
import ccxt
import asyncio
import httpx
from datetime import datetime
from config import Config


# CoinGecko coin IDs for each symbol
COINGECKO_IDS = {
    "BTC/USDT": "bitcoin",
    "ETH/USDT": "ethereum",
    "BNB/USDT": "binancecoin",
    "SOL/USDT": "solana",
    "XRP/USDT": "ripple",
    "DOGE/USDT": "dogecoin",
}

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"


class MarketData:
    def __init__(self):
        self._exchange    = None
        self._price_cache = {}
        self._cache_time  = {}
        self._cache_ttl   = 8   # seconds

    # ── CoinGecko — primary source (always works on Render) ───────────────────

    async def _fetch_from_coingecko(self, symbols: list) -> dict:
        """
        Fetch multiple prices at once from CoinGecko.
        Returns dict: {"BTC/USDT": 64948.0, "ETH/USDT": 1758.5, ...}
        """
        ids_needed = [COINGECKO_IDS[s] for s in symbols if s in COINGECKO_IDS]
        if not ids_needed:
            return {}

        params = {
            "ids":           ",".join(ids_needed),
            "vs_currencies": "usd",
        }

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(COINGECKO_URL, params=params)
            r.raise_for_status()
            data = r.json()

        result = {}
        for symbol in symbols:
            coin_id = COINGECKO_IDS.get(symbol)
            if coin_id and coin_id in data:
                price = float(data[coin_id]["usd"])
                result[symbol] = price
                self._price_cache[symbol] = price
                self._cache_time[symbol]  = datetime.utcnow().timestamp()

        return result

    # ── Binance via CCXT — fallback (works locally, may be blocked on Render) ─

    def _get_exchange(self):
        if self._exchange is None:
            params = {"enableRateLimit": True}
            key    = Config.get_api_key()
            secret = Config.get_api_secret()
            if key and secret:
                params["apiKey"] = key
                params["secret"] = secret
            try:
                name = Config.get("exchange", "binance")
                self._exchange = getattr(ccxt, name)(params)
            except Exception:
                self._exchange = ccxt.binance({"enableRateLimit": True})
        return self._exchange

    def _fetch_from_binance(self, symbol: str) -> float:
        ex     = self._get_exchange()
        ticker = ex.fetch_ticker(symbol)
        price  = float(ticker["last"])
        self._price_cache[symbol] = price
        self._cache_time[symbol]  = datetime.utcnow().timestamp()
        return price

    # ── Cache check ───────────────────────────────────────────────────────────

    def _is_cached(self, key: str) -> bool:
        if key not in self._cache_time:
            return False
        return (datetime.utcnow().timestamp() - self._cache_time[key]) < self._cache_ttl

    # ── Public interface ──────────────────────────────────────────────────────

    async def get_prices_async(self) -> dict:
        """
        Fetch all 4 trading coin prices.
        Tries CoinGecko first, falls back to Binance, then cached, then mock.
        Returns: {"BTC-USDT": 64948.0, ...}  (dash format for dashboard)
        """
        symbols = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"]

        # Try CoinGecko first
        try:
            prices = await self._fetch_from_coingecko(symbols)
            if prices:
                result = {s.replace("/", "-"): v for s, v in prices.items()}
                btc = result.get("BTC-USDT", 0)
                print(f"[MARKET] CoinGecko live prices — BTC: ${btc:,.2f}")
                return result
        except Exception as e:
            print(f"[MARKET] CoinGecko failed: {e} — trying Binance...")

        # Fallback: try Binance
        result = {}
        for symbol in symbols:
            try:
                price = await asyncio.to_thread(self._fetch_from_binance, symbol)
                result[symbol.replace("/", "-")] = price
            except Exception as e:
                print(f"[MARKET] Binance failed for {symbol}: {e}")
                # Use cache or mock
                if symbol in self._price_cache:
                    result[symbol.replace("/", "-")] = self._price_cache[symbol]
                else:
                    result[symbol.replace("/", "-")] = self._mock_price(symbol)

        btc = result.get("BTC-USDT", 0)
        print(f"[MARKET] Prices fetched — BTC: ${btc:,.2f}")
        return result

    async def get_price_async(self, symbol: str = "BTC/USDT") -> float:
        """Get single price — used by trading bots every 10 seconds."""
        # Use cache if fresh
        if self._is_cached(symbol):
            return self._price_cache[symbol]

        # Try CoinGecko
        try:
            prices = await self._fetch_from_coingecko([symbol])
            if symbol in prices:
                return prices[symbol]
        except Exception:
            pass

        # Try Binance
        try:
            return await asyncio.to_thread(self._fetch_from_binance, symbol)
        except Exception as e:
            print(f"[MARKET] All price sources failed for {symbol}: {e}")

        # Last resort: cache or mock
        if symbol in self._price_cache:
            return self._price_cache[symbol]
        return self._mock_price(symbol)

    def get_price(self, symbol: str = "BTC/USDT") -> float:
        return asyncio.get_event_loop().run_until_complete(
            self.get_price_async(symbol)
        )

    async def get_candles_async(self, symbol: str,
                                timeframe: str = "1h", limit: int = 100) -> list:
        """Candles still come from Binance — used for charts only."""
        try:
            def fetch():
                ex    = self._get_exchange()
                ohlcv = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
                return [
                    {"time": int(c[0]/1000), "open": c[1], "high": c[2],
                     "low": c[3], "close": c[4], "volume": c[5]}
                    for c in ohlcv
                ]
            return await asyncio.to_thread(fetch)
        except Exception as e:
            print(f"[MARKET] Candles failed: {e}")
            return []

    async def get_balance_async(self) -> dict:
        capital = float(Config.get("capital_usdt", 7.69))
        if not Config.get_api_key():
            return {"total_usdt": capital, "free_usdt": capital,
                    "BTC": 0.0, "ETH": 0.0, "paper_mode": True}
        try:
            def fetch():
                ex = self._get_exchange()
                return ex.fetch_balance()
            balance = await asyncio.to_thread(fetch)
            return {
                "total_usdt": float(balance.get("USDT", {}).get("total", 0)),
                "free_usdt":  float(balance.get("USDT", {}).get("free",  0)),
                "BTC":        float(balance.get("BTC",  {}).get("total", 0)),
                "ETH":        float(balance.get("ETH",  {}).get("total", 0)),
                "paper_mode": False,
            }
        except Exception as e:
            print(f"[MARKET] Balance fetch failed: {e}")
            return {"total_usdt": capital, "free_usdt": capital,
                    "paper_mode": True, "error": str(e)}

    def _mock_price(self, symbol: str) -> float:
        """Emergency fallback only — should never appear in normal operation."""
        print(f"[MARKET] EMERGENCY MOCK used for {symbol} — all sources failed")
        mocks = {
            "BTC/USDT": 64948.0, "ETH/USDT": 1758.5,
            "BNB/USDT": 609.89,  "SOL/USDT": 72.30,
        }
        return mocks.get(symbol, 100.0)