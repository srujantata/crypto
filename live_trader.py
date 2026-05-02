"""
Live / paper trader using Alpaca.
Run: python live_trader.py
Ctrl+C to stop.
"""
import time
import sys
from datetime import datetime
from colorama import Fore, Style, init
from exchange import get_client, fetch_ohlcv, get_balance, get_position_qty, place_order
from strategy import generate_signals
from config import SYMBOL, TIMEFRAME, RISK_PER_TRADE, MODE

init(autoreset=True)

POLL_SECONDS = 60


def log(msg: str, color=Fore.WHITE):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{color}[{ts}] {msg}{Style.RESET_ALL}")


def run():
    log(f"Starting bot — mode={MODE.upper()}  pair={SYMBOL}  tf={TIMEFRAME}", Fore.CYAN)
    if MODE == "live":
        log("LIVE MODE — real money at risk. Ctrl+C within 5s to abort.", Fore.RED)
        time.sleep(5)

    client = get_client()
    balance = get_balance(client)
    log(f"Account — Cash: ${balance['cash']:,.2f}  Portfolio: ${balance['portfolio_value']:,.2f}", Fore.CYAN)

    in_position = False
    btc_held = 0.0

    while True:
        try:
            df = fetch_ohlcv(limit=100)
            df = generate_signals(df)
            last = df.iloc[-1]
            signal = int(last["signal"])

            balance = get_balance(client)
            log(
                f"Price={last['close']:.2f}  RSI={last['rsi']:.1f}  "
                f"EMA_fast={last['ema_fast']:.2f}  signal={signal}  "
                f"Cash=${balance['cash']:,.2f}",
            )

            if signal == 1 and not in_position:
                cash = balance["cash"]
                trade_usd = cash * RISK_PER_TRADE
                qty = trade_usd / last["close"]
                if qty > 0.0001:
                    log(f"BUY  {qty:.6f} BTC @ {last['close']:.2f}  (${trade_usd:.2f})", Fore.GREEN)
                    place_order(client, "buy", SYMBOL, qty)
                    btc_held = qty
                    in_position = True

            elif signal == -1 and in_position:
                actual_qty = get_position_qty(client, SYMBOL)
                if actual_qty > 0.0001:
                    log(f"SELL {actual_qty:.6f} BTC @ {last['close']:.2f}", Fore.RED)
                    place_order(client, "sell", SYMBOL, actual_qty)
                btc_held = 0.0
                in_position = False

        except KeyboardInterrupt:
            log("Stopped by user.", Fore.YELLOW)
            sys.exit(0)
        except Exception as e:
            log(f"Error: {e}", Fore.RED)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
