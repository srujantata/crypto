"""
Cloud backend — FastAPI + WebSocket + security.
Deploy to Railway: railway up
Run locally:  uvicorn server:app --host 0.0.0.0 --port 8000
"""
import asyncio
import collections
import csv
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s — %(message)s",
)
log = logging.getLogger("server")

# ── Security config ───────────────────────────────────────────────────────────
API_SECRET    = os.getenv("BOT_API_SECRET", "change-me-before-deploying")
ALLOWED_HOSTS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
MAX_CONNS     = int(os.getenv("MAX_WS_CONNECTIONS", "10"))

_bearer = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    token = credentials.credentials
    expected = hashlib.sha256(API_SECRET.encode()).hexdigest()
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid API token")
    return token


def ws_token_valid(token: str) -> bool:
    expected = hashlib.sha256(API_SECRET.encode()).hexdigest()
    return hmac.compare_digest(token, expected)


# ── Rate limiter (per IP) ─────────────────────────────────────────────────────
_rate_store: dict = {}


def rate_limit(request: Request, max_per_minute: int = 60):
    ip  = request.client.host
    now = time.time()
    hits = [t for t in _rate_store.get(ip, []) if now - t < 60]
    if len(hits) >= max_per_minute:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _rate_store[ip] = hits + [now]


async def _cleanup_rate_store():
    """Prune stale IP entries every 5 minutes to prevent unbounded growth."""
    while True:
        await asyncio.sleep(300)
        now = time.time()
        for ip in list(_rate_store.keys()):
            _rate_store[ip] = [t for t in _rate_store[ip] if now - t < 60]
            if not _rate_store[ip]:
                del _rate_store[ip]


# ── Live bot (singleton) ──────────────────────────────────────────────────────
from cloud_bot import CloudLiveBot

live_bot    = CloudLiveBot()
_ws_clients: Set[WebSocket] = set()
_ws_lock    = asyncio.Lock()

_event_loop: Optional[asyncio.AbstractEventLoop] = None

# In-memory trade log — survives within a session even if cloud_trades.csv resets.
# Capped at 500 entries (newest kept on overflow).
_trade_history: collections.deque = collections.deque(maxlen=500)


async def _broadcast_raw(msg: str):
    async with _ws_lock:
        dead = set()
        for ws in _ws_clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        _ws_clients.difference_update(dead)


def _live_bot_event(event_type: str, payload: dict):
    """Called from cloud_bot thread — push to all WebSocket clients."""
    # Capture completed trades into in-memory log for /live/trades endpoint
    if event_type == "bot_trade":
        from datetime import datetime
        _trade_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "symbol":    payload.get("symbol", ""),
            "action":    payload.get("action", ""),
            "price":     f"{payload.get('price', 0):.4f}",
            "qty":       f"{payload.get('qty', 0):.6f}",
            "pnl":       f"{payload.get('pnl', ''):.2f}" if payload.get("pnl") not in (None, "") else "",
        })
    msg = json.dumps({"type": event_type, **payload})
    if _event_loop and _event_loop.is_running():
        asyncio.run_coroutine_threadsafe(_broadcast_raw(msg), _event_loop)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _event_loop
    _event_loop = asyncio.get_running_loop()
    asyncio.ensure_future(_cleanup_rate_store())

    live_bot._on_event = _live_bot_event
    live_bot.start()

    log.info("Server started — live bot running")
    yield

    live_bot.stop()
    log.info("Server shutting down")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Crypto Markets API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_HOSTS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# ── REST endpoints ────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status":   "ok",
        "live_bot": live_bot.status(),
    }


@app.get("/live", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def get_live_status():
    return live_bot.status()


@app.get("/live/trades", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def get_live_trades():
    # Merge CSV trades + in-memory trades, deduplicated by timestamp+symbol
    seen: set = set()
    trades: list = []

    # CSV (may be empty/missing after redeploy)
    path = os.path.join(os.path.dirname(__file__), "cloud_trades.csv")
    if os.path.exists(path):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                key = (row.get("timestamp", ""), row.get("symbol", ""))
                if key not in seen:
                    seen.add(key)
                    trades.append(row)

    # In-memory (current session)
    for row in _trade_history:
        key = (row.get("timestamp", ""), row.get("symbol", ""))
        if key not in seen:
            seen.add(key)
            trades.append(row)

    # Sort newest first
    trades.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return trades


# ── WebSocket endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = ""):
    if not ws_token_valid(token):
        await ws.close(code=4001, reason="Unauthorized")
        return

    # Accept before adding to set — prevents dead sockets in the client list
    await ws.accept()

    async with _ws_lock:
        if len(_ws_clients) >= MAX_CONNS:
            await ws.close(code=4002, reason="Max connections reached")
            return
        _ws_clients.add(ws)

    log.info(f"WS client connected (total: {len(_ws_clients)})")

    try:
        await ws.send_text(json.dumps({
            "type": "init",
            "data": live_bot.status(),
        }))
    except Exception:
        pass

    try:
        while True:
            await asyncio.sleep(30)
            await ws.send_text(json.dumps({"type": "ping"}))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        async with _ws_lock:
            _ws_clients.discard(ws)
        log.info(f"WS client disconnected (total: {len(_ws_clients)})")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0",
                port=int(os.getenv("PORT", 8000)), reload=False)
