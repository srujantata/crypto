# Crypto Markets Bot — Project Context

## What This Is
A fully automated crypto trading dashboard with cloud deployment. Built in Python with a CustomTkinter GUI, FastAPI cloud backend, and Alpaca paper trading integration.

## Architecture
```
Railway (24/7 cloud)
├── server.py          FastAPI + WebSocket backend
├── cloud_bot.py       Live Alpaca trading loop (8 symbols) — reconnects automatically
├── simulator.py       5 parallel risk profile simulations
└── strategy.py        EMA crossover + ADX + RSI + volume filters

Local PC / Mac (dashboard only)
└── app.py             CustomTkinter GUI — reads from cloud via WebSocket
```

## Cloud Deployment
- **Platform**: Railway.app — project name "sunny-victory"
- **URL**: `https://crypto-production-5b12.up.railway.app`
- **WebSocket**: `wss://crypto-production-5b12.up.railway.app/ws`
- **GitHub repo**: `https://github.com/srujantata/crypto`
- **Auto-deploys** on every `git push` to `main`

## Trading Setup
- **Exchange**: Alpaca (paper trading — no real money)
- **Account**: `tatasrujan@gmail.com`
- **Symbols**: BTC/USD, ETH/USD, SOL/USD, DOGE/USD, AVAX/USD, LINK/USD, LTC/USD, COIN
- **Timeframe**: 15m candles
- **Risk per trade**: 5% of portfolio per signal
- **Data source**: yfinance (free, no API key)

## Strategy Logic
1. **EMA crossover**: Fast(9) crosses above Slow(21) → BUY signal
2. **ADX filter**: Only trade when ADX > 25 (trending market, not choppy)
3. **Volume filter**: Only trade on above-average volume
4. **RSI filter**: Don't buy when RSI > 70 (overbought)
5. **Multi-timeframe**: 1h trend must confirm 15m signal direction
6. **Trailing stop**: Exit if price drops 2.5% from peak since entry

## 5 Cloud Simulation Profiles
Each runs with $10,000 virtual capital simultaneously:
| Profile | Risk | EMA | Timeframe | ADX Min |
|---|---|---|---|---|
| conservative | 2% | 9/21 | 1h | 30 |
| moderate | 5% | 9/21 | 15m | 25 |
| aggressive | 10% | 5/15 | 15m | 20 |
| scalper | 3% | 3/8 | 5m | 20 |
| swing | 8% | 14/35 | 4h | 30 |

## Key Files
| File | Purpose |
|---|---|
| `app.py` | Main GUI — run this locally |
| `server.py` | Cloud FastAPI backend |
| `cloud_bot.py` | Alpaca trading loop (runs on Railway) |
| `simulator.py` | 5 profile simulation engine |
| `strategy.py` | Signal generation logic |
| `exchange.py` | Alpaca + yfinance connectors |
| `config.py` | All tunable parameters |
| `backtest.py` | Historical replay across all symbols |
| `health_check.py` | Validates cloud + Alpaca status |
| `gpu_monitor.py` | Logs GPU temps every 5min to gpu_temps.csv |
| `analyze_gpu.py` | Analyzes GPU temp history for rental safety |

## Environment Variables (.env)
```
ALPACA_API_KEY=PKNOE7P6BSCC4LSIKRNTAWQWAK
ALPACA_SECRET=<secret — check local .env file>
MODE=paper
TIMEFRAME=15m
RISK_PER_TRADE=0.05
BOT_API_SECRET=CryptoBot2026!
CLOUD_WS_URL=wss://crypto-production-5b12.up.railway.app/ws
CLOUD_TOKEN=715fd66a6f9edf2de693b5560a79f075d3083d20839ef2706c3788d20fb12888
ALLOWED_ORIGINS=*
```
Railway has all these set in its Variables tab.
Cloud bot also reads: `EMA_FAST`, `EMA_SLOW`, `ADX_MIN`, `RSI_OVERBOUGHT`, `TRAILING_STOP_PCT`, `POLL_SECONDS` from Railway env (all have sensible defaults, change in Variables tab without redeploying).

## Running Locally (Mac or Windows)
```bash
# Install deps
pip install -r requirements.txt

# Run the dashboard
python app.py

# Run health check
python health_check.py

# Run backtest
python backtest.py
```
On Windows the venv path is `venv\Scripts\python.exe`.
On Mac use `venv/bin/python`.
The GUI exe (Windows only) is at `dist/TradingBot.exe` — rebuild with:
```
pyinstaller --noconfirm --onefile --windowed --name TradingBot --add-data ".env;." app.py
```

## Live Settings (no restart needed)
The GUI Settings panel applies all changes immediately via `_live_cfg` dict in `app.py`:
- Risk per trade, Timeframe, EMA Fast/Slow, Min ADX, RSI Overbought, Trailing Stop %, Poll interval
- **Kill Switch** button (⏸) pauses new signal execution without stopping the loop or closing positions

## Production Hardening (done 2026-05-02)
- **cloud_bot.py**: reconnect loop with exponential backoff (30s → 5min cap) — bot never dies silently
- **server.py**: `asyncio.get_running_loop()`, `ws.accept()` before client set add, rate_store cleanup task
- **simulator.py**: all duplicated strategy code replaced with `generate_signals()` calls
- **exchange.py**: `fetch_ohlcv` retries 3× with backoff; `place_order` validates qty > 0
- **strategy.py**: `generate_signals()` accepts live params — one source of truth for all callers
- **backtest.py**: tracks and displays max drawdown per symbol
- `live_trader.py` deleted (broken/dead — superseded by cloud_bot.py)

## Current State (as of 2026-05-02)
- Cloud bot running 24/7 on Railway ✅
- BTC/USD position open — entered @ $76,434, currently +2.66% unrealized ✅
- GPU temp logging running on Windows PC (auto-starts at login) ✅
- GPU analysis scheduled for 2026-05-03 to decide on Vast.ai rental ✅
- RGB lights scheduled: off at 10pm, on at 7:30am via Windows Task Scheduler ✅
- GitHub PAT was shared in chat — regenerate it at github.com/settings/tokens ⚠️

## Known Issues / Next Steps
- ADX is currently below 25 on most pairs (choppy market) — bot is correctly waiting
- COIN has ADX 30+ occasionally — most likely next to signal
- Consider tuning ADX threshold down to 22 if market stays choppy for a week+
- iCUE LINK H170i AIO not showing in iCUE — LINK Hub USB cable needs connecting to USB 2.0 header on Z790 HERO motherboard
- Vast.ai GPU rental: check gpu_temps.csv on 2026-05-03 before deciding

## Hardware (Windows PC)
- CPU: Intel i9-13900K (24 cores)
- RAM: 64 GB
- GPU: NVIDIA GeForce RTX 4090 (Gigabyte Gaming OC, ~4 years old)
- Motherboard: ASUS ROG MAXIMUS Z790 HERO
- Cooling: Corsair iCUE LINK H170i 420mm AIO
- Peripherals: Razer BlackWidow V4 Pro, Razer Naga Pro V2, Corsair MM700 mousepad

## User Profile
DevOps engineer learning AI agents, transitioning toward DevSecOps.
Prefers DevOps analogies (pipelines, IaC, observability).
