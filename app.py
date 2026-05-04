"""
Trading Bot GUI — multi-asset edition with Cloud dashboard
Run: python app.py
"""
import math
import threading
import time
import csv
import os
import json
import asyncio
import logging
import platform as _platform
import sys as _sys
import subprocess as _subprocess
import websockets
from datetime import datetime
from queue import Queue, Empty

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s — %(message)s",
)
log = logging.getLogger("app")

import customtkinter as ctk
import tkinter as _tk
from PIL import Image, ImageDraw

if _sys.platform == "win32":
    import pystray
else:
    pystray = None

from dotenv import load_dotenv

# ── Platform ──────────────────────────────────────────────────────────────────
_OS = _platform.system()   # "Darwin" | "Windows" | "Linux"

FONT_MONO = "Menlo"    if _OS == "Darwin" else ("Consolas"  if _OS == "Windows" else "Ubuntu Mono")
FONT_UI   = "Menlo"    if _OS == "Darwin" else ("Consolas"  if _OS == "Windows" else "Ubuntu Mono")

def _beep(freq: int = 1000):
    if _sys.platform == "win32":
        import winsound
        winsound.Beep(freq, 300)
    else:
        sound = "/System/Library/Sounds/Ping.aiff" if freq >= 1000 else "/System/Library/Sounds/Basso.aiff"
        _subprocess.Popen(["afplay", sound], stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)

def _notify(title: str, message: str):
    """Native OS notification — osascript on Mac, silent elsewhere."""
    if _sys.platform == "darwin":
        _subprocess.Popen(
            ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
            stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL,
        )

# .env resolution
_HERE = os.path.dirname(os.path.abspath(__file__))
for _env_path in [
    os.path.join(_HERE, ".env"),
    os.path.join(os.path.dirname(os.path.abspath(_sys.argv[0])), ".env"),
    os.path.expanduser("~/trading-bot/.env"),
]:
    if os.path.exists(_env_path):
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=True)
        break

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Design tokens — Claude Code aesthetic ─────────────────────────────────────
BG_BASE    = "#0a0a0a"   # near-black base
BG_CARD    = "#111111"   # card / panel
BG_ROW_A   = "#111111"   # table row even
BG_ROW_B   = "#0d0d0d"   # table row odd
BG_HDR     = "#0a0a0a"   # header
BG_INPUT   = "#1a1a1a"   # input fields

ACCENT     = "#da7756"   # Claude coral-orange
GREEN      = "#3fb950"   # muted emerald
RED        = "#f85149"   # rose red
YELLOW     = "#e3b341"   # amber warning
CYAN       = "#58a6ff"   # info blue
PURPLE     = "#bc8cff"   # purple accent

TEXT_PRI   = "#e6edf3"   # primary text
TEXT_SEC   = "#8b949e"   # secondary / muted
BORDER     = "#1e1e1e"   # very subtle border
BORDER_MED = "#2d2d2d"   # medium border for separators


def _make_tray_icon() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 63, 63], fill=BG_CARD)
    d.polygon([(38, 4), (20, 34), (32, 34), (26, 60), (44, 30), (32, 30), (38, 4)], fill=ACCENT)
    return img


_q: Queue = Queue()

_live_cfg: dict = {
    "risk":           float(os.getenv("RISK_PER_TRADE", "0.05")),
    "timeframe":      os.getenv("TIMEFRAME", "15m"),
    "ema_fast":       9,
    "ema_slow":       21,
    "adx_min":        25,
    "rsi_overbought": 70,
    "trailing_stop":  0.025,
    "poll_seconds":   60,
    "kill_switch":    False,
}

def _post(kind: str, **kwargs):
    _q.put({"kind": kind, **kwargs})


# ── helpers ───────────────────────────────────────────────────────────────────
def _log_trade(symbol, action, price, qty, pnl=None):
    path = os.path.join(_HERE, "trades.csv")
    is_new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp", "symbol", "action", "price", "qty", "pnl"])
        w.writerow([datetime.now().isoformat(), symbol, action,
                    f"{price:.4f}", f"{qty:.6f}",
                    f"{pnl:.2f}" if pnl is not None else ""])


# ── bot logic ─────────────────────────────────────────────────────────────────
def _trade_symbol(symbol: str, state: dict, client, bal: dict):
    from exchange import fetch_ohlcv, get_position_qty, place_order, is_crypto, is_market_open
    from strategy import generate_signals, get_higher_tf_trend

    if _live_cfg["kill_switch"]:
        return

    # Skip equities outside NYSE/NASDAQ hours
    if not is_crypto(symbol) and not is_market_open():
        _post("log", msg=f"{symbol} skipped — market closed", color="gray")
        return

    timeframe      = _live_cfg["timeframe"]
    risk           = _live_cfg["risk"]
    ema_fast       = _live_cfg["ema_fast"]
    ema_slow       = _live_cfg["ema_slow"]
    adx_min        = _live_cfg["adx_min"]
    rsi_overbought = _live_cfg["rsi_overbought"]
    trailing_stop  = _live_cfg["trailing_stop"]

    from config import ATR_TRAIL_MULT
    import pandas as _pd

    df = fetch_ohlcv(limit=150, symbol=symbol, timeframe=timeframe)
    if df is None or len(df) < 2:
        return
    df = generate_signals(df, ema_fast=ema_fast, ema_slow=ema_slow,
                          rsi_overbought=rsi_overbought, adx_min=adx_min)
    if len(df) == 0:
        return
    last   = df.iloc[-1]
    price  = float(last["close"])
    rsi    = float(last["rsi"])
    ema_f  = float(last["ema_fast"])
    adx    = float(last["adx"])
    atr    = float(last["atr"]) if "atr" in df.columns and not _pd.isna(last["atr"]) else 0.0
    signal = int(last["signal"])

    # ATR-adaptive trailing stop
    if state["in_position"] and state["peak"] > 0:
        state["peak"] = max(state["peak"], price)
        atr_entry = state.get("atr_entry", 0.0)
        if atr_entry > 0:
            stop_dist      = atr_entry * ATR_TRAIL_MULT
            drop_threshold = stop_dist / state["peak"]
        else:
            drop_threshold = trailing_stop  # fallback to fixed pct
        drop = (state["peak"] - price) / state["peak"]
        if drop >= drop_threshold:
            signal = -1
            _post("log", msg=f"{symbol} TRAILING STOP — {drop*100:.1f}% "
                             f"(threshold {drop_threshold*100:.1f}%) from peak ${state['peak']:,.4f}",
                  color="orange")

    if signal == 1 and not state["in_position"]:
        htf = get_higher_tf_trend(symbol, ema_fast, ema_slow)
        if htf == "bear":
            _post("log", msg=f"{symbol} BUY blocked — 1h trend bearish", color="yellow")
            signal = 0
        elif htf == "bull":
            _post("log", msg=f"{symbol} 1h trend confirmed bullish", color="cyan")

    min_qty   = 0.00001 if is_crypto(symbol) else 0.001
    precision = 1e5     if is_crypto(symbol) else 1e3

    if signal == 1 and not state["in_position"]:
        trade_usd = bal["cash"] * risk
        qty = trade_usd / price
        if qty > min_qty:
            place_order(client, "buy", symbol, qty)
            state["in_position"] = True
            state["entry"]       = price
            state["peak"]        = price
            state["atr_entry"]   = atr   # store ATR at entry for adaptive stop
            _post("log", msg=f"BUY  {symbol}  {qty:.5f} @ ${price:,.4f}  ADX={adx:.1f}  (${trade_usd:,.2f})", color="green")
            _post("trade", symbol=symbol, action="BUY", price=price, pnl=0)
            _log_trade(symbol, "BUY", price, qty)

    elif signal == -1 and state["in_position"]:
        actual_qty = math.floor(get_position_qty(client, symbol) * precision) / precision
        if actual_qty > min_qty:
            place_order(client, "sell", symbol, actual_qty)
            pnl = (price - state["entry"]) * actual_qty
            color = "green" if pnl >= 0 else "red"
            _post("log", msg=f"SELL {symbol}  {actual_qty:.5f} @ ${price:,.4f}  PnL: ${pnl:+,.2f}", color=color)
            _post("trade", symbol=symbol, action="SELL", price=price, pnl=pnl)
            _log_trade(symbol, "SELL", price, actual_qty, pnl)
        state["in_position"] = False
        state["entry"]       = 0.0
        state["peak"]        = 0.0

    _post("tick", symbol=symbol, price=price, rsi=rsi, adx=adx,
          ema_fast=ema_f, signal=signal, in_position=state["in_position"])


