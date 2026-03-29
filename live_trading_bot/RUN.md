# Running the Trading Bot

## Prerequisites

- **Python 3.10+** with [uv](https://docs.astral.sh/uv/)
- **Hyperliquid account** with funded wallet (live only)
- **Private key** from your Hyperliquid wallet

## Installation

Dependencies are in the repo's `pyproject.toml`. From the repo root:

```bash
uv sync
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### Required Variables

| Variable | Description | Example |
|---|---|---|
| `HYPERLIQUID_PRIVATE_KEY` | Your wallet private key (main wallet or API wallet) | `0xabc123...` |
| `DRY_RUN` | `true` = simulated trading, `false` = real money | `true` |

### API Wallet Setup

If you trade through an API wallet (recommended for security), set both:

| Variable | Description | Example |
|---|---|---|
| `HYPERLIQUID_PRIVATE_KEY` | Your **API wallet** private key | `0xabc123...` |
| `HYPERLIQUID_MAIN_WALLET` | Your **main wallet** address (holds the funds) | `0xdef456...` |

The API wallet signs orders; the main wallet holds equity and positions. You can create an API wallet from the Hyperliquid dashboard under Settings > API Wallets.

If `HYPERLIQUID_MAIN_WALLET` is not set, the bot assumes `HYPERLIQUID_PRIVATE_KEY` is the main wallet's key (simpler setup, but the main key is stored in `.env`).

### Trading Variables

| Variable | Default | Description |
|---|---|---|
| `TRADING_PAIRS` | `BTC,ETH,SOL,XRP` | Comma-separated symbols to trade |
| `MAX_LEVERAGE` | `20` | Max leverage per position |
| `MAX_POSITION_PCT` | `0.30` | Max position as fraction of equity |
| `DAILY_LOSS_LIMIT_PCT` | `0.05` | Kill switch at 5% daily loss |
| `BAR_INTERVAL` | `15m` | Candle interval (determines which strategy loads) |
| `STRATEGY_MODULE` | *(auto)* | Override strategy module path. If unset, derived from `BAR_INTERVAL` |

### Strategy Selection

`BAR_INTERVAL` auto-selects the matching strategy:

| BAR_INTERVAL | Strategy Module | Backtest Return | Status |
|---|---|---|---|
| `15m` | `strategies.strategy_15m` | +646% (tuned) | **Recommended** |
| `5m` | `strategies.strategy_5m` | +10.6% (baseline) | Untuned |
| `1m` | `strategies.strategy_1m` | +3.6% (tuned) | Thin edge |
| `1h` | `_bt_strategy` | +130% | Upstream hourly |

### Dry Run Settings

| Variable | Default | Description |
|---|---|---|
| `DRY_RUN_INITIAL_CAPITAL` | `10000` | Starting capital for simulated portfolio |

### Optional: Alerts

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token for trade/error alerts |
| `TELEGRAM_CHAT_ID` | Telegram chat ID to receive alerts |
| `DISCORD_WEBHOOK_URL` | Discord webhook URL for alerts |

Alerts are optional. The bot works without them — it just won't send notifications.

---

## Dry Run

Dry run simulates trading against live market data without sending real orders. It uses the same strategy, data feed, and risk checks as live mode. No capital is at risk.

### Start

```bash
cd live_trading_bot
DRY_RUN=true python3 bot.py
```

Or set `DRY_RUN=true` in `.env` and just run:

```bash
cd live_trading_bot
python3 bot.py
```

### What Happens

1. Loads your private key (used to connect to Hyperliquid's public API — no orders placed)
2. Connects to Hyperliquid via WebSocket for live candle and funding data
3. Builds bar history (15m strategy needs ~250 bars = ~62.5 hours to warm up)
4. Runs the strategy on each new bar
5. Logs signals, positions, and equity to `logs/bot.log` and `trading_bot.db`
6. **Does not** place any orders or interact with your wallet balance

### Monitoring

```bash
# Tail logs
tail -f logs/bot.log

# Check the database
sqlite3 trading_bot.db "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 10;"
sqlite3 trading_bot.db "SELECT * FROM signal_records ORDER BY timestamp DESC LIMIT 10;"
```

### Stop

`Ctrl+C`. The bot catches SIGINT and shuts down gracefully (closes WebSocket, database, alert connections).

---

## Live Trading

Live trading places real orders on Hyperliquid with real capital. Make sure you understand the risks before proceeding.

### Pre-Flight Checklist

1. **Dry run passed** — Run dry run for at least 24-48 hours. Verify signals look correct and the bot handles reconnections gracefully.
2. **Funded wallet** — Deposit USDC on Hyperliquid (Arbitrum) into your perps account. The bot uses your perps balance as margin.
3. **Wallet configured** — Either your main wallet PK is in `.env`, or you've set up an API wallet with `HYPERLIQUID_MAIN_WALLET` pointing to your funded main wallet (see API Wallet Setup above).
4. **Leverage is set correctly** — `MAX_LEVERAGE=20` for the 15m strategy (POS=2.00 means each position is 2x equity). Confirm this matches your risk tolerance.
5. **Alerts configured** — Set up at least Telegram or Discord alerts so you get notified of trades and errors.
6. **`.env` is correct** — `DRY_RUN=false` (or unset, defaults to false), `BAR_INTERVAL=15m`.

### Start

```bash
cd live_trading_bot
DRY_RUN=false python3 bot.py
```

Or set in `.env`:
```
DRY_RUN=false
```

### What Happens

1. Loads your private key and connects to Hyperliquid
2. Sets leverage to `MAX_LEVERAGE` on all trading pairs
3. Connects via WebSocket for live data
4. Runs the strategy on each new bar
5. **Places real orders** through Hyperliquid's API (market orders, IOC)
6. Logs everything to `logs/bot.log` and `trading_bot.db`

### Monitoring

```bash
# Tail logs (JSON format)
tail -f logs/bot.log

# Recent trades
sqlite3 trading_bot.db "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 10;"

# Recent signals
sqlite3 trading_bot.db "SELECT * FROM signal_records ORDER BY timestamp DESC LIMIT 10;"

# Trade count today
sqlite3 trading_bot.db "SELECT COUNT(*) FROM trades WHERE date(timestamp) = date('now');"

# Daily PnL
sqlite3 trading_bot.db "SELECT SUM(pnl) FROM trades WHERE date(timestamp) = date('now');"
```

### Stop

`Ctrl+C`. The bot catches SIGINT and shuts down gracefully. Open positions remain open — they are NOT closed automatically on shutdown. You can close them manually on Hyperliquid.

---

## Backtesting

Run historical backtests against saved candle data to evaluate strategy performance before risking capital.

### From `live_trading_bot/` directory

```bash
cd live_trading_bot

# 15m strategy (recommended)
python3 backtest/backtest_interval.py --interval 15m --strategy strategies.strategy_15m

# 5m strategy
python3 backtest/backtest_interval.py --interval 5m --strategy strategies.strategy_5m

# 1m strategy
python3 backtest/backtest_interval.py --interval 1m --strategy strategies.strategy_1m

# Download fresh data then backtest
python3 backtest/backtest_interval.py --interval 15m --strategy strategies.strategy_15m --download --hours 1080
```

### Parameter Tuning

```bash
make -C live_trading_bot tune
```

Downloads 15m candle data, runs single-parameter sweeps → secondary sweeps → forward stepwise accumulation → adaptive multi-parameter grid → OOS validation with 8 workers. All results go to `param_snapshots` (audit trail) and the single best to `active_params` — the strategy reads from this table at startup. No JSON config files involved.

See `results.md` for full tuning results and strategy comparison.

---

## Testing & Validation

### Golden-Master Snapshot Testing

Run the strategy against historical data and save a snapshot. After code changes, re-run and compare to verify behavior is unchanged.

```bash
cd live_trading_bot

# Generate a baseline snapshot
uv run python backtest/backtest_interval.py \
  --interval 15m --strategy strategies.strategy_15m \
  --snapshot harness/baselines/baseline_15m.json

# After changes, generate a new snapshot and compare
uv run python backtest/backtest_interval.py \
  --interval 15m --strategy strategies.strategy_15m \
  --snapshot /tmp/after_change.json

uv run python harness/compare.py harness/baselines/baseline_15m.json /tmp/after_change.json
```

Output is `PASS` (identical) or `FAIL` with the first divergent signal/trade.

### Side-by-Side Live vs Dry-Run

Run multiple bot instances in parallel on 1m intervals to compare live execution against dry-run simulation.

```bash
cd live_trading_bot

# 2 dry-run + 1 live instance for 5 minutes
uv run python harness/side_by_side.py --duration 5m --dry-runs 2 --live-runs 1
```

This launches separate bot processes, waits for the duration, then compares signal agreement across instances. Live requires `DRY_RUN=false` credentials configured.

---

## Troubleshooting

### "HYPERLIQUID_PRIVATE_KEY not set"
Private key is required even in dry run (used for API authentication). Set it in `.env` or as an environment variable.

### "ModuleNotFoundError: No module named 'config'"
Make sure you're running from inside `live_trading_bot/`, not the repo root. The bot uses relative imports within its directory.

### Bot starts but no signals appear
The strategy needs warm-up bars loaded at startup. `LOOKBACK_BARS` is auto-derived from `BAR_INTERVAL` (1m=1000, 5m=1000, 15m=500). If you override `LOOKBACK_BARS`, it must be at least `LONG_WINDOW + 1` for the active strategy (1m needs 721+, 15m needs 145+).

### Reconnection issues
The bot auto-reconnects on WebSocket disconnect with exponential backoff (1s → 60s max). If it can't reconnect after 60s, it logs an error and keeps trying.

### Database locked
Only one bot instance should use `trading_bot.db` at a time. If you see "database is locked" errors, another process is using the file.

### Ctrl+C doesn't stop the bot
This is a known issue with the websockets library blocking on read. Press Ctrl+C twice, or kill the process: `pkill -f "python3 bot.py"`.
