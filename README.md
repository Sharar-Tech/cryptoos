# CryptoOS — Personal Crypto Trading Bot

A fully automated crypto trading system with a web dashboard.
Built for personal use with **KES 1,000** starting capital.
**100% free hosting** — no subscriptions, no monthly fees.

---

## What This System Does

| Module | What It Does |
|---|---|
| **Spot Bot** | Buys/sells BTC automatically using MA crossover strategy |
| **Futures Bot** | Opens Long/Short positions using RSI + MA (⚠️ use after spot works) |
| **Staking Tracker** | Tracks staking rewards and APR across your positions |
| **Risk Manager** | Stops trading if daily loss limit is hit — protects your capital |
| **Dashboard** | Live prices, trade history, bot controls, logs — all in one page |

---

## Free Stack Used

| Component | Service | Cost |
|---|---|---|
| Backend hosting | Render.com | FREE |
| Frontend hosting | GitHub Pages | FREE |
| Database | SQLite (on disk) | FREE |
| Exchange | Binance or Bybit | FREE (pay trading fees ~0.1%) |
| Domain | yourname.onrender.com | FREE |
| Market data | CCXT library | FREE (open source) |

---

## File Structure

```
crypto-bot/
├── backend/
│   ├── main.py              ← FastAPI server (all routes)
│   ├── control_engine.py   ← ON/OFF switcher for modules
│   ├── spot_bot.py         ← Spot trading bot (MA crossover)
│   ├── futures_bot.py      ← Futures bot (RSI + MA)
│   ├── staking_tracker.py  ← Staking rewards tracker
│   ├── risk_manager.py     ← Daily loss limit + safety
│   ├── market_data.py      ← Prices, candles, balance
│   ├── database.py         ← SQLite database layer
│   ├── config.py           ← All settings
│   └── requirements.txt    ← Python packages
├── frontend/
│   └── index.html          ← Full dashboard (no build needed)
├── database/
│   └── schema.sql          ← Database table definitions
├── scripts/
│   └── start.sh            ← Local dev startup script
├── render.yaml             ← Render.com deploy config
├── .env.example            ← Environment variables template
└── .gitignore              ← Keeps secrets off GitHub
```

---

# STEP-BY-STEP IMPLEMENTATION GUIDE

Follow these steps IN ORDER. Do not skip ahead.

---

## PHASE 1 — Set Up Your Computer (One Time)

### Step 1: Install Python

1. Go to https://python.org/downloads
2. Download Python 3.11 or newer
3. Install it — tick "Add Python to PATH" during install
4. Open Terminal / Command Prompt and confirm:
   ```
   python --version
   ```
   You should see `Python 3.11.x`

### Step 2: Install Git

1. Go to https://git-scm.com/downloads
2. Download and install for your OS
3. Confirm:
   ```
   git --version
   ```

### Step 3: Get the project files

Create a folder on your computer called `cryptoos`, then copy all the project files into it following the file structure above.

Or if you already pushed to GitHub:
```bash
git clone https://github.com/YOUR_USERNAME/cryptoos.git
cd cryptoos
```

---

## PHASE 2 — Test Locally (Paper Mode First — No Real Money)

### Step 4: Set up environment variables

```bash
# In the project root folder:
cp .env.example backend/.env
```

Open `backend/.env` in any text editor (Notepad works). For now, leave the API keys blank — we'll run in paper/simulation mode first:

```
EXCHANGE_API_KEY=
EXCHANGE_API_SECRET=
EXCHANGE=binance
CAPITAL_USDT=10
DAILY_LOSS_LIMIT_PCT=5
TRADE_SIZE_PCT=10
MAX_DAILY_TRADES=20
STOP_LOSS_PCT=2
TAKE_PROFIT_PCT=3
DEFAULT_SYMBOL=BTC/USDT
DB_PATH=cryptoos.db
```

### Step 5: Install Python packages

```bash
cd backend
pip install -r requirements.txt
```

Wait for it to finish. You'll see packages installing.

### Step 6: Start the backend

```bash
# Still inside the backend folder:
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

You should see:
```
INFO: Uvicorn running on http://0.0.0.0:8000
```

### Step 7: Open the dashboard

1. Open the file `frontend/index.html` directly in your browser (Chrome/Firefox)
2. You'll see the CryptoOS dashboard
3. It will show "OFFLINE" at first — that's normal until both are running

> **To open it:** Find the file in your file explorer → Right-click → Open with → Chrome

### Step 8: Test the bot in Paper Mode

1. On the dashboard, click the **CONTROL** tab
2. Toggle **Spot Trading** to ON
3. Click the **LOGS** tab — you should see the bot making decisions
4. Watch the **TRADES** tab — paper trades will appear

The bot is now trading with fake money. Watch it for a few hours to understand how it behaves.

---

## PHASE 3 — Connect Real Exchange (When Ready)

### Step 9: Create Binance Account

1. Go to https://binance.com and create an account
2. Complete KYC verification (ID required)
3. Deposit KES 1,000 via M-Pesa or bank transfer

### Step 10: Create API Keys (CRITICAL — Read Carefully)

1. In Binance: Account → API Management → Create API
2. Give it a name: "CryptoOS Bot"
3. **ENABLE:** Spot & Margin Trading
4. **DISABLE:** Withdrawals (VERY IMPORTANT — bot must never be able to withdraw)
5. **DISABLE:** Futures (enable only when ready for futures module)
6. Set IP restriction (optional but recommended — your IP only)
7. Copy both: `API Key` and `Secret Key` — you only see the secret ONCE

### Step 11: Add real keys to .env

Open `backend/.env`:
```
EXCHANGE_API_KEY=paste_your_api_key_here
EXCHANGE_API_SECRET=paste_your_secret_key_here
CAPITAL_USDT=7
```

KES 1,000 ≈ $7 USDT (adjust based on current rate)

Restart the backend:
```bash
# Press Ctrl+C to stop, then:
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The bot now connects to real Binance. Paper mode still ON — it reads real prices but doesn't place orders yet.

