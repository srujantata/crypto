# Crypto Markets Bot — Project Context

## What This Is
A fully automated crypto + US stock trading dashboard with 24/7 cloud deployment, real-time WebSocket dashboard, AI-driven autonomous monitoring, and a self-tuning strategy engine.

## Architecture
```
Railway (24/7 cloud)
├── server.py          FastAPI + WebSocket backend — REST API + real-time push
├── cloud_bot.py       Live Alpaca trading loop (12 symbols) — reconnects with backoff
└── strategy.py        EMA crossover + ADX slope + RSI + MACD + volume + candle filters

Local PC / Mac (dashboard only)
└── app.py             CustomTkinter GUI — connects to cloud via WebSocket

AI Layer (Claude Code autonomous loop)
└── /loop monitor      10-min polling loop — detects problems, auto-patches code, pushes fixes
```

## Cloud Deployment
- **Platform**: Railway.app — project name "sunny-victory"
- **URL**: `https://crypto-production-5b12.up.railway.app`
- **WebSocket**: `wss://crypto-production-5b12.up.railway.app/ws`
- **GitHub repo**: `https://github.com/srujantata/crypto`
- **Auto-deploys** on every `git push` to `main`
- **Health check**: `GET /health` (no auth required)
- **Live status**: `GET /live` (Bearer token required)
- **Trade history**: `GET /live/trades` (Bearer token required)

## Trading Setup
- **Exchange**: Alpaca (paper trading — no real money)
- **Account**: `tatasrujan@gmail.com`
- **Data source**: yfinance (free, no API key)
- **Risk per trade**: 5% of portfolio per signal
- **Poll interval**: 60 seconds

## Symbols Traded (12 total)

| Symbol | Type | Timeframe | ADX Min | ATR Stop | Notes |
|--------|------|-----------|---------|----------|-------|
| BTC/USD | Crypto | 15m | 30 | 2.0x | High liquidity anchor |
| ETH/USD | Crypto | 15m | 30 | 2.0x | High liquidity anchor |
| SOL/USD | Crypto | 15m | 28 | 2.5x | |
| AVAX/USD | Crypto | 15m | 28 | 2.5x | |
| LINK/USD | Crypto | 15m | 28 | 2.5x | |
| LTC/USD | Crypto | 15m | 28 | 2.5x | |
| DOGE/USD | Crypto | 15m | 35 | 3.5x | EXIT-ONLY — ADX=35 blocks new entries; Hurst~0.5 random walk |
| COIN | Stock | 1h | 25 | 1.5x | Crypto-correlated equity |
| NVDA | Stock | 1h | 25 | 1.5x | |
| TSLA | Stock | 1h | 25 | 1.5x | |
| AMD | Stock | 1h | 25 | 1.5x | |
| META | Stock | 1h | 25 | 1.5x | |

Stocks gate on `is_market_open()` (NYSE/NASDAQ hours 9:30–16:00 ET Mon–Fri).
Crypto trades 24/7.

## Strategy Logic

### BUY Signals — two complementary modes

**Mode A — Crossover**
- EMA fast(9) crossed above slow(21) within last 3 bars
- ADX > per-symbol threshold AND ADX slope > 0 (must be RISING, not just above threshold)
- MACD histogram positive
- 40 < RSI < 70
- Volume > 1.5x 20-bar average
- Slow EMA rising (confirms uptrend direction)
- Candle body > 40% of candle range (rejects doji/spinning-top false breakouts)

**Mode B — Pullback continuation**
- EMA fast > slow for 3+ bars (uptrend established)
- RSI pulled back into 42–58 reload zone
- MACD histogram positive
- ADX > threshold AND rising
- Volume > average

**Higher Timeframe Filter** — 1h EMA + MACD must agree before any BUY executes.

### SELL Signals — any one triggers
1. EMA fast crosses below slow
2. RSI > 75 (overbought exhaustion — RSI_OVERBOUGHT+5)
3. ADX < 20 AND MACD histogram negative (trend dying — `ADX_FADE_EXIT`)
4. ATR trailing stop: price drops (ATR_at_entry × per-symbol multiplier) from peak
5. Hard stop-loss: position down > 5% from entry (`HARD_STOP_PCT`)

### Re-entry Cooldown
2-hour lockout after any sell — prevents churn on whipsaw exits.
State persisted to `cooldown_state.json` so it survives Railway redeployments.

### Key Parameters (config.py)
```python
EMA_FAST        = 9
EMA_SLOW        = 21
RSI_OVERBOUGHT  = 70      # sell triggers at 75 (OB + 5)
RSI_OVERSOLD    = 40      # buy floor
VOL_SURGE_MULT  = 1.5     # raised from 1.2 — crossover needs real volume
ADX_FADE_EXIT   = 20      # raised from 18 — catches dying trends earlier
ATR_TRAIL_MULT  = 2.0     # BTC/ETH baseline; alts/stocks override per SYMBOL_ATR_MULT
HARD_STOP_PCT   = 0.05    # 5% hard floor — backstop for immediate reversals
REENTRY_COOLDOWN_SECS = 7200  # 2h cooldown via env var
```

