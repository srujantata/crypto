"""
Multi-profile simulation engine.
Runs 5 risk profiles in parallel, each with $10,000 virtual capital.
Also supports historical replay for fast backtesting.
"""
import csv
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

log = logging.getLogger("simulator")

# ── Risk profiles ─────────────────────────────────────────────────────────────
PROFILES = {
    "conservative": {"risk": 0.02, "ema_fast": 9,  "ema_slow": 21, "adx_min": 30, "tf": "1h",  "stop": 0.020, "capital": 10_000},
    "moderate":     {"risk": 0.05, "ema_fast": 9,  "ema_slow": 21, "adx_min": 25, "tf": "15m", "stop": 0.025, "capital": 10_000},
    "aggressive":   {"risk": 0.10, "ema_fast": 5,  "ema_slow": 15, "adx_min": 20, "tf": "15m", "stop": 0.030, "capital": 10_000},
    "scalper":      {"risk": 0.03, "ema_fast": 3,  "ema_slow": 8,  "adx_min": 20, "tf": "5m",  "stop": 0.015, "capital": 10_000},
    "swing":        {"risk": 0.08, "ema_fast": 14, "ema_slow": 35, "adx_min": 30, "tf": "4h",  "stop": 0.050, "capital": 10_000},
}

SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "AVAX/USD",
           "LINK/USD", "LTC/USD", "COIN"]

REPLAY_TIMEOUT_SECONDS = 120  # per-profile replay timeout


@dataclass
class Position:
    symbol:      str
    entry_price: float
    qty:         float
    peak_price:  float


@dataclass
class Trade:
    timestamp: str
    profile:   str
    symbol:    str
    action:    str
    price:     float
    qty:       float
    pnl:       float = 0.0


@dataclass
class SimState:
    profile:     str
    capital:     float
    start_cap:   float
    positions:   Dict[str, Position] = field(default_factory=dict)
    trades:      List[Trade]         = field(default_factory=list)
    total_pnl:   float               = 0.0
    win_trades:  int                 = 0
    loss_trades: int                 = 0
    last_update: str                 = ""

    @property
    def portfolio_value(self) -> float:
        return self.capital

    @property
    def total_return_pct(self) -> float:
        return (self.portfolio_value - self.start_cap) / self.start_cap * 100

    @property
    def win_rate(self) -> float:
        total = self.win_trades + self.loss_trades
        return (self.win_trades / total * 100) if total > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "profile":       self.profile,
            "capital":       round(self.capital, 2),
            "start_cap":     self.start_cap,
            "total_pnl":     round(self.total_pnl, 2),
            "return_pct":    round(self.total_return_pct, 2),
            "win_rate":      round(self.win_rate, 1),
            "total_trades":  self.win_trades + self.loss_trades,
            "positions":     {s: {"entry": p.entry_price, "qty": p.qty}
                              for s, p in self.positions.items()},
            "last_update":   self.last_update,
            "recent_trades": [asdict(t) for t in self.trades[-10:]],
        }


class SimulationEngine:
    """Runs one risk profile's strategy in a background thread."""

    def __init__(self, profile_name: str, on_update=None):
        self.name       = profile_name
        self.cfg        = PROFILES[profile_name]
        self.state      = SimState(profile=profile_name,
                                   capital=self.cfg["capital"],
                                   start_cap=self.cfg["capital"])
        self._on_update = on_update
        self._stop      = threading.Event()
        self._thread    = None
        self._log_path  = os.path.join(os.path.dirname(__file__),
                                       f"trades_{profile_name}.csv")

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"Sim-{self.name}")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        intervals = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
        sleep_sec = intervals.get(self.cfg["tf"], 900)
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(sleep_sec)

    def _tick(self):
        from exchange import fetch_ohlcv
        from strategy import generate_signals

        for symbol in SYMBOLS:
            try:
                df = fetch_ohlcv(limit=150, symbol=symbol, timeframe=self.cfg["tf"])
                if df is None or len(df) < 30:
                    continue

                df     = generate_signals(df,
                                          ema_fast=self.cfg["ema_fast"],
                                          ema_slow=self.cfg["ema_slow"],
                                          adx_min=self.cfg["adx_min"])
                last   = df.iloc[-1]
                price  = float(last["close"])
                signal = int(last["signal"])
                in_pos = symbol in self.state.positions

                # trailing stop overrides signal
                if in_pos:
                    pos = self.state.positions[symbol]
                    pos.peak_price = max(pos.peak_price, price)
                    if pos.peak_price > 0:
                        drop = (pos.peak_price - price) / pos.peak_price
                        if drop >= self.cfg["stop"]:
                            self._sell(symbol, price)
                            continue

                if signal == 1 and not in_pos:
                    self._buy(symbol, price)
                elif signal == -1 and in_pos:
                    self._sell(symbol, price)

            except Exception as e:
                log.warning(f"[{self.name}] {symbol} tick error: {e}")

        self.state.last_update = datetime.now().isoformat()
        if self._on_update:
            try:
                self._on_update(self.name, self.state.to_dict())
            except Exception as e:
                log.warning(f"[{self.name}] update callback error: {e}")

    def _buy(self, symbol: str, price: float):
        trade_usd = self.state.capital * self.cfg["risk"]
        qty = trade_usd / price
        if qty < 0.00001 or trade_usd > self.state.capital:
            return
        self.state.capital -= trade_usd
        self.state.positions[symbol] = Position(symbol, price, qty, price)
        t = Trade(datetime.now().isoformat(), self.name, symbol, "BUY", price, qty)
        self.state.trades.append(t)
        self._log_trade(t)

    def _sell(self, symbol: str, price: float):
        if symbol not in self.state.positions:
            return
        pos = self.state.positions.pop(symbol)
        proceeds = pos.qty * price
        pnl = proceeds - (pos.qty * pos.entry_price)
        self.state.capital += proceeds
        self.state.total_pnl += pnl
        if pnl > 0:
            self.state.win_trades += 1
        else:
            self.state.loss_trades += 1
        t = Trade(datetime.now().isoformat(), self.name, symbol, "SELL", price, pos.qty, pnl)
        self.state.trades.append(t)
        self._log_trade(t)

    def _log_trade(self, t: Trade):
        is_new = not os.path.exists(self._log_path)
        with open(self._log_path, "a", newline="") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["timestamp", "profile", "symbol", "action", "price", "qty", "pnl"])
            w.writerow([t.timestamp, t.profile, t.symbol, t.action,
                        f"{t.price:.4f}", f"{t.qty:.6f}", f"{t.pnl:.2f}"])


