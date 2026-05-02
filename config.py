import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET  = os.getenv("ALPACA_SECRET", "")
MODE    = os.getenv("MODE", "paper")

SYMBOLS = [
    # Crypto — backtested, positive or borderline performers
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "DOGE/USD",
    "AVAX/USD",
    "LINK/USD",
    "LTC/USD",
    # Stocks
    "COIN",
]

TIMEFRAME      = os.getenv("TIMEFRAME", "15m")
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.05"))  # raised to 5%

# Strategy parameters
EMA_FAST       = 9
EMA_SLOW       = 21
RSI_PERIOD     = 14
RSI_OVERBOUGHT = 70

BACKTEST_LIMIT = 150