## AI Autonomous Monitoring Loop

### What It Does
Claude Code runs a `/loop` command that fires every 10 minutes, autonomously:
1. **Fetches** Railway health endpoint + trade history API
2. **Prices** all open positions via yfinance `fast_info`
3. **Runs strategy signals** (`generate_signals`) on each position to get live ADX/RSI/MACD
4. **Detects problems**: ghost exits, positions near hard stop, ADX fade, MACD crossings
5. **Auto-patches** `config.py` or `cloud_bot.py` if a pattern of failures is detected
6. **Commits + pushes** to GitHub → Railway auto-deploys the fix
7. **Schedules next wakeup** in 600 seconds and loops forever

### How to Start the Loop
```
/loop 10-minute trading bot monitor loop: fetch health + trades, analyze positions,
      auto-fix problems, push to git. Report: PORTFOLIO STATUS | OPEN POSITIONS |
      TODAY'S TRADES | PROBLEMS FOUND | ACTIONS TAKEN. Schedule next wakeup 600s.
```

### What the Loop Has Fixed (live examples, 2026-05-08)
| Problem Detected | Auto-Fix Applied | Commit | Result |
|-----------------|-----------------|--------|--------|
| META ADX=19.3 with MACD negative — old fade-exit threshold (18) too low to catch it | Raised `ADX_FADE_EXIT` 18→20 in `config.py` | `7cccf20` | META armed for exit; DOGE also caught at ADX=18.8 → exited +1.65% |

### Loop Outputs Per Iteration
- **PORTFOLIO STATUS**: connected/disconnected, position count, market open/closed
- **OPEN POSITIONS**: entry, current price, P&L%, delta vs last check, alert flags
- **TODAY'S TRADES**: all API trade records with timestamps, prices, P&L, ghost labels
- **PROBLEMS FOUND**: ADX fades, MACD flips, hard-stop proximity, ghost exit patterns
- **ACTIONS TAKEN**: code changes made, commits pushed, or "none" if clean

### Ghost Exit Pattern (known)
When Alpaca processes a sell fill faster than the bot's next poll, `actual_qty < min_qty`
→ bot emits a ghost `bot_trade` event (qty=None, pnl=None, note="ext") without placing an
order. State resets to flat. Trade history shows "ext.exit". This is cosmetic — no money
is lost, position WAS sold correctly. The loop detects and logs these but does not alert.

## Production Hardening

### Cloud Bot (cloud_bot.py)
- Reconnect loop with exponential backoff (30s → 5min cap) — never dies silently
- `_sync_positions()` on startup resumes open Alpaca positions after Railway restarts
- Cooldown state persisted to `cooldown_state.json` — survives redeploys
- Ghost exit branch: emits `bot_trade` event even when position externally closed
- ATR stored at entry time (`atr_entry`) for adaptive trailing stop distance
- Per-symbol ADX threshold + ATR multiplier looked up from `config.py` dicts

### Server (server.py)
- In-memory trade deque (`collections.deque(maxlen=500)`) accumulates trades within session
- `/live/trades` merges CSV + memory with dedup by (timestamp, symbol)
- Bearer token auth (SHA-256 of `BOT_API_SECRET`) on all REST + WebSocket endpoints
- Rate limiter: 60 requests/min per IP with periodic cleanup task
- WebSocket: `ws.accept()` before adding to client set; max connection cap

### Strategy (strategy.py)
- ADX slope filter: `df["adx_slope"] = df["adx"].diff(3)` — ADX must be RISING at entry
- Candle body filter: `candle_body_pct > 0.4` — rejects doji false breakouts
- In-progress candle dropped in `exchange.py` (`df.iloc[:-1]`) before signal computation
- `generate_signals()` is the single source of truth — used by bot, backtest, simulator, loop

### Exchange (exchange.py)
- `fetch_ohlcv` retries 3× with exponential backoff on network errors
- `place_order` validates qty > 0 before submitting
- `is_market_open()` gates equity symbols to NYSE hours
- `get_symbol_timeframe()` routes stocks to 1h, crypto to configured timeframe

## Key Files
| File | Purpose |
|------|---------|
| `app.py` | Local GUI dashboard (CustomTkinter) |
| `server.py` | Cloud FastAPI backend + WebSocket hub |
| `cloud_bot.py` | Alpaca trading loop — runs 24/7 on Railway |
| `strategy.py` | Signal generation — single source of truth |
| `exchange.py` | Alpaca + yfinance connectors |
| `config.py` | All parameters, symbol lists, per-symbol overrides |
| `backtest.py` | Historical replay with drawdown tracking |
| `health_check.py` | Validates cloud + Alpaca connectivity |
| `cooldown_state.json` | Persisted last_sell_time per symbol (auto-written) |
| `cloud_trades.csv` | Trade log on Railway (resets on redeploy — mitigated by in-memory deque) |

