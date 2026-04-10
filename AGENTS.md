# AGENTS.md

Instructions for AI agents working with this codebase.

## Quick Reference

| Action | Command |
|--------|---------|
| Install deps | `uv sync` |
| Download data | `uv run prepare.py` |
| Run backtest | `uv run backtest.py` |
| Run benchmarks | `uv run run_benchmarks.py` |
| Run autoresearch | `./scripts/daily_autoresearch.sh [total] [batch_size]` |
| Promote best to active | `uv run python scripts/promote_best.py` |
| Start live bot (dry) | `cd live_trading_bot && DRY_RUN=true uv run bot.py` |
| Start live bot (real) | `cd live_trading_bot && DRY_RUN=false uv run bot.py` |
| PnL check | `cd live_trading_bot && uv run pnl.py` |
| Create vault | `cd live_trading_bot && uv run python -m vault.cli create --name "NAME" --desc "DESCRIPTION" --usd 100` |
| Vault status | `cd live_trading_bot && uv run python -m vault.cli status` |
| Deposit to vault | `cd live_trading_bot && uv run python -m vault.cli deposit --usd 100` |
| Withdraw from vault | `cd live_trading_bot && uv run python -m vault.cli withdraw --usd 50` |
| Vault portfolio | `cd live_trading_bot && uv run python -m vault.cli portfolio` |
| Vault followers | `cd live_trading_bot && uv run python -m vault.cli followers` |

