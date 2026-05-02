import pandas as pd
import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from config import API_KEY, SECRET, MODE, TIMEFRAME, BACKTEST_LIMIT

_YF_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
                 "1h": "1h", "4h": "4h", "1d": "1d"}
_YF_PERIOD   = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d",
                 "1h": "730d", "4h": "730d", "1d": "5y"}


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


def to_yf_ticker(symbol: str) -> str:
    """Convert internal symbol to yfinance ticker."""
    if is_crypto(symbol):
        base, quote = symbol.split("/")
        quote = "USD" if quote == "USDT" else quote
        return f"{base}-{quote}"
    return symbol  # stocks use ticker directly (MSTR, COIN)


def to_alpaca_symbol(symbol: str) -> str:
    """Convert internal symbol to Alpaca order symbol."""
    if is_crypto(symbol):
        return symbol.replace("/", "")  # BTC/USD -> BTCUSD
    return symbol  # stocks unchanged


def get_client() -> TradingClient:
    paper = MODE != "live"
    return TradingClient(API_KEY, SECRET, paper=paper)


def fetch_ohlcv(limit: int = BACKTEST_LIMIT, symbol: str = "BTC/USD",
                timeframe: str = TIMEFRAME) -> pd.DataFrame:
    ticker   = to_yf_ticker(symbol)
    interval = _YF_INTERVAL.get(timeframe, "1h")
    # Stocks: use longer period for backtesting; 15m only available for 60 days on yfinance
    period   = _YF_PERIOD.get(timeframe, "60d")
    raw = yf.download(ticker, period=period, interval=interval,
                      progress=False, auto_adjust=True)
    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index.name = "timestamp"
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.tail(limit).dropna()


def get_balance(client: TradingClient) -> dict:
    account = client.get_account()
    return {
        "cash": float(account.cash),
        "portfolio_value": float(account.portfolio_value),
    }


def get_position_qty(client: TradingClient, symbol: str) -> float:
    alpaca_sym = to_alpaca_symbol(symbol)
    try:
        pos = client.get_open_position(alpaca_sym)
        return float(pos.qty)
    except Exception:
        return 0.0


def place_order(client: TradingClient, side: str, symbol: str, qty: float):
    alpaca_sym = to_alpaca_symbol(symbol)
    order = MarketOrderRequest(
        symbol=alpaca_sym,
        qty=round(qty, 6),
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
    )
    return client.submit_order(order)
