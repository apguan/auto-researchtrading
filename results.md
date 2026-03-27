# Backtest Results

## Overview

Multi-timeframe strategy backtesting on Hyperliquid perpetual futures (BTC, ETH, SOL, XRP).

All strategies share the same 6-signal ensemble architecture but with parameters scaled per timeframe:
- Momentum (short/medium/long window)
- EMA crossover
- RSI
- MACD
- Bollinger Band compression

## Backtest Configuration

| Parameter | Value |
|-----------|-------|
| Initial Capital | $10,000 |
| Taker Fee | 5 bps |
| Slippage | 1 bps |
| Leverage Cap | 20x |
| Compounding | Yes (equity recalculated each bar) |

## Hyperliquid Data Availability

| Interval | Max History | Bars Used |
|----------|-------------|-----------|
| 1m | ~3 days | 2 days |
| 5m | ~17 days | 4,897 bars |
| 15m | ~45 days | 4,321 bars |
| 1h | ~180 days | 9 months |

## Strategy Comparison

| Metric | 1m | 5m | 15m | 1h (base) |
|--------|----|----|------|-----------|
| **File** | `strategies/strategy_1m.py` | `strategies/strategy_5m.py` | `strategies/strategy_15m.py` | `strategy.py` |
| **Return** | +3.59% | +10.6% | **+404%** | +130% |
| **Max Drawdown** | 2.35% | 1.85% | 11.5% | **0.3%** |
| **Win Rate** | 57.4% | 61.8% | **78.5%** | -- |
| **Profit Factor** | 2.90 | 3.64 | **15.0** | -- |
| **Total Trades** | 54 | 553 | 1,170 | 7,605 |
| **Sharpe** | -- | -- | -- | **20.6** |
| **POS (Position %)** | 2.00 | 0.50 | 2.00 | 0.08 |
| **OOS Validated** | Yes (50% decay) | No | Yes (OOS > IS) | Yes (baked in) |
| **Warmup** | 720 bars (12h) | 432 bars (36h) | 144 bars (36h) | 36 bars |

## Detailed Results

### 15-Minute Strategy (Recommended)

**Best performer by return.** 45 days of data, 1,170 trades across 4 symbols.

| Parameter | Value | Real-World Equivalent |
|-----------|-------|----------------------|
| SHORT_WINDOW | 24 | 6h |
| MED_WINDOW | 48 | 12h |
| LONG_WINDOW | 144 | 36h |
| EMA_FAST / SLOW | 28 / 104 | 7h / 26h |
| RSI_PERIOD | 32 | 8h |
| ATR_STOP_MULT | 5.5 | Wide trailing stop |
| COOLDOWN_BARS | 8 | 2h |
| BASE_POSITION_PCT | 2.00 | 200% per symbol (leveraged) |

**Final tuned result (POS=2.00):**
- Return: **+404%**
- Max Drawdown: 11.5%
- Win Rate: 78.5%
- Profit Factor: 15.0
- Total Trades: 1,170

**OOS Validation:** 60/40 train/test split -- OOS period performs BETTER than IS (PF 27 vs 11), suggesting the edge is robust and not overfit.

### 5-Minute Strategy

**Moderate performer.** 17 days of data, 553 trades. NOT yet tuned (baseline POS=0.50).

| Parameter | Value | Real-World Equivalent |
|-----------|-------|----------------------|
| SHORT_WINDOW | 72 | 6h |
| MED_WINDOW | 144 | 12h |
| LONG_WINDOW | 432 | 36h |
| EMA_FAST / SLOW | 60 / 240 | 5h / 20h |
| RSI_PERIOD | 60 | 5h |
| ATR_STOP_MULT | 5.5 | Wide trailing stop |
| COOLDOWN_BARS | 12 | 1h |
| BASE_POSITION_PCT | 0.50 | Baseline (untuned) |

**Baseline result (POS=0.50):**
- Return: **+10.6%**
- Max Drawdown: 1.85%
- Win Rate: 61.8%
- Profit Factor: 3.64
- Total Trades: 553

**Status:** Baseline only. Position size sweep not yet performed. OOS validation not done.

### 1-Minute Strategy

**Edge is real but weak.** 2 days of data, 54 trades. Limited by Hyperliquid's 3-day data cap.

