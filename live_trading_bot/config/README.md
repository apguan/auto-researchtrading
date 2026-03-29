# Config

## optimized_params.json

This file is loaded at import time by `strategies/strategy_15m.py` and overrides
the module-level constants. **These are pre-tuned defaults, not the best-performing
params.**

The tuning sweep (`backtest/tune_15m.py`) found better values (ATR_STOP_MULT=8.0,
MIN_VOTES=5, RSI_OVERBOUGHT=65, RSI_PERIOD=28, COOLDOWN_BARS=12) which yield
+646% vs +404% on the 15m backtest. These tuned values are hardcoded in the
strategy source but get overwritten by this JSON file.

TODO:
- Support per-interval param files (e.g. `optimized_params_15m.json`, `optimized_params_1m.json`)
- Update this file with tuned 15m values once per-interval loading is implemented
- Do NOT apply 15m-tuned params to 1m/5m strategies (they have different optimal values)
