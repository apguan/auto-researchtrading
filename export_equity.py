#!/usr/bin/env python3
"""Export equity curve from the current strategy to CSV for charting."""
import csv
from datetime import datetime, timedelta
from prepare import load_data, run_backtest
from strategy import Strategy

strategy = Strategy()
data = load_data("val")
result = run_backtest(strategy, data)

# VAL_START = 2024-07-01, hourly bars
start = datetime(2024, 7, 1)
timestamps = [start + timedelta(hours=i) for i in range(len(result.equity_curve))]

with open("equity_curve.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timestamp", "equity"])
    for ts, eq in zip(timestamps, result.equity_curve):
        w.writerow([ts.strftime("%Y-%m-%d %H:%M"), f"{eq:.2f}"])

print(f"Exported {len(result.equity_curve)} data points to equity_curve.csv")
print(f"Start equity: ${result.equity_curve[0]:,.2f}")
print(f"End equity:   ${result.equity_curve[-1]:,.2f}")
print(f"Return:       {result.total_return_pct:.1f}%")
