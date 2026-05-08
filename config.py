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
    # DOGE/USD removed — Hurst exponent ~0.5 (near random walk), poor trend-following candidate
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
ADX_FADE_EXIT   = 18    # sell when ADX drops below this AND MACD turns negative
ATR_TRAIL_MULT  = 2.0   # raised 1.8→2.0: BTC/ETH trailing stop baseline
HARD_STOP_PCT   = 0.05  # hard stop-loss — exit if position drops > 5% from entry price

# Stock-specific overrides — slower timeframe + higher trend filter
STOCK_TIMEFRAME = "1h"  # stocks use 1h candles — reduces whipsaw vs 15m
STOCK_ADX_MIN   = 25    # stocks on 1h are smoother — 25 appropriate (stocks < crypto threshold)

# Per-symbol ADX thresholds (research: 15m crypto needs HIGHER ADX than 1h stocks)
# BTC/ETH: 30 (high-liquidity, trend reliable at 30 on 15m)
# Other crypto: 28 (raised from 20 — Wilder's 25 was for daily, 15m needs higher)
# Stocks: 25 (1h candles = smoother ADX, institutional liquidity)
SYMBOL_ADX_MIN = {
    "BTC/USD":  30,
    "ETH/USD":  30,
    "SOL/USD":  28,
    "AVAX/USD": 28,
    "LINK/USD": 28,
    "LTC/USD":  28,
    "COIN":     25,
    "NVDA":     25,
    "TSLA":     25,
    "AMD":      25,
    "META":     25,
}

# Per-symbol ATR trailing stop multiplier
# Crypto alts (higher beta) need wider stops to avoid noise-triggered exits
# Stocks on 1h are smoother — tighter stops capture more profit
SYMBOL_ATR_MULT = {
    "BTC/USD":  2.0,
    "ETH/USD":  2.0,
    "SOL/USD":  2.5,
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
