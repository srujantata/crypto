"""
Cloud live trading bot — runs on Railway 24/7.
Connects to Alpaca paper/live, trades all symbols,
broadcasts events via the server's WebSocket.
"""
import csv
import logging
import math
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s — %(message)s",
)
log = logging.getLogger("cloud_bot")


class CloudLiveBot:
    """
    Runs the live Alpaca trading loop in a background thread.
    on_event(type, payload) is called for every trade/tick so
    the FastAPI WebSocket layer can push it to connected dashboards.

    All strategy params are read from env vars on each connect so
    Railway Variables tab changes take effect on the next reconnect.
    """

    SYMBOLS = [
        "BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD",
        "AVAX/USD", "LINK/USD", "LTC/USD", "COIN",
    ]

    def __init__(self, on_event: Optional[Callable] = None):
        self._on_event  = on_event or (lambda t, p: None)
        self._stop      = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock      = threading.Lock()
        self._states    = {s: {"in_position": False, "entry": 0.0, "peak": 0.0}
                           for s in self.SYMBOLS}
        self._client    = None
        self._connected = False
        self._log_path  = os.path.join(os.path.dirname(__file__), "cloud_trades.csv")

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="CloudBot")
        self._thread.start()
        log.info("CloudLiveBot started")

    def stop(self):
        self._stop.set()
        log.info("CloudLiveBot stopping")

    def status(self) -> dict:
        with self._lock:
            positions = {s: dict(v) for s, v in self._states.items() if v["in_position"]}
        return {
            "connected": self._connected,
            "positions": {s: {"entry": v["entry"]} for s, v in positions.items()},
            "symbols":   self.SYMBOLS,
        }

    # ── retry wrapper ─────────────────────────────────────────────────────────

    def _loop(self):
        """Outer retry loop — reconnects with exponential backoff on any failure."""
        retry_delay = 30
        while not self._stop.is_set():
            try:
                self._connect_and_trade()
                break  # clean stop via stop event
            except Exception as e:
                self._connected = False
                log.error(f"Bot session ended: {e}", exc_info=True)
                self._emit("bot_log", {
                    "msg":   f"[ERROR] Disconnected: {e} — reconnecting in {retry_delay}s",
                    "color": "red",
                })
                self._stop.wait(retry_delay)
                retry_delay = min(retry_delay * 2, 300)  # cap at 5 min

    # ── single trading session ────────────────────────────────────────────────

    def _connect_and_trade(self):
        """
        One connect + trade session. Raises on any unrecoverable error
        so _loop can retry with backoff.
        All params re-read from env so Railway Variables changes take effect
        without redeploying.
        """
        from exchange import get_client, get_balance, fetch_ohlcv, get_position_qty, place_order
        from strategy import generate_signals, get_higher_tf_trend
        from config import EMA_FAST, EMA_SLOW, TIMEFRAME

        risk    = float(os.getenv("RISK_PER_TRADE",     "0.05"))
        ema_f   = int(  os.getenv("EMA_FAST",            str(EMA_FAST)))
        ema_s   = int(  os.getenv("EMA_SLOW",            str(EMA_SLOW)))
        tf      =       os.getenv("TIMEFRAME",            TIMEFRAME)
        adx_min = int(  os.getenv("ADX_MIN",             "25"))
        rsi_ob  = int(  os.getenv("RSI_OVERBOUGHT",      "70"))
        trail   = float(os.getenv("TRAILING_STOP_PCT",   "0.025"))
        poll    = int(  os.getenv("POLL_SECONDS",        "60"))

        self._client = get_client()
        bal = get_balance(self._client)
        self._connected = True
        self._emit("bot_connected", {
            "cash":      bal["cash"],
            "portfolio": bal["portfolio_value"],
            "mode":      os.getenv("MODE", "paper").upper(),
        })
        log.info(f"Connected — ${bal['cash']:,.2f} cash | tf={tf} risk={risk*100:.0f}% ADX>{adx_min}")
        self._sync_positions(get_position_qty)

        while not self._stop.is_set():
            try:
                bal = get_balance(self._client)
                self._emit("bot_balance", {"cash": bal["cash"], "portfolio": bal["portfolio_value"]})

                for symbol in self.SYMBOLS:
                    if self._stop.is_set():
                        break
                    try:
                        self._process(
                            symbol, bal,
                            fetch_ohlcv, generate_signals, get_higher_tf_trend,
                            get_position_qty, place_order,
                            risk, ema_f, ema_s, tf, trail, adx_min, rsi_ob,
                        )
                    except Exception as e:
                        log.warning(f"{symbol}: {e}")
                        self._emit("bot_log", {"msg": f"[WARN] {symbol}: {e}", "color": "yellow"})
                    time.sleep(1)

            except Exception as e:
                log.error(f"Cycle error: {e}", exc_info=True)
                raise  # escalate to _loop for reconnect

            self._stop.wait(poll)

        self._connected = False
        self._emit("bot_stopped", {})

    # ── position sync ─────────────────────────────────────────────────────────

    def _sync_positions(self, get_position_qty):
        try:
            for pos in self._client.get_all_positions():
                matched = next((s for s in self.SYMBOLS if s.replace("/", "") == pos.symbol), None)
                if not matched:
                    continue
                with self._lock:
                    self._states[matched].update({
                        "in_position": True,
                        "entry":       float(pos.avg_entry_price),
                        "peak":        float(pos.current_price),
                    })
                log.info(f"Resumed {matched} @ ${pos.avg_entry_price}")
                self._emit("bot_resumed", {
                    "symbol": matched,
                    "entry":  float(pos.avg_entry_price),
                    "qty":    float(pos.qty),
                })
        except Exception as e:
            log.warning(f"Position sync error: {e}")

    # ── per-symbol logic ──────────────────────────────────────────────────────

    def _process(self, symbol, bal,
                 fetch_ohlcv, generate_signals, get_higher_tf_trend,
                 get_position_qty, place_order,
                 risk, ema_fast, ema_slow, timeframe,
                 trail_pct, adx_min, rsi_ob):

        df = fetch_ohlcv(limit=150, symbol=symbol, timeframe=timeframe)
        if df is None or len(df) < 30:
            return

        df     = generate_signals(df,
                                  ema_fast=ema_fast, ema_slow=ema_slow,
                                  adx_min=adx_min, rsi_overbought=rsi_ob)
        last   = df.iloc[-1]
        price  = float(last["close"])
        rsi    = float(last["rsi"])
        adx    = float(last["adx"])
        signal = int(last["signal"])

        with self._lock:
            state = dict(self._states[symbol])  # snapshot for read

        self._emit("bot_tick", {
            "symbol":      symbol,
            "price":       price,
            "rsi":         rsi,
            "adx":         adx,
            "signal":      signal,
            "in_position": state["in_position"],
        })

        # trailing stop — guard against zero/negative peak
        if state["in_position"] and state["peak"] > 0:
            new_peak = max(state["peak"], price)
            drop = (new_peak - price) / new_peak
            with self._lock:
                self._states[symbol]["peak"] = new_peak
            if drop >= trail_pct:
                signal = -1
                self._emit("bot_log", {
                    "msg":   f"{symbol} TRAILING STOP — dropped {drop*100:.1f}% from peak",
                    "color": "orange",
                })

        # higher-timeframe trend filter on BUY
        if signal == 1 and not state["in_position"]:
            htf = get_higher_tf_trend(symbol, ema_fast, ema_slow)
            if htf == "bear":
                self._emit("bot_log", {"msg": f"{symbol} BUY blocked — 1h bearish", "color": "yellow"})
                signal = 0

        # BUY
        if signal == 1 and not state["in_position"]:
            trade_usd = bal["cash"] * risk
            qty = trade_usd / price
            if qty > 0.00001:
                place_order(self._client, "buy", symbol, qty)
                with self._lock:
                    self._states[symbol].update({"in_position": True, "entry": price, "peak": price})
                self._log_trade(symbol, "BUY", price, qty)
                self._emit("bot_trade", {
                    "symbol":    symbol,
                    "action":    "BUY",
                    "price":     price,
                    "qty":       qty,
                    "trade_usd": trade_usd,
                    "adx":       adx,
                })
                log.info(f"BUY  {symbol}  {qty:.5f} @ ${price:.2f}  ADX={adx:.1f}")

        # SELL
        elif signal == -1 and state["in_position"]:
            actual_qty = math.floor(get_position_qty(self._client, symbol) * 1e5) / 1e5
            if actual_qty > 0.00001:
                place_order(self._client, "sell", symbol, actual_qty)
                pnl = (price - state["entry"]) * actual_qty
                self._log_trade(symbol, "SELL", price, actual_qty, pnl)
                self._emit("bot_trade", {
                    "symbol": symbol,
                    "action": "SELL",
                    "price":  price,
                    "qty":    actual_qty,
                    "pnl":    pnl,
                })
                log.info(f"SELL {symbol}  {actual_qty:.5f} @ ${price:.2f}  PnL ${pnl:+.2f}")
            with self._lock:
                self._states[symbol].update({"in_position": False, "entry": 0.0, "peak": 0.0})

    # ── helpers ───────────────────────────────────────────────────────────────

    def _emit(self, event_type: str, payload: dict):
        try:
            self._on_event(event_type, payload)
        except Exception:
            pass

    def _log_trade(self, symbol, action, price, qty, pnl=None):
        is_new = not os.path.exists(self._log_path)
        with open(self._log_path, "a", newline="") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["timestamp", "symbol", "action", "price", "qty", "pnl"])
            w.writerow([
                datetime.now().isoformat(), symbol, action,
                f"{price:.4f}", f"{qty:.6f}",
                f"{pnl:.2f}" if pnl is not None else "",
            ])
