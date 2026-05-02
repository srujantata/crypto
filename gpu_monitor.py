"""
GPU Temperature Monitor — logs every 5 minutes to gpu_temps.csv
Run on startup via Task Scheduler (set up automatically by setup_monitor.ps1)
"""
import subprocess
import csv
import os
import time
from datetime import datetime

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_temps.csv")
INTERVAL = 300  # 5 minutes


def read_gpu():
    result = subprocess.run(
        ["C:\\Windows\\System32\\nvidia-smi.exe",
         "--query-gpu=temperature.gpu,memory.used,power.draw,utilization.gpu,fan.speed",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True
    )
    parts = [p.strip() for p in result.stdout.strip().split(",")]
    return {
        "temp_c":     int(parts[0]),
        "vram_mb":    int(parts[1]),
        "power_w":    float(parts[2]),
        "gpu_pct":    int(parts[3]),
        "fan_pct":    int(parts[4]),
    }


def log(data: dict):
    is_new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp", "temp_c", "vram_mb", "power_w", "gpu_pct", "fan_pct"])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            data["temp_c"], data["vram_mb"],
            data["power_w"], data["gpu_pct"], data["fan_pct"]
        ])


def print_status(data: dict):
    temp = data["temp_c"]
    if temp >= 85:
        status = "CRITICAL"
    elif temp >= 75:
        status = "HOT"
    elif temp >= 65:
        status = "WARM"
    else:
        status = "GOOD"
    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
          f"Temp={temp}°C ({status})  "
          f"Power={data['power_w']:.0f}W  "
          f"GPU={data['gpu_pct']}%  "
          f"Fan={data['fan_pct']}%  "
          f"VRAM={data['vram_mb']}MB")


if __name__ == "__main__":
    print(f"GPU Monitor started — logging to {LOG_PATH} every 5 min")
    while True:
        try:
            data = read_gpu()
            log(data)
            print_status(data)
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(INTERVAL)
