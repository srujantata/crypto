"""
Cloud live trading bot — runs on Railway 24/7.
Connects to Alpaca paper/live, trades all symbols,
broadcasts events via the server's WebSocket.
"""
import threading
import time
import csv
import os
import math
import logging
from datetime import datetime
from typing import Callable, Optional

log = logging.getLogger("cloud_bot")


class CloudLiveBot:
    """
    Runs the live Alpaca trading loop in a background thread.
    on_event(type, payload) is called for every trade/tick so
    the FastAPI WebSocket layer can push it to connected dashboards.
    """

    SYMBOLS = [
        "BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD",
        "AVAX/USD", "LINK/USD", "LTC/USD", "COIN",
    ]
    TRAILING_STOP_PCT = 0.025
    POLL_SECONDS      = 60

    def __init__(self, on_event: Optional[Callable] = None):
        self._on_event   = on_event or (lambda t, p: None)
        self._stop       = threading.Event()
        self._thread     = None
        self._states     = {s: {"in_position": False, "entry": 0.0, "peak": 0.0}
                            for s in self.SYMBOLS}
        self._client     = None
        self._connected  = False
        self._log_path   = os.path.join(os.path.dirname(__file__), "cloud_trades.csv")

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
        positions = {s: v for s, v in self._states.items() if v["in_position"]}
        return {
            "connected": self._connected,
            "positions": {s: {"entry": v["entry"]} for s, v in positions.items()},
            "symbols":   self.SYMBOLS,
        }

    # ── main loop ─────────────────────────────────────────────────────────────
    def _loop(self):
        # connect
        try:
            from exchange import get_client, get_balance, fetch_ohlcv, get_position_qty, place_order
            from strategy import generate_signals, get_higher_tf_trend
            from config import RISK_PER_TRADE, EMA_FAST, EMA_SLOW, TIMEFRAME
        except Exception as e:
            log.error(f"Import error: {e}")
            self._emit("bot_error", {"message": str(e)})
            return

        try:
            self._client = get_client()
            bal = get_balance(self._client)
            self._connected = True
            self._emit("bot_connected", {
                "cash": bal["cash"],
                "portfolio": bal["portfolio_value"],
                "mode": os.getenv("MODE", "paper").upper(),
            })
            log.info(f"Connected to Alpaca — ${bal['cash']:,.2f} cash")
        except Exception as e:
            log.error(f"Alpaca connection failed: {e}")
            self._emit("bot_error", {"message": f"Alpaca connection failed: {e}"})
            return

        # sync existing positions
        self._sync_positions(get_position_qty)

        # trading loop
        while not self._stop.is_set():
            try:
                bal = get_balance(self._client)
                self._emit("bot_balance", {
                    "cash": bal["cash"],
                    "portfolio": bal["portfolio_value"],
                })

                for symbol in self.SYMBOLS:
                    if self._stop.is_set():
                        break
                    try:
                        self._process(symbol, bal, fetch_ohlcv, generate_signals,
                                      get_higher_tf_trend, get_position_qty,
                                      place_order, RISK_PER_TRADE, EMA_FAST, EMA_SLOW, TIMEFRAME)
                    except Exception as e:
                        log.warning(f"{symbol}: {e}")
                        self._emit("bot_error", {"symbol": symbol, "message": str(e)})
                    time.sleep(1)

            except Exception as e:
                log.error(f"Loop error: {e}")

            self._stop.wait(self.POLL_SECONDS)

        self._emit("bot_stopped", {})

    def _sync_positions(self, get_position_qty):
        """On startup, sync any existing Alpaca positions into local state."""
        try:
            open_positions = self._client.get_all_positions()
            for pos in open_positions:
                sym_raw = pos.symbol
                matched = next((s for s in self.SYMBOLS if s.replace("/", "") == sym_raw), None)
                if matched:
                    self._states[matched]["in_position"] = True
                    self._states[matched]["entry"]       = float(pos.avg_entry_price)
                    self._states[matched]["peak"]        = float(pos.current_price)
                    log.info(f"Resumed {matched} @ ${pos.avg_entry_price}")
                    self._emit("bot_resumed", {
                        "symbol": matched,
                        "entry":  float(pos.avg_entry_price),
                        "qty":    float(pos.qty),
                    })
        except Exception as e:
            log.warning(f"Position sync error: {e}")

    def _process(self, symbol, bal, fetch_ohlcv, generate_signals,
                 get_higher_tf_trend, get_position_qty, place_order,
                 risk, ema_fast, ema_slow, timeframe):
        df = fetch_ohlcv(limit=150, symbol=symbol, timeframe=timeframe)
        if df is None or len(df) < 30:
            return

        df     = generate_signals(df)
        last   = df.iloc[-1]
        price  = float(last["close"])
        rsi    = float(last["rsi"])
        adx    = float(last["adx"])
        signal = int(last["signal"])
        state  = self._states[symbol]

        # emit tick
        self._emit("bot_tick", {
            "symbol": symbol, "price": price,
            "rsi": rsi, "adx": adx, "signal": signal,
            "in_position": state["in_position"],
        })

        # trailing stop
        if state["in_position"]:
            state["peak"] = max(state["peak"], price)
            drop = (state["peak"] - price) / state["peak"]
            if drop >= self.TRAILING_STOP_PCT:
                signal = -1
                self._emit("bot_log", {
                    "msg": f"{symbol} TRAILING STOP — dropped {drop*100:.1f}% from peak",
                    "color": "orange"
                })

        # HTF filter on BUY
        if signal == 1 and not state["in_position"]:
            htf = get_higher_tf_trend(symbol, ema_fast, ema_slow)
            if htf == "bear":
                self._emit("bot_log", {"msg": f"{symbol} BUY blocked — 1h bearish", "color": "yellow"})
                signal = 0

        # execute BUY
        if signal == 1 and not state["in_position"]:
            trade_usd = bal["cash"] * risk
            qty = trade_usd / price
            if qty > 0.00001:
                place_order(self._client, "buy", symbol, qty)
                state["in_position"] = True
                state["entry"]       = price
                state["peak"]        = price
                self._log_trade(symbol, "BUY", price, qty)
                self._emit("bot_trade", {
                    "symbol": symbol, "action": "BUY",
                    "price": price, "qty": qty,
                    "trade_usd": trade_usd, "adx": adx,
                })
                log.info(f"BUY {symbol} {qty:.5f} @ ${price:.2f}")

        # execute SELL
        elif signal == -1 and state["in_position"]:
            actual_qty = math.floor(get_position_qty(self._client, symbol) * 1e5) / 1e5
            if actual_qty > 0.00001:
                place_order(self._client, "sell", symbol, actual_qty)
                pnl = (price - state["entry"]) * actual_qty
                self._log_trade(symbol, "SELL", price, actual_qty, pnl)
                self._emit("bot_trade", {
                    "symbol": symbol, "action": "SELL",
                    "price": price, "qty": actual_qty, "pnl": pnl,
                })
                log.info(f"SELL {symbol} {actual_qty:.5f} @ ${price:.2f} PnL ${pnl:+.2f}")
            state["in_position"] = False
            state["entry"]       = 0.0
            state["peak"]        = 0.0

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
                f"{pnl:.2f}" if pnl is not None else ""
            ])
