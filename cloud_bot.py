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

import pandas as pd

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

    # Populated from config at class load time so Railway Variables changes
    # require a redeploy (symbols aren't hot-reloaded, params are).
    from config import SYMBOLS as _cfg_symbols
    SYMBOLS = list(_cfg_symbols)

    def __init__(self, on_event: Optional[Callable] = None):
        self._on_event  = on_event or (lambda t, p: None)
        self._stop      = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock      = threading.Lock()
        self._states    = {s: {"in_position": False, "entry": 0.0, "peak": 0.0,
                               "last_sell_time": 0.0}
                           for s in self.SYMBOLS}
        self._client    = None
        self._connected = False
        self._log_path      = os.path.join(os.path.dirname(__file__), "cloud_trades.csv")
        self._cooldown_path = os.path.join(os.path.dirname(__file__), "cooldown_state.json")
        self._load_cooldown_state()

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
        from exchange import get_client, get_balance, fetch_ohlcv, get_position_qty, place_order, get_symbol_timeframe
        from strategy import generate_signals, get_higher_tf_trend
        from config import EMA_FAST, EMA_SLOW, TIMEFRAME, ATR_TRAIL_MULT, STOCK_ADX_MIN, SYMBOL_ADX_MIN, SYMBOL_ATR_MULT

        risk        = float(os.getenv("RISK_PER_TRADE",     "0.05"))
        ema_f       = int(  os.getenv("EMA_FAST",            str(EMA_FAST)))
        ema_s       = int(  os.getenv("EMA_SLOW",            str(EMA_SLOW)))
        tf          =       os.getenv("TIMEFRAME",            TIMEFRAME)
        rsi_ob      = int(  os.getenv("RSI_OVERBOUGHT",      "70"))
        trail_pct   = float(os.getenv("TRAILING_STOP_PCT",   "0.025"))
        trail_mult  = float(os.getenv("ATR_TRAIL_MULT",      str(ATR_TRAIL_MULT)))
        hard_stop   = float(os.getenv("HARD_STOP_PCT",       "0.05"))
        poll        = int(  os.getenv("POLL_SECONDS",        "60"))

        self._client = get_client()
        bal = get_balance(self._client)
        self._connected = True
        self._emit("bot_connected", {
            "cash":      bal["cash"],
            "portfolio": bal["portfolio_value"],
            "mode":      os.getenv("MODE", "paper").upper(),
        })
        log.info(f"Connected — ${bal['cash']:,.2f} cash | tf={tf} risk={risk*100:.0f}% "
                 f"per-symbol ADX/ATR | ATR-trail×{trail_mult}")
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
                            get_symbol_timeframe,
                            SYMBOL_ADX_MIN, SYMBOL_ATR_MULT,
                            risk, ema_f, ema_s, tf, trail_pct, trail_mult, rsi_ob, hard_stop,
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
                 get_symbol_timeframe,
                 symbol_adx_min, symbol_atr_mult,
                 risk, ema_fast, ema_slow, timeframe,
                 trail_pct, trail_mult, rsi_ob, hard_stop=0.05):

        from exchange import is_market_open, is_crypto
        # Skip equity symbols outside NYSE/NASDAQ trading hours
        if not is_crypto(symbol) and not is_market_open():
            self._emit("bot_log", {
                "msg":   f"{symbol} skipped — market closed",
                "color": "gray",
            })
            return

        # Per-symbol timeframe, ADX threshold, and ATR multiplier
        sym_tf         = get_symbol_timeframe(symbol, timeframe)
        sym_adx        = symbol_adx_min.get(symbol, 28 if is_crypto(symbol) else 25)
        sym_trail_mult = symbol_atr_mult.get(symbol, trail_mult)

        df = fetch_ohlcv(limit=150, symbol=symbol, timeframe=sym_tf)
        if df is None or len(df) < 30:
            return

        df     = generate_signals(df,
                                  ema_fast=ema_fast, ema_slow=ema_slow,
                                  adx_min=sym_adx, rsi_overbought=rsi_ob)
        last   = df.iloc[-1]
        price  = float(last["close"])
        rsi    = float(last["rsi"])
        adx    = float(last["adx"])
        atr    = float(last["atr"]) if "atr" in df.columns and not pd.isna(last["atr"]) else 0.0
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

        # ATR-adaptive trailing stop
        # Uses ATR × multiplier when available; falls back to fixed trail_pct
        if state["in_position"] and state["peak"] > 0:
            new_peak = max(state["peak"], price)
            with self._lock:
                self._states[symbol]["peak"] = new_peak
            # adaptive stop distance: ATR-based if ATR is valid, else fixed pct
            atr_at_entry = state.get("atr_entry", 0.0)
            if atr_at_entry > 0:
                stop_dist = atr_at_entry * sym_trail_mult   # per-symbol ATR multiplier
                drop_threshold = stop_dist / new_peak
            else:
                drop_threshold = trail_pct  # fallback to fixed pct
            drop = (new_peak - price) / new_peak
            if drop >= drop_threshold:
                signal = -1
                self._emit("bot_log", {
                    "msg":   f"{symbol} TRAILING STOP — dropped {drop*100:.1f}% "
                             f"(threshold {drop_threshold*100:.1f}%) from peak",
                    "color": "orange",
                })

        # Hard stop-loss: backstop for trending-against-us positions
        # Trailing stop only fires after price peaks; hard stop catches entries
        # that go immediately underwater (e.g. DOGE ADX downtrend, ETH dump).
        if state["in_position"] and state["entry"] > 0 and signal != -1:
            loss_pct = (state["entry"] - price) / state["entry"]
            if loss_pct >= hard_stop:
                signal = -1
                self._emit("bot_log", {
                    "msg":   f"{symbol} HARD STOP — down {loss_pct*100:.1f}% from entry "
                             f"${state['entry']:.2f} (limit {hard_stop*100:.0f}%)",
                    "color": "red",
                })

        # Re-entry cooldown: block BUY if last sell was within 2h (prevents churn on choppy symbols)
        cooldown_secs = int(os.getenv("REENTRY_COOLDOWN_SECS", "7200"))  # default 2 hours
        if signal == 1 and not state["in_position"]:
            elapsed = time.time() - state.get("last_sell_time", 0.0)
            if elapsed < cooldown_secs:
                self._emit("bot_log", {
                    "msg":   f"{symbol} BUY blocked — cooldown {int((cooldown_secs - elapsed)/60)}min remaining",
                    "color": "yellow",
                })
                signal = 0

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
            min_qty = 0.00001 if is_crypto(symbol) else 0.001
            if qty > min_qty:
                place_order(self._client, "buy", symbol, qty)
                with self._lock:
                    self._states[symbol].update({
                        "in_position": True,
                        "entry":       price,
                        "peak":        price,
                        "atr_entry":   atr,   # store ATR at entry for adaptive stop
                    })
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
            min_qty = 0.00001 if is_crypto(symbol) else 0.001
            precision = 1e5 if is_crypto(symbol) else 1e3
            actual_qty = math.floor(get_position_qty(self._client, symbol) * precision) / precision
            if actual_qty > min_qty:
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
            else:
                # Position already closed externally — emit single event, no CSV write
                # (avoids double-entry: server.py memory handles the log)
                self._emit("bot_trade", {
                    "symbol":   symbol,
                    "action":   "SELL",
                    "price":    price,
                    "qty":      None,       # None = externally closed, qty unknown
                    "pnl":      None,       # None = P&L unknown (position was closed outside bot)
                    "note":     "ext",      # tag for display
                })
                log.info(f"SELL {symbol} — position already closed externally (qty~0)")
            with self._lock:
                self._states[symbol].update({"in_position": False, "entry": 0.0, "peak": 0.0,
                                             "last_sell_time": time.time()})
            self._save_cooldown_state()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _load_cooldown_state(self):
        """Restore last_sell_time from disk so cooldowns survive redeploys."""
        try:
            import json as _json
            if os.path.exists(self._cooldown_path):
                data = _json.loads(open(self._cooldown_path).read())
                for sym, ts in data.items():
                    if sym in self._states:
                        self._states[sym]["last_sell_time"] = float(ts)
                log.info(f"Cooldown state loaded for {len(data)} symbols")
        except Exception as e:
            log.warning(f"Could not load cooldown state: {e}")

    def _save_cooldown_state(self):
        """Persist last_sell_time to disk after every sell."""
        try:
            import json as _json
            data = {s: v["last_sell_time"] for s, v in self._states.items()
                    if v.get("last_sell_time", 0) > 0}
            with open(self._cooldown_path, "w") as f:
                f.write(_json.dumps(data))
        except Exception as e:
            log.warning(f"Could not save cooldown state: {e}")

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
