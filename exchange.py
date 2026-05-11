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


# ── Event / macro filters ──────────────────────────────────────────────────────

_earnings_cache: dict = {}   # symbol → (date_checked, days_to_earnings)
_vix_cache: tuple = (0.0, 0.0)   # (value, timestamp)

def days_to_earnings(symbol: str) -> int:
    """
    Return the number of calendar days until the next earnings date for a stock.
    Returns 999 for crypto (no earnings). Cached per symbol per session.
    Uses yfinance calendar — free, no API key.
    """
    if is_crypto(symbol):
        return 999
    now_ts = time.time()
    cached = _earnings_cache.get(symbol)
    if cached and now_ts - cached[0] < 3600:   # 1h cache
        return cached[1]
    try:
        info = yf.Ticker(symbol).calendar
        # calendar is a dict with key 'Earnings Date' → list of datetimes
        if info is not None and "Earnings Date" in info:
            dates = info["Earnings Date"]
            if hasattr(dates, '__iter__'):
                today = datetime.now(_ET).date()
                future = [d.date() if hasattr(d, 'date') else d
                          for d in dates if (d.date() if hasattr(d, 'date') else d) >= today]
                if future:
                    days = (min(future) - today).days
                    _earnings_cache[symbol] = (now_ts, days)
                    return days
    except Exception as e:
        log.debug(f"days_to_earnings {symbol}: {e}")
    _earnings_cache[symbol] = (now_ts, 999)
    return 999


def get_vix() -> float:
    """
    Fetch the current CBOE VIX level via yfinance (^VIX).
    Cached for 15 minutes. Returns 0.0 on failure (fail-open).
    VIX > 30 = high fear / market panic — avoid new entries.
    """
    global _vix_cache
    val, ts = _vix_cache
    if time.time() - ts < 900:   # 15 min cache
        return val
    try:
        raw = yf.download("^VIX", period="1d", interval="1h",
                          progress=False, auto_adjust=True)
        if not raw.empty:
            vix = float(raw["Close"].iloc[-1])
            _vix_cache = (vix, time.time())
            log.info(f"VIX={vix:.1f}")
            return vix
    except Exception as e:
        log.debug(f"get_vix: {e}")
    return val   # return last known value on failure


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
            # Drop last (in-progress) candle — it's incomplete and causes false signals
            # when an EMA crossover appears on an unfinished bar then reverses on close
            return df.iloc[:-1].tail(limit).dropna()
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
