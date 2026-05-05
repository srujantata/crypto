import logging
import time
from datetime import datetime

import pandas as pd
import pytz
import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from config import API_KEY, SECRET, MODE, TIMEFRAME, BACKTEST_LIMIT

log = logging.getLogger("exchange")

_YF_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
                "1h": "1h", "4h": "4h", "1d": "1d"}
_YF_PERIOD   = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d",
                "1h": "730d", "4h": "730d", "1d": "5y"}

_FETCH_RETRIES = 3
_FETCH_BACKOFF = 2  # seconds, doubles each retry


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


_TF_ORDER = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]

def get_symbol_timeframe(symbol: str, base_tf: str) -> str:
    """Stocks use a slower timeframe to cut whipsaw — always at least STOCK_TIMEFRAME.
    Crypto keeps base_tf unchanged."""
    if is_crypto(symbol):
        return base_tf
    from config import STOCK_TIMEFRAME
    base_i  = _TF_ORDER.index(base_tf)        if base_tf        in _TF_ORDER else 2
    stock_i = _TF_ORDER.index(STOCK_TIMEFRAME) if STOCK_TIMEFRAME in _TF_ORDER else 4
    return _TF_ORDER[max(base_i, stock_i)]


_ET = pytz.timezone("America/New_York")

def is_market_open() -> bool:
    """True when US equities market is open (9:30–16:00 ET, Mon–Fri)."""
    now = datetime.now(_ET)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    open_  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= now <= close_


def to_yf_ticker(symbol: str) -> str:
    if is_crypto(symbol):
        base, quote = symbol.split("/")
        quote = "USD" if quote == "USDT" else quote
        return f"{base}-{quote}"
    return symbol


def to_alpaca_symbol(symbol: str) -> str:
    if is_crypto(symbol):
        return symbol.replace("/", "")
    return symbol


def get_client() -> TradingClient:
    paper = MODE != "live"
    return TradingClient(API_KEY, SECRET, paper=paper)


def fetch_ohlcv(limit: int = BACKTEST_LIMIT, symbol: str = "BTC/USD",
                timeframe: str = TIMEFRAME) -> pd.DataFrame:
    """Fetch OHLCV data with retry on transient network errors."""
    ticker   = to_yf_ticker(symbol)
    interval = _YF_INTERVAL.get(timeframe, "1h")
    period   = _YF_PERIOD.get(timeframe, "60d")

    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(_FETCH_RETRIES):
        try:
            raw = yf.download(ticker, period=period, interval=interval,
                              progress=False, auto_adjust=True)
            if raw.empty:
                raise ValueError(f"yfinance returned empty data for {ticker}")
            df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.columns = ["open", "high", "low", "close", "volume"]
            df.index.name = "timestamp"
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            return df.tail(limit).dropna()
        except Exception as e:
            last_exc = e
            if attempt < _FETCH_RETRIES - 1:
                delay = _FETCH_BACKOFF * (2 ** attempt)
                log.warning(f"fetch_ohlcv {symbol} attempt {attempt+1} failed: {e} — retrying in {delay}s")
                time.sleep(delay)

    raise last_exc


def get_balance(client: TradingClient) -> dict:
    account = client.get_account()
    return {
        "cash":            float(account.cash),
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
    if qty <= 0:
        raise ValueError(f"place_order: qty must be positive, got {qty}")
    alpaca_sym = to_alpaca_symbol(symbol)
    # Stocks: DAY orders only valid during market hours.
    # Crypto: GTC because exchange is 24/7.
    tif = TimeInForce.GTC if is_crypto(symbol) else TimeInForce.DAY
    order = MarketOrderRequest(
        symbol=alpaca_sym,
        qty=round(qty, 6),
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=tif,
    )
    result = client.submit_order(order)
    log.info(f"Order submitted: {side.upper()} {qty:.6f} {alpaca_sym} (TIF={tif.value}) → id={result.id}")
    return result
