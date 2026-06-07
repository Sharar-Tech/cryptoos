"""
CryptoOS - Personal Trading Bot Backend
FastAPI + CCXT + WebSockets
Free hosting: Render.com
"""

# ── Load .env FIRST before anything else reads os.environ ────────────────────
from dotenv import load_dotenv
load_dotenv()  # reads backend/.env into environment

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import asyncio
import json
import logging
from datetime import datetime

from control_engine import ControlEngine
from spot_bot import SpotBot
from futures_bot import FuturesBot
from staking_tracker import StakingTracker
from risk_manager import RiskManager
from market_data import MarketData
from database import Database
from config import Config

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cryptoos")

# ── Global instances (created once, shared everywhere) ───────────────────────
control         = ControlEngine()
db              = Database()
risk            = RiskManager(db)
market          = MarketData()
spot_bot        = SpotBot(control, db, risk, market)
futures_bot     = FuturesBot(control, db, risk, market)
staking_tracker = StakingTracker(control, db, market)

# Active WebSocket connections
active_connections: list[WebSocket] = []


# ── Startup / Shutdown ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs on startup and shutdown."""
    # Startup
    mode = "PAPER" if control.is_paper_mode() else "🔴 LIVE"
    capital = Config.get("capital_usdt", 10)
    api_key = Config.get_api_key()
    key_status = f"{api_key[:6]}..." if api_key else "NOT SET (paper mode only)"

    logger.info("=" * 50)
    logger.info("  CryptoOS Trading Bot — Starting")
    logger.info(f"  Mode      : {mode}")
    logger.info(f"  Capital   : ${capital} USDT")
    logger.info(f"  Exchange  : {Config.get('exchange', 'binance')}")
    logger.info(f"  API Key   : {key_status}")
    logger.info(f"  Symbol    : {Config.get('default_symbol', 'BTC/USDT')}")
    logger.info("=" * 50)

    db.log("info", f"CryptoOS started — Mode: {mode} | Capital: ${capital} USDT")

    # Re-enable any modules that were ON before restart
    state = control.get_state()
    if state.get("spot"):
        logger.info("Auto-resuming Spot bot (was ON before restart)")
        asyncio.create_task(spot_bot.run())
    if state.get("futures"):
        logger.info("Auto-resuming Futures bot (was ON before restart)")
        asyncio.create_task(futures_bot.run())
    if state.get("staking"):
        logger.info("Auto-resuming Staking tracker (was ON before restart)")
        asyncio.create_task(staking_tracker.run())

    yield  # App runs here

    # Shutdown
    logger.info("CryptoOS shutting down — stopping all bots...")
    control.stop_all()
    db.log("info", "CryptoOS stopped cleanly")


app = FastAPI(
    title="CryptoOS Trading Bot",
    version="1.0.0",
    description="Personal automated crypto trading system",
    lifespan=lifespan,
)

# ── CORS — allows file://, localhost, and any hosted domain ──────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── WebSocket — pushes live data to dashboard every 3 seconds ─────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"Dashboard connected ({len(active_connections)} active)")
    try:
        while True:
            data = await _build_live_data()
            await websocket.send_text(json.dumps(data))
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logger.info("Dashboard disconnected")
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)


async def _build_live_data() -> dict:
    """Assembles the full live payload sent to the dashboard."""
    try:
        prices = await market.get_prices_async()
    except Exception:
        prices = {}
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "prices":    prices,
        "bot_state": control.get_state(),
        "stats":     db.get_stats(),
        "risk":      risk.get_daily_summary(),
    }


# ── Health check — used by Render and cron-job.org to keep server awake ───────
@app.get("/health")
async def health():
    api_connected = bool(Config.get_api_key())
    return {
        "status":        "ok",
        "time":          datetime.utcnow().isoformat(),
        "mode":          "paper" if control.is_paper_mode() else "live",
        "api_connected": api_connected,
        "bots": {
            "spot":    control.is_enabled("spot"),
            "futures": control.is_enabled("futures"),
            "staking": control.is_enabled("staking"),
        },
    }


# ── Bot Control ───────────────────────────────────────────────────────────────
@app.post("/bot/spot/start")
async def start_spot(background_tasks: BackgroundTasks):
    if risk.daily_loss_exceeded():
        return JSONResponse(
            {"error": "Daily loss limit reached. Bot blocked for safety. Resets at midnight."},
            status_code=400,
        )
    if control.is_enabled("spot"):
        return {"status": "already_running", "message": "Spot bot is already ON"}

    control.enable("spot")
    db.log("info", "Spot trading STARTED by user", module="spot")
    background_tasks.add_task(spot_bot.run)
    return {
        "status":  "spot_started",
        "message": "Spot bot is now running",
        "mode":    "PAPER" if control.is_paper_mode() else "LIVE",
    }


