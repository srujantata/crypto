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

## Current State (as of 2026-05-14 ~13:15 UTC)

### Open Positions
Bot is **FLAT — 0 positions**. Last confirmed by health API iter 248.

### Market Context (iter 248 summary)
- Crypto ADX collapsed across the board after May 12 selloff recovery — choppy, no BUY entries
- BTC MACD surged to +47.4 but ADX=20.9 (just above fade-exit threshold of 20) — bot holding off
- ETH ADX=18.7 (below fade-exit 20), MACD barely positive — fragile
- NVDA: ADX=57.3 RISING, MACD=+0.197 (positive), RSI=68.4 — most promising stock setup
- META: MACD=+2.23, ADX=29.4 — positive signal but RSI=65.6 elevated
- AVAX RSI=67.1, LTC RSI=65.8 — getting overbought without proper trend structure

---

## AI Monitoring Loop — Resume Instructions

**Any LLM (local or cloud) can resume this loop by following these exact steps each iteration:**

### Step 1 — Health check (run in parallel with Step 2)
```bash
curl -s -X GET "https://crypto-production-5b12.up.railway.app/health" \
  -H "Authorization: Bearer REDACTED_SECRET"
```
Confirm: `status=ok`, `connected=true`. Note positions{} content.

### Step 2 — Signal scan (run in parallel with Step 1)

Create file `__sig{N}.py` using the template below, then run:
```
python __sig{N}.py
```

**CRITICAL rules:**
- ASCII only in print() — no emoji (Windows charmap error)
- Stocks always use 1h (get_symbol_timeframe handles this)
- SELL reasons: cross_dn | rsi_exh(>75.0) | trend_die(adx<20 AND macd<0) | ema_neg
- BUY reasons: ModeA | ModeB
- Freshness: [---]=stale, [NEW]=+1 bar, [+Nb]=multi-bar jump

### Step 3 — Report format
```
ITER N — YYYY-MM-DD ~HH:MM UTC
PORTFOLIO: connected/disconnected | N positions | open/closed
SIGNALS table: sym | tf | fresh | signal | ADX(delta) | MACD(delta) | RSI(delta)
PROBLEMS: anything needing attention
ACTIONS: code fixes or "none"
```

### Step 4 — Schedule next wakeup
- Market hours (13:30–20:00 UTC Mon–Fri): 600s (10 min)
- Overnight / weekend: 1800s (30 min)

---

## Monitoring Loop PREV State (for iter 249)

**Next iter: 249. Last completed: 248 at ~2026-05-14 13:15 UTC.**

```python
PREV_TS = {
    "BTC/USD":  "2026-05-14 13:15",
    "ETH/USD":  "2026-05-14 13:15",
    "SOL/USD":  "2026-05-14 13:15",
    "DOGE/USD": "2026-05-14 13:15",
    "AVAX/USD": "2026-05-14 13:15",
    "LINK/USD": "2026-05-14 13:15",
    "LTC/USD":  "2026-05-14 13:15",
    "COIN":     "2026-05-13 19:30",
    "NVDA":     "2026-05-13 19:30",
    "TSLA":     "2026-05-13 19:30",
    "AMD":      "2026-05-13 19:30",
    "META":     "2026-05-13 19:30",
}

PREV = {
    "BTC/USD":  {"adx": 20.9,  "macd":  47.3723, "rsi": 62.761},
    "ETH/USD":  {"adx": 18.7,  "macd":   0.4387, "rsi": 52.230},
    "SOL/USD":  {"adx": 15.9,  "macd":   0.0548, "rsi": 58.014},
    "DOGE/USD": {"adx": 20.9,  "macd":   0.0003, "rsi": 62.487},
    "AVAX/USD": {"adx": 24.6,  "macd":   0.0139, "rsi": 67.133},
    "LINK/USD": {"adx": 13.8,  "macd":   0.0112, "rsi": 62.221},
    "LTC/USD":  {"adx": 16.7,  "macd":   0.0505, "rsi": 65.817},
    "COIN":     {"adx": 19.5,  "macd":  -0.9851, "rsi": 47.701},
    "NVDA":     {"adx": 57.3,  "macd":   0.1970, "rsi": 68.446},
    "TSLA":     {"adx": 29.1,  "macd":  -0.1882, "rsi": 62.028},
    "AMD":      {"adx": 34.8,  "macd":  -1.9676, "rsi": 54.731},
    "META":     {"adx": 29.4,  "macd":   2.2322, "rsi": 65.623},
}
```