## Environment Variables (.env)
```
ALPACA_API_KEY=REDACTED_ALPACA_KEY
ALPACA_SECRET=<secret — check local .env file>
MODE=paper
TIMEFRAME=15m
RISK_PER_TRADE=0.05
BOT_API_SECRET=REDACTED_SECRET
CLOUD_WS_URL=wss://crypto-production-5b12.up.railway.app/ws
CLOUD_TOKEN=REDACTED_TOKEN
ALLOWED_ORIGINS=*
```

Railway Variables tab overrides (no redeploy needed for these):
`EMA_FAST`, `EMA_SLOW`, `ADX_MIN`, `RSI_OVERBOUGHT`, `ATR_TRAIL_MULT`,
`HARD_STOP_PCT`, `TRAILING_STOP_PCT`, `POLL_SECONDS`, `REENTRY_COOLDOWN_SECS`

## Running Locally (Windows)
```bash
# Install deps
pip install -r requirements.txt

# Run the dashboard GUI
python app.py

# Run health check
python health_check.py

# Run backtest
python backtest.py
```
The GUI exe is at `dist/TradingBot.exe` — rebuild after code changes with:
```
pyinstaller --clean --noconfirm --onefile --windowed --name TradingBot app.py
```

## Live Settings (no restart needed)
The GUI Settings panel applies all changes immediately via `_live_cfg` dict in `app.py`:
- Risk per trade, Timeframe, EMA Fast/Slow, Min ADX, RSI Overbought, Trailing Stop %, Poll interval
- **Kill Switch** button (⏸) pauses new BUY signal execution without closing open positions

## Current State (as of 2026-05-08)

### Open Positions
| Symbol | Entry | Current | P&L% | Armed Exit |
|--------|-------|---------|------|-----------|
| AMD | $349.64 | $455.19 | +30.2% | SELL at 9:30 ET (RSI>75) |
| TSLA | $394.83 | $428.35 | +8.5% | Trailing stop active |
| LINK/USD | $9.998 | ~$10.37 | +3.7% | Trailing stop active |
| LTC/USD | $57.41 | ~$58.28 | +1.5% | Trailing stop active |
| BTC/USD | $80,224 | ~$80,229 | +0.0% | ADX<20 but MACD+ holds |
| ETH/USD | $2,335 | ~$2,308 | -1.1% | ADX=24.5, watching fade |
| META | $624.87 | $609.63 | -2.4% | SELL at 9:30 ET (ADX fade-exit) |

### Exits Today (2026-05-08)
| Symbol | Exit Price | P&L | Trigger |
|--------|-----------|-----|---------|
| DOGE/USD | $0.1093 | +1.65% | ADX_FADE_EXIT=20 (new rule) |
| AVAX/USD | $9.9800 | +3.80% | RSI overbought (77.0) |
| SOL/USD | $92.49 | ~0% | Externally closed |

### Monitoring Status
- AI loop: running every 10 minutes since 3:10 PM CDT
- 30+ iterations completed, 0 hard stops, 0 bad entries
- 1 code fix auto-applied and deployed: `ADX_FADE_EXIT 18→20` (commit `7cccf20`)

## Known Issues / Next Steps
- **Ghost exit pattern**: SOL/AVAX/DOGE all exiting as ghosts — Alpaca fills orders faster
  than bot poll cycle. Cosmetic only. Fix would require checking order fill status directly
  rather than position qty on next poll. Low priority.
- **ETH ADX watch**: ADX=24.5, MACD negative — fade-exit fires at ADX<20. Monitor overnight.
- **BTC choppy**: ADX=17.2 below entry threshold but MACD=+16.8 prevents premature sell.
  Will exit on EMA crossover, RSI>75, or if MACD turns negative.
- **iCUE LINK H170i AIO** not showing in iCUE — LINK Hub USB cable needs connecting to USB 2.0
  header on Z790 HERO motherboard.

## Hardware (Windows PC)
- CPU: Intel i9-13900K (24 cores)
- RAM: 64 GB
- GPU: NVIDIA GeForce RTX 4090 (Gigabyte Gaming OC)
- Motherboard: ASUS ROG MAXIMUS Z790 HERO
- Cooling: Corsair iCUE LINK H170i 420mm AIO
- Peripherals: Razer BlackWidow V4 Pro, Razer Naga Pro V2, Corsair MM700 mousepad

## User Profile
DevOps engineer learning AI agents, transitioning toward DevSecOps.
Prefers DevOps analogies (pipelines, IaC, observability).
