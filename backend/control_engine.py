"""
CryptoOS - Control Engine
Central ON/OFF switcher for all trading modules.

State is persisted to bot_state.json so modules survive server restarts.
Paper mode is always read from the environment first — .env file wins
over whatever was saved in bot_state.json, so you can never accidentally
go live just because the state file was saved that way.
"""
import json
import os
import logging
from datetime import datetime

logger = logging.getLogger("cryptoos.control")


class ControlEngine:

    def __init__(self):
        # Safe defaults — everything OFF, paper mode ON
        self._state = {
            "spot":       False,
            "futures":    False,
            "staking":    False,
            "paper_mode": True,
        }

        # Step 1 — load whatever was saved from the last session
        self._load_state()

        # Step 2 — environment / .env always overrides the saved state
        # This is the fix from the uploaded excerpt:
        # if PAPER_MODE=false is in .env, go live regardless of saved state
        self._apply_env_overrides()

    # ── State file ────────────────────────────────────────────────────────────

    def _state_file(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_state.json")

    def _load_state(self):
        """Load persisted ON/OFF state from the last session."""
        try:
            path = self._state_file()
            if os.path.exists(path):
                with open(path, "r") as f:
                    saved = json.load(f)
                # Only restore module toggles — never restore paper_mode from
                # the file alone; the env check below handles that safely
                for key in ("spot", "futures", "staking"):
                    if key in saved:
                        self._state[key] = bool(saved[key])
                logger.info(f"State loaded from {path}")
        except Exception as e:
            logger.warning(f"Could not load bot_state.json: {e} — using defaults")

    def _save_state(self):
        """Persist current ON/OFF state so restarts can resume cleanly."""
        try:
            with open(self._state_file(), "w") as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save bot_state.json: {e}")

    def _apply_env_overrides(self):
        """
        Read PAPER_MODE from the environment (.env file) and apply it.
        This runs AFTER _load_state so the env always wins.

        .env examples:
            PAPER_MODE=true   → simulation (safe default)
            PAPER_MODE=false  → live trading with real money
        """
        env_val = os.environ.get("PAPER_MODE", "true").strip().lower()
        live_mode = (env_val == "false")

        if live_mode and self._state["paper_mode"]:
            logger.warning("PAPER_MODE=false detected in environment — switching to LIVE trading")
            self._state["paper_mode"] = False

        elif not live_mode and not self._state["paper_mode"]:
            logger.info("PAPER_MODE=true — running in simulation mode")
            self._state["paper_mode"] = True

        else:
            mode = "LIVE" if not self._state["paper_mode"] else "PAPER"
            logger.info(f"Mode: {mode}")

    # ── Module control ────────────────────────────────────────────────────────

    def enable(self, module: str):
        """Turn a module ON. Saves state immediately."""
        if module not in self._state:
            logger.warning(f"Unknown module: {module}")
            return
        self._state[module] = True
        self._save_state()
        logger.info(f"Module [{module.upper()}] → ON")

    def disable(self, module: str):
        """Turn a module OFF. Saves state immediately."""
        if module not in self._state:
            return
        self._state[module] = False
        self._save_state()
        logger.info(f"Module [{module.upper()}] → OFF")

    def is_enabled(self, module: str) -> bool:
        return bool(self._state.get(module, False))

    def stop_all(self):
        """Emergency stop — turns off every trading module."""
        for key in ("spot", "futures", "staking"):
            self._state[key] = False
        self._save_state()
        logger.warning("ALL modules stopped")

    # ── Paper mode ────────────────────────────────────────────────────────────

    def set_paper_mode(self, enabled: bool):
        """
        Switch between paper and live mode at runtime.
        Also updates bot_state.json so it survives restarts.
        Note: if PAPER_MODE is set in .env, that will override this
        on the next restart. To make live mode permanent, set
        PAPER_MODE=false in your .env file.
        """
        self._state["paper_mode"] = bool(enabled)
        self._save_state()
        mode = "PAPER (simulation)" if enabled else "LIVE (real money)"
        logger.warning(f"Paper mode set to: {mode}")

    def is_paper_mode(self) -> bool:
        return bool(self._state.get("paper_mode", True))

    # ── State snapshot ────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        """Full state dict — sent to the dashboard via WebSocket."""
        return {
            "spot":         self._state.get("spot", False),
            "futures":      self._state.get("futures", False),
            "staking":      self._state.get("staking", False),
            "paper_mode":   self._state.get("paper_mode", True),
            "any_active":   any([
                                self._state.get("spot", False),
                                self._state.get("futures", False),
                                self._state.get("staking", False),
                            ]),
            "updated_at":   datetime.utcnow().isoformat(),
        }

    def get_active_modules(self) -> list[str]:
        """Returns list of modules that are currently ON."""
        return [m for m in ("spot", "futures", "staking") if self._state.get(m, False)]
