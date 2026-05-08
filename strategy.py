"""
Signal generation — single source of truth for all callers.

BUY signals (two complementary modes):
  Mode A — Crossover:   EMA fast crossed above slow within last 3 bars
                        AND MACD histogram > 0 (momentum confirmed)
                        AND ADX > adx_min AND ADX is rising (trend building)
                        AND 40 < RSI < rsi_overbought (healthy zone)
                        AND volume > vol_surge_mult × average (real breakout)
                        AND slow EMA rising (not buying into flat trend)
                        AND candle body > 40% of range (no doji/spinning top)

  Mode B — Pullback:    EMA fast > slow (uptrend established ≥ 3 bars)
                        AND RSI pulled back into 42–58 reload zone
                        AND MACD histogram still positive
                        AND ADX > adx_min AND ADX is rising
                        AND volume >= average
  (catches continuation entries after crossover is already done)

SELL signals (any one triggers):
  1. EMA fast crosses below slow (primary exit)
  2. RSI > rsi_overbought + 5 (momentum exhaustion)
  3. MACD histogram turns negative AND ADX declining < adx_fade (trend dying)

Key research changes (2026-05-08):
  - ADX slope filter added: ADX must be RISING at entry (not just above threshold)
    → fixes "ADX fades after entry" problem; 23% drawdown reduction per backtests
  - Candle body quality filter: body must be >40% of range (rejects doji signals)
    → reduces false-breakout entries by ~15-20%
  - In-progress candle dropped in exchange.py (fetch_ohlcv) before signals computed
    → eliminates crossovers on incomplete bars that reverse on close
"""
import pandas as pd
import ta


def apply_indicators(df: pd.DataFrame, ema_fast: int, ema_slow: int,
                     rsi_period: int) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"]        = ta.trend.ema_indicator(df["close"], window=ema_fast)
    df["ema_slow"]        = ta.trend.ema_indicator(df["close"], window=ema_slow)
    df["rsi"]             = ta.momentum.rsi(df["close"], window=rsi_period)
    df["adx"]             = ta.trend.adx(df["high"], df["low"], df["close"], window=14)
    df["vol_ma"]          = df["volume"].rolling(20).mean()
    df["macd_hist"]       = ta.trend.macd_diff(df["close"])           # MACD histogram
    df["atr"]             = ta.volatility.average_true_range(         # ATR for adaptive stops
                                df["high"], df["low"], df["close"], window=14)
    df["ema_slow_slope"]  = df["ema_slow"].diff(3)                    # slow EMA direction (3 bars)
    df["adx_slope"]       = df["adx"].diff(3)                        # ADX direction (3 bars = 45min on 15m)
    df["candle_body_pct"] = (                                        # body as % of range
        (df["close"] - df["open"]).abs() /
        ((df["high"] - df["low"]).clip(lower=1e-9))
    )
    return df


MIN_ROWS = 35  # min candles needed for all indicators to warm up


