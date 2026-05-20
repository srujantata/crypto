import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET  = os.getenv("ALPACA_SECRET", "")
MODE    = os.getenv("MODE", "paper")

CRYPTO_SYMBOLS = [
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "DOGE/USD",   # kept for exit-only — ADX threshold set to 35 in SYMBOL_ADX_MIN so no new entries
    "AVAX/USD",
    "LINK/USD",
    "LTC/USD",
]

# US equities — traded only during market hours (9:30–16:00 ET, Mon–Fri)
STOCK_SYMBOLS = [
    "COIN",   # Coinbase  — high crypto correlation, volatile
    "NVDA",   # NVIDIA    — strong trend behaviour
    "TSLA",   # Tesla     — high ATR, EMA-friendly
    "AMD",    # AMD       — tech momentum
    "META",   # Meta      — liquid, large-cap
]

SYMBOLS = CRYPTO_SYMBOLS + STOCK_SYMBOLS

TIMEFRAME      = os.getenv("TIMEFRAME", "15m")
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.05"))

# Strategy parameters — crypto (15m default)
EMA_FAST        = 9
EMA_SLOW        = 21
RSI_PERIOD      = 14
RSI_OVERBOUGHT  = 70
RSI_OVERSOLD    = 40    # RSI floor for BUY — rejects failed oversold bounces
VOL_SURGE_MULT  = 1.5   # raised 1.2→1.5: crossover entries need 1.5× average volume
ADX_FADE_EXIT   = 20    # sell when ADX drops below this AND MACD turns negative
                        # raised 18→20: catches dying trends earlier (META-style: ADX~19, MACD neg)
ATR_TRAIL_MULT  = 2.0   # raised 1.8→2.0: BTC/ETH trailing stop baseline
HARD_STOP_PCT   = 0.05  # hard stop-loss — exit if position drops > 5% from entry price

# Stock-specific overrides — slower timeframe + higher trend filter
STOCK_TIMEFRAME = "1h"  # stocks use 1h candles — reduces whipsaw vs 15m
STOCK_ADX_MIN   = 22    # 1h candles produce lower ADX vs 15m for equivalent trend strength.
                        # Wilder's 25 was calibrated for daily charts. On 1h, 22 ≈ daily 25.
                        # Lowered 25→22 (2026-05-20): prevents blocking genuine stock trends
                        # (e.g. AMD ADX=23.4 with MACD +4.39 and EMA:bull was incorrectly filtered)

# Per-symbol ADX thresholds (research: 15m crypto needs HIGHER ADX than 1h stocks)
# BTC/ETH: 30 (high-liquidity, trend reliable at 30 on 15m)
# Other crypto: 28 (raised from 20 — Wilder's 25 was for daily, 15m needs higher)
# Stocks: 25 (1h candles = smoother ADX, institutional liquidity)
SYMBOL_ADX_MIN = {
    "BTC/USD":  30,
    "ETH/USD":  30,
    "SOL/USD":  28,
    "DOGE/USD": 35,   # exit-only: ADX never reaches 35 in normal markets, blocks new entries
    "AVAX/USD": 28,
    "LINK/USD": 28,
    "LTC/USD":  28,
    # Stocks on 1h: threshold 22 (lowered from 25 on 2026-05-20)
    # 1h ADX is structurally lower than 15m ADX for same trend strength.
    # 22 on 1h ≈ 25 on daily ≈ 28 on 15m in terms of trend significance.
    "COIN":     22,
    "NVDA":     22,
    "TSLA":     22,
    "AMD":      22,
    "META":     22,
}

# Per-symbol ATR trailing stop multiplier
# Crypto alts (higher beta) need wider stops to avoid noise-triggered exits
# Stocks on 1h are smoother — tighter stops capture more profit
SYMBOL_ATR_MULT = {
    "BTC/USD":  2.0,
    "ETH/USD":  2.0,
    "SOL/USD":  2.5,
    "DOGE/USD": 3.5,   # wide stop for noisy meme coin — let existing position breathe to exit
    "AVAX/USD": 2.5,
    "LINK/USD": 2.5,
    "LTC/USD":  2.5,
    "COIN":     1.5,
    "NVDA":     1.5,
    "TSLA":     1.5,
    "AMD":      1.5,
    "META":     1.5,
}

BACKTEST_LIMIT = 150
