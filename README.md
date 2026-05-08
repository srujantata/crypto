# Crypto Markets — Automated Trading Bot

A fully automated crypto + US stock trading dashboard with 24/7 cloud deployment, real-time WebSocket dashboard, and a self-tuning strategy engine.

![paper trading](https://img.shields.io/badge/mode-paper%20trading-blue)
![Railway](https://img.shields.io/badge/cloud-Railway.app-blueviolet)
![Python](https://img.shields.io/badge/python-3.13-green)
![Alpaca](https://img.shields.io/badge/broker-Alpaca-yellow)

---

## Architecture

```
Railway (24/7 cloud)
├── server.py        FastAPI + WebSocket backend
├── cloud_bot.py     Live Alpaca trading loop — auto-reconnects with backoff
└── strategy.py      Signal generation (EMA + ADX + RSI + volume + MACD)

Local PC / Mac (dashboard only)
└── app.py           CustomTkinter GUI — connects to cloud via WebSocket
```

The cloud bot trades continuously and pushes every tick, trade, and balance update to connected dashboards over WebSocket in real time.

---

## Features

- **12 symbols** — 6 crypto (24/7) + 5 US stocks (market hours gated)
- **Dual-mode signals** — EMA crossover breakouts + pullback continuation entries
- **Per-symbol parameters** — ADX thresholds and ATR trailing stop multipliers tuned per asset class
- **Multi-layer protection** — ATR trailing stop + hard stop-loss + re-entry cooldown
- **Higher timeframe filter** — 1h trend must confirm 15m entry direction
- **Self-healing cloud bot** — exponential backoff reconnect (30s → 5min cap), never dies silently
- **Live dashboard** — real-time price, RSI, ADX, signal, P&L per symbol
- **Trade history** — in-memory + CSV trade log with realized P&L per trade
- **Kill switch** — pause new entries without closing positions

---

## Symbols Traded

| Symbol | Type | Timeframe | ADX Min | ATR Stop |
|--------|------|-----------|---------|----------|
| BTC/USD | Crypto | 15m | 30 | 2.0× |
| ETH/USD | Crypto | 15m | 30 | 2.0× |
| SOL/USD | Crypto | 15m | 28 | 2.5× |
| AVAX/USD | Crypto | 15m | 28 | 2.5× |
| LINK/USD | Crypto | 15m | 28 | 2.5× |
| LTC/USD | Crypto | 15m | 28 | 2.5× |
| COIN | Stock | 1h | 25 | 1.5× |
| NVDA | Stock | 1h | 25 | 1.5× |
| TSLA | Stock | 1h | 25 | 1.5× |
| AMD | Stock | 1h | 25 | 1.5× |
| META | Stock | 1h | 25 | 1.5× |

> DOGE/USD is kept in the symbol list for exit-only (ADX threshold 35 prevents new entries).

---

## Strategy Logic

### BUY Signals — two complementary modes

**Mode A — Crossover**
- EMA fast(9) crossed above slow(21) within last 3 bars
- ADX > threshold **AND** ADX is rising (slope over 3 bars > 0)
- MACD histogram positive
- 40 < RSI < 70
- Volume > 1.5× 20-bar average
- Slow EMA rising (trend direction confirmed)
- Candle body > 40% of candle range (rejects doji/spinning top false breakouts)

**Mode B — Pullback**
- EMA fast > slow for ≥ 3 bars (uptrend established)
- RSI pulled back into 42–58 reload zone
- MACD histogram positive
- ADX > threshold AND rising
- Volume > average

**Higher Timeframe Filter** — 1h EMA + MACD must agree with the 15m direction before any BUY executes.

### SELL Signals — any one triggers

1. EMA fast crosses below slow
2. RSI > 75 (momentum exhaustion)
3. ADX < 18 AND MACD histogram negative (trend dying)
4. ATR trailing stop hit (drops X× ATR from peak)
5. Hard stop-loss: position down > 5% from entry

### Re-entry Cooldown
2-hour lockout after any sell — prevents churn on whipsaw exits. State persisted to disk so it survives service restarts.

---

## Cloud Deployment

| | |
|---|---|
| **Platform** | [Railway.app](https://railway.app) |
| **URL** | `https://crypto-production-5b12.up.railway.app` |
| **WebSocket** | `wss://crypto-production-5b12.up.railway.app/ws` |
| **Auto-deploy** | Every `git push` to `main` |
| **Health check** | `GET /health` |

---

## Local Setup

```bash
# Clone
git clone https://github.com/srujantata/crypto.git
cd crypto

# Install dependencies
pip install -r requirements.txt

# Copy and fill in credentials
cp .env.example .env
# Edit .env with your Alpaca API key + secret

# Run the dashboard
python app.py

# Run health check
python health_check.py

# Run backtest
python backtest.py
```

### Windows Executable

A pre-built `TradingBot.exe` is available in `dist/` (Windows only).

To rebuild after code changes:
```bash
pyinstaller --clean --noconfirm --onefile --windowed --name TradingBot app.py
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ALPACA_API_KEY` | — | Alpaca API key |
| `ALPACA_SECRET` | — | Alpaca secret key |
| `MODE` | `paper` | `paper` or `live` |
| `TIMEFRAME` | `15m` | Base candle timeframe |
| `RISK_PER_TRADE` | `0.05` | Fraction of cash per trade (5%) |
| `EMA_FAST` | `9` | Fast EMA period |
| `EMA_SLOW` | `21` | Slow EMA period |
| `ADX_MIN` | `28` | Fallback ADX threshold (overridden per-symbol) |
| `RSI_OVERBOUGHT` | `70` | RSI ceiling for BUY signals |
| `ATR_TRAIL_MULT` | `2.0` | ATR trailing stop multiplier baseline |
| `HARD_STOP_PCT` | `0.05` | Hard stop-loss from entry (5%) |
| `REENTRY_COOLDOWN_SECS` | `7200` | Seconds before re-entry allowed after sell |
| `POLL_SECONDS` | `60` | Trading loop interval |
| `BOT_API_SECRET` | — | Bearer token for REST + WebSocket auth |

Railway Variables tab changes to `RISK_PER_TRADE`, `TIMEFRAME`, `ADX_MIN` etc. take effect on the next reconnect without redeploying.

---

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

---

## Dashboard

The GUI (`app.py`) connects to the Railway WebSocket and displays:

- Live price, RSI, ADX, signal for every symbol
- Position status with entry price and unrealized P&L %
- Last trade details with realized P&L
- Trade history panel (most recent 40 trades, cloud + local CSV)
- Activity log with color-coded events
- Settings panel for hot-reloading strategy parameters
- Kill switch to pause entries without disrupting open positions

---

## Production Hardening

- Cloud bot reconnects with exponential backoff (30s → 5min cap)
- `fetch_ohlcv` retries 3× with exponential backoff on network errors
- In-progress candle dropped before signal computation (prevents false crossovers on incomplete bars)
- `place_order` validates qty > 0 before submitting
- WebSocket rate limiter (60 req/min per IP)
- Bearer token auth on all REST and WebSocket endpoints
- In-memory trade log in `server.py` survives CSV resets on redeploy

---

## Broker

[Alpaca](https://alpaca.markets) — commission-free paper and live trading.  
Account: paper trading mode (no real money at risk).  
Data: [yfinance](https://github.com/ranaroussi/yfinance) — free OHLCV feed.

---

## License

MIT