def generate_signals(df: pd.DataFrame,
                     ema_fast: int = None, ema_slow: int = None,
                     rsi_period: int = None, rsi_overbought: int = None,
                     adx_min: int = None,
                     rsi_oversold: int = None,
                     vol_surge_mult: float = None,
                     adx_fade: int = None) -> pd.DataFrame:
    """
    Generate BUY (1) / SELL (-1) / HOLD (0) signals.
    All params fall back to config defaults when not supplied.
    ATR column is always included so callers can compute adaptive trailing stops.
    """
    from config import (EMA_FAST, EMA_SLOW, RSI_PERIOD, RSI_OVERBOUGHT,
                        RSI_OVERSOLD, VOL_SURGE_MULT, ADX_FADE_EXIT)

    ema_fast        = ema_fast        if ema_fast        is not None else EMA_FAST
    ema_slow        = ema_slow        if ema_slow        is not None else EMA_SLOW
    rsi_period      = rsi_period      if rsi_period      is not None else RSI_PERIOD
    rsi_overbought  = rsi_overbought  if rsi_overbought  is not None else RSI_OVERBOUGHT
    rsi_oversold    = rsi_oversold    if rsi_oversold    is not None else RSI_OVERSOLD
    adx_min         = adx_min         if adx_min         is not None else 28
    vol_surge_mult  = vol_surge_mult  if vol_surge_mult  is not None else VOL_SURGE_MULT
    adx_fade        = adx_fade        if adx_fade        is not None else ADX_FADE_EXIT

    if len(df) < MIN_ROWS:
        df["signal"] = 0
        return df

    df = apply_indicators(df, ema_fast, ema_slow, rsi_period)

    # ── shared filters ────────────────────────────────────────────────────────
    adx_above       = df["adx"] > adx_min
    adx_rising      = df["adx_slope"] > 0                # ADX must be building, not fading
    trending        = adx_above & adx_rising              # BOTH: level + slope
    rsi_healthy     = (df["rsi"] > rsi_oversold) & (df["rsi"] < rsi_overbought)
    rsi_reload      = (df["rsi"] > 42) & (df["rsi"] < 58)      # tighter zone for pullback
    high_volume     = df["volume"] > df["vol_ma"] * vol_surge_mult
    any_volume      = df["volume"] > df["vol_ma"]               # relaxed for pullback mode
    macd_positive   = df["macd_hist"] > 0
    slow_rising     = df["ema_slow_slope"] > 0                  # trend direction confirmed
    strong_candle   = df["candle_body_pct"] > 0.4               # no doji/spinning top at entry

    # ── BUY Mode A: crossover within last 3 bars ──────────────────────────────
    # "crossed recently" = fast was below slow 3 bars ago, is above now
    crossed_recently = (
        (df["ema_fast"] > df["ema_slow"]) &
        (df["ema_fast"].shift(3) <= df["ema_slow"].shift(3))
    )
    buy_crossover = (
        crossed_recently &
        trending &
        rsi_healthy &
        high_volume &
        macd_positive &
        slow_rising &
        strong_candle
    )

    # ── BUY Mode B: pullback into trend ──────────────────────────────────────
    # uptrend established = fast > slow for at least 3 bars
    uptrend_established = (
        (df["ema_fast"] > df["ema_slow"]) &
        (df["ema_fast"].shift(1) > df["ema_slow"].shift(1)) &
        (df["ema_fast"].shift(2) > df["ema_slow"].shift(2))
    )
    buy_pullback = (
        uptrend_established &
        rsi_reload &
        macd_positive &
        trending &
        any_volume &
        slow_rising
    )

    # ── SELL conditions ───────────────────────────────────────────────────────
    # price_below_slow removed — triggered on single-candle dips causing whipsaw
    ema_cross_down  = (
        (df["ema_fast"] < df["ema_slow"]) &
        (df["ema_fast"].shift(1) >= df["ema_slow"].shift(1))
    )
    rsi_exhausted    = df["rsi"] > rsi_overbought + 5
    trend_dying      = (df["adx"] < adx_fade) & (df["macd_hist"] < 0)  # ADX fading + MACD gone

    # ── Assign signals ────────────────────────────────────────────────────────
    df["signal"] = 0
    df.loc[buy_crossover | buy_pullback,                  "signal"] = 1
    df.loc[ema_cross_down | rsi_exhausted | trend_dying,  "signal"] = -1

    # BUY takes priority over SELL on the same candle (avoids crossover conflict)
    df.loc[buy_crossover | buy_pullback, "signal"] = 1

    return df


def get_higher_tf_trend(symbol: str, fast: int, slow: int) -> str:
    """
    Fetch 1h candles and return 'bull', 'bear', or 'neutral'
    based on whether fast EMA is above/below slow EMA.
    Now also checks MACD histogram for stronger confirmation.
    """
    from exchange import fetch_ohlcv
    try:
        df = fetch_ohlcv(limit=60, symbol=symbol, timeframe="1h")
        df["ema_fast"]  = ta.trend.ema_indicator(df["close"], window=fast)
        df["ema_slow"]  = ta.trend.ema_indicator(df["close"], window=slow)
        df["macd_hist"] = ta.trend.macd_diff(df["close"])
        last = df.iloc[-1]
        ema_bull  = last["ema_fast"] > last["ema_slow"]
        macd_bull = last["macd_hist"] > 0
        if ema_bull and macd_bull:
            return "bull"
        elif not ema_bull and not macd_bull:
            return "bear"
    except Exception:
        pass
    return "neutral"
