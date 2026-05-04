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
    "DOGE/USD",
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
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.05"))  # raised to 5%

# Strategy parameters
EMA_FAST        = 9
EMA_SLOW        = 21
RSI_PERIOD      = 14
RSI_OVERBOUGHT  = 70
RSI_OVERSOLD    = 40    # RSI floor for BUY — rejects failed oversold bounces
VOL_SURGE_MULT  = 1.2   # volume must be 1.2× average for crossover entries
ADX_FADE_EXIT   = 18    # sell when ADX drops below this AND MACD turns negative
ATR_TRAIL_MULT  = 1.8   # trailing stop = ATR × this multiplier (adaptive to volatility)

BACKTEST_LIMIT = 150
