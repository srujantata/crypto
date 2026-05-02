"""
Cloud backend — FastAPI + WebSocket + security.
Deploy to Railway: railway up
Run locally:  uvicorn server:app --host 0.0.0.0 --port 8000
"""
import asyncio
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


# ── Simulation manager + Live bot (singletons) ───────────────────────────────
from simulator import SimulationManager, run_all_replays
from cloud_bot import CloudLiveBot

manager  = SimulationManager()
live_bot = CloudLiveBot()
_ws_clients: Set[WebSocket] = set()
_ws_lock = asyncio.Lock()

# Stored at startup so background threads can schedule coroutines safely.
# Using asyncio.get_running_loop() instead of deprecated get_event_loop().
_event_loop: Optional[asyncio.AbstractEventLoop] = None


async def _broadcast_raw(msg: str):
    async with _ws_lock:
        dead = set()
        for ws in _ws_clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        _ws_clients.difference_update(dead)


async def _broadcast(name: str, state: dict):
    msg = json.dumps({"type": "sim_update", "profile": name, "data": state})
    await _broadcast_raw(msg)


def _sync_broadcast(name: str, state: dict):
    """Called from simulator thread — schedule async broadcast on the stored loop."""
    if _event_loop and _event_loop.is_running():
        asyncio.run_coroutine_threadsafe(_broadcast(name, state), _event_loop)


def _live_bot_event(event_type: str, payload: dict):
    """Called from cloud_bot thread — push to all WebSocket clients."""
    msg = json.dumps({"type": event_type, **payload})
    if _event_loop and _event_loop.is_running():
        asyncio.run_coroutine_threadsafe(_broadcast_raw(msg), _event_loop)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _event_loop
    _event_loop = asyncio.get_running_loop()
    asyncio.ensure_future(_cleanup_rate_store())

    manager.add_listener(_sync_broadcast)
    live_bot._on_event = _live_bot_event
    live_bot.start()
    manager.start_all()

    log.info("Server started — live bot + simulations running")
    yield

    live_bot.stop()
    manager.stop_all()
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
        "profiles": list(manager.get_states().keys()),
        "live_bot": live_bot.status(),
    }


@app.get("/live", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def get_live_status():
    return live_bot.status()


@app.get("/live/trades", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def get_live_trades():
    path = os.path.join(os.path.dirname(__file__), "cloud_trades.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@app.get("/portfolios", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def get_portfolios():
    return manager.get_states()


@app.get("/portfolio/{profile}", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def get_portfolio(profile: str):
    states = manager.get_states()
    if profile not in states:
        raise HTTPException(status_code=404, detail=f"Profile '{profile}' not found")
    return states[profile]


@app.get("/trades/{profile}", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def get_trades(profile: str):
    path = os.path.join(os.path.dirname(__file__), f"trades_{profile}.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@app.post("/replay", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def run_replay(months: int = 6):
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, run_all_replays, months)
    return results


@app.post("/control/{action}", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def control(action: str):
    if action == "stop":
        manager.stop_all()
        return {"status": "stopped"}
    elif action == "start":
        manager.start_all()
        return {"status": "started"}
    raise HTTPException(status_code=400, detail="Unknown action")


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
            "data": manager.get_states(),
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
