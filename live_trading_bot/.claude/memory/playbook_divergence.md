# Divergence investigation playbook

You are investigating a divergence between the live trading bot and the dry-run
bot. This playbook is the institutional memory from the original investigation
on 2026-04-06 that found the position sync race condition. Follow it
step by step.

## Architecture refresher

The Railway service `trading-bot` runs `live_trading_bot/harness/side_by_side.py`,
which spawns subprocess instances of `live_trading_bot/bot.py`:
- 1 instance with `DRY_RUN=false` → real trades on Hyperliquid (`order_id` is a numeric HL OID)
- N instances with `DRY_RUN=true` → simulated trades (`order_id` starts with `dry-`)

Both write to the same Supabase `trades` table. They are distinguished by the
`order_id` prefix.

There is a second Railway service `daily-cron` which **should** run
`data_pipeline/daily_tune.py` for parameter optimization. If you find it
running anything else (especially `bot.py`), that's the bug — see "Critical
failure mode: duplicate live bot" below.

## The 6 most likely failure modes

In rough order of historical likelihood:

### 1. Position sync race (THE bug from 2026-04-06)

**Symptom**: Live bot has many duplicate consecutive entries (no exit between them),
position-aware strategy generates wrong signals, signals diverge from dry.

**Cause**: `_on_bar()` runs as `asyncio.create_task` in `bar_builder.py:122`.
`account_state` fetched at line 223 of `bot.py` becomes stale during processing
because tick callbacks fire concurrently. `sync_positions` later uses the stale
snapshot and wipes positions that were just opened by ticks.

**Fix**: `bot.py` has a `_fresh_sync_positions()` helper that re-fetches
`account_state` immediately before syncing. All 3 sync sites should call this
helper, not `sync_positions(account_state, ...)` with the stale snapshot.

**How to verify the fix is in place**:
```bash
grep -n "_fresh_sync_positions\|sync_positions(account_state" live_trading_bot/bot.py
```
There should be 3 calls to `_fresh_sync_positions()` and ZERO calls passing
`account_state` directly.

**How to verify the bug regressed**:
```sql
-- Run via railway run --service trading-bot:
SELECT
  COUNT(*) FILTER (WHERE pnl IS NULL) AS entries,
  COUNT(*) AS total
FROM trades
WHERE timestamp >= NOW() - INTERVAL '2 hours'
  AND order_id NOT LIKE 'dry-%';
```
Then look for consecutive entries (entry → entry without exit between them) per
symbol. The original bug had 64% of all entries being consecutive duplicates.
A healthy state has < 5%.

### 2. Different bar interval between live and dry

**Symptom**: Live and dry trade at noticeably different cadences. Different
symbols. Different number of trades per hour.