@app.post("/bot/spot/stop")
async def stop_spot():
    control.disable("spot")
    db.log("info", "Spot trading STOPPED by user", module="spot")
    return {"status": "spot_stopped"}


@app.post("/bot/futures/start")
async def start_futures(background_tasks: BackgroundTasks):
    if risk.daily_loss_exceeded():
        return JSONResponse(
            {"error": "Daily loss limit reached. Bot blocked for safety."},
            status_code=400,
        )
    if control.is_enabled("futures"):
        return {"status": "already_running", "message": "Futures bot is already ON"}

    control.enable("futures")
    db.log("warn", "Futures trading STARTED — leverage active", module="futures")
    background_tasks.add_task(futures_bot.run)
    return {
        "status":  "futures_started",
        "warning": "Leverage active — max 3x hardcoded",
        "mode":    "PAPER" if control.is_paper_mode() else "LIVE",
    }


@app.post("/bot/futures/stop")
async def stop_futures():
    control.disable("futures")
    db.log("info", "Futures trading STOPPED by user", module="futures")
    return {"status": "futures_stopped"}


@app.post("/bot/staking/start")
async def start_staking(background_tasks: BackgroundTasks):
    if control.is_enabled("staking"):
        return {"status": "already_running"}
    control.enable("staking")
    db.log("info", "Staking tracker STARTED", module="staking")
    background_tasks.add_task(staking_tracker.run)
    return {"status": "staking_started"}


@app.post("/bot/staking/stop")
async def stop_staking():
    control.disable("staking")
    db.log("info", "Staking tracker STOPPED", module="staking")
    return {"status": "staking_stopped"}


@app.post("/bot/stop-all")
async def stop_all_bots():
    """Emergency stop — kills every module instantly."""
    control.stop_all()
    db.log("warn", "EMERGENCY STOP — all bots halted by user")
    return {"status": "all_stopped", "message": "All bots stopped"}


# ── Staking ───────────────────────────────────────────────────────────────────
@app.get("/staking/summary")
async def get_staking_summary():
    return staking_tracker.get_summary()


@app.post("/staking/position")
async def add_staking_position(body: dict):
    asset      = body.get("asset", "BTC")
    amount     = float(body.get("amount", 0))
    stake_type = body.get("stake_type", "flexible")
    days       = int(body.get("days_locked", 0))
    pos = staking_tracker.add_position(asset, amount, stake_type, days)
    return {"status": "position_added", "position": pos}


# ── Portfolio & Trades ────────────────────────────────────────────────────────
@app.get("/portfolio")
async def get_portfolio():
    try:
        balance = await market.get_balance_async()
        history = db.get_portfolio_history(days=30)
        return {"balance": balance, "history": history}
    except Exception as e:
        logger.error(f"Portfolio fetch error: {e}")
        return {"error": str(e), "balance": {"total_usdt": 0, "paper_mode": True}}


@app.get("/trades")
async def get_trades(limit: int = 50):
    return {"trades": db.get_trades(limit)}


@app.get("/trades/open")
async def get_open_trades():
    return {"trades": db.get_open_trades()}


@app.get("/logs")
async def get_logs(limit: int = 100):
    return {"logs": db.get_logs(limit)}


# ── Market Data ───────────────────────────────────────────────────────────────
@app.get("/market/price/{symbol}")
async def get_price(symbol: str):
    try:
        price = await market.get_price_async(symbol.replace("-", "/"))
        return {
            "symbol":    symbol,
            "price":     price,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/market/candles/{symbol}")
async def get_candles(symbol: str, timeframe: str = "1h", limit: int = 100):
    try:
        candles = await market.get_candles_async(
            symbol.replace("-", "/"), timeframe, limit
        )
        return {"symbol": symbol, "timeframe": timeframe, "candles": candles}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Risk ──────────────────────────────────────────────────────────────────────
@app.get("/risk/summary")
async def risk_summary():
    return risk.get_daily_summary()


# ── Settings ──────────────────────────────────────────────────────────────────
@app.post("/settings/capital")
async def set_capital(body: dict):
    amount = float(body.get("amount", 0))
    if amount <= 0:
        return JSONResponse({"error": "Capital must be greater than 0"}, status_code=400)
    Config.set_capital(amount)
    db.log("info", f"Capital updated to ${amount} USDT")
    return {"status": "capital_set", "amount": amount}


@app.post("/settings/paper-mode")
async def set_paper_mode(body: dict):
    enabled = bool(body.get("enabled", True))
    control.set_paper_mode(enabled)
    mode = "PAPER" if enabled else "LIVE"
    db.log("warn" if not enabled else "info", f"Mode switched to {mode}")
    return {"status": "mode_set", "paper_mode": enabled, "mode": mode}


@app.get("/settings")
async def get_settings():
    settings = Config.get_all()
    settings["paper_mode"] = control.is_paper_mode()
    settings["api_key_set"] = bool(Config.get_api_key())
    return settings
