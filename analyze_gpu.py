"""
GPU Temperature Analysis — run after 3-4 days of logging
Reads gpu_temps.csv and gives a rental recommendation.
"""
import csv
import os
from datetime import datetime

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_temps.csv")


def analyze():
    if not os.path.exists(LOG_PATH):
        print("No data yet — gpu_temps.csv not found. Is the monitor running?")
        return

    rows = []
    with open(LOG_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if len(rows) < 10:
        print(f"Only {len(rows)} readings so far — need more data. Check back later.")
        return

    temps      = [int(r["temp_c"])    for r in rows]
    powers     = [float(r["power_w"]) for r in rows]
    fans       = [int(r["fan_pct"])   for r in rows]
    gpu_loads  = [int(r["gpu_pct"])   for r in rows]

    avg_temp   = sum(temps)   / len(temps)
    max_temp   = max(temps)
    min_temp   = min(temps)
    avg_power  = sum(powers)  / len(powers)
    over_70    = sum(1 for t in temps if t > 70)
    over_80    = sum(1 for t in temps if t > 80)
    pct_over70 = over_70 / len(temps) * 100

    print("\n" + "="*55)
    print("  GPU TEMPERATURE REPORT — RTX 4090")
    print(f"  Data range: {rows[0]['timestamp']} → {rows[-1]['timestamp']}")
    print(f"  Total readings: {len(rows)} ({len(rows)*5 // 60}h {(len(rows)*5) % 60}m of data)")
    print("="*55)
    print(f"  Avg temp    : {avg_temp:.1f}°C")
    print(f"  Max temp    : {max_temp}°C")
    print(f"  Min temp    : {min_temp}°C")
    print(f"  Avg power   : {avg_power:.1f}W")
    print(f"  Readings >70°C : {over_70} ({pct_over70:.1f}%)")
    print(f"  Readings >80°C : {over_80}")
    print("="*55)

    # Recommendation logic
    if max_temp > 85:
        verdict  = "NO — NOT SAFE TO RENT"
        reason   = f"Max temp hit {max_temp}°C which risks permanent VRAM damage."
        advice   = "Clean dust from card, replace thermal paste, improve case airflow first."
        color    = "DANGER"
    elif max_temp > 78 or pct_over70 > 20:
        verdict  = "CAUTION — RENT WITH STRICT LIMITS"
        reason   = f"Max {max_temp}°C and {pct_over70:.0f}% of readings above 70°C."
        advice   = "Set power limit to 70% and temp limit to 70°C on Vast.ai before renting."
        color    = "WARNING"
    elif avg_temp < 65 and max_temp < 75:
        verdict  = "YES — SAFE TO RENT"
        reason   = f"Average {avg_temp:.1f}°C, max {max_temp}°C — excellent thermal headroom."
        advice   = "Set power limit to 80% and temp limit to 75°C as a precaution."
        color    = "GOOD"
    else:
        verdict  = "YES — SAFE WITH PRECAUTIONS"
        reason   = f"Average {avg_temp:.1f}°C, max {max_temp}°C — acceptable for a 4yr old card."
        advice   = "Set power limit to 75% and temp limit to 72°C on Vast.ai."
        color    = "OK"

    print(f"\n  VERDICT : {verdict}")
    print(f"  Reason  : {reason}")
    print(f"  Action  : {advice}")
    print("="*55 + "\n")


if __name__ == "__main__":
    analyze()