**Cause**: `side_by_side.py` used to hardcode `BAR_INTERVAL=1m`, overriding the
env var. The fix (PR #12) made `--interval` optional and only override the env
when explicitly passed.

**How to verify**:
```bash
grep -n "BAR_INTERVAL" live_trading_bot/harness/side_by_side.py
```
Should show that `env["BAR_INTERVAL"]` is only set if `args.interval` is
truthy. If you see an unconditional `env["BAR_INTERVAL"] = args.interval`, the
bug is back.

Also check Railway env: `BAR_INTERVAL` should be set on the trading-bot
service, and the harness should NOT pass `--interval` in the start command.

### 3. CRITICAL: Duplicate live bot from another service

**Symptom**: Reduce-only errors in logs ("Reduce only order would increase
position"). Infinite loops trying to close a position that another bot already
moved. PnL bleeds despite no obvious bug. Sync clears continue at low rate.

**Cause**: Another Railway service (or local process) is also running
`live_trading_bot/bot.py` against the same Hyperliquid account. They compete
for position state.

**This happened on 2026-04-06**: The `daily-cron` service had no custom start
command in the dashboard and inherited `startCommand = "uv run live_trading_bot/bot.py"`
from `railway.toml`. Both `trading-bot` and `daily-cron` ran live bots
simultaneously.

**How to verify**:
```bash
# Check railway services
railway run --service trading-bot -- env | grep -E 'DRY_RUN|RAILWAY_SERVICE_NAME'
railway run --service daily-cron -- env | grep -E 'DRY_RUN|RAILWAY_SERVICE_NAME' 2>&1
# Look at recent logs from each
railway logs --service daily-cron 2>&1 | grep -E 'Closing position|Entry signal' | tail -20
```
If `daily-cron` (or any non-trading-bot service) shows trading activity in its
logs, that's the bug. **Critical**: pause the offending service before doing
anything else.

The PR #12 fix (require explicit start commands) prevents this from recurring.
Verify it's still in place:
```bash
grep -A1 "\[deploy\]" railway.toml
```
There should be NO `startCommand` line. If there is, someone re-added it.

### 4. Strategy state divergence

**Symptom**: Live and dry trade the same symbols but make opposite directional
decisions on the same bars.

**Cause**: The strategy in `strategy.py` is **position-aware** (lines 132-176).
It branches on `current_pos = portfolio.positions.get(symbol, 0.0)` and
generates different signals for flat vs long vs short. So if live and dry have
even slightly different position state, they generate different signals, which
makes their position state diverge more — a feedback loop.

This is downstream of any other bug that causes position state to drift. If
you see direction disagreements but the sync race rate is low and there's no
duplicate bot, look for:
- Different equity (`DRY_RUN_INITIAL_CAPITAL` for dry vs real account for live)
  causing different `MAX_POSITION_PCT` clamping
- Different param sets loaded from `param_snapshots` table at different times
- Stale `_position_sizes` from a fill that the live exchange returned as PENDING

### 5. Fee recording bug (cosmetic, doesn't affect trading)

**Symptom**: DB shows ~$0 fees for low-coin-count symbols (BTC, ETH) but real
HL fees are higher. The bot's PnL reports look profitable but the actual
account is bleeding.

**Cause**: `bot.py:_record_execution_order` previously had
`fee=order.filled_size * 0.0005` which is `coins * rate`, not `notional * rate`.
For BTC with `filled_size=0.001`, this gave $0 instead of $0.034.

**Fix (PR #11)**: Changed to `order.filled_size * order.avg_fill_price * 0.0005`.

This doesn't cause divergence directly but it makes the divergence look worse
than it is in DB-only reports. Always cross-check with `live_trading_bot/pnl.py
<window>` which queries Hyperliquid directly.

### 6. Stop manager orphan accumulation

**Symptom**: Hyperliquid open orders count grows over time. Eventually new
stops fail to place. Positions get hit with no stop loss.

**Cause**: `StopManager.load_existing_stops` (`live_trading_bot/exchange/stop_manager.py`)
filters out trigger orders for symbols without positions and silently skips
them. So stops left over after a position closes (e.g., from a failed cancel)
accumulate forever.

**How to verify**:
```python
# Run via: railway run --service trading-bot -- python -c "..."
import asyncio
from live_trading_bot.config import get_private_key
from live_trading_bot.exchange.hyperliquid import HyperliquidClient
from live_trading_bot.exchange.types import OrderType

async def main():
    c = HyperliquidClient(private_key=get_private_key())
    state = await c.get_account_state()
    orders = await c.get_open_orders()
    triggers = [o for o in orders if o.order_type == OrderType.TRIGGER]
    pos_syms = set(state.positions.keys())
    orphans = [o for o in triggers if o.symbol not in pos_syms]
    print(f"Triggers: {len(triggers)}, Orphans: {len(orphans)}")
    for o in orphans:
        print(f"  {o.symbol} {o.side.value} @ {o.price} (id {o.id})")
    await c.close()

asyncio.run(main())
```
If there are orphans, they're not actively dangerous (reduce-only) but they
indicate `StopManager` isn't cleaning up.

**Fix is pending**: When `load_existing_stops` runs, it should cancel
trigger orders that don't match a current position. There may also be a
direction-mismatch case (SELL stop on a SHORT position).

## Diagnostic SQL templates

Replace `2 hours` with your window. Run via
`railway run --service trading-bot -- uv run python -c "..."`.

### Live vs dry breakdown
```sql
SELECT
  CASE WHEN order_id LIKE 'dry-%' THEN 'dry' ELSE 'live' END AS kind,
  COUNT(*) AS trades,
  COUNT(*) FILTER (WHERE pnl IS NULL) AS entries,
  COUNT(*) FILTER (WHERE pnl IS NOT NULL) AS exits,
  ROUND(SUM(pnl)::numeric, 2) AS realized_pnl,
  ROUND(SUM(fee)::numeric, 2) AS fees
FROM trades
WHERE timestamp >= NOW() - INTERVAL '2 hours'
GROUP BY kind;
```

### Per-symbol breakdown for live
```sql
SELECT symbol,
  COUNT(*) AS trades,
  COUNT(*) FILTER (WHERE pnl IS NULL) AS entries,
  COUNT(*) FILTER (WHERE pnl IS NOT NULL) AS exits,
  ROUND(SUM(pnl)::numeric, 2) AS realized_pnl
FROM trades
WHERE timestamp >= NOW() - INTERVAL '2 hours'
  AND order_id NOT LIKE 'dry-%'
GROUP BY symbol
ORDER BY symbol;
```

### Find consecutive entries (sync clears) for a specific symbol
```sql
WITH ordered AS (
  SELECT timestamp, side, size, price, pnl,
         LAG(pnl) OVER (ORDER BY timestamp) AS prev_pnl
  FROM trades
  WHERE symbol = 'ALGO'
    AND order_id NOT LIKE 'dry-%'
    AND timestamp >= NOW() - INTERVAL '4 hours'
  ORDER BY timestamp
)
SELECT * FROM ordered WHERE pnl IS NULL AND prev_pnl IS NULL;
```

## Log filters that worked

```bash
# Recent sync clears (the original bug signature)
railway logs --service trading-bot -n 5000 | grep 'Position cleared on sync'

# Entry/exit lifecycle for a symbol
railway logs --service trading-bot -n 5000 | grep -E 'ALGO.*(Entry signal|Closing position|Position cleared)'

# Stop order activity
railway logs --service trading-bot -n 5000 | grep -E 'Stop placed|Stop cancelled|Stop order rejected'

# Reduce-only errors (sign of duplicate bot)
railway logs --service trading-bot -n 5000 | grep 'Reduce only order would increase'

# Reconciliation events
railway logs --service trading-bot -n 5000 | grep -E 'Reconciled|Position cleared on sync'
```

## How to write the fix PR

1. Branch name: `auto-fix/<short-description>` (e.g. `auto-fix/sync-race-regression`)
2. Base branch: **always `harness`**, never `main`
3. Write a regression test that would have caught the bug
4. Run the full test suite: `uv run pytest live_trading_bot/tests/ -v`
5. PR title: short and specific
6. PR body must include:
   - Link to the incident report at `/tmp/incident.md` (or paste it)
   - The metric values that triggered the alert
   - Root cause explanation
   - Why this fix addresses the root cause (not just the symptom)
   - Test plan

## When to give up

If after running through all 6 failure modes you can't identify the cause:

1. Write a PR titled `auto-fix/incident-<date>-investigation-needed`
2. Include the incident report verbatim
3. Include any hypotheses you formed and what you ruled out
4. Stop and let the human take over

Do NOT make speculative changes hoping they'll help. The cost of a wrong fix
in trading code is higher than the cost of waiting for human review.

## Files you'll most often need to read

- `live_trading_bot/bot.py` — main bot loop, `_on_bar`, `_fresh_sync_positions`
- `live_trading_bot/execution/execution_engine.py` — `on_tick`, `sync_positions`, `_close_position`
- `live_trading_bot/exchange/hyperliquid.py` — `place_order`, `_parse_order_result`
- `live_trading_bot/exchange/dry_exchange.py` — comparison reference (always fills correctly)
- `live_trading_bot/harness/side_by_side.py` — the harness that spawns live + dry subprocesses
- `strategy.py` — position-aware signal generation
- `live_trading_bot/data/bar_builder.py` — the `create_task` line that started it all
- `live_trading_bot/exchange/stop_manager.py` — stop loss management

## What to never touch in an auto-fix

- `railway.toml` deployment config
- Any `.env` files
- The Dockerfile (the no-CMD design is intentional)
- Database migrations
- Anything in `data_pipeline/` (separate concern from trading)
