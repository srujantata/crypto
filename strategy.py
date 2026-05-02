import pandas as pd
import ta


def apply_indicators(df: pd.DataFrame, ema_fast: int, ema_slow: int, rsi_period: int) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"]   = ta.trend.ema_indicator(df["close"], window=ema_fast)
    df["ema_slow"]   = ta.trend.ema_indicator(df["close"], window=ema_slow)
    df["rsi"]        = ta.momentum.rsi(df["close"], window=rsi_period)
    df["adx"]        = ta.trend.adx(df["high"], df["low"], df["close"], window=14)
    df["vol_ma"]     = df["volume"].rolling(20).mean()
    return df


MIN_ROWS = 30  # minimum candles needed for ADX + EMA to be meaningful


def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rules:
      BUY  (1):  EMA fast crosses above EMA slow
                 AND ADX > 25 (trending market)
                 AND RSI < 70 (not overbought)
                 AND volume > 20-period average (real breakout)
      SELL (-1): EMA fast crosses below EMA slow
                 OR RSI > 75 (momentum exhaustion)
    """
    from config import EMA_FAST, EMA_SLOW, RSI_PERIOD, RSI_OVERBOUGHT

    if len(df) < MIN_ROWS:
        df["signal"] = 0
        return df

    df = apply_indicators(df, EMA_FAST, EMA_SLOW, RSI_PERIOD)

    ema_cross_up   = (df["ema_fast"] > df["ema_slow"]) & (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))
    ema_cross_down = (df["ema_fast"] < df["ema_slow"]) & (df["ema_fast"].shift(1) >= df["ema_slow"].shift(1))

    trending       = df["adx"] > 25
    not_overbought = df["rsi"] < RSI_OVERBOUGHT
    high_volume    = df["volume"] > df["vol_ma"]

    df["signal"] = 0
    df.loc[ema_cross_up   & trending & not_overbought & high_volume, "signal"] = 1
    df.loc[ema_cross_down | (df["rsi"] > 75),                        "signal"] = -1

    return df


def get_higher_tf_trend(symbol: str, fast: int, slow: int) -> str:
    """
    Fetch 1h candles and return 'bull', 'bear', or 'neutral'
    based on whether fast EMA is above/below slow EMA.
    Used to confirm 15m signals align with the bigger trend.
    """
    from exchange import fetch_ohlcv
    try:
        df = fetch_ohlcv(limit=60, symbol=symbol, timeframe="1h")
        df["ema_fast"] = ta.trend.ema_indicator(df["close"], window=fast)
        df["ema_slow"] = ta.trend.ema_indicator(df["close"], window=slow)
        last = df.iloc[-1]
        if last["ema_fast"] > last["ema_slow"]:
            return "bull"
        elif last["ema_fast"] < last["ema_slow"]:
            return "bear"
    except Exception:
        pass
    return "neutral"