### Step 12: Go Live

In the dashboard Settings tab, you'll see Paper Mode status.
To disable paper mode — edit `backend/.env`:
```
PAPER_MODE=false
```

⚠️ Only do this when you've watched paper mode work successfully for at least 24 hours.

---

## PHASE 4 — Deploy Online (Free, Always Running)

Your local computer has to stay on for the bot to work. To make it run 24/7 for free, deploy to Render.

### Step 13: Push to GitHub

1. Go to https://github.com and create a free account
2. Create a new repository called `cryptoos` (set to **Private**)
3. In your project folder:

```bash
git init
git add .
git commit -m "Initial CryptoOS build"
git remote add origin https://github.com/YOUR_USERNAME/cryptoos.git
git push -u origin main
```

> **IMPORTANT:** The `.gitignore` file prevents your `.env` (with API keys) from being uploaded. Never manually add `.env` to GitHub.

### Step 14: Deploy Backend to Render (Free)

1. Go to https://render.com → Sign up with GitHub
2. Click **New** → **Blueprint**
3. Select your `cryptoos` repository
4. Render will read `render.yaml` automatically
5. Click **Apply**

It will build and deploy. Takes about 3 minutes.

Once done, you get a free URL like:
```
https://cryptoos-backend.onrender.com
```

### Step 15: Add API keys to Render

1. In Render dashboard → your service → **Environment**
2. Add each variable from your `.env` file:
   - `EXCHANGE_API_KEY` → your key
   - `EXCHANGE_API_SECRET` → your secret
   - All other variables too
3. Click **Save Changes** — Render restarts automatically

### Step 16: Update Dashboard with your Render URL

Open `frontend/index.html` and find this line near the top:
```javascript
const API_BASE = window.location.hostname === 'localhost'
  ? 'http://localhost:8000'
  : window.location.origin.replace(/:\d+$/, ':8000');
```

Replace with:
```javascript
const API_BASE = 'https://cryptoos-backend.onrender.com';
```

### Step 17: Host Dashboard on GitHub Pages (Free)

1. In GitHub: repository → **Settings** → **Pages**
2. Source: Deploy from branch → `main` → `/frontend` folder
3. Save

Your dashboard will be live at:
```
https://YOUR_USERNAME.github.io/cryptoos/
```

Bookmark this. This is your control room, accessible from any device.

---

## PHASE 5 — Configure & Monitor

### Step 18: Set your capital

In the dashboard → **Settings** tab:
- Set Capital to your actual USDT amount (e.g. 7 for KES 1,000)
- This tells the risk manager how much to protect

### Step 19: Start trading

1. Open your dashboard
2. Go to **CONTROL** tab
3. Toggle **Spot Trading** → ON
4. Go to **LOGS** tab and watch the bot's decisions
5. Go to **TRADES** tab and see trades as they happen

### Step 20: Daily monitoring routine

Each day, check:
- **Dashboard → Risk** → Is daily loss still within limit?
- **Dashboard → Trades** → Win rate > 50%?
- **Dashboard → Logs** → Any errors?

If win rate drops below 40% for 3+ days, stop the bot and review.

---

## Safety Rules (READ THESE)

```
❌ Never give the API key withdrawal permissions
❌ Never enable futures until spot is profitable for 2+ weeks  
❌ Never risk more than 5% of capital in one trade
❌ Never trade coins you don't understand
❌ Never keep API keys in code or GitHub

✅ Always start in paper mode first
✅ Always set a daily loss limit
✅ Always test changes in paper mode before going live
✅ Always keep your API key secret (treat like a password)
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Dashboard shows OFFLINE | Backend isn't running. Start it with `uvicorn main:app --port 8000` |
| "API key invalid" error | Check keys in `.env` — no extra spaces |
| Bot not trading | Check LOGS tab — risk limit may be hit |
| Render service sleeping | Free tier sleeps after 15min. Upgrade to $7/mo Starter, or use a free cron pinger like https://cron-job.org |
| Balance shows 0 | Exchange not connected — running in paper mode |

---

## What the Bot Does With KES 1,000

With $7-10 USDT:
- Trade size per trade: ~$1 (10% of capital)
- Max daily loss before auto-stop: $0.50 (5%)
- Strategy: MA crossover on BTC/USDT every 10 seconds
- Expected: 3-8 trades per day depending on market volatility

Start here. Once profitable, add more capital.

---

## Next Upgrades (Future)

- [ ] Telegram bot alerts (get notified of every trade)
- [ ] Backtesting engine (test strategy on past data)
- [ ] Multi-coin support (ETH, SOL alongside BTC)
- [ ] AI sentiment scoring
- [ ] Email report every morning
