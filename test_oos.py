"""
Out-of-sample test runner. Runs strategy on TEST split (Apr 2025 - Dec 2025).
This is the held-out data never used during optimization.
"""
import time
from prepare import load_data, run_backtest, compute_score

t_start = time.time()

from strategy import Strategy

for split_name in ["val", "test", "train"]:
    strategy = Strategy()
    data = load_data(split_name)
    print(f"\n=== {split_name.upper()} SET ===")
    print(f"Loaded {sum(len(df) for df in data.values())} bars across {len(data)} symbols")

    result = run_backtest(strategy, data)
    score = compute_score(result)

    print(f"score:              {score:.6f}")
    print(f"sharpe:             {result.sharpe:.6f}")
    print(f"total_return_pct:   {result.total_return_pct:.6f}")
    print(f"max_drawdown_pct:   {result.max_drawdown_pct:.6f}")
    print(f"num_trades:         {result.num_trades}")
    print(f"win_rate_pct:       {result.win_rate_pct:.6f}")
    print(f"profit_factor:      {result.profit_factor:.6f}")
    print(f"annual_turnover:    {result.annual_turnover:.2f}")

t_end = time.time()
print(f"\nTotal time: {t_end - t_start:.1f}s")