### Full script template (copy-paste into __sig{N}.py):
```python
import sys
sys.path.insert(0, r'D:\Srujan\Claude\crypto')
from exchange import fetch_ohlcv, get_symbol_timeframe
from strategy import generate_signals
from config import SYMBOL_ADX_MIN

SYMBOLS = ["BTC/USD","ETH/USD","SOL/USD","DOGE/USD","AVAX/USD","LINK/USD","LTC/USD",
           "COIN","NVDA","TSLA","AMD","META"]

# UPDATE these each iteration with the latest values from the previous scan:
PREV_TS = {
    "BTC/USD":  "2026-05-14 13:15",
    "ETH/USD":  "2026-05-14 13:15",
    "SOL/USD":  "2026-05-14 13:15",
    "DOGE/USD": "2026-05-14 13:15",
    "AVAX/USD": "2026-05-14 13:15",
    "LINK/USD": "2026-05-14 13:15",
    "LTC/USD":  "2026-05-14 13:15",
    "COIN":     "2026-05-13 19:30",
    "NVDA":     "2026-05-13 19:30",
    "TSLA":     "2026-05-13 19:30",
    "AMD":      "2026-05-13 19:30",
    "META":     "2026-05-13 19:30",
}
PREV = {
    "BTC/USD":  {"adx": 20.9,  "macd":  47.3723, "rsi": 62.761},
    "ETH/USD":  {"adx": 18.7,  "macd":   0.4387, "rsi": 52.230},
    "SOL/USD":  {"adx": 15.9,  "macd":   0.0548, "rsi": 58.014},
    "DOGE/USD": {"adx": 20.9,  "macd":   0.0003, "rsi": 62.487},
    "AVAX/USD": {"adx": 24.6,  "macd":   0.0139, "rsi": 67.133},
    "LINK/USD": {"adx": 13.8,  "macd":   0.0112, "rsi": 62.221},
    "LTC/USD":  {"adx": 16.7,  "macd":   0.0505, "rsi": 65.817},
    "COIN":     {"adx": 19.5,  "macd":  -0.9851, "rsi": 47.701},
    "NVDA":     {"adx": 57.3,  "macd":   0.1970, "rsi": 68.446},
    "TSLA":     {"adx": 29.1,  "macd":  -0.1882, "rsi": 62.028},
    "AMD":      {"adx": 34.8,  "macd":  -1.9676, "rsi": 54.731},
    "META":     {"adx": 29.4,  "macd":   2.2322, "rsi": 65.623},
}

TF_MINUTES = {"1m":1,"5m":5,"15m":15,"30m":30,"1h":60,"4h":240,"1d":1440}

def badge(sig):
    if sig == 1:  return "BUY  [^]"
    if sig == -1: return "SELL [v]"
    return "HOLD [=]"

def d(val, key, sym):
    prev = PREV.get(sym, {}).get(key)
    if prev is None: return ""
    delta = val - prev
    sign = "+" if delta >= 0 else ""
    return f" ({sign}{delta:.3f})"

def freshness(ts, prev_ts, tf):
    if ts == prev_ts:
        return "[---]"
    tf_min = TF_MINUTES.get(tf, 15)
    try:
        from datetime import datetime
        fmt = "%Y-%m-%d %H:%M"
        t1 = datetime.strptime(prev_ts, fmt)
        t2 = datetime.strptime(ts, fmt)
        diff_min = int((t2 - t1).total_seconds() / 60)
        bars = diff_min // tf_min
        if bars == 1:
            return "[NEW]"
        return f"[+{bars}b]"
    except:
        return "[NEW]"

def scan(sym):
    prev_ts = PREV_TS.get(sym, "")
    tf = get_symbol_timeframe(sym, "15m")
    df = fetch_ohlcv(limit=150, symbol=sym, timeframe=tf)
    adx_min = SYMBOL_ADX_MIN.get(sym, 28)
    df2 = generate_signals(df, adx_min=adx_min)
    last = df2.iloc[-1]
    ts = str(df2.index[-1])[:16]
    fresh = freshness(ts, prev_ts, tf)
    sig = int(last.get("signal", 0))
    px = float(last["close"])
    adx = float(last.get("adx", 0))
    macd = float(last.get("macd_hist", 0))
    rsi = float(last.get("rsi", 0))
    is_new = fresh != "[---]"
    adx_d  = d(adx,  "adx",  sym) if is_new else ""
    macd_d = d(macd, "macd", sym) if is_new else ""
    rsi_d  = d(rsi,  "rsi",  sym) if is_new else ""
    sell_reasons = []
    if sig == -1:
        if last.get("ema_cross_down", False):    sell_reasons.append("cross_dn")
        if rsi > 75.0:                            sell_reasons.append("rsi_exh(>75.0)")
        if adx < 20 and macd < 0:                sell_reasons.append("trend_die")
        if last.get("ema_neg_confirmed", False):  sell_reasons.append("ema_neg")
    buy_reasons = []
    if sig == 1:
        if last.get("mode_a", False): buy_reasons.append("ModeA")
        if last.get("mode_b", False): buy_reasons.append("ModeB")
    flag = "!!" if sig != 0 else ""
    reason_str = ""
    if sell_reasons: reason_str = " -- " + ",".join(sell_reasons)
    if buy_reasons:  reason_str = " -- " + ",".join(buy_reasons)
    print(f"{sym:<10} {tf}  {fresh} ts={ts}  px={px:.3f}")
    print(f"           {badge(sig)}{flag}  ADX={adx:.1f}{adx_d}  MACD={macd:.4f}{macd_d}  RSI={rsi:.3f}{rsi_d}{reason_str}")

print("=== ITER 249 SIGNAL SCAN ===")
for sym in SYMBOLS:
    try:
        scan(sym)
    except Exception as e:
        print(f"{sym:<10} ERROR: {e}")
print("=== DONE ===")
```

## Known Issues / Next Steps
- **Ghost exit pattern**: Alpaca fills faster than bot poll — cosmetic, no money lost.
- **Crypto ADX collapsed**: All crypto ADX <25 after May 12-13 chop. No BUY entries until trend rebuilds.
- **NVDA watch**: ADX=57.3 rising, MACD positive — most likely next BUY candidate on open.
- **ETH fragile**: ADX=18.7 below fade-exit threshold — if bot ever enters, exits immediately.
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
