"""
Multi-symbol backtester
Run: python backtest.py
"""
import pandas as pd
from colorama import Fore, Style, init
from exchange import fetch_ohlcv
from strategy import generate_signals
from config import SYMBOLS, RISK_PER_TRADE, TIMEFRAME

init(autoreset=True)


def backtest_symbol(symbol: str, initial_capital: float, timeframe: str = "1h") -> dict:
    try:
        df = fetch_ohlcv(limit=1000, symbol=symbol, timeframe=timeframe)
        df = generate_signals(df)
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

    capital     = initial_capital
    held        = 0.0
    in_position = False
    entry_price = 0.0
    trades      = []

    for i, row in df.iterrows():
        price  = float(row["close"])
        signal = int(row["signal"])

        if signal == 1 and not in_position:
            trade_val = capital * RISK_PER_TRADE
            qty       = trade_val / price
            held      = qty
            capital  -= trade_val
            in_position = True
            entry_price = price
            trades.append({"action": "BUY", "price": price, "qty": qty})

        elif signal == -1 and in_position:
            proceeds = held * price
            pnl      = proceeds - (held * entry_price)
            capital += proceeds
            trades.append({"action": "SELL", "price": price, "qty": held, "pnl": pnl})
            held        = 0.0
            in_position = False

    # close open position at last price
    if in_position:
        capital += held * float(df["close"].iloc[-1])

    sells      = [t for t in trades if t["action"] == "SELL"]
    wins       = [t for t in sells if t.get("pnl", 0) > 0]
    losses     = [t for t in sells if t.get("pnl", 0) <= 0]
    total_ret  = (capital - initial_capital) / initial_capital * 100
    win_rate   = len(wins) / len(sells) * 100 if sells else 0
    avg_win    = sum(t["pnl"] for t in wins)  / len(wins)   if wins   else 0
    avg_loss   = sum(t["pnl"] for t in losses)/ len(losses) if losses else 0

    return {
        "symbol":      symbol,
        "trades":      len(sells),
        "win_rate":    win_rate,
        "total_ret":   total_ret,
        "final_val":   capital,
        "avg_win":     avg_win,
        "avg_loss":    avg_loss,
        "error":       None,
    }


BACKTEST_TF = "1h"  # use 1h for backtesting — gives 730 days vs 60 days on 15m


def run_all(initial_capital: float = 10_000.0):
    print(f"\n{Fore.CYAN}Running backtest on {len(SYMBOLS)} symbols "
          f"| Timeframe: {BACKTEST_TF} (1yr+ history) | Risk: {RISK_PER_TRADE*100:.0f}% per trade{Style.RESET_ALL}\n")

    results = []
    for sym in SYMBOLS:
        print(f"  Backtesting {sym}...", end=" ", flush=True)
        r = backtest_symbol(sym, initial_capital, timeframe=BACKTEST_TF)
        results.append(r)
        if r.get("error"):
            print(f"{Fore.RED}ERROR: {r['error']}{Style.RESET_ALL}")
        else:
            color = Fore.GREEN if r["total_ret"] > 0 else Fore.RED
            print(f"{color}{r['total_ret']:+.2f}%{Style.RESET_ALL} | "
                  f"{r['trades']} trades | WR {r['win_rate']:.0f}%")

    # summary table
    valid = [r for r in results if not r.get("error")]
    print(f"\n{Fore.YELLOW}{'='*65}")
    print(f"  {'SYMBOL':<12} {'RETURN':>8} {'TRADES':>7} {'WIN RATE':>10} "
          f"{'AVG WIN':>9} {'AVG LOSS':>10}")
    print(f"{'='*65}{Style.RESET_ALL}")

    total_pnl = 0.0
    for r in valid:
        color = Fore.GREEN if r["total_ret"] > 0 else Fore.RED
        print(f"  {r['symbol']:<12} "
              f"{color}{r['total_ret']:>+7.2f}%{Style.RESET_ALL} "
              f"{r['trades']:>7} "
              f"{r['win_rate']:>9.1f}% "
              f"{Fore.GREEN}${r['avg_win']:>8.2f}{Style.RESET_ALL} "
              f"{Fore.RED}${r['avg_loss']:>8.2f}{Style.RESET_ALL}")
        total_pnl += r["total_ret"]

    avg_ret = total_pnl / len(valid) if valid else 0
    best    = max(valid, key=lambda x: x["total_ret"]) if valid else None
    worst   = min(valid, key=lambda x: x["total_ret"]) if valid else None

    print(f"{Fore.YELLOW}{'='*65}{Style.RESET_ALL}")
    print(f"  Average return  : {Fore.GREEN if avg_ret>0 else Fore.RED}{avg_ret:+.2f}%{Style.RESET_ALL}")
    if best:
        print(f"  Best performer  : {Fore.GREEN}{best['symbol']} ({best['total_ret']:+.2f}%){Style.RESET_ALL}")
    if worst:
        print(f"  Worst performer : {Fore.RED}{worst['symbol']} ({worst['total_ret']:+.2f}%){Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{'='*65}{Style.RESET_ALL}\n")

    return results


if __name__ == "__main__":
    run_all()