class SimulationManager:
    """Manages all 5 profiles and broadcasts state to listeners."""

    def __init__(self):
        self._engines: Dict[str, SimulationEngine] = {}
        self._states:  Dict[str, dict] = {}
        self._listeners = []
        self._lock = threading.Lock()

    def add_listener(self, fn):
        self._listeners.append(fn)

    def remove_listener(self, fn):
        self._listeners.remove(fn)

    def start_all(self):
        for name in PROFILES:
            eng = SimulationEngine(name, on_update=self._on_update)
            self._engines[name] = eng
            self._states[name]  = eng.state.to_dict()
            eng.start()

    def stop_all(self):
        for eng in self._engines.values():
            eng.stop()

    def get_states(self) -> dict:
        with self._lock:
            return dict(self._states)

    def _on_update(self, name: str, state: dict):
        with self._lock:
            self._states[name] = state
        for fn in list(self._listeners):
            try:
                fn(name, state)
            except Exception as e:
                log.warning(f"Listener error: {e}")


# ── Historical replay ─────────────────────────────────────────────────────────
def replay_profile(profile_name: str, months: int = 6) -> dict:
    """
    Replay last N months of 1h data through a profile at full speed.
    Uses generate_signals() — same logic as live trading, no divergence.
    """
    from exchange import fetch_ohlcv
    from strategy import generate_signals

    cfg   = PROFILES[profile_name]
    state = SimState(profile=profile_name, capital=cfg["capital"], start_cap=cfg["capital"])
    tf    = "1h"
    limit = months * 30 * 24

    for symbol in SYMBOLS:
        try:
            df = fetch_ohlcv(limit=limit, symbol=symbol, timeframe=tf)
            if df is None or len(df) < 50:
                continue

            df = generate_signals(df,
                                  ema_fast=cfg["ema_fast"],
                                  ema_slow=cfg["ema_slow"],
                                  adx_min=cfg["adx_min"])
            df.dropna(subset=["signal"], inplace=True)

            pos: Optional[Position] = None

            for i in range(len(df)):
                row   = df.iloc[i]
                price = float(row["close"])
                sig   = int(row["signal"])

                if pos and pos.peak_price > 0:
                    pos.peak_price = max(pos.peak_price, price)
                    drop = (pos.peak_price - price) / pos.peak_price
                    if drop >= cfg["stop"]:
                        pnl = (price - pos.entry_price) * pos.qty
                        state.capital    += pos.qty * price
                        state.total_pnl  += pnl
                        state.win_trades  += (1 if pnl > 0 else 0)
                        state.loss_trades += (0 if pnl > 0 else 1)
                        pos = None
                        continue

                if sig == 1 and not pos:
                    trade_usd = state.capital * cfg["risk"]
                    qty = trade_usd / price
                    if qty > 0.00001 and trade_usd <= state.capital:
                        state.capital -= trade_usd
                        pos = Position(symbol, price, qty, price)

                elif sig == -1 and pos:
                    pnl = (price - pos.entry_price) * pos.qty
                    state.capital    += pos.qty * price
                    state.total_pnl  += pnl
                    state.win_trades  += (1 if pnl > 0 else 0)
                    state.loss_trades += (0 if pnl > 0 else 1)
                    pos = None

            if pos:
                last_price = float(df.iloc[-1]["close"])
                pnl = (last_price - pos.entry_price) * pos.qty
                state.capital   += pos.qty * last_price
                state.total_pnl += pnl

        except Exception as e:
            log.warning(f"replay_profile [{profile_name}] {symbol}: {e}")

    return state.to_dict()


def run_all_replays(months: int = 6) -> dict:
    """Run all 5 profiles through historical replay and return comparison."""
    results = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(replay_profile, name, months): name for name in PROFILES}
        for f in futures:
            name = futures[f]
            try:
                results[name] = f.result(timeout=REPLAY_TIMEOUT_SECONDS)
            except FuturesTimeoutError:
                results[name] = {"error": f"Timed out after {REPLAY_TIMEOUT_SECONDS}s"}
            except Exception as e:
                results[name] = {"error": str(e)}
    return results
