# auto-researchtrading

Autonomous trading strategy research on Hyperliquid perpetual futures, using [Karpathy's autoresearch pattern](https://github.com/karpathy/autoresearch) for strategy discovery. An AI agent autonomously modifies `strategy.py`, backtests each change, and keeps only improvements — no human intervention required.

## Quickstart

### Prerequisites

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Setup

```bash
git clone https://github.com/Nunchi-trade/auto-researchtrading.git
cd auto-researchtrading

# Download historical data (BTC, ETH, SOL hourly OHLCV + funding rates)
# Data is cached to ~/.cache/autotrader/data/ — only needs to run once
uv run prepare.py
```

No API keys are required. Data is fetched from public CryptoCompare and Hyperliquid APIs.

### Run a Backtest

```bash
# Run the current strategy against validation data
uv run backtest.py
```

Output:

```
score:              20.634000
sharpe:             20.634000
total_return_pct:   130.000000
max_drawdown_pct:   0.300000
num_trades:         7605
...
```

### Run All Benchmarks

```bash
# Compare 5 reference strategies against each other
uv run run_benchmarks.py
```

## Running Your Own Experiments

### The Rules

- **Only edit `strategy.py`** — this is the single mutable file
- **Do not modify** `prepare.py`, `backtest.py`, or anything in `benchmarks/`
- **No new dependencies** — only `numpy`, `pandas`, `scipy`, `requests`, `pyarrow`, and stdlib
- **Time budget:** 120 seconds per backtest

### Manual Experiment Loop

```bash
# 1. Create a branch for your experiments
git checkout -b autotrader/myexp

# 2. Edit strategy.py with your idea
#    (modify parameters, signals, entry/exit logic, etc.)

# 3. Run the backtest
uv run backtest.py

# 4. If score improved → keep it
git add strategy.py && git commit -m "exp1: description of change"

# 5. If score got worse → revert
git reset --hard HEAD~1
```

### Autonomous Experiment Loop (with Claude Code)

The intended workflow uses [Claude Code](https://docs.anthropic.com/en/docs/claude-code) with the `/autoresearch` skill to run experiments autonomously:

```bash
# From the repo root, start Claude Code
claude

# Then run the autoresearch skill
/autoresearch
```

The agent will:
1. Read the current strategy and scores
2. Propose and implement a modification to `strategy.py`
3. Run `uv run backtest.py` and parse the score
4. Keep the change if score improved, revert if not
5. Repeat indefinitely until interrupted

See `program.md` for detailed instructions on guiding the autonomous loop.

### Strategy Interface

Your strategy must implement a `Strategy` class in `strategy.py`:

```python
class Strategy:
    def __init__(self):
        # Initialize any tracking state
        pass

    def on_bar(self, bar_data: dict, portfolio: PortfolioState) -> list[Signal]:
        """
        Called once per hourly bar across all symbols.

        Args:
            bar_data: dict of symbol → BarData
                - BarData.close, .open, .high, .low, .volume, .funding_rate
                - BarData.history: DataFrame of last 500 bars
            portfolio: PortfolioState
                - portfolio.cash: available cash
                - portfolio.positions: dict of symbol → signed USD notional

        Returns:
            List of Signal(symbol, target_position, order_type="market")
            target_position is signed USD notional (+long, -short, 0=close)
        """
        return []
```

### Scoring

```
score = sharpe × √(min(trades/50, 1.0)) − drawdown_penalty − turnover_penalty
```

- `sharpe = mean(daily_returns) / std(daily_returns) × √365`
- `drawdown_penalty = max(0, max_drawdown_pct − 15) × 0.05`
- `turnover_penalty = max(0, annual_turnover/capital − 500) × 0.001`
- Hard cutoffs (score = −999): fewer than 10 trades, drawdown > 50%, lost > 50% of capital

### Data Available

| Field | Description |
|-------|-------------|
| `bar_data[symbol].history` | DataFrame of last 500 hourly bars |
| Columns | `timestamp`, `open`, `high`, `low`, `close`, `volume`, `funding_rate` |
| Symbols | BTC, ETH, SOL |
| Validation period | 2024-07-01 to 2025-03-31 |
| Initial capital | $100,000 |
| Fees | 2 bps maker, 5 bps taker, 1 bps slippage |

## Results

### Autotrader: Score Progression

| Experiment | Score | Sharpe | Max DD | Trades | Key Change |
|-----------|-------|--------|--------|--------|------------|
| Baseline (simple_momentum) | 2.724 | 2.724 | 7.6% | 9081 | Starting point |
| exp15 | 8.393 | 8.823 | 3.1% | 2562 | 5-signal ensemble, 4/5 votes, cooldown |
| exp28 | 9.382 | 9.944 | 3.0% | 2545 | ATR 5.5 trailing stop |
| exp37 | 10.305 | 11.125 | 2.3% | 3212 | BB width compression (6th signal) |
| exp42 | 11.302 | 11.886 | 1.4% | 3024 | Remove funding boost |
| exp46 | 13.480 | 14.015 | 1.4% | 3157 | Remove strength scaling |
| exp56 | 14.592 | 14.666 | 0.7% | 4205 | Cooldown 3 |
| exp66 | 15.718 | 15.849 | 0.7% | 4467 | Simplified momentum |
| exp72 | 19.697 | 20.099 | 0.7% | 6283 | **RSI period 8** |
| exp86 | 19.859 | 20.498 | 0.6% | 7534 | Cooldown 2 |
| **exp102** | **20.634** | **20.634** | **0.3%** | **7605** | RSI 50/50, BB 85, position 0.08 |

**Final score: 20.634** (7.6x improvement over baseline)

### Key Discoveries (in order of impact)

1. **RSI period 8** (+5 points) — Faster RSI is much better for hourly crypto data. Standard 14-period is too slow.
2. **Remove strength scaling** (+1.7 points) — Uniform position sizing beats momentum-weighted sizing.
3. **Simplified momentum** (+0.8 points) — Just `ret_short > threshold`, no multi-timeframe confirmation needed.
4. **BB width compression signal** (+0.9 points) — Bollinger Band width percentile as 6th ensemble signal.
5. **ATR 5.5 trailing stop** (+1 point) — Hold winners much longer than conventional 3.5x ATR.
6. **Simplification** (+2 points total) — Removing pyramiding, funding boost, BTC filter, and correlation filter all improved score.
7. **Position size 0.08** (+0.6 points) — Smaller positions eliminate turnover penalty.

### Biggest Lesson: Simplicity Wins

The strongest gains came from *removing* complexity, not adding it. Features that seem smart in theory (BTC lead-lag filter, correlation-based weight adjustment, momentum strength scaling, pyramiding) all hurt performance in practice. The final strategy is remarkably simple.

## Best Strategy Architecture

**6-signal ensemble with 4/6 majority vote:**

| Signal | Bull Condition | Bear Condition |
|--------|---------------|----------------|
| Momentum | 12h return > dynamic threshold | 12h return < -dynamic threshold |
| Very-short momentum | 6h return > threshold*0.5 | 6h return < -threshold*0.5 |
| EMA crossover | EMA(12) > EMA(26) | EMA(12) < EMA(26) |
| RSI(8) | RSI > 50 | RSI < 50 |
| MACD(12,26,9) | MACD histogram > 0 | MACD histogram < 0 |
| BB compression | BB width < 85th percentile | BB width < 85th percentile |

**Exit conditions:**
- ATR trailing stop: 5.5x ATR from peak
- RSI overbought/oversold: exit longs at RSI > 70, exit shorts at RSI < 30
- Signal flip: reverse position when opposing signal fires

**Key parameters:**
- `BASE_POSITION_PCT = 0.08` — Per-symbol position size as fraction of equity
- `COOLDOWN_BARS = 2` — Minimum bars between exit and re-entry
- `RSI_PERIOD = 8` — Fast RSI for hourly crypto
- `ATR_STOP_MULT = 5.5` — Wide trailing stop to let winners run
- Dynamic momentum threshold adapts to realized volatility

## Project Structure

```
├── strategy.py          # The only file you edit — your strategy lives here
├── backtest.py          # Entry point — runs one backtest (fixed, do not modify)
├── prepare.py           # Data download + backtest engine (fixed, do not modify)
├── run_benchmarks.py    # Run all 5 benchmark strategies
├── benchmarks/          # 5 reference strategies for comparison
├── program.md           # Detailed instructions for the autonomous loop
├── STRATEGIES.md        # Complete evolution log of all 103 experiments
└── charts/              # Visualization PNGs of experiment progression
```

## Branches

- `main` — Base scaffold and data pipeline
- `autotrader/mar10c` — Best autotrader strategy (score 20.634)
- `autoresearch/mar10-opus` — LLM training optimization experiments
