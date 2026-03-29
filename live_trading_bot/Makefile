WORKERS  := 8
SUBSAMPLE := 4

.PHONY: tune tune-full tune-fast tune-no-oos download

# Default: daily tune with subsampled screening (recommended)
tune:
	cd .. && uv run live_trading_bot/data_pipeline/daily_tune.py --workers $(WORKERS) --subsample $(SUBSAMPLE)

# Full data, no subsampling (slower but more thorough)
tune-full:
	cd .. && uv run live_trading_bot/data_pipeline/daily_tune.py --workers $(WORKERS) --subsample 1

# Quick pass: heavy subsampling for fast iteration
tune-fast:
	cd .. && uv run live_trading_bot/data_pipeline/daily_tune.py --workers $(WORKERS) --subsample 8

# Skip OOS validation
tune-no-oos:
	cd .. && uv run live_trading_bot/data_pipeline/daily_tune.py --workers $(WORKERS) --subsample $(SUBSAMPLE) --no-oos

# Download 15m data only (no tuning)
download:
	cd .. && uv run -c "import sys; sys.path.insert(0, 'live_trading_bot'); from backtest.backtest_interval import download_all_data; download_all_data(hours_back=1300, interval='15m')"