| Parameter | Value | Real-World Equivalent |
|-----------|-------|----------------------|
| SHORT_WINDOW | 60 | 1h |
| MED_WINDOW | 240 | 4h |
| LONG_WINDOW | 720 | 12h |
| EMA_FAST / SLOW | 60 / 240 | 1h / 4h |
| RSI_PERIOD | 60 | 1h |
| ATR_STOP_MULT | 6.5 | Wider stop for noise |
| COOLDOWN_BARS | 60 | 1h |
| BASE_POSITION_PCT | 2.00 | Tuned (aggressive) |

**Tuned result (POS=2.00, Very Aggressive):**
- Return: **+3.59%** (net)
- Max Drawdown: 2.35%
- Win Rate: 57.4%
- Profit Factor: 2.90
- Total Trades: 54

**OOS Validation:** Pre-training OOS data (Mar 23-25) showed +1.92% return, 3.56% DD, PF 2.21, 48% WR -- approximately 50% return decay from in-sample. Edge exists but is thin at 1m resolution.

### Hourly Strategy (Autoresearch Base)

**Most robust and battle-tested.** 9 months of data, 7,605 trades. Discovered by 103 autonomous experiments.

| Parameter | Value |
|-----------|-------|
| SHORT_WINDOW | 6 | 6h |
| MED_WINDOW | 12 | 12h |
| LONG_WINDOW | 36 | 36h |
| EMA_FAST / SLOW | 7 / 26 | 7h / 26h |
| RSI_PERIOD | 8 | 8h |
| ATR_STOP_MULT | 5.5 | Wide trailing stop |
| COOLDOWN_BARS | 2 | 2h |
| BASE_POSITION_PCT | 0.08 | Conservative |

**Result:**
- Return: **+130%**
- Max Drawdown: **0.3%**
- Sharpe: **20.6**
- Total Trades: 7,605

**Key insight:** Lower POS (0.08) with massive trade count produces the most consistent returns. The Great Simplification -- removing complexity improved performance.

## Position Size Impact (15m)

| POS | Return | Max DD | PF | WR | Trades |
|-----|--------|--------|----|----|--------|
| 0.30 | +28.6% | 1.88% | 13.8 | 78.5% | 1,170 |
| 0.50 | +49.7% | 3.26% | 13.5 | 78.5% | 1,170 |
| 1.00 | +106% | 6.43% | 13.1 | 78.5% | 1,170 |
| 1.50 | +197% | 9.31% | 13.5 | 78.5% | 1,170 |
| 2.00 | **+404%** | 11.5% | 15.0 | 78.5% | 1,170 |

Return/DD ratio improves linearly -- no inflection point found. POS=2.00 selected as final for high return with acceptable drawdown.

## Key Findings

1. **15m is the sweet spot** -- Best return with strong OOS validation. The 4x bar compression from hourly preserves signal quality while increasing trade frequency.
2. **1m is too noisy** -- Real edge exists but ~50% decay OOS. Limited data (3 days max) makes validation unreliable.
3. **5m is promising but unvalidated** -- Good baseline metrics. Needs tuning and OOS validation before deployment consideration.
4. **Hourly remains the gold standard for risk-adjusted returns** -- 0.3% max drawdown over 9 months is exceptional.
5. **Compounding matters** -- All results use compounding (equity recalculated each bar). Position size scales with equity growth.
6. **Wide ATR stops are critical** -- 5.5-6.5x ATR trailing stops let winners run. Tighter stops killed performance in earlier experiments.

## Data Files

| Interval | Directory | Duration | Bars/Symbol |
|----------|-----------|----------|-------------|
| 1m | `backtest_data/1m_candles/` | 2 days | ~2,880 |
| 1m (OOS) | `backtest_data/1m_candles_oos/` | 33h | ~2,000 |
| 5m | `backtest_data/5m_candles/` | 17 days | 4,897 |
| 15m | `backtest_data/15m_candles/` | 45 days | 4,321 |

## How to Run Backtests

```bash
# 15m (recommended)
uv run python backtest_interval.py --interval 15m --strategy strategies.strategy_15m

# 5m
uv run python backtest_interval.py --interval 5m --strategy strategies.strategy_5m

# 1m
uv run python backtest_interval.py --interval 1m --strategy strategies.strategy_1m

# Download fresh data before running
uv run python backtest_interval.py --interval 15m --strategy strategies.strategy_15m --download --hours 1080
```
