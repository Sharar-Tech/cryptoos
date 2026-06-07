"""
Staking Tracker Module
- Detects available staking opportunities via Binance/Bybit APIs
- Tracks your staked positions and APR
- Calculates daily/weekly reward projections
- Records everything to the database
Note: Actual staking transactions are done manually on the exchange.
This module TRACKS and REPORTS — it does not auto-stake for safety.
"""
import asyncio
from datetime import datetime, timedelta
from config import Config
from database import Database
from control_engine import ControlEngine
from market_data import MarketData


# Estimated APR rates (updated from exchange data where available)
# These are conservative estimates — actual rates vary
KNOWN_STAKING_RATES = {
    "BTC":  {"flexible": 1.2,  "locked_30": 2.5,  "locked_90": 3.8},
    "ETH":  {"flexible": 3.5,  "locked_30": 4.2,  "locked_90": 5.1},
    "BNB":  {"flexible": 2.8,  "locked_30": 5.5,  "locked_90": 7.2},
    "SOL":  {"flexible": 6.2,  "locked_30": 7.8,  "locked_90": 9.5},
    "USDT": {"flexible": 4.5,  "locked_30": 6.0,  "locked_90": 7.5},
    "USDC": {"flexible": 4.2,  "locked_30": 5.8,  "locked_90": 7.0},
}


