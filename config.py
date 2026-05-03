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
EMA_FAST       = 9
EMA_SLOW       = 21
RSI_PERIOD     = 14
RSI_OVERBOUGHT = 70

BACKTEST_LIMIT = 150
