"""
Trading Bot GUI — multi-asset edition with Cloud dashboard
Run: python app.py
"""
import threading
import time
import csv
import os
import json
import asyncio
import websockets
from datetime import datetime
from queue import Queue, Empty

import customtkinter as ctk
import pystray
import winsound
from PIL import Image, ImageDraw

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def _make_tray_icon() -> Image.Image:
    """Draw a simple lightning bolt icon for the tray."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    d.rectangle([0, 0, 63, 63], fill="#1a1a2e")
    d.polygon([(38, 4), (20, 34), (32, 34), (26, 60), (44, 30), (32, 30), (38, 4)],
              fill="#00d4ff")
    return img

_q: Queue = Queue()

def _post(kind: str, **kwargs):
    _q.put({"kind": kind, **kwargs})


# ── helpers ───────────────────────────────────────────────────────────────────
def _log_trade(symbol, action, price, qty, pnl=None):
    path = os.path.join(os.path.dirname(__file__), "trades.csv")
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
    """Run one iteration of the strategy for a single symbol."""
    from exchange import fetch_ohlcv, get_position_qty, place_order
    from strategy import generate_signals, get_higher_tf_trend
    from config import TIMEFRAME, RISK_PER_TRADE, EMA_FAST, EMA_SLOW

    TRAILING_STOP_PCT = 0.025

    df = fetch_ohlcv(limit=150, symbol=symbol, timeframe=TIMEFRAME)
    if df is None or len(df) < 2:
        return  # no data returned by yfinance, skip this cycle
    df = generate_signals(df)
    if len(df) == 0:
        return
    last   = df.iloc[-1]
    price  = float(last["close"])
    rsi    = float(last["rsi"])
    ema_f  = float(last["ema_fast"])
    adx    = float(last["adx"])
    signal = int(last["signal"])

    # trailing stop
    if state["in_position"]:
        state["peak"] = max(state["peak"], price)
        drop = (state["peak"] - price) / state["peak"]
        if drop >= TRAILING_STOP_PCT:
            signal = -1
            _post("log", msg=f"{symbol} TRAILING STOP — {drop*100:.1f}% from peak ${state['peak']:,.4f}", color="orange")

    # multi-timeframe filter on BUY
    if signal == 1 and not state["in_position"]:
        htf = get_higher_tf_trend(symbol, EMA_FAST, EMA_SLOW)
        if htf == "bear":
            _post("log", msg=f"{symbol} BUY blocked — 1h trend bearish", color="yellow")
            signal = 0
        elif htf == "bull":
            _post("log", msg=f"{symbol} 1h trend confirmed bullish", color="cyan")

    if signal == 1 and not state["in_position"]:
        trade_usd = bal["cash"] * RISK_PER_TRADE
        qty = trade_usd / price
        if qty > 0.00001:
            place_order(client, "buy", symbol, qty)
            state["in_position"] = True
            state["entry"]       = price
            state["peak"]        = price
            _post("log", msg=f"BUY  {symbol}  {qty:.5f} @ ${price:,.4f}  ADX={adx:.1f}  (${trade_usd:,.2f})", color="green")
            _post("trade", symbol=symbol, action="BUY", price=price, pnl=0)
            _log_trade(symbol, "BUY", price, qty)

    elif signal == -1 and state["in_position"]:
        import math
        actual_qty = math.floor(get_position_qty(client, symbol) * 1e5) / 1e5  # floor to 5dp
        if actual_qty > 0.00001:
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

    POLL_SECONDS = 60

    _post("log", msg=f"Bot started — {MODE.upper()} | {len(SYMBOLS)} pairs | {TIMEFRAME}", color="cyan")
    _post("log", msg=f"Trading: {', '.join(SYMBOLS)}", color="cyan")

    try:
        client = get_client()
        bal    = get_balance(client)
        _post("balance", cash=bal["cash"], portfolio=bal["portfolio_value"])
        _post("log", msg=f"Connected — Cash: ${bal['cash']:,.2f}  Portfolio: ${bal['portfolio_value']:,.2f}", color="cyan")
    except Exception as e:
        _post("log", msg=f"[ERROR] Connection failed: {e}", color="red")
        return

    # per-symbol state — sync existing positions from Alpaca on startup
    states = {s: {"in_position": False, "entry": 0.0, "peak": 0.0} for s in SYMBOLS}
    try:
        from exchange import get_position_qty
        open_positions = client.get_all_positions()
        for pos in open_positions:
            # Alpaca uses "BTCUSD", we use "BTC/USD"
            sym_raw = pos.symbol  # e.g. "BTCUSD"
            matched = next((s for s in SYMBOLS if s.replace("/", "") == sym_raw), None)
            if matched:
                qty        = float(pos.qty)
                avg_entry  = float(pos.avg_entry_price)
                cur_price  = float(pos.current_price)
                states[matched]["in_position"] = True
                states[matched]["entry"]       = avg_entry
                states[matched]["peak"]        = cur_price
                _post("log", msg=f"Resumed {matched} position — {qty:.5f} @ ${avg_entry:,.4f} (opened earlier)", color="yellow")
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
                time.sleep(1)  # small pause between symbols to respect rate limits

        except Exception as e:
            _post("log", msg=f"[ERROR] {e}", color="red")

        stop_event.wait(POLL_SECONDS)

    _post("log", msg="Bot stopped.", color="yellow")


# ── GUI ───────────────────────────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Crypto Markets")

        self.resizable(True, True)
        self.minsize(700, 500)

        # maximize to whichever monitor the window opens on (works with dual display)
        self.state("zoomed")
        self.update_idletasks()

        # get actual window width AFTER maximize (not raw screen — avoids dual-display combined width)
        actual_w = self.winfo_width()
        if actual_w < 100:  # not rendered yet, fallback
            actual_w = 1920
        self._scale = max(0.8, min(actual_w / 1920, 2.0))

        self._stop_event: threading.Event | None = None
        self._bot_thread: threading.Thread | None = None
        self._running    = False
        self._on_top     = False
        self._symbol_rows: dict = {}
        self._tray_icon: pystray.Icon | None = None
        self._daily_pnl  = 0.0

        # cloud sync
        self._cloud_url    = os.getenv("CLOUD_WS_URL", "")
        self._cloud_token  = os.getenv("CLOUD_TOKEN", "")
        self._cloud_thread: threading.Thread | None = None
        self._cloud_rows:   dict = {}
        self._ws_status    = "disconnected"

        self._build_ui()
        self._setup_tray()
        self._poll_queue()

    def _build_ui(self):
        from config import SYMBOLS

        s = self._scale
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # header
        hdr = ctk.CTkFrame(self, fg_color="#1a1a2e", corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="⚡ Crypto Markets",
                     font=("Arial", int(20*s), "bold"),
                     text_color="#00d4ff").pack(side="left", padx=int(16*s), pady=int(12*s))
        self._status_dot = ctk.CTkLabel(hdr, text="● STOPPED",
                                         font=("Arial", int(13*s), "bold"), text_color="#ff4444")
        self._status_dot.pack(side="right", padx=int(16*s))
        self._lbl_pnl = ctk.CTkLabel(hdr, text="P&L: $0.00",
                                      font=("Arial", int(13*s), "bold"), text_color="#888888")
        self._lbl_pnl.pack(side="right", padx=int(8*s))

        # tab view
        tabs = ctk.CTkTabview(self, fg_color="#0a0a1a",
                               segmented_button_fg_color="#1a1a2e",
                               segmented_button_selected_color="#0055aa",
                               segmented_button_selected_hover_color="#0066cc")
        tabs.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        tab_local = tabs.add("📈  Live Bot")
        tab_cloud = tabs.add("☁️  Cloud Simulations")
        self._build_cloud_tab(tab_cloud)

        # ── LOCAL BOT TAB ──────────────────────────────────────────────────────
        # portfolio strip
        port = ctk.CTkFrame(tab_local, fg_color="#16213e", corner_radius=10)
        port.pack(fill="x", padx=int(14*s), pady=(int(10*s), 4))
        port.columnconfigure((0, 1), weight=1)
        self._lbl_cash      = self._stat(port, "CASH",      "$—", 0)
        self._lbl_portfolio = self._stat(port, "PORTFOLIO", "$—", 1)

        # unified table — header + rows in one frame so columns align perfectly
        COL_WEIGHTS = [1, 2, 1, 1, 1, 2, 2]  # SYMBOL PRICE RSI ADX SIGNAL STATUS LAST
        COL_HEADS   = ["SYMBOL", "PRICE", "RSI", "ADX", "SIGNAL", "STATUS", "LAST TRADE"]

        tbl = ctk.CTkFrame(tab_local, fg_color="#0a0a1a", corner_radius=8)
        tbl.pack(fill="x", padx=int(14*s), pady=(6, 4))
        for ci, wt in enumerate(COL_WEIGHTS):
            tbl.columnconfigure(ci, weight=wt, uniform="col")

        # header row
        for ci, txt in enumerate(COL_HEADS):
            ctk.CTkLabel(tbl, text=txt, font=("Arial", int(10*s), "bold"),
                         text_color="#555", anchor="center"
                         ).grid(row=0, column=ci, padx=6, pady=(8, 4), sticky="ew")

        # separator line
        sep = ctk.CTkFrame(tbl, height=1, fg_color="#222244", corner_radius=0)
        sep.grid(row=1, column=0, columnspan=len(COL_HEADS), sticky="ew", padx=4)

        # data rows
        for i, sym in enumerate(SYMBOLS):
            ri  = i + 2  # offset for header + separator
            bg  = "#0f0f23" if i % 2 == 0 else "#0d0d1e"
            pad = (int(8*s), int(8*s))
            fs  = int(13*s)
            base = sym.split("/")[0] if "/" in sym else sym

            # row background spanning all columns
            row_bg = ctk.CTkFrame(tbl, fg_color=bg, corner_radius=4, height=int(44*s))
            row_bg.grid(row=ri, column=0, columnspan=len(COL_HEADS),
                        sticky="ew", padx=2, pady=1)
            row_bg.grid_propagate(False)

            lbl_sym       = ctk.CTkLabel(tbl, text=base,  font=("Arial", fs, "bold"),
                                          text_color="#00d4ff", anchor="center", fg_color=bg)
            lbl_price     = ctk.CTkLabel(tbl, text="—",   font=("Arial", fs),
                                          anchor="center", fg_color=bg)
            lbl_rsi       = ctk.CTkLabel(tbl, text="—",   font=("Arial", fs),
                                          anchor="center", fg_color=bg)
            lbl_adx       = ctk.CTkLabel(tbl, text="—",   font=("Arial", fs),
                                          anchor="center", fg_color=bg)
            lbl_signal    = ctk.CTkLabel(tbl, text="IDLE", font=("Arial", fs, "bold"),
                                          anchor="center", fg_color=bg)
            lbl_status    = ctk.CTkLabel(tbl, text="FLAT", font=("Arial", int(11*s)),
                                          text_color="#555", anchor="center", fg_color=bg)
            lbl_lasttrade = ctk.CTkLabel(tbl, text="—",   font=("Arial", int(11*s)),
                                          text_color="#555", anchor="center", fg_color=bg)

            for ci, lbl in enumerate([lbl_sym, lbl_price, lbl_rsi, lbl_adx,
                                       lbl_signal, lbl_status, lbl_lasttrade]):
                lbl.grid(row=ri, column=ci, padx=6, pady=pad, sticky="ew")

            self._symbol_rows[sym] = {
                "price": lbl_price, "rsi": lbl_rsi, "adx": lbl_adx,
                "signal": lbl_signal, "status": lbl_status, "last": lbl_lasttrade,
            }

        # buttons
        btn_row = ctk.CTkFrame(tab_local, fg_color="transparent")
        btn_row.pack(fill="x", padx=int(14*s), pady=(int(8*s), 4))
        bw = int(170*s)
        self._btn_start = ctk.CTkButton(btn_row, text="▶  Start Bot", width=bw,
                                         fg_color="#00aa44", hover_color="#008833",
                                         font=("Arial", int(13*s), "bold"), command=self._start)
        self._btn_start.pack(side="left", padx=(0, 6))
        self._btn_stop = ctk.CTkButton(btn_row, text="■  Stop Bot", width=bw,
                                        fg_color="#aa2222", hover_color="#881111",
                                        font=("Arial", int(13*s), "bold"),
                                        state="disabled", command=self._stop)
        self._btn_stop.pack(side="left", padx=(0, 6))
        self._btn_ontop = ctk.CTkButton(btn_row, text="📌 Pin", width=int(80*s),
                                         fg_color="#334455", hover_color="#445566",
                                         font=("Arial", int(12*s)), command=self._toggle_ontop)
        self._btn_ontop.pack(side="left")
        ctk.CTkButton(btn_row, text="📄 Trades CSV", width=int(140*s),
                       fg_color="#333355", hover_color="#444466",
                       font=("Arial", int(12*s)),
                       command=self._open_csv).pack(side="right")

        # log — expands to fill remaining space
        log_frame = ctk.CTkFrame(tab_local, fg_color="#0f0f23", corner_radius=10)
        log_frame.pack(fill="both", expand=True, padx=int(14*s), pady=(0, int(12*s)))
        ctk.CTkLabel(log_frame, text="Activity Log",
                     font=("Arial", int(12*s), "bold"), text_color="#888"
                     ).pack(anchor="w", padx=10, pady=(6, 0))
        self._log_box = ctk.CTkTextbox(log_frame, font=("Courier", int(11*s)),
                                        fg_color="#0a0a1a", text_color="#cccccc",
                                        state="disabled", wrap="word")
        self._log_box.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        for tag, color in [("green","#00ff88"),("red","#ff4444"),("cyan","#00d4ff"),
                            ("yellow","#ffdd00"),("orange","#ffaa00"),("white","#cccccc")]:
            self._log_box._textbox.tag_config(tag, foreground=color)

    def _build_cloud_tab(self, parent):
        """Cloud simulation dashboard — shows 5 profiles in real time."""
        s = self._scale
        PROFILE_COLORS = {
            "conservative": "#00aaff",
            "moderate":     "#00ff88",
            "aggressive":   "#ff6600",
            "scalper":      "#ffdd00",
            "swing":        "#cc44ff",
        }
        PROFILES_ORDER = ["conservative", "moderate", "aggressive", "scalper", "swing"]

        # connection status bar
        conn_bar = ctk.CTkFrame(parent, fg_color="#0a0a1a", corner_radius=8)
        conn_bar.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(conn_bar, text="Cloud Server:", font=("Arial", int(11*s)),
                     text_color="#555").pack(side="left", padx=10, pady=6)
        self._lbl_ws_url = ctk.CTkLabel(conn_bar,
                                         text=self._cloud_url or "Not configured — set CLOUD_WS_URL in .env",
                                         font=("Arial", int(11*s)), text_color="#888")
        self._lbl_ws_url.pack(side="left")
        self._lbl_ws_status = ctk.CTkLabel(conn_bar, text="● DISCONNECTED",
                                            font=("Arial", int(11*s), "bold"), text_color="#ff4444")
        self._lbl_ws_status.pack(side="right", padx=10)
        ctk.CTkButton(conn_bar, text="Connect", width=int(90*s),
                       fg_color="#0055aa", hover_color="#0066cc",
                       font=("Arial", int(11*s)),
                       command=self._connect_cloud).pack(side="right", padx=6, pady=4)

        # replay button
        replay_bar = ctk.CTkFrame(parent, fg_color="transparent")
        replay_bar.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkButton(replay_bar, text="⚡ Run 6-Month Replay (all profiles)",
                       width=int(300*s), fg_color="#333355", hover_color="#444466",
                       font=("Arial", int(12*s), "bold"),
                       command=self._run_replay).pack(side="left")
        self._lbl_replay = ctk.CTkLabel(replay_bar, text="", font=("Arial", int(11*s)),
                                         text_color="#888")
        self._lbl_replay.pack(side="left", padx=10)

        # profile cards
        cards_frame = ctk.CTkFrame(parent, fg_color="transparent")
        cards_frame.pack(fill="x", padx=10, pady=4)
        for ci in range(len(PROFILES_ORDER)):
            cards_frame.columnconfigure(ci, weight=1)

        self._cloud_rows = {}
        for ci, name in enumerate(PROFILES_ORDER):
            color = PROFILE_COLORS[name]
            card  = ctk.CTkFrame(cards_frame, fg_color="#0f0f23", corner_radius=10)
            card.grid(row=0, column=ci, padx=6, pady=4, sticky="nsew")
            card.columnconfigure(0, weight=1)

            # profile name strip
            ctk.CTkFrame(card, fg_color=color, height=4, corner_radius=2
                          ).grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 0))
            ctk.CTkLabel(card, text=name.upper(), font=("Arial", int(11*s), "bold"),
                         text_color=color).grid(row=1, column=0, pady=(6, 0))

            lbl_ret = ctk.CTkLabel(card, text="—", font=("Arial", int(22*s), "bold"),
                                    text_color="#ffffff")
            lbl_ret.grid(row=2, column=0, pady=2)
            lbl_trades = ctk.CTkLabel(card, text="0 trades", font=("Arial", int(10*s)),
                                       text_color="#666")
            lbl_trades.grid(row=3, column=0)
            lbl_wr = ctk.CTkLabel(card, text="WR: —", font=("Arial", int(10*s)),
                                   text_color="#666")
            lbl_wr.grid(row=4, column=0, pady=(0, 6))

            self._cloud_rows[name] = {
                "return": lbl_ret, "trades": lbl_trades, "winrate": lbl_wr
            }

        # recent trades log for cloud
        log_f = ctk.CTkFrame(parent, fg_color="#0f0f23", corner_radius=10)
        log_f.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        ctk.CTkLabel(log_f, text="Cloud Activity", font=("Arial", int(12*s), "bold"),
                     text_color="#888").pack(anchor="w", padx=10, pady=(6, 0))
        self._cloud_log = ctk.CTkTextbox(log_f, font=("Courier", int(11*s)),
                                          fg_color="#0a0a1a", text_color="#cccccc",
                                          state="disabled", wrap="word")
        self._cloud_log.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        for tag, c in [("green","#00ff88"),("red","#ff4444"),("cyan","#00d4ff"),
                        ("yellow","#ffdd00"),("white","#cccccc")]:
            self._cloud_log._textbox.tag_config(tag, foreground=c)

    def _update_cloud_card(self, name: str, data: dict):
        row = self._cloud_rows.get(name)
        if not row:
            return
        ret  = data.get("return_pct", 0)
        col  = "#00ff88" if ret >= 0 else "#ff4444"
        row["return"].configure(text=f"{ret:+.2f}%", text_color=col)
        row["trades"].configure(text=f"{data.get('total_trades', 0)} trades")
        row["winrate"].configure(text=f"WR: {data.get('win_rate', 0):.1f}%")

        # log recent trades
        for t in data.get("recent_trades", [])[-3:]:
            ts  = t.get("timestamp", "")[:19]
            sym = t.get("symbol", "")
            act = t.get("action", "")
            pnl = float(t.get("pnl", 0))
            c   = "green" if act == "BUY" or pnl >= 0 else "red"
            msg = f"[{ts}] [{name[:4].upper()}] {act} {sym}  PnL: ${pnl:+.2f}\n"
            self._cloud_log.configure(state="normal")
            self._cloud_log._textbox.insert("end", msg, c)
            self._cloud_log._textbox.see("end")
            self._cloud_log.configure(state="disabled")

    def _connect_cloud(self):
        if not self._cloud_url or not self._cloud_token:
            self._lbl_ws_status.configure(text="● NO CONFIG", text_color="#ff8800")
            return
        if self._cloud_thread and self._cloud_thread.is_alive():
            return
        self._cloud_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._cloud_thread.start()

    def _ws_loop(self):
        """WebSocket client — connects to cloud, receives updates."""
        import asyncio

        async def _run():
            url = f"{self._cloud_url}?token={self._cloud_token}"
            while True:
                try:
                    self.after(0, lambda: self._lbl_ws_status.configure(
                        text="● CONNECTING...", text_color="#ffaa00"))
                    async with websockets.connect(url, ping_interval=20) as ws:
                        self.after(0, lambda: self._lbl_ws_status.configure(
                            text="● LIVE", text_color="#00ff88"))
                        async for raw in ws:
                            msg = json.loads(raw)
                            if msg["type"] == "init":
                                for name, data in msg["data"].items():
                                    self.after(0, self._update_cloud_card, name, data)
                            elif msg["type"] == "sim_update":
                                name = msg["profile"]
                                self.after(0, self._update_cloud_card, name, msg["data"])
                except Exception:
                    self.after(0, lambda: self._lbl_ws_status.configure(
                        text="● RECONNECTING...", text_color="#ff8800"))
                    await asyncio.sleep(5)

        asyncio.run(_run())

    def _run_replay(self):
        self._lbl_replay.configure(text="Running replay...", text_color="#ffaa00")
        def _do():
            from simulator import run_all_replays
            results = run_all_replays(months=6)
            def _show():
                best = max(results, key=lambda k: results[k].get("return_pct", -999))
                self._lbl_replay.configure(
                    text=f"Done! Best: {best} ({results[best].get('return_pct',0):+.2f}%)",
                    text_color="#00ff88")
                for name, data in results.items():
                    self._update_cloud_card(name, data)
            self.after(0, _show)
        threading.Thread(target=_do, daemon=True).start()

    def _stat(self, parent, label, value, col):
        s = self._scale
        f = ctk.CTkFrame(parent, fg_color="#0f0f23", corner_radius=8)
        f.grid(row=0, column=col, padx=6, pady=int(8*s), sticky="ew")
        ctk.CTkLabel(f, text=label, font=("Arial", int(10*s)), text_color="#666").pack(pady=(int(6*s), 0))
        lbl = ctk.CTkLabel(f, text=value, font=("Arial", int(18*s), "bold"), text_color="#ffffff")
        lbl.pack(pady=(0, int(6*s)))
        return lbl

    def _start(self):
        if self._running:
            return
        self._running    = True
        self._stop_event = threading.Event()
        self._bot_thread = threading.Thread(target=bot_loop, args=(self._stop_event,), daemon=True)
        self._bot_thread.start()
        self._status_dot.configure(text="● RUNNING", text_color="#00ff88")
        self._btn_start.configure(state="disabled")
        self._btn_stop.configure(state="normal")

    def _stop(self):
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        self._status_dot.configure(text="● STOPPED", text_color="#ff4444")
        self._btn_start.configure(state="normal")
        self._btn_stop.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                msg = _q.get_nowait()
                kind = msg["kind"]

                if kind == "tick":
                    sym  = msg["symbol"]
                    row  = self._symbol_rows.get(sym)
                    if row:
                        row["price"].configure(text=f"${msg['price']:,.4f}")
                        rsi = msg["rsi"]
                        rsi_color = "#ff4444" if rsi > 70 else ("#ffaa00" if rsi < 30 else "#cccccc")
                        row["rsi"].configure(text=f"{rsi:.1f}", text_color=rsi_color)
                        adx = msg["adx"]
                        adx_color = "#00ff88" if adx > 25 else "#ff4444"
                        row["adx"].configure(text=f"{adx:.1f}", text_color=adx_color)
                        sig = msg["signal"]
                        sig_text  = {1: "BUY", -1: "SELL", 0: "HOLD"}.get(sig, "—")
                        sig_color = {1: "#00ff88", -1: "#ff4444", 0: "#888888"}.get(sig, "#fff")
                        row["signal"].configure(text=sig_text, text_color=sig_color)
                        in_pos = msg["in_position"]
                        row["status"].configure(
                            text="● IN POSITION" if in_pos else "FLAT",
                            text_color="#00ff88" if in_pos else "#666666"
                        )

                elif kind == "balance":
                    self._lbl_cash.configure(text=f"${msg['cash']:,.2f}")
                    self._lbl_portfolio.configure(text=f"${msg['portfolio']:,.2f}")

                elif kind == "trade":
                    sym    = msg["symbol"]
                    action = msg["action"]
                    price  = msg["price"]
                    pnl    = msg.get("pnl", 0)
                    self._daily_pnl += pnl
                    pnl_color = "#00ff88" if self._daily_pnl >= 0 else "#ff4444"
                    self._lbl_pnl.configure(
                        text=f"P&L: ${self._daily_pnl:+,.2f}",
                        text_color=pnl_color
                    )
                    row = self._symbol_rows.get(sym)
                    if row:
                        t_color = "#00ff88" if action == "BUY" else "#ff4444"
                        row["last"].configure(
                            text=f"{action} ${price:,.2f}",
                            text_color=t_color
                        )
                    # sound alert
                    freq = 1200 if action == "BUY" else 600
                    threading.Thread(
                        target=lambda f=freq: winsound.Beep(f, 300),
                        daemon=True
                    ).start()

                elif kind == "log":
                    self._append_log(msg["msg"], msg.get("color", "white"))

        except Empty:
            pass
        self.after(500, self._poll_queue)

    def _append_log(self, text: str, color: str = "white"):
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {text}\n"
        self._log_box.configure(state="normal")
        self._log_box._textbox.insert("end", line, color)
        self._log_box._textbox.see("end")
        self._log_box.configure(state="disabled")

    def _toggle_ontop(self):
        self._on_top = not self._on_top
        self.wm_attributes("-topmost", self._on_top)
        self._btn_ontop.configure(
            text="📌 Pinned" if self._on_top else "📌 Pin",
            fg_color="#0055aa" if self._on_top else "#334455"
        )

    def _open_csv(self):
        path = os.path.join(os.path.dirname(__file__), "trades.csv")
        if os.path.exists(path):
            os.startfile(path)
        else:
            self._append_log("No trades logged yet.", "yellow")

    # ── system tray ───────────────────────────────────────────────────────────
    def _setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show",         self._tray_show, default=True),
            pystray.MenuItem("Start Bot",    lambda icon, item: self.after(0, self._start)),
            pystray.MenuItem("Stop Bot",     lambda icon, item: self.after(0, self._stop)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",         self._tray_quit),
        )
        self._tray_icon = pystray.Icon(
            "CryptoBot",
            _make_tray_icon(),
            "Crypto Trading Bot",
            menu,
        )
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _tray_show(self, icon=None, item=None):
        """Restore window from tray."""
        self.after(0, self.deiconify)
        self.after(0, self.lift)
        self.after(0, self.focus_force)

    def _tray_quit(self, icon=None, item=None):
        """Full quit from tray menu."""
        self._stop()
        if self._tray_icon:
            self._tray_icon.stop()
        self.after(0, self.destroy)

    def on_closing(self):
        """X button — minimize to tray instead of quitting."""
        self.withdraw()  # hide window, keep running
        self._tray_icon.notify(
            "Bot still running in background.\nRight-click tray icon to quit.",
            "Crypto Trading Bot"
        )


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