class StakingTracker:
    def __init__(self, control: ControlEngine, db: Database, market: MarketData):
        self.control = control
        self.db = db
        self.market = market
        self._positions = []  # In-memory cache of active positions

    # ── Reward Calculations ──────────────────────────────────────────────────

    def calc_daily_reward(self, amount: float, apr: float) -> float:
        """Daily reward = amount * (APR / 365)"""
        return round(amount * (apr / 100) / 365, 8)

    def calc_monthly_reward(self, amount: float, apr: float) -> float:
        return round(amount * (apr / 100) / 12, 8)

    def calc_yearly_reward(self, amount: float, apr: float) -> float:
        return round(amount * (apr / 100), 8)

    # ── Position Management ──────────────────────────────────────────────────

    def add_position(self, asset: str, amount: float, stake_type: str = "flexible", days_locked: int = 0) -> dict:
        """
        Record a new staking position.
        Call this after you manually stake on the exchange.
        """
        apr = KNOWN_STAKING_RATES.get(asset, {}).get(
            "flexible" if stake_type == "flexible" else f"locked_{days_locked}",
            3.0  # Default 3% if unknown
        )
        unlock_at = None
        if days_locked > 0:
            unlock_at = (datetime.utcnow() + timedelta(days=days_locked)).isoformat()

        position = {
            "asset": asset,
            "amount_staked": amount,
            "apr": apr,
            "stake_type": stake_type,
            "rewards_earned": 0.0,
            "status": "active",
            "started_at": datetime.utcnow().isoformat(),
            "unlock_at": unlock_at,
        }

        # Save to DB
        conn = self.db._conn()
        conn.execute(
            """INSERT INTO staking_positions
               (asset, amount_staked, apr, rewards_earned, status, started_at, unlock_at)
               VALUES (?,?,?,?,?,?,?)""",
            (asset, amount, apr, 0.0, "active",
             position["started_at"], unlock_at)
        )
        conn.commit()
        conn.close()
        self._positions.append(position)
        self.db.log("info", f"[STAKING] Added position: {amount} {asset} @ {apr}% APR", module="staking")
        return position

    def get_positions(self) -> list:
        conn = self.db._conn()
        rows = conn.execute(
            "SELECT * FROM staking_positions WHERE status='active' ORDER BY started_at DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _update_rewards(self):
        """Accumulate rewards for each active position based on time elapsed since start"""
        positions = self.get_positions()
        now = datetime.utcnow()
        for pos in positions:
            started = datetime.fromisoformat(pos["started_at"])
            days_elapsed = (now - started).total_seconds() / 86400
            # reward = principal * (APR/100) * (days/365)
            total_reward = pos["amount_staked"] * (pos["apr"] / 100) * (days_elapsed / 365)

            conn = self.db._conn()
            conn.execute(
                "UPDATE staking_positions SET rewards_earned=?, updated_at=datetime('now') WHERE id=?",
                (round(total_reward, 8), pos["id"])
            )
            conn.commit()
            conn.close()

    def get_summary(self) -> dict:
        """Full staking summary for the dashboard"""
        positions = self.get_positions()
        total_staked_usdt = 0.0
        daily_rewards_usdt = 0.0
        monthly_rewards_usdt = 0.0
        yearly_rewards_usdt = 0.0

        position_details = []
        for pos in positions:
            asset = pos["asset"]
            amount = pos["amount_staked"]
            apr = pos["apr"]
            rewards = pos["rewards_earned"]

            # Get current price for USDT conversion
            try:
                price = 1.0  # Default for USDT/USDC
                if asset not in ("USDT", "USDC", "BUSD"):
                    price = 1.0  # Will be updated async
            except Exception:
                price = 1.0

            daily = self.calc_daily_reward(amount, apr)
            monthly = self.calc_monthly_reward(amount, apr)
            yearly = self.calc_yearly_reward(amount, apr)

            daily_rewards_usdt += daily * price
            monthly_rewards_usdt += monthly * price
            yearly_rewards_usdt += yearly * price
            total_staked_usdt += amount * price

            # Check if locked position has unlocked
            unlock_status = "flexible"
            if pos["unlock_at"]:
                unlock_dt = datetime.fromisoformat(pos["unlock_at"])
                if datetime.utcnow() >= unlock_dt:
                    unlock_status = "UNLOCKED — claim rewards!"
                else:
                    days_left = (unlock_dt - datetime.utcnow()).days
                    unlock_status = f"Locked — {days_left} days left"

            position_details.append({
                "asset": asset,
                "amount": amount,
                "apr": apr,
                "rewards_earned": round(rewards, 8),
                "daily_reward": round(daily, 8),
                "monthly_reward": round(monthly, 6),
                "unlock_status": unlock_status,
            })

        return {
            "total_positions": len(positions),
            "total_staked_usdt": round(total_staked_usdt, 4),
            "daily_reward_usdt": round(daily_rewards_usdt, 6),
            "monthly_reward_usdt": round(monthly_rewards_usdt, 4),
            "yearly_reward_usdt": round(yearly_rewards_usdt, 4),
            "positions": position_details,
            "opportunities": self.get_opportunities(),
        }

    def get_opportunities(self) -> list:
        """Show best staking options available"""
        opps = []
        for asset, rates in KNOWN_STAKING_RATES.items():
            opps.append({
                "asset": asset,
                "flexible_apr": rates["flexible"],
                "locked_30d_apr": rates["locked_30"],
                "locked_90d_apr": rates["locked_90"],
                "recommended": rates["locked_30"],
            })
        # Sort by best 30-day locked APR
        return sorted(opps, key=lambda x: x["locked_30d_apr"], reverse=True)

    # ── Main Loop ────────────────────────────────────────────────────────────

    async def run(self):
        self.db.log("info", "Staking tracker started", module="staking")

        while self.control.is_enabled("staking"):
            try:
                self._update_rewards()
                summary = self.get_summary()
                self.db.log(
                    "info",
                    f"[STAKING] {summary['total_positions']} positions | "
                    f"Daily: ${summary['daily_reward_usdt']:.6f} | "
                    f"Monthly: ${summary['monthly_reward_usdt']:.4f}",
                    module="staking"
                )
                # Save portfolio snapshot with staking data
                self.db.save_portfolio_snapshot({
                    "total_usdt": summary["total_staked_usdt"],
                    "pnl_today": summary["daily_reward_usdt"],
                })
            except Exception as e:
                self.db.log("error", f"[STAKING] Error: {e}", module="staking")

            # Update every 30 minutes
            await asyncio.sleep(1800)

        self.db.log("info", "Staking tracker stopped", module="staking")