def bot_loop(stop_event: threading.Event):
    try:
        from exchange import get_client, get_balance
        from config import SYMBOLS, TIMEFRAME, MODE
    except Exception as e:
        _post("log", msg=f"[ERROR] Import failed: {e}", color="red")
        return

    _post("log", msg=f"Bot started — {MODE.upper()} | {len(SYMBOLS)} pairs | {_live_cfg['timeframe']}", color="cyan")
    _post("log", msg=f"Trading: {', '.join(SYMBOLS)}", color="cyan")

    try:
        client = get_client()
        bal    = get_balance(client)
        _post("balance", cash=bal["cash"], portfolio=bal["portfolio_value"])
        _post("log", msg=f"Connected — Cash: ${bal['cash']:,.2f}  Portfolio: ${bal['portfolio_value']:,.2f}", color="cyan")
    except Exception as e:
        _post("log", msg=f"[ERROR] Connection failed: {e}", color="red")
        return

    states = {s: {"in_position": False, "entry": 0.0, "peak": 0.0} for s in SYMBOLS}
    try:
        from exchange import get_position_qty
        open_positions = client.get_all_positions()
        for pos in open_positions:
            sym_raw = pos.symbol
            matched = next((s for s in SYMBOLS if s.replace("/", "") == sym_raw), None)
            if matched:
                qty       = float(pos.qty)
                avg_entry = float(pos.avg_entry_price)
                cur_price = float(pos.current_price)
                states[matched]["in_position"] = True
                states[matched]["entry"]       = avg_entry
                states[matched]["peak"]        = cur_price
                _post("log", msg=f"Resumed {matched} position — {qty:.5f} @ ${avg_entry:,.4f}", color="yellow")
        if not open_positions:
            _post("log", msg="No existing positions found — starting fresh", color="cyan")
    except Exception as e:
        _post("log", msg=f"[WARN] Could not sync positions: {e}", color="yellow")

    while not stop_event.is_set():
        try:
            bal = get_balance(client)
            _post("balance", cash=bal["cash"], portfolio=bal["portfolio_value"])
            for symbol in SYMBOLS:
                if stop_event.is_set():
                    break
                try:
                    _trade_symbol(symbol, states[symbol], client, bal)
                except Exception as e:
                    _post("log", msg=f"[ERROR] {symbol}: {e}", color="red")
                time.sleep(1)
        except Exception as e:
            _post("log", msg=f"[ERROR] {e}", color="red")
        stop_event.wait(_live_cfg["poll_seconds"])

    _post("log", msg="Bot stopped.", color="yellow")


