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

BACKTEST_TF = "1h"  # 1h gives 730 days of history vs 60 days for 15m


def _max_drawdown(equity_curve: list) -> float:
    """Peak-to-trough max drawdown as a positive percentage."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def backtest_symbol(symbol: str, initial_capital: float, timeframe: str = BACKTEST_TF) -> dict:
    try:
        df = fetch_ohlcv(limit=1000, symbol=symbol, timeframe=timeframe)
        df = generate_signals(df)
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

    capital      = initial_capital
    held         = 0.0
    in_position  = False
    entry_price  = 0.0
    trades: list = []
    equity_curve = [capital]

    for _, row in df.iterrows():
        price  = float(row["close"])
        signal = int(row["signal"])

        if signal == 1 and not in_position:
            trade_val    = capital * RISK_PER_TRADE
            qty          = trade_val / price
            held         = qty
            capital     -= trade_val
            in_position  = True
            entry_price  = price
            trades.append({"action": "BUY", "price": price, "qty": qty})

        elif signal == -1 and in_position:
            proceeds = held * price
            pnl      = proceeds - (held * entry_price)
            capital += proceeds
            trades.append({"action": "SELL", "price": price, "qty": held, "pnl": pnl})
            held        = 0.0
            in_position = False

        # track portfolio value each bar (held uses last known entry for simplicity)
        mark_to_market = capital + (held * price if in_position else 0.0)
        equity_curve.append(mark_to_market)

    if in_position:
        capital += held * float(df["close"].iloc[-1])

    sells    = [t for t in trades if t["action"] == "SELL"]
    wins     = [t for t in sells if t.get("pnl", 0) > 0]
    losses   = [t for t in sells if t.get("pnl", 0) <= 0]
    total_ret = (capital - initial_capital) / initial_capital * 100
    win_rate  = len(wins) / len(sells) * 100 if sells else 0
    avg_win   = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
    avg_loss  = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    max_dd    = _max_drawdown(equity_curve)

    return {
        "symbol":    symbol,
        "trades":    len(sells),
        "win_rate":  win_rate,
        "total_ret": total_ret,
        "final_val": capital,
        "avg_win":   avg_win,
        "avg_loss":  avg_loss,
        "max_dd":    max_dd,
        "error":     None,
    }


def run_all(initial_capital: float = 10_000.0):
    print(f"\n{Fore.CYAN}Running backtest on {len(SYMBOLS)} symbols "
          f"| Timeframe: {BACKTEST_TF} (1yr+ history) | Risk: {RISK_PER_TRADE*100:.0f}% per trade"
          f"{Style.RESET_ALL}\n")

    results = []
    for sym in SYMBOLS:
        print(f"  Backtesting {sym}...", end=" ", flush=True)
        r = backtest_symbol(sym, initial_capital)
        results.append(r)
        if r.get("error"):
            print(f"{Fore.RED}ERROR: {r['error']}{Style.RESET_ALL}")
        else:
            color = Fore.GREEN if r["total_ret"] > 0 else Fore.RED
            print(f"{color}{r['total_ret']:+.2f}%{Style.RESET_ALL} | "
                  f"{r['trades']} trades | WR {r['win_rate']:.0f}% | "
                  f"MaxDD {Fore.RED}{r['max_dd']:.1f}%{Style.RESET_ALL}")

    valid = [r for r in results if not r.get("error")]
    print(f"\n{Fore.YELLOW}{'='*75}")
    print(f"  {'SYMBOL':<12} {'RETURN':>8} {'TRADES':>7} {'WIN RATE':>10} "
          f"{'AVG WIN':>9} {'AVG LOSS':>10} {'MAX DD':>8}")
    print(f"{'='*75}{Style.RESET_ALL}")

    sum_ret = 0.0
    for r in valid:
        color = Fore.GREEN if r["total_ret"] > 0 else Fore.RED
        print(f"  {r['symbol']:<12} "
              f"{color}{r['total_ret']:>+7.2f}%{Style.RESET_ALL} "
              f"{r['trades']:>7} "
              f"{r['win_rate']:>9.1f}% "
              f"{Fore.GREEN}${r['avg_win']:>8.2f}{Style.RESET_ALL} "
              f"{Fore.RED}${r['avg_loss']:>8.2f}{Style.RESET_ALL} "
              f"{Fore.RED}{r['max_dd']:>7.1f}%{Style.RESET_ALL}")
        sum_ret += r["total_ret"]

    avg_ret = sum_ret / len(valid) if valid else 0
    best    = max(valid, key=lambda x: x["total_ret"]) if valid else None
    worst   = min(valid, key=lambda x: x["total_ret"]) if valid else None
    safest  = min(valid, key=lambda x: x["max_dd"])    if valid else None

    print(f"{Fore.YELLOW}{'='*75}{Style.RESET_ALL}")
    print(f"  Avg return      : {Fore.GREEN if avg_ret>0 else Fore.RED}{avg_ret:+.2f}%{Style.RESET_ALL}")
    if best:
        print(f"  Best performer  : {Fore.GREEN}{best['symbol']} ({best['total_ret']:+.2f}%){Style.RESET_ALL}")
    if worst:
        print(f"  Worst performer : {Fore.RED}{worst['symbol']} ({worst['total_ret']:+.2f}%){Style.RESET_ALL}")
    if safest:
        print(f"  Lowest max DD   : {Fore.CYAN}{safest['symbol']} ({safest['max_dd']:.1f}%){Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{'='*75}{Style.RESET_ALL}\n")

    return results


if __name__ == "__main__":
    run_all()
