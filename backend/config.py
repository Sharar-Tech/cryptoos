"""
CryptoOS - Config
All settings loaded from environment variables (.env file).
Priority: runtime override > .env / environment > hardcoded defaults
"""
import os
import json

# Runtime overrides (set via API during a session, reset on restart)
_runtime_config = {}

# ── Defaults ──────────────────────────────────────────────────────────────────
# These are used when a value is not in .env and not overridden at runtime.
DEFAULTS = {
    # Capital & sizing
    "capital_usdt":         10.0,   # KES 1,000 ≈ $7–10 USDT
    "trade_size_pct":       10.0,   # % of capital used per trade
    # Risk limits
    "daily_loss_limit_pct": 5.0,    # auto-stop if daily loss exceeds this %
    "max_daily_trades":     20,     # hard cap on trades per day
    "stop_loss_pct":        2.0,    # close trade if price drops this %
    "take_profit_pct":      3.0,    # close trade if price gains this %
    # Strategy
    "default_symbol":       "BTC/USDT",
    "timeframe":            "5m",
    "strategy":             "ma_crossover",
    # Exchange
    "exchange":             "binance",
    "max_leverage":         3,      # futures only — never exceed 3x
    # Mode — ALWAYS starts as paper/simulation unless .env says otherwise
    "paper_mode":           True,
}


class Config:

    # ── Read a setting ────────────────────────────────────────────────────────
    @staticmethod
    def get(key: str, fallback=None):
        """
        Priority order:
        1. Runtime override (set via API call this session)
        2. Environment variable / .env file
        3. DEFAULTS dict above
        4. fallback argument
        """
        # 1. Runtime override
        if key in _runtime_config:
            return _runtime_config[key]

        # 2. Environment variable (always uppercase in .env)
        env_val = os.environ.get(key.upper())
        if env_val is not None:
            # Try to parse as JSON so booleans/numbers come through correctly
            try:
                return json.loads(env_val)
            except (json.JSONDecodeError, ValueError):
                return env_val

        # 3. Hardcoded default
        if key in DEFAULTS:
            return DEFAULTS[key]

        # 4. Caller's fallback
        return fallback

    # ── Paper mode — explicit helper so nothing is ambiguous ─────────────────
    @staticmethod
    def is_paper_mode() -> bool:
        """
        Returns True  → simulation, no real orders placed
        Returns False → live trading with real money

        Reads PAPER_MODE from .env:
            PAPER_MODE=true   → paper (default, safe)
            PAPER_MODE=false  → live (real money)
        """
        # Runtime override takes priority
        if "paper_mode" in _runtime_config:
            return bool(_runtime_config["paper_mode"])

        # Read from environment / .env
        env_val = os.environ.get("PAPER_MODE", "true").strip().lower()
        return env_val != "false"   # anything except "false" stays as paper

    # ── Write helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def set(key: str, value):
        """Override any setting at runtime (does not persist after restart)."""
        _runtime_config[key] = value

    @staticmethod
    def set_capital(amount: float):
        """Shortcut for updating capital mid-session."""
        _runtime_config["capital_usdt"] = float(amount)

    @staticmethod
    def set_paper_mode(enabled: bool):
        """Switch paper/live mode at runtime."""
        _runtime_config["paper_mode"] = bool(enabled)

    # ── API credentials — read-only, never stored in runtime config ───────────
    @staticmethod
    def get_api_key() -> str:
        """Returns the Binance/Bybit API key from .env, or empty string."""
        return os.environ.get("EXCHANGE_API_KEY", "").strip()

    @staticmethod
    def get_api_secret() -> str:
        """Returns the API secret from .env, or empty string."""
        return os.environ.get("EXCHANGE_API_SECRET", "").strip()

    @staticmethod
    def has_api_keys() -> bool:
        """True if both key and secret are present in the environment."""
        return bool(Config.get_api_key() and Config.get_api_secret())

    # ── Dump all settings (safe — never exposes secrets) ─────────────────────
    @staticmethod
    def get_all() -> dict:
        """
        Returns merged settings for the /settings API endpoint.
        Secrets are never included.
        """
        result = {
            **DEFAULTS,
            **_runtime_config,
            # Always recalculate these from environment so they're current
            "paper_mode":    Config.is_paper_mode(),
            "api_key_set":   Config.has_api_keys(),
            "exchange":      Config.get("exchange", "binance"),
            "capital_usdt":  Config.get("capital_usdt", 10.0),
        }
        # Scrub any secrets that should never leave the server
        for secret_key in ("api_key", "api_secret", "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET"):
            result.pop(secret_key, None)
        return result

    # ── Convenience: computed values used by risk manager ────────────────────
    @staticmethod
    def max_daily_loss_usdt() -> float:
        """Absolute dollar amount the bot is allowed to lose today."""
        capital = Config.get("capital_usdt", 10.0)
        pct     = Config.get("daily_loss_limit_pct", 5.0)
        return round(float(capital) * (float(pct) / 100), 4)

    @staticmethod
    def trade_size_usdt() -> float:
        """Dollar amount to use per individual trade."""
        capital = Config.get("capital_usdt", 10.0)
        pct     = Config.get("trade_size_pct", 10.0)
        return round(float(capital) * (float(pct) / 100), 4)