# ── GUI ───────────────────────────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Crypto Markets")
        self.configure(fg_color=BG_BASE)
        self.resizable(True, True)
        self.minsize(900, 600)

        # window sizing — zoomed on Windows, large default on Mac
        if _OS == "Windows":
            self.state("zoomed")
        else:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            w  = min(sw, 1440)
            h  = min(sh - 80, 900)
            x  = (sw - w) // 2
            y  = 40
            self.geometry(f"{w}x{h}+{x}+{y}")

        self.update_idletasks()
        actual_w = self.winfo_width()
        if actual_w < 100:
            actual_w = 1280
        self._scale = max(0.75, min(actual_w / 1440, 1.6))

        self._stop_event: threading.Event | None = None
        self._bot_thread: threading.Thread | None = None
        self._running    = False
        self._on_top     = False
        self._symbol_rows: dict = {}
        self._tray_icon  = None
        self._daily_pnl  = 0.0

        self._cloud_url    = os.getenv("CLOUD_WS_URL", "")
        self._cloud_token  = os.getenv("CLOUD_TOKEN", "")
        self._cloud_thread: threading.Thread | None = None
        self._cloud_rows:   dict = {}
        self._ws_status    = "disconnected"

        self._entry_prices: dict = {}
        self._last_updated: dict = {}

        self._build_menu()
        self._build_ui()
        if pystray:
            self._setup_tray()
        self._poll_queue()
        self._tick_clock()

        threading.Thread(target=self._price_fetcher, daemon=True).start()
        threading.Thread(target=self._fetch_initial_state, daemon=True).start()

        if self._cloud_url and self._cloud_token:
            self.after(1500, self._connect_cloud)

    # ── native menu bar ───────────────────────────────────────────────────────
    def _build_menu(self):
        menubar = _tk.Menu(self, bg=BG_CARD, fg=TEXT_PRI,
                           activebackground=ACCENT, activeforeground=BG_BASE,
                           relief="flat", bd=0)

        # File / App menu
        file_menu = _tk.Menu(menubar, tearoff=0, bg=BG_CARD, fg=TEXT_PRI,
                             activebackground=ACCENT, activeforeground=BG_BASE)
        file_menu.add_command(label="Preferences…",
                              accelerator="Command-," if _OS == "Darwin" else "Ctrl-,",
                              command=self._open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.on_closing,
                              accelerator="Command-Q" if _OS == "Darwin" else "Alt-F4")
        menubar.add_cascade(label="File", menu=file_menu)

        # Bot menu
        bot_menu = _tk.Menu(menubar, tearoff=0, bg=BG_CARD, fg=TEXT_PRI,
                            activebackground=ACCENT, activeforeground=BG_BASE)
        bot_menu.add_command(label="Toggle Kill Switch",
                             accelerator="Command-K" if _OS == "Darwin" else "Ctrl-K",
                             command=self._toggle_kill_switch)
        bot_menu.add_separator()
        bot_menu.add_command(label="Emergency Sell All…", command=self._emergency_sell)
        menubar.add_cascade(label="Bot", menu=bot_menu)

        # View menu
        view_menu = _tk.Menu(menubar, tearoff=0, bg=BG_CARD, fg=TEXT_PRI,
                             activebackground=ACCENT, activeforeground=BG_BASE)
        view_menu.add_command(label="Pin on Top",
                              accelerator="Command-T" if _OS == "Darwin" else "Ctrl-T",
                              command=self._toggle_ontop)
        view_menu.add_separator()
        view_menu.add_command(label="Cloud Trades CSV", command=self._open_cloud_trades)
        view_menu.add_command(label="Local Trades CSV",  command=self._open_csv)
        menubar.add_cascade(label="View", menu=view_menu)

        self.config(menu=menubar)

        mod = "Command" if _OS == "Darwin" else "Control"
        self.bind(f"<{mod}-comma>", lambda e: self._open_settings())
        self.bind(f"<{mod}-k>",     lambda e: self._toggle_kill_switch())
        self.bind(f"<{mod}-t>",     lambda e: self._toggle_ontop())
        self.bind(f"<{mod}-q>",     lambda e: self.on_closing())

    # ── live clock ────────────────────────────────────────────────────────────
    def _tick_clock(self):
        if hasattr(self, "_lbl_clock"):
            self._lbl_clock.configure(text=datetime.now().strftime("%H:%M:%S"))
        self.after(1000, self._tick_clock)

    # ── main UI ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        from config import SYMBOLS
        s = self._scale

        def F(size, bold=False):
            return (FONT_UI, int(size * s), "bold") if bold else (FONT_UI, int(size * s))

        # ── Header bar ────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=BG_HDR, corner_radius=0, height=int(52 * s))
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        # left: wordmark
        left = ctk.CTkFrame(hdr, fg_color="transparent")
        left.pack(side="left", padx=int(24 * s), pady=0, fill="y")
        ctk.CTkLabel(left, text="◆ Crypto Markets",
                     font=F(15, bold=True), text_color=TEXT_PRI).pack(side="left", pady=0)
        ctk.CTkLabel(left, text="  paper trading",
                     font=F(11), text_color=TEXT_SEC).pack(side="left", pady=0)

        # center: clock
        center = ctk.CTkFrame(hdr, fg_color="transparent")
        center.place(relx=0.5, rely=0.5, anchor="center")
        self._lbl_clock = ctk.CTkLabel(center, text="--:--:--",
                                        font=F(13), text_color=TEXT_SEC)
        self._lbl_clock.pack()

        # right: P&L + status
        right = ctk.CTkFrame(hdr, fg_color="transparent")
        right.pack(side="right", padx=int(24 * s), fill="y")
        self._status_dot = ctk.CTkLabel(right, text="● cloud active",
                                         font=F(11), text_color=GREEN)
        self._status_dot.pack(side="right", padx=(12, 0))
        self._lbl_pnl = ctk.CTkLabel(right, text="p&l  $0.00",
                                      font=F(11), text_color=TEXT_SEC)
        self._lbl_pnl.pack(side="right")

        # thin accent line under header
        ctk.CTkFrame(self, fg_color=BORDER_MED, height=1, corner_radius=0).pack(fill="x")

        # ── Tab view ──────────────────────────────────────────────────────────
        tabs = ctk.CTkTabview(
            self,
            fg_color=BG_BASE,
            segmented_button_fg_color=BG_BASE,
            segmented_button_selected_color=BG_CARD,
            segmented_button_selected_hover_color="#1e1e1e",
            segmented_button_unselected_color=BG_BASE,
            segmented_button_unselected_hover_color="#141414",
            text_color=TEXT_SEC,
            text_color_disabled=TEXT_SEC,
            border_color=BORDER_MED,
            border_width=1,
        )
        tabs.pack(fill="both", expand=True, padx=0, pady=0)
        tab_local = tabs.add("  live bot  ")
        tab_cloud = tabs.add("  cloud sims  ")
        self._build_cloud_tab(tab_cloud)

        # ── Portfolio strip ───────────────────────────────────────────────────
        port = ctk.CTkFrame(tab_local, fg_color=BG_CARD,
                             corner_radius=6, border_width=1, border_color=BORDER_MED)
        port.pack(fill="x", padx=int(16 * s), pady=(int(12 * s), 6))
        port.columnconfigure((0, 1, 2), weight=1)
        self._lbl_cash      = self._stat(port, "cash",      "$—", 0)
        self._lbl_portfolio = self._stat(port, "portfolio", "$—", 1)
        self._stat_mode     = self._stat(port, "mode",   "paper", 2)

        # ── Market table ──────────────────────────────────────────────────────
        COL_W = [1, 2, 1, 1, 1, 2, 2, 2]
        COL_H = ["symbol", "price", "rsi", "adx", "signal", "status", "last trade", "unreal p&l"]

        tbl = ctk.CTkFrame(tab_local, fg_color=BG_CARD,
                            corner_radius=6, border_width=1, border_color=BORDER_MED)
        tbl.pack(fill="x", padx=int(16 * s), pady=(0, 6))
        for ci, wt in enumerate(COL_W):
            tbl.columnconfigure(ci, weight=wt, uniform="col")

        # column headers
        for ci, txt in enumerate(COL_H):
            ctk.CTkLabel(tbl, text=txt, font=F(9, bold=True),
                         text_color=TEXT_SEC, anchor="center"
                         ).grid(row=0, column=ci, padx=8, pady=(10, 4), sticky="ew")

        # header separator
        ctk.CTkFrame(tbl, height=1, fg_color=BORDER_MED, corner_radius=0
                     ).grid(row=1, column=0, columnspan=len(COL_H), sticky="ew", padx=0)

        # data rows
        for i, sym in enumerate(SYMBOLS):
            ri   = i + 2
            bg   = BG_ROW_A if i % 2 == 0 else BG_ROW_B
            pad  = (int(7 * s), int(7 * s))
            base = sym.split("/")[0] if "/" in sym else sym

            ctk.CTkFrame(tbl, fg_color=bg, corner_radius=0, height=int(40 * s)
                         ).grid(row=ri, column=0, columnspan=len(COL_H),
                                sticky="ew", padx=0, pady=0)

            lbl_sym   = ctk.CTkLabel(tbl, text=base,   font=F(12, bold=True),
                                      text_color=ACCENT, anchor="center", fg_color=bg)
            lbl_price = ctk.CTkLabel(tbl, text="—",    font=F(12),
                                      text_color=TEXT_PRI, anchor="center", fg_color=bg)
            lbl_rsi   = ctk.CTkLabel(tbl, text="—",    font=F(11),
                                      text_color=TEXT_SEC, anchor="center", fg_color=bg)
            lbl_adx   = ctk.CTkLabel(tbl, text="—",    font=F(11),
                                      text_color=TEXT_SEC, anchor="center", fg_color=bg)
            lbl_sig   = ctk.CTkLabel(tbl, text="idle", font=F(11, bold=True),
                                      text_color=TEXT_SEC, anchor="center", fg_color=bg)
            lbl_stat  = ctk.CTkLabel(tbl, text="flat", font=F(10),
                                      text_color=TEXT_SEC, anchor="center", fg_color=bg)
            lbl_last  = ctk.CTkLabel(tbl, text="—",    font=F(10),
                                      text_color=TEXT_SEC, anchor="center", fg_color=bg)
            lbl_upnl  = ctk.CTkLabel(tbl, text="—",    font=F(11),
                                      text_color=TEXT_SEC, anchor="center", fg_color=bg)

            for ci, lbl in enumerate([lbl_sym, lbl_price, lbl_rsi, lbl_adx,
                                       lbl_sig, lbl_stat, lbl_last, lbl_upnl]):
                lbl.grid(row=ri, column=ci, padx=8, pady=pad, sticky="ew")

            self._symbol_rows[sym] = {
                "price": lbl_price, "rsi": lbl_rsi, "adx": lbl_adx,
                "signal": lbl_sig, "status": lbl_stat,
                "last": lbl_last, "upnl": lbl_upnl,
            }

        # ── Info banner ───────────────────────────────────────────────────────
        info = ctk.CTkFrame(tab_local, fg_color="#120e0a", corner_radius=6,
                             border_width=1, border_color="#2a1e10")
        info.pack(fill="x", padx=int(16 * s), pady=(int(6 * s), 4))
        ctk.CTkLabel(info,
                     text="  ▸  cloud bot is trading 24/7 on railway  ·  this tab shows live prices",
                     font=F(10), text_color=ACCENT).pack(side="left", padx=10, pady=5)

        # ── Action bar ────────────────────────────────────────────────────────
        bar = ctk.CTkFrame(tab_local, fg_color="transparent")
        bar.pack(fill="x", padx=int(16 * s), pady=(4, 6))

        def _btn(parent, text, cmd, fg, hover, width=None, side="left"):
            b = ctk.CTkButton(
                parent, text=text, command=cmd,
                font=F(11), fg_color=fg, hover_color=hover,
                text_color=TEXT_PRI, corner_radius=5,
                border_width=1, border_color=BORDER_MED,
                width=width or int(150 * s), height=int(30 * s),
            )
            b.pack(side=side, padx=(0, 6))
            return b

        self._btn_start = _btn(bar, "cloud trades csv", self._open_cloud_trades, BG_CARD, "#1a1a1a")
        self._btn_stop  = _btn(bar, "emergency sell all", self._emergency_sell,
                               "#1a0505", "#2a0808", width=int(170 * s))
        self._btn_kill  = _btn(bar, "⏸  kill switch", self._toggle_kill_switch,
                               "#1a0f00", "#2a1800", width=int(130 * s))
        self._btn_ontop = _btn(bar, "pin", self._toggle_ontop,
                               BG_CARD, "#1a1a1a", width=int(60 * s))
        _btn(bar, "settings", self._open_settings, BG_CARD, "#1a1a1a", width=int(90 * s))
        _btn(bar, "trades csv", self._open_csv, BG_CARD, "#1a1a1a",
             width=int(110 * s), side="right")

        # ── Activity log ──────────────────────────────────────────────────────
        log_wrap = ctk.CTkFrame(tab_local, fg_color=BG_CARD,
                                corner_radius=6, border_width=1, border_color=BORDER_MED)
        log_wrap.pack(fill="both", expand=True, padx=int(16 * s), pady=(0, int(14 * s)))

        log_hdr = ctk.CTkFrame(log_wrap, fg_color="transparent")
        log_hdr.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(log_hdr, text="activity log",
                     font=F(9, bold=True), text_color=TEXT_SEC).pack(side="left")

        ctk.CTkFrame(log_wrap, fg_color=BORDER, height=1, corner_radius=0
                     ).pack(fill="x", padx=0, pady=(6, 0))

        self._log_box = ctk.CTkTextbox(
            log_wrap, font=(FONT_MONO, int(11 * s)),
            fg_color=BG_BASE, text_color=TEXT_SEC,
            state="disabled", wrap="word",
        )
        self._log_box.pack(fill="both", expand=True, padx=0, pady=0)
        for tag, color in [
            ("green",  GREEN),  ("red",    RED),    ("cyan",   CYAN),
            ("yellow", YELLOW), ("orange", ACCENT),  ("white",  TEXT_PRI),
        ]:
            self._log_box._textbox.tag_config(tag, foreground=color)

    # ── cloud tab ─────────────────────────────────────────────────────────────
    def _build_cloud_tab(self, parent):
        s = self._scale

        def F(size, bold=False):
            return (FONT_UI, int(size * s), "bold") if bold else (FONT_UI, int(size * s))

        PROFILE_COLORS = {
            "conservative": CYAN,
            "moderate":     GREEN,
            "aggressive":   ACCENT,
            "scalper":      YELLOW,
            "swing":        PURPLE,
        }
        PROFILES_ORDER = ["conservative", "moderate", "aggressive", "scalper", "swing"]

        # connection bar
        conn = ctk.CTkFrame(parent, fg_color=BG_CARD,
                             corner_radius=6, border_width=1, border_color=BORDER_MED)
        conn.pack(fill="x", padx=int(16 * s), pady=(int(12 * s), 4))
        ctk.CTkLabel(conn, text="ws", font=F(10, bold=True),
                     text_color=TEXT_SEC).pack(side="left", padx=12, pady=8)
        self._lbl_ws_url = ctk.CTkLabel(
            conn,
            text=self._cloud_url or "not configured — set CLOUD_WS_URL in .env",
            font=F(10), text_color=TEXT_SEC,
        )
        self._lbl_ws_url.pack(side="left")
        ctk.CTkButton(conn, text="connect", width=int(80 * s), height=int(26 * s),
                       fg_color=BG_BASE, hover_color="#1a1a1a",
                       border_width=1, border_color=BORDER_MED,
                       font=F(10), text_color=TEXT_PRI,
                       corner_radius=4, command=self._connect_cloud
                       ).pack(side="right", padx=8, pady=5)
        self._lbl_ws_status = ctk.CTkLabel(conn, text="● disconnected",
                                            font=F(10), text_color=RED)
        self._lbl_ws_status.pack(side="right", padx=8)

        # replay bar
        replay_bar = ctk.CTkFrame(parent, fg_color="transparent")
        replay_bar.pack(fill="x", padx=int(16 * s), pady=(0, 8))
        ctk.CTkButton(replay_bar, text="▸  run 6-month replay  (all profiles)",
                       font=F(11), height=int(30 * s),
                       fg_color=BG_CARD, hover_color="#1a1a1a",
                       border_width=1, border_color=BORDER_MED,
                       text_color=TEXT_PRI, corner_radius=4,
                       command=self._run_replay).pack(side="left")
        self._lbl_replay = ctk.CTkLabel(replay_bar, text="",
                                         font=F(10), text_color=TEXT_SEC)
        self._lbl_replay.pack(side="left", padx=12)

        # profile cards
        cards = ctk.CTkFrame(parent, fg_color="transparent")
        cards.pack(fill="x", padx=int(16 * s), pady=4)
        for ci in range(len(PROFILES_ORDER)):
            cards.columnconfigure(ci, weight=1)

        self._cloud_rows = {}
        for ci, name in enumerate(PROFILES_ORDER):
            color = PROFILE_COLORS[name]
            card  = ctk.CTkFrame(cards, fg_color=BG_CARD,
                                  corner_radius=6, border_width=1, border_color=BORDER_MED)
            card.grid(row=0, column=ci, padx=5, pady=2, sticky="nsew")
            card.columnconfigure(0, weight=1)

            # accent top bar
            ctk.CTkFrame(card, fg_color=color, height=2, corner_radius=0
                          ).grid(row=0, column=0, sticky="ew")

            ctk.CTkLabel(card, text=name, font=F(9, bold=True),
                         text_color=color).grid(row=1, column=0, pady=(8, 0), padx=12, sticky="w")

            lbl_ret = ctk.CTkLabel(card, text="—",
                                    font=(FONT_MONO, int(22 * s), "bold"),
                                    text_color=TEXT_PRI)
            lbl_ret.grid(row=2, column=0, pady=(2, 0), padx=12, sticky="w")

            lbl_trades = ctk.CTkLabel(card, text="0 trades",
                                       font=F(9), text_color=TEXT_SEC)
            lbl_trades.grid(row=3, column=0, padx=12, sticky="w")

            lbl_wr = ctk.CTkLabel(card, text="win rate  —",
                                   font=F(9), text_color=TEXT_SEC)
            lbl_wr.grid(row=4, column=0, padx=12, pady=(0, 8), sticky="w")

            # mini return bar
            bar_bg = ctk.CTkFrame(card, fg_color=BG_BASE, height=3, corner_radius=2)
            bar_bg.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 8))
            bar_fill = ctk.CTkFrame(bar_bg, fg_color=color, height=3, corner_radius=2, width=0)
            bar_fill.place(x=0, y=0, relheight=1.0)

            self._cloud_rows[name] = {
                "return": lbl_ret, "trades": lbl_trades,
                "winrate": lbl_wr, "bar": bar_fill, "bar_bg": bar_bg,
            }

        # cloud activity log
        log_wrap = ctk.CTkFrame(parent, fg_color=BG_CARD,
                                corner_radius=6, border_width=1, border_color=BORDER_MED)
        log_wrap.pack(fill="both", expand=True,
                      padx=int(16 * s), pady=(8, int(14 * s)))
        ctk.CTkLabel(log_wrap, text="cloud activity",
                     font=(FONT_UI, int(9 * s), "bold"),
                     text_color=TEXT_SEC).pack(anchor="w", padx=12, pady=(8, 0))
        ctk.CTkFrame(log_wrap, fg_color=BORDER, height=1, corner_radius=0
                     ).pack(fill="x", pady=(4, 0))
        self._cloud_log = ctk.CTkTextbox(
            log_wrap, font=(FONT_MONO, int(11 * s)),
            fg_color=BG_BASE, text_color=TEXT_SEC,
            state="disabled", wrap="word",
        )
        self._cloud_log.pack(fill="both", expand=True, padx=0, pady=0)
        for tag, c in [("green", GREEN), ("red", RED), ("cyan", CYAN),
                        ("yellow", YELLOW), ("white", TEXT_PRI)]:
            self._cloud_log._textbox.tag_config(tag, foreground=c)

    # ── stat widget ───────────────────────────────────────────────────────────
    def _stat(self, parent, label, value, col):
        s = self._scale
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=0, column=col, padx=int(20 * s), pady=int(12 * s), sticky="ew")
        ctk.CTkLabel(f, text=label,
                     font=(FONT_UI, int(9 * s), "bold"), text_color=TEXT_SEC).pack(anchor="w")
        lbl = ctk.CTkLabel(f, text=value,
                            font=(FONT_MONO, int(22 * s), "bold"), text_color=TEXT_PRI)
        lbl.pack(anchor="w")
        return lbl

    # ── cloud card update ─────────────────────────────────────────────────────
    def _update_cloud_card(self, name: str, data: dict):
        row = self._cloud_rows.get(name)
        if not row:
            return
        ret = data.get("return_pct", 0)
        col = GREEN if ret >= 0 else RED
        row["return"].configure(text=f"{ret:+.2f}%", text_color=col)
        row["trades"].configure(text=f"{data.get('total_trades', 0)} trades")
        row["winrate"].configure(text=f"win rate  {data.get('win_rate', 0):.1f}%")

        # return bar fill (cap at 100%, min 0)
        pct = max(0.0, min(abs(ret) / 20.0, 1.0))   # 20% = full bar
        bg = row["bar_bg"]
        bg.update_idletasks()
        w = bg.winfo_width()
        row["bar"].configure(fg_color=col, width=max(1, int(w * pct)))

        for t in data.get("recent_trades", [])[-3:]:
            ts  = t.get("timestamp", "")[:19]
            sym = t.get("symbol", "")
            act = t.get("action", "")
            pnl = float(t.get("pnl", 0))
            c   = "green" if act == "BUY" or pnl >= 0 else "red"
            msg = f"[{ts}] [{name[:4]}] {act} {sym}  pnl ${pnl:+.2f}\n"
            self._cloud_log.configure(state="normal")
            self._cloud_log._textbox.insert("end", msg, c)
            self._cloud_log._textbox.see("end")
            self._cloud_log.configure(state="disabled")

    # ── WebSocket cloud connection ─────────────────────────────────────────────
    def _connect_cloud(self):
        if not self._cloud_url or not self._cloud_token:
            self._lbl_ws_status.configure(text="● no config", text_color=YELLOW)
            return
        if self._cloud_thread and self._cloud_thread.is_alive():
            return
        self._cloud_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._cloud_thread.start()

    def _ws_loop(self):
        async def _run():
            url = f"{self._cloud_url}?token={self._cloud_token}"
            while True:
                try:
                    self.after(0, lambda: self._lbl_ws_status.configure(
                        text="● connecting…", text_color=YELLOW))
                    async with websockets.connect(url, ping_interval=20) as ws:
                        self.after(0, lambda: self._lbl_ws_status.configure(
                            text="● live", text_color=GREEN))
                        async for raw in ws:
                            msg = json.loads(raw)
                            t   = msg.get("type", "")
                            if t == "init":
                                for name, data in msg["data"].items():
                                    self.after(0, self._update_cloud_card, name, data)
                            elif t == "sim_update":
                                self.after(0, self._update_cloud_card, msg["profile"], msg["data"])
                            elif t == "bot_tick":
                                self.after(0, self._on_cloud_tick, msg)
                            elif t == "bot_balance":
                                self.after(0, self._on_cloud_balance, msg)
                            elif t == "bot_trade":
                                self.after(0, self._on_cloud_trade, msg)
                            elif t == "bot_connected":
                                self.after(0, self._on_cloud_balance, msg)
                            elif t == "bot_log":
                                self.after(0, self._append_log,
                                           msg.get("msg", ""), msg.get("color", "white"))
                except Exception:
                    self.after(0, lambda: self._lbl_ws_status.configure(
                        text="● reconnecting…", text_color=YELLOW))
                    await asyncio.sleep(5)
        asyncio.run(_run())

    def _on_cloud_tick(self, msg: dict):
        sym = msg.get("symbol", "")
        _q.put({"kind": "tick", "symbol": sym,
                "price": msg.get("price", 0), "rsi": msg.get("rsi", 0),
                "adx": msg.get("adx", 0), "ema_fast": 0,
                "signal": msg.get("signal", 0),
                "in_position": msg.get("in_position", False)})

    def _on_cloud_balance(self, msg: dict):
        _q.put({"kind": "balance",
                "cash": msg.get("cash", 0),
                "portfolio": msg.get("portfolio", msg.get("portfolio_value", 0))})

    def _on_cloud_trade(self, msg: dict):
        sym    = msg.get("symbol", "")
        action = msg.get("action", "")
        price  = msg.get("price", 0)
        pnl    = msg.get("pnl", 0)
        _q.put({"kind": "trade", "symbol": sym, "action": action, "price": price, "pnl": pnl})
        color = "green" if action == "BUY" or pnl >= 0 else "red"
        _q.put({"kind": "log",
                "msg": f"cloud {action} {sym} @ ${price:,.4f}  pnl ${pnl:+.2f}",
                "color": color})

    def _run_replay(self):
        self._lbl_replay.configure(text="running…", text_color=YELLOW)
        def _do():
            from simulator import run_all_replays
            results = run_all_replays(months=6)
            def _show():
                best = max(results, key=lambda k: results[k].get("return_pct", -999))
                self._lbl_replay.configure(
                    text=f"done  ·  best: {best} ({results[best].get('return_pct',0):+.2f}%)",
                    text_color=GREEN)
                for name, data in results.items():
                    self._update_cloud_card(name, data)
            self.after(0, _show)
        threading.Thread(target=_do, daemon=True).start()

    # ── queue consumer ────────────────────────────────────────────────────────
    def _poll_queue(self):
        try:
            while True:
                msg  = _q.get_nowait()
                kind = msg["kind"]

                if kind == "tick":
                    sym   = msg["symbol"]
                    price = msg["price"]
                    row   = self._symbol_rows.get(sym)
                    self._last_updated[sym] = datetime.now().strftime("%H:%M:%S")
                    if row:
                        row["price"].configure(text=f"${price:,.4f}", text_color=TEXT_PRI)
                        rsi = msg["rsi"]
                        rsi_col = RED if rsi > 70 else (YELLOW if rsi < 30 else TEXT_SEC)
                        row["rsi"].configure(text=f"{rsi:.1f}", text_color=rsi_col)
                        adx = msg["adx"]
                        row["adx"].configure(text=f"{adx:.1f}",
                                             text_color=GREEN if adx > 25 else RED)
                        sig = msg["signal"]
                        sig_txt = {1: "buy", -1: "sell", 0: "hold"}.get(sig, "—")
                        sig_col = {1: GREEN, -1: RED, 0: TEXT_SEC}.get(sig, TEXT_SEC)
                        row["signal"].configure(text=sig_txt, text_color=sig_col)
                        in_pos = msg["in_position"]
                        row["status"].configure(
                            text="● in position" if in_pos else "flat",
                            text_color=GREEN if in_pos else TEXT_SEC,
                        )
                        entry = self._entry_prices.get(sym, 0)
                        if in_pos and entry > 0:
                            pct = (price - entry) / entry * 100
                            row["upnl"].configure(
                                text=f"{pct:+.2f}%",
                                text_color=GREEN if pct >= 0 else RED,
                            )
                        else:
                            row["upnl"].configure(text="—", text_color=TEXT_SEC)

                elif kind == "balance":
                    self._lbl_cash.configure(text=f"${msg['cash']:,.2f}")
                    self._lbl_portfolio.configure(text=f"${msg['portfolio']:,.2f}")

                elif kind == "trade":
                    sym    = msg["symbol"]
                    action = msg["action"]
                    price  = msg["price"]
                    pnl    = msg.get("pnl", 0)
                    if action == "BUY":
                        self._entry_prices[sym] = price
                    else:
                        self._entry_prices.pop(sym, None)
                    self._daily_pnl += pnl
                    pnl_col = GREEN if self._daily_pnl >= 0 else RED
                    self._lbl_pnl.configure(
                        text=f"p&l  ${self._daily_pnl:+,.2f}", text_color=pnl_col)
                    row = self._symbol_rows.get(sym)
                    if row:
                        row["last"].configure(
                            text=f"{action.lower()} ${price:,.2f}",
                            text_color=GREEN if action == "BUY" else RED,
                        )
                    threading.Thread(
                        target=lambda f=1200 if action == "BUY" else 600: _beep(f),
                        daemon=True,
                    ).start()
                    _notify("Crypto Markets",
                            f"{action} {sym} @ ${price:,.2f}  pnl ${pnl:+.2f}")

                elif kind == "log":
                    self._append_log(msg["msg"], msg.get("color", "white"))

        except Empty:
            pass
        self.after(500, self._poll_queue)

    def _append_log(self, text: str, color: str = "white"):
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"  {ts}  {text}\n"
        self._log_box.configure(state="normal")
        self._log_box._textbox.insert("end", line, color)
        self._log_box._textbox.see("end")
        self._log_box.configure(state="disabled")

    # ── data fetchers ─────────────────────────────────────────────────────────
    def _fetch_initial_state(self):
        try:
            from exchange import get_client, get_balance
            from config import SYMBOLS
            client = get_client()
            bal    = get_balance(client)
            _q.put({"kind": "balance", "cash": bal["cash"], "portfolio": bal["portfolio_value"]})
            for pos in client.get_all_positions():
                matched = next((s for s in SYMBOLS if s.replace("/", "") == pos.symbol), None)
                if matched:
                    self._entry_prices[matched] = float(pos.avg_entry_price)
            _q.put({"kind": "log",
                    "msg": f"alpaca synced — cash ${bal['cash']:,.2f}  portfolio ${bal['portfolio_value']:,.2f}",
                    "color": "cyan"})
        except Exception as e:
            _q.put({"kind": "log", "msg": f"balance sync: {e}", "color": "red"})

    def _price_fetcher(self):
        while True:
            try:
                from exchange import fetch_ohlcv
                from strategy import generate_signals
                from config import SYMBOLS
                for sym in SYMBOLS:
                    try:
                        df = fetch_ohlcv(limit=60, symbol=sym, timeframe=_live_cfg["timeframe"])
                        if df is None or len(df) < 2:
                            continue
                        df = generate_signals(df,
                                              ema_fast=_live_cfg["ema_fast"],
                                              ema_slow=_live_cfg["ema_slow"],
                                              rsi_overbought=_live_cfg["rsi_overbought"],
                                              adx_min=_live_cfg["adx_min"])
                        row    = df.iloc[-1]
                        in_pos = sym in self._entry_prices
                        _q.put({"kind": "tick", "symbol": sym,
                                "price":      float(row["close"]),
                                "rsi":        float(row["rsi"]),
                                "adx":        float(row["adx"]),
                                "ema_fast":   float(row["ema_fast"]),
                                "signal":     int(row["signal"]),
                                "in_position": in_pos})
                        time.sleep(0.5)
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(60)

    # ── file actions ──────────────────────────────────────────────────────────
    def _open_cloud_trades(self):
        path = os.path.join(_HERE, "cloud_trades.csv")
        if os.path.exists(path):
            if _sys.platform == "win32":
                os.startfile(path)
            elif _sys.platform == "darwin":
                _subprocess.Popen(["open", path])
            else:
                _subprocess.Popen(["xdg-open", path])
        else:
            self._append_log("no cloud trades yet — bot is watching for signals", "yellow")

    def _open_csv(self):
        path = os.path.join(_HERE, "trades.csv")
        if os.path.exists(path):
            if _sys.platform == "win32":
                os.startfile(path)
            elif _sys.platform == "darwin":
                _subprocess.Popen(["open", path])
            else:
                _subprocess.Popen(["xdg-open", path])
        else:
            self._append_log("no trades logged yet", "yellow")

    # ── emergency sell ────────────────────────────────────────────────────────
    def _emergency_sell(self):
        from tkinter import messagebox
        if not messagebox.askyesno("emergency sell",
                                   "sell ALL open positions immediately?\nthis cannot be undone."):
            return
        def _do():
            try:
                from exchange import get_client, get_position_qty, place_order
                from config import SYMBOLS
                client    = get_client()
                positions = client.get_all_positions()
                if not positions:
                    self._append_log("no open positions to sell", "yellow")
                    return
                for pos in positions:
                    matched = next((s for s in SYMBOLS if s.replace("/", "") == pos.symbol), None)
                    if matched:
                        qty = math.floor(float(pos.qty) * 1e5) / 1e5
                        place_order(client, "sell", matched, qty)
                        self._append_log(f"emergency sell {matched} {qty:.5f}", "red")
            except Exception as e:
                self._append_log(f"emergency sell error: {e}", "red")
        threading.Thread(target=_do, daemon=True).start()

    # ── settings modal ────────────────────────────────────────────────────────
    def _open_settings(self):
        s   = self._scale
        win = ctk.CTkToplevel(self)
        win.title("preferences")
        win.geometry(f"{int(460*s)}x{int(560*s)}")
        win.resizable(False, False)
        win.configure(fg_color=BG_BASE)
        win.grab_set()

        def F(size, bold=False):
            return (FONT_UI, int(size * s), "bold") if bold else (FONT_UI, int(size * s))

        ctk.CTkLabel(win, text="preferences",
                     font=F(14, bold=True), text_color=TEXT_PRI).pack(pady=(20, 4), padx=24, anchor="w")
        ctk.CTkLabel(win, text="all changes apply immediately — no restart needed",
                     font=F(10), text_color=TEXT_SEC).pack(padx=24, anchor="w")
        ctk.CTkFrame(win, fg_color=BORDER_MED, height=1, corner_radius=0
                     ).pack(fill="x", pady=(12, 4))

        def row(label, value):
            f = ctk.CTkFrame(win, fg_color="transparent")
            f.pack(fill="x", padx=24, pady=4)
            ctk.CTkLabel(f, text=label, anchor="w", font=F(11),
                         text_color=TEXT_SEC, width=int(220 * s)).pack(side="left")
            e = ctk.CTkEntry(f, width=int(140 * s), font=(FONT_MONO, int(11 * s)),
                              fg_color=BG_INPUT, border_color=BORDER_MED,
                              text_color=TEXT_PRI, border_width=1)
            e.insert(0, str(value))
            e.pack(side="right")
            return e

        e_risk   = row("risk per trade (%)",         int(_live_cfg["risk"] * 100))
        e_tf     = row("timeframe  (1m / 5m / 15m / 1h)", _live_cfg["timeframe"])
        e_ema_f  = row("ema fast",                   _live_cfg["ema_fast"])
        e_ema_s  = row("ema slow",                   _live_cfg["ema_slow"])
        e_adx    = row("min adx  (trend threshold)", _live_cfg["adx_min"])
        e_rsi_ob = row("rsi overbought threshold",   _live_cfg["rsi_overbought"])
        e_trail  = row("trailing stop (%)",           int(_live_cfg["trailing_stop"] * 100))
        e_poll   = row("poll interval  (seconds)",    _live_cfg["poll_seconds"])

        ctk.CTkFrame(win, fg_color=BORDER_MED, height=1, corner_radius=0
                     ).pack(fill="x", pady=(8, 0))

        def save():
            import re
            try:
                risk   = float(e_risk.get()) / 100
                tf     = e_tf.get().strip()
                ema_f  = int(e_ema_f.get())
                ema_s  = int(e_ema_s.get())
                adx    = int(e_adx.get())
                rsi_ob = int(e_rsi_ob.get())
                trail  = float(e_trail.get()) / 100
                poll   = int(e_poll.get())
            except ValueError as exc:
                self._append_log(f"settings error: {exc}", "red")
                return
            _live_cfg.update({"risk": risk, "timeframe": tf, "ema_fast": ema_f,
                               "ema_slow": ema_s, "adx_min": adx, "rsi_overbought": rsi_ob,
                               "trailing_stop": trail, "poll_seconds": poll})
            env_path = os.path.join(_HERE, ".env")
            try:
                with open(env_path, "r") as f:
                    content = f.read()
                content = re.sub(r"RISK_PER_TRADE=.*", f"RISK_PER_TRADE={risk}", content)
                content = re.sub(r"TIMEFRAME=.*",      f"TIMEFRAME={tf}",        content)
                with open(env_path, "w") as f:
                    f.write(content)
            except Exception as exc:
                self._append_log(f"could not save .env: {exc}", "yellow")
            self._append_log(
                f"settings applied — risk {risk*100:.0f}%  tf {tf}  "
                f"ema {ema_f}/{ema_s}  adx>{adx}  rsi<{rsi_ob}  trail {trail*100:.1f}%  poll {poll}s",
                "green")
            win.destroy()

        ctk.CTkButton(win, text="apply", command=save,
                       font=F(12, bold=True), height=int(34 * s),
                       fg_color=ACCENT, hover_color="#c4674a",
                       text_color=BG_BASE, corner_radius=4).pack(pady=16, padx=24, fill="x")

    # ── controls ──────────────────────────────────────────────────────────────
    def _start(self):
        if self._running:
            return
        self._running    = True
        self._stop_event = threading.Event()
        self._bot_thread = threading.Thread(target=bot_loop, args=(self._stop_event,), daemon=True)
        self._bot_thread.start()
        self._status_dot.configure(text="● running", text_color=GREEN)
        self._btn_start.configure(state="disabled")
        self._btn_stop.configure(state="normal")

    def _stop(self):
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        self._status_dot.configure(text="● stopped", text_color=RED)
        self._btn_start.configure(state="normal")
        self._btn_stop.configure(state="disabled")

    def _toggle_kill_switch(self):
        _live_cfg["kill_switch"] = not _live_cfg["kill_switch"]
        active = _live_cfg["kill_switch"]
        self._btn_kill.configure(
            text="▸  resume" if active else "⏸  kill switch",
            fg_color="#2a0505" if active else "#1a0f00",
            hover_color="#3a0808" if active else "#2a1800",
        )
        self._append_log(
            "kill switch ON — new signals paused (open positions held)" if active
            else "kill switch OFF — trading resumed",
            "red" if active else "green",
        )

    def _toggle_ontop(self):
        self._on_top = not self._on_top
        self.wm_attributes("-topmost", self._on_top)
        self._btn_ontop.configure(
            text="pinned" if self._on_top else "pin",
            fg_color="#0a1a2a" if self._on_top else BG_CARD,
            text_color=CYAN if self._on_top else TEXT_PRI,
        )

    # ── system tray (Windows only) ────────────────────────────────────────────
    def _setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show",      self._tray_show, default=True),
            pystray.MenuItem("Start Bot", lambda icon, item: self.after(0, self._start)),
            pystray.MenuItem("Stop Bot",  lambda icon, item: self.after(0, self._stop)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",      self._tray_quit),
        )
        self._tray_icon = pystray.Icon("CryptoBot", _make_tray_icon(),
                                        "Crypto Markets", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _tray_show(self, icon=None, item=None):
        self.after(0, self.deiconify)
        self.after(0, self.lift)
        self.after(0, self.focus_force)

    def _tray_quit(self, icon=None, item=None):
        self._stop()
        if self._tray_icon:
            self._tray_icon.stop()
        self.after(0, self.destroy)

    def on_closing(self):
        if pystray and self._tray_icon:
            self.withdraw()
            self._tray_icon.notify(
                "Bot still running in background.\nRight-click tray icon to quit.",
                "Crypto Markets",
            )
        else:
            self._stop()
            self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
