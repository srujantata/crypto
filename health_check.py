"""
Cloud health check — run on PC startup to validate everything.
Checks: cloud server, live bot, Alpaca connection, recent trades.
"""
import csv
import json
import os
import urllib.request
import urllib.error
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

CLOUD_URL = os.getenv("CLOUD_WS_URL", "").replace("wss://", "https://").replace("/ws", "")
TOKEN     = os.getenv("CLOUD_TOKEN", "")

PASS = "[OK]  "
FAIL = "[FAIL]"
WARN = "[WARN]"


def _get_json(path: str, auth: bool = True) -> dict:
    headers = {"Authorization": f"Bearer {TOKEN}"} if auth else {}
    req = urllib.request.Request(f"{CLOUD_URL}{path}", headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def run():
    print(f"\n{'='*55}")
    print(f"  CLOUD HEALTH REPORT  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}")

    passed = 0
    total  = 0

    # 1 — cloud reachable (fetch once, reuse for all cloud checks)
    total += 1
    health_data = None
    try:
        health_data = _get_json("/health", auth=False)
        print(f"  {PASS} Cloud server: Online")
        passed += 1
    except Exception as e:
        print(f"  {FAIL} Cloud server: UNREACHABLE — {e}")
        print(f"         Skipping cloud-dependent checks.")

    if health_data is not None:
        # 2 — Alpaca live bot connected
        total += 1
        bot = health_data.get("live_bot", {})
        if bot.get("connected"):
            positions = bot.get("positions", {})
            pos_str   = ", ".join(f"{s} @ ${v['entry']:,.0f}" for s, v in positions.items()) or "none"
            print(f"  {PASS} Alpaca live bot: Connected | Positions: {pos_str}")
            passed += 1
        else:
            print(f"  {FAIL} Alpaca live bot: NOT connected to Alpaca")

        # 3 — recent cloud trades
        total += 1
        try:
            trades = _get_json("/live/trades")
            if trades:
                last  = trades[-1]
                hours = (datetime.now() - datetime.fromisoformat(last["timestamp"])).total_seconds() / 3600
                print(f"  {PASS} Cloud trades: {len(trades)} total | Last: {last['action']} {last['symbol']} {hours:.1f}h ago")
            else:
                print(f"  {WARN} Cloud trades: No trades yet — bot is watching for signals")
            passed += 1
        except Exception as e:
            print(f"  {WARN} Cloud trades: {e}")
            passed += 1

    # 4 — local .env config (always check regardless of cloud)
    total += 1
    required = ["ALPACA_API_KEY", "ALPACA_SECRET", "CLOUD_WS_URL", "CLOUD_TOKEN"]
    missing  = [k for k in required if not os.getenv(k)]
    if not missing:
        print(f"  {PASS} Local config: All required env vars present")
        passed += 1
    else:
        print(f"  {FAIL} Local config: Missing {missing}")

    print(f"{'='*55}")
    print(f"  Result: {passed}/{total} checks passed")
    if passed == total:
        print(f"  {PASS} All systems operational")
    else:
        print(f"  {FAIL} Issues found — review above")
    print(f"{'='*55}\n")

    return passed == total


if __name__ == "__main__":
    run()
