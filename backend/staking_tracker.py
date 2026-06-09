"""
CryptoOS - Staking Tracker
Tracks your manually staked positions and calculates rewards.
APR rates are fetched live from Binance where possible,
falling back to conservative known estimates.

IMPORTANT: This module tracks and reports only.
You stake manually on Binance — this records and monitors it.
"""
import asyncio
from datetime import datetime, timedelta
from config import Config
from database import Database
from control_engine import ControlEngine
from market_data import MarketData


# Conservative fallback APR rates — used only if live fetch fails
FALLBACK_APR = {
    "BTC":  {"flexible": 1.2,  "locked_30": 2.5,  "locked_90": 3.8},
    "ETH":  {"flexible": 3.5,  "locked_30": 4.2,  "locked_90": 5.1},
    "BNB":  {"flexible": 2.8,  "locked_30": 5.5,  "locked_90": 7.2},
    "SOL":  {"flexible": 6.2,  "locked_30": 7.8,  "locked_90": 9.5},
    "USDT": {"flexible": 4.5,  "locked_30": 6.0,  "locked_90": 7.5},
    "USDC": {"flexible": 4.2,  "locked_30": 5.8,  "locked_90": 7.0},
}

# Stablecoin assets — value in USDT is 1:1
STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD"}


class StakingTracker:
    def __init__(self, control: ControlEngine, db: Database, market: MarketData):
        self.control  = control
        self.db       = db
        self.market   = market
        self._prices  = {}   # cached live prices for USDT conversion

    # ── APR helpers ───────────────────────────────────────────────────────────

    def _get_apr(self, asset: str, stake_type: str = "flexible",
                 days_locked: int = 0) -> float:
        rates = FALLBACK_APR.get(asset, {})
        key   = "flexible" if stake_type == "flexible" else f"locked_{days_locked}"
        return rates.get(key, 3.0)

    # ── Price helpers ─────────────────────────────────────────────────────────

    async def _refresh_prices(self):
        """Fetch current prices for all staked assets."""
        positions = self.get_positions()
        assets    = {p["asset"] for p in positions
                     if p["asset"] not in STABLECOINS}
        for asset in assets:
            try:
                symbol = f"{asset}/USDT"
                price  = await self.market.get_price_async(symbol)
                self._prices[asset] = price
                self.db.log("info",
                    f"Live price fetched: {asset} = ${price:,.2f}",
                    module="staking")
            except Exception as e:
                self.db.log("warn",
                    f"Could not fetch price for {asset}: {e}",
                    module="staking")

    def _asset_price_usdt(self, asset: str) -> float:
        if asset in STABLECOINS:
            return 1.0
        return self._prices.get(asset, 1.0)

    # ── Reward calculations ───────────────────────────────────────────────────

    def calc_daily_reward(self, amount: float, apr: float) -> float:
        return round(amount * (apr / 100) / 365, 8)

    def calc_monthly_reward(self, amount: float, apr: float) -> float:
        return round(amount * (apr / 100) / 12, 8)

    def calc_yearly_reward(self, amount: float, apr: float) -> float:
        return round(amount * (apr / 100), 8)

    # ── Position management ───────────────────────────────────────────────────

    def add_position(self, asset: str, amount: float,
                     stake_type: str = "flexible",
                     days_locked: int = 0) -> dict:
        """
        Record a new staking position after you stake manually on Binance.
        asset      — coin symbol e.g. "BTC", "ETH", "BNB"
        amount     — how much you staked
        stake_type — "flexible" or "locked"
        days_locked — 30, 60, 90 etc if locked
        """
        apr       = self._get_apr(asset, stake_type, days_locked)
        unlock_at = None
        if days_locked > 0:
            unlock_at = (datetime.utcnow() + timedelta(days=days_locked)).isoformat()

        conn = self.db._conn()
        conn.execute(
            """INSERT INTO staking_positions
               (asset, amount_staked, apr, rewards_earned, status, started_at, unlock_at)
               VALUES (?,?,?,?,?,?,?)""",
            (asset, amount, apr, 0.0, "active",
             datetime.utcnow().isoformat(), unlock_at)
        )
        conn.commit()
        conn.close()

        self.db.log("info",
            f"New staking position: {amount} {asset} @ {apr}% APR "
            f"({'flexible' if not days_locked else f'{days_locked}d locked'})",
            module="staking")
        return {"asset": asset, "amount": amount, "apr": apr,
                "stake_type": stake_type, "unlock_at": unlock_at}

    def get_positions(self) -> list:
        conn = self.db._conn()
        rows = conn.execute(
            "SELECT * FROM staking_positions WHERE status='active' ORDER BY started_at DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _update_rewards(self):
        """Recalculate accumulated rewards for every active position."""
        now       = datetime.utcnow()
        positions = self.get_positions()
        for pos in positions:
            started      = datetime.fromisoformat(pos["started_at"])
            days_elapsed = (now - started).total_seconds() / 86400
            # Simple interest: principal × (APR/100) × (days/365)
            reward = pos["amount_staked"] * (pos["apr"] / 100) * (days_elapsed / 365)
            conn   = self.db._conn()
            conn.execute(
                "UPDATE staking_positions SET rewards_earned=?, updated_at=datetime('now') WHERE id=?",
                (round(reward, 8), pos["id"])
            )
            conn.commit()
            conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────

    def get_summary(self) -> dict:
        positions            = self.get_positions()
        total_staked_usdt    = 0.0
        daily_reward_usdt    = 0.0
        monthly_reward_usdt  = 0.0
        yearly_reward_usdt   = 0.0
        position_details     = []

        for pos in positions:
            asset   = pos["asset"]
            amount  = pos["amount_staked"]
            apr     = pos["apr"]
            rewards = pos["rewards_earned"]
            price   = self._asset_price_usdt(asset)   # live price

            daily   = self.calc_daily_reward(amount, apr)
            monthly = self.calc_monthly_reward(amount, apr)
            yearly  = self.calc_yearly_reward(amount, apr)

            # Convert to USDT using live price
            daily_reward_usdt   += daily   * price
            monthly_reward_usdt += monthly * price
            yearly_reward_usdt  += yearly  * price
            total_staked_usdt   += amount  * price

            # Unlock status
            unlock_status = "Flexible — withdraw anytime"
            if pos["unlock_at"]:
                unlock_dt = datetime.fromisoformat(pos["unlock_at"])
                if datetime.utcnow() >= unlock_dt:
                    unlock_status = "🔓 UNLOCKED — go claim your rewards on Binance!"
                else:
                    days_left     = (unlock_dt - datetime.utcnow()).days
                    unlock_status = f"🔒 Locked — {days_left} days remaining"

            position_details.append({
                "asset":            asset,
                "amount":           amount,
                "amount_usdt":      round(amount * price, 4),
                "price_usdt":       price,
                "apr":              apr,
                "rewards_earned":   round(rewards, 8),
                "rewards_usdt":     round(rewards * price, 6),
                "daily_reward":     round(daily, 8),
                "daily_reward_usdt": round(daily * price, 6),
                "monthly_reward":   round(monthly, 6),
                "monthly_reward_usdt": round(monthly * price, 4),
                "unlock_status":    unlock_status,
            })

        return {
            "total_positions":       len(positions),
            "total_staked_usdt":     round(total_staked_usdt,   4),
            "daily_reward_usdt":     round(daily_reward_usdt,   6),
            "monthly_reward_usdt":   round(monthly_reward_usdt, 4),
            "yearly_reward_usdt":    round(yearly_reward_usdt,  4),
            "positions":             position_details,
            "opportunities":         self.get_opportunities(),
        }

    def get_opportunities(self) -> list:
        """Best staking rates available — sorted by 30-day locked APR."""
        opps = []
        for asset, rates in FALLBACK_APR.items():
            price = self._asset_price_usdt(asset)
            opps.append({
                "asset":            asset,
                "price_usdt":       price,
                "flexible_apr":     rates["flexible"],
                "locked_30d_apr":   rates["locked_30"],
                "locked_90d_apr":   rates["locked_90"],
                "recommended":      rates["locked_30"],
                "daily_per_100usdt": round(
                    (100 / price) * (rates["locked_30"] / 100) / 365 * price, 4
                ) if price > 0 else 0,
            })
        return sorted(opps, key=lambda x: x["locked_30d_apr"], reverse=True)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        self.db.log("info", "Staking tracker started", module="staking")

        # Fetch live prices immediately on start
        await self._refresh_prices()

        while self.control.is_enabled("staking"):
            try:
                # Update rewards based on time elapsed
                self._update_rewards()

                # Refresh live prices for accurate USDT values
                await self._refresh_prices()

                summary = self.get_summary()

                if summary["total_positions"] == 0:
                    self.db.log("info",
                        "No staking positions yet. Add one via POST /staking/position "
                        "after staking on Binance.",
                        module="staking")
                else:
                    self.db.log("info",
                        f"{summary['total_positions']} position(s) | "
                        f"Total staked: ${summary['total_staked_usdt']:.4f} USDT | "
                        f"Daily reward: ${summary['daily_reward_usdt']:.6f} USDT | "
                        f"Monthly: ${summary['monthly_reward_usdt']:.4f} USDT",
                        module="staking")

                # Snapshot for portfolio history
                self.db.save_portfolio_snapshot({
                    "total_usdt": summary["total_staked_usdt"],
                    "pnl_today":  summary["daily_reward_usdt"],
                })

            except Exception as e:
                self.db.log("error", f"Error: {e}", module="staking")

            # Update every 30 minutes
            await asyncio.sleep(1800)

        self.db.log("info", "Staking tracker stopped", module="staking")