## Prerequisites

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** — package manager (no pip, no venv management)
- **[OpenCode](https://github.com/Nunchi-trade/agent-cli)** — for autonomous experiment loops

## Environment

Copy `.env.example` to `.env` at repo root. Required vars:

```bash
cp .env.example .env
```

| Variable | Purpose |
|----------|---------|
| `SUPABASE_DB_URL` | PostgreSQL connection string — stores experiment results and active params |
| `HYPERLIQUID_PRIVATE_KEY` | Wallet private key — required even for dry run (API auth) |
| `HYPERLIQUID_MAIN_WALLET` | Main wallet address — set if using API wallet for live trading |

Backtesting and autoresearch only need `SUPABASE_DB_URL`. Live trading needs all three.

## Project Structure

```
strategy.py              # The ONLY file autoresearch edits — your strategy lives here
backtest.py              # Runs one backtest (fixed harness, do not modify)
prepare.py               # Data download + backtest engine (fixed harness, do not modify)
strategy_types.py        # Shared dataclasses (Signal, BarData, PortfolioState, BacktestResult)
strategy_utils.py        # Indicator helpers (ema, calc_rsi, calc_atr, etc.)
constants.py             # Single source of truth: symbols, fees, API URLs, strategy defaults
program.md               # Instructions for the autonomous experiment loop

scripts/
  daily_autoresearch.sh  # Orchestrates daily automated experiments via OpenCode
  save_to_db.py          # Parse backtest output and save to param_snapshots
  promote_best.py        # Promote best PASS experiment to active (is_active=TRUE)
  download_daily_data.py # Download fresh 1h candles + funding from Hyperliquid
  backfill_results.py    # Bulk-insert historical experiments from results.tsv into DB

live_trading_bot/        # Production trading bot (separate docs: live_trading_bot/RUN.md)
  bot.py                 # Entry point — connects to Hyperliquid, runs strategy on live bars
  execution/             # Order execution engine
  config/settings.py     # Loads params from DB, env vars, or defaults
  strategies/            # Multi-timeframe strategies (15m, 5m, 1m)
  backtest/              # Live bot's own backtesting harness
  data_pipeline/         # Data download and parameter tuning
```

## Autoresearch Mode

The core idea: an AI agent autonomously modifies `strategy.py`, backtests each change, and keeps only improvements. Karpathy-style autoresearch applied to trading strategy discovery.

### Setup

```bash
# 1. Install deps and download data
uv sync
uv run prepare.py

# 2. Make sure .env has SUPABASE_DB_URL
grep SUPABASE_DB_URL .env

# 3. Ensure opencode CLI is available
which opencode
```

### Running Experiments Manually

The experiment loop (from `program.md`):

1. Read `program.md` and current `strategy.py`
2. Modify `strategy.py` with an experimental idea
3. `git add strategy.py && git commit -m "expN: description"`
4. `uv run backtest.py > run.log 2>&1`
5. Parse: `grep "^score:" run.log`
6. Record in `results.tsv`
7. Save to DB: `uv run python scripts/save_to_db.py run.log "expN: description" PASS` (or FAIL)
8. If score improved: keep. If not: `git reset --hard HEAD~1`
9. Repeat

**Rules:**
- Only edit `strategy.py`
- Do not modify `prepare.py`, `backtest.py`, or `benchmarks/`
- No new dependencies (only numpy, pandas, scipy, requests, pyarrow, stdlib)
- Higher score is better. Baseline: 2.724

### Running Daily Autoresearch (Automated)

`scripts/daily_autoresearch.sh` automates the full pipeline:

```bash
# Default: 100 experiments in 10 batches
./scripts/daily_autoresearch.sh

# Custom counts
./scripts/daily_autoresearch.sh 50            # 50 experiments
./scripts/daily_autoresearch.sh 100 5         # 100 experiments, batches of 5
```

**What it does:**

1. Downloads fresh 6 months of 1h candle data from Hyperliquid (clears cache first)
2. Creates/checkout a dated branch (`autotrader/apr04` etc.) from `feat/auto_tuning`
3. Runs experiments in batches via `opencode run` — each batch is an autonomous agent session
4. Every experiment is saved to DB (both PASS and FAIL)
5. Every experiment is reverted after saving (strategy.py returns to harness state)
6. Promotes the best PASS experiment to active (`is_active=TRUE` in DB)

**Cron setup:**
```bash
# Run at 6am UTC daily
0 6 * * * cd /path/to/auto-researchtrading && ./scripts/daily_autoresearch.sh >> data_pipeline/logs/daily_auto.log 2>&1
```

**Continuing a batch:** if the script is interrupted, re-run with the same arguments. It creates/checks out the same dated branch and appends to `results.tsv`.

### After Autoresearch

The best experiment gets promoted automatically. To use it:

1. **Check the results:** `tail -20 results.tsv` or query the DB
2. **Verify the active params:** `cd live_trading_bot && uv run python -c "from config.settings import get_settings; s = get_settings(); print(s.BASE_POSITION_PCT, s.ATR_STOP_MULT, s.TRADING_PAIRS)"`
3. **Run the live bot** — it loads the active params from DB on startup

## Backtesting

### Repo-Root Backtest (1h strategy)

```bash
uv run backtest.py                # Score the current strategy.py
uv run run_benchmarks.py         # Compare against 5 reference strategies
```

Data cached in `~/.cache/autotrader/data/`. Re-download: `uv run prepare.py`.

### Live Bot Backtest (multi-timeframe)

```bash
cd live_trading_bot

# 15m strategy (recommended)
uv run backtest/backtest_interval.py --interval 15m --strategy strategies.strategy_15m

# With fresh data
uv run backtest/backtest_interval.py --interval 15m --strategy strategies.strategy_15m --download --hours 1080
```

## Live Trading

See `live_trading_bot/RUN.md` for full details.

### Quick Start

```bash
cd live_trading_bot

# Dry run (no real orders, simulates against live data)
DRY_RUN=true uv run bot.py

# Live (real orders, real capital)
DRY_RUN=false uv run bot.py
```

### How It Works

1. On startup, `settings.py` loads strategy params from DB (`param_snapshots` where `is_active=TRUE`). This includes trading symbols from the `symbol` column.
2. If no DB params found, falls back to `_discover_usdc_cross_margin_perps()` (API call to Hyperliquid) or `ALL_SYMBOLS` from `constants.py`.
3. Connects to Hyperliquid via WebSocket for live candles and funding data.
4. Runs the strategy (selected by `BAR_INTERVAL`) on each new bar.
5. Execution engine places market orders with leverage/risk checks.

### Key Config (`.env` or env vars)

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `false` | `true` = simulated, `false` = real |
| `BAR_INTERVAL` | `15m` | Candle interval (selects strategy) |
| `TRADING_PAIRS` | auto | Override symbols (DB takes precedence) |
| `MAX_LEVERAGE` | `20` | Max total leverage |
| `MAX_POSITION_PCT` | `0.30` | Max single position as fraction of equity |
| `DAILY_LOSS_LIMIT_PCT` | `0.05` | Kill switch at 5% daily loss |
| `DRY_RUN_INITIAL_CAPITAL` | `10000` | Starting capital for dry run |

### Monitoring

```bash
cd live_trading_bot

# Tail logs
tail -f logs/bot.log

# Recent trades
sqlite3 trading_bot.db "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 10;"

# Daily PnL
uv run pnl.py              # Today
uv run pnl.py 7d           # Last 7 days
```

## Vault Management

### Creating a Vault

Vaults are managed trading accounts on Hyperliquid. Creating a vault costs 100 USDC (gas fee) + minimum 100 USDC initial deposit. The vault leader receives 10% of profits and must maintain ≥5% ownership.

```bash
cd live_trading_bot
uv run python -m vault.cli create --name "My Strategy Vault" --desc "Automated momentum strategy" --usd 500
```

### Vault-Driven Trading

To trade on behalf of a vault, set `HYPERLIQUID_VAULT_ADDRESS` in your environment. The bot will place all orders under the vault's account:

```bash
export HYPERLIQUID_VAULT_ADDRESS=0xYourVaultAddress
cd live_trading_bot && DRY_RUN=true uv run bot.py
```

### Managing Deposits/Withdrawals

```bash
# Check vault status
uv run python -m vault.cli status

# Deposit USDC (from your personal account into the vault)
uv run python -m vault.cli deposit --usd 100

# Withdraw USDC (from the vault back to your personal account)
uv run python -m vault.cli withdraw --usd 50

# View open positions
uv run python -m vault.cli portfolio

# View followers
uv run python -m vault.cli followers
```

## Dynamic Symbol Flow

The symbol list is fully dynamic — no hardcoded lists in the trading path.

```
Tuning path (autoresearch):
  prepare.py::_discover_symbols() → Hyperliquid API → SYMBOLS list
  → download_data() → parquet files
  → backtest → strategy.on_bar(bar_data) iterates bar_data keys
  → _RUNTIME_SYMBOLS captures actual symbols
  → save_experiment_to_db() → "BTC,ETH,SOL,..." in DB symbol column

Live path:
  settings.py::_load_active_db_params() → DB symbol column → TRADING_PAIRS
  (overrides API discovery if DB snapshot exists)
```

- `ALL_SYMBOLS` in `constants.py` — fallback only (5 symbols)
- `BENCHMARK_SYMBOLS` in `constants.py` — 12 symbols for benchmark comparisons only
- The DB snapshot from tuning is the single source of truth for live trading

## DB Schema

Table `param_snapshots` stores every experiment result. Key columns:

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | text | Comma-separated symbols traded (e.g. `BTC,ETH,SOL`) |
| `is_active` | bool | Only one row should be TRUE — the live bot reads this |
| `is_best` | bool | Best scoring experiment per sweep |
| `score` | float | Composite score (higher = better) |
| `sharpe`, `total_return_pct`, `max_drawdown_pct` | float | Performance metrics |
| `status` | text | `PASS` (improved over baseline) or `FAIL` |
| `run_date` | timestamptz | When the experiment ran |
| `description` | text | Human-readable experiment description |

Strategy params (e.g. `BASE_POSITION_PCT`, `ATR_STOP_MULT`) are stored as individual columns. The full list is in `constants.py` → `PARAM_COLUMNS`.

## Key Constraints

- `COOLDOWN_BARS` in `constants.py` must stay at `0` — strategy.py has its own internal cooldown
- Do not re-add correlation filtering or funding boost — explicitly tested and removed ("The Great Simplification")
- `TAKE_PROFIT_PCT=99.0` is intentionally disabled
- `strategy_types.py` has zero project imports — keeps `strategy.py` importable without side effects
- `prepare.py` re-exports dataclasses from `strategy_types.py` for backward compat — do not break `from prepare import Signal`
