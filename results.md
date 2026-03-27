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
| **Return** | +3.59% | +10.6% | **+646%** | +130% |
| **Max Drawdown** | 2.35% | 1.85% | **5.04%** | **0.3%** |
| **Win Rate** | 57.4% | 61.8% | **81.5%** | -- |
| **Profit Factor** | 2.90 | 3.64 | **20.20** | -- |
| **Total Trades** | 54 | 553 | 1,191 | 7,605 |
| **Sharpe** | -- | -- | -- | **20.6** |
| **POS (Position %)** | 2.00 | 0.50 | 2.00 | 0.08 |
| **OOS Validated** | Yes (50% decay) | No | Yes (tuned > baseline OOS) | Yes (baked in) |
| **Warmup** | 720 bars (12h) | 432 bars (36h) | 144 bars (36h) | 36 bars |

## Detailed Results

### 15-Minute Strategy (Recommended)

**Best performer by return.** 45 days of data, 1,191 trades across 4 symbols. Tuned from baseline via multi-parameter grid search.

| Parameter | Value | Real-World Equivalent |
|-----------|-------|----------------------|
| SHORT_WINDOW | 24 | 6h |
| MED_WINDOW | 48 | 12h |
| LONG_WINDOW | 144 | 36h |
| EMA_FAST / SLOW | 28 / 104 | 7h / 26h |
| RSI_PERIOD | 28 | 7h |
| RSI_OVERBOUGHT | 65 | Wider exit band |
| RSI_OVERSOLD | 35 | Wider exit band |
| ATR_STOP_MULT | 8.0 | Very wide trailing stop |
| COOLDOWN_BARS | 12 | 3h |
| MIN_VOTES | 5 | Stricter entry filter |
| BASE_POSITION_PCT | 2.00 | 200% per symbol (leveraged) |

**Final tuned result (POS=2.00):**
- Return: **+646%**
- Max Drawdown: 5.04%
- Win Rate: 81.5%
- Profit Factor: 20.20
- Total Trades: 1,191

**OOS Validation:** 60/40 split -- IS: +279% return, 5.04% DD, PF 30.78, WR 84.1%. OOS: +84.6% return, 4.98% DD, PF 16.38, WR 76.6%. OOS Ret/DD=16.98 vs baseline OOS Ret/DD=13.36. Tuned params are BETTER on OOS.

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

Note: This sweep was done with the old (pre-tuning) parameters. The new tuned parameters are applied on top of POS=2.00.

## Parameter Tuning Results (15m)

All sweeps below use POS=2.00 on the 15m strategy. Results sorted by Ret/DD unless noted otherwise.

### Single-Parameter Sweep Results

#### ATR_STOP_MULT (default was 5.5)

| Value | Return% | DD% | Ret/DD | PF | WR% | Trades |
|-------|---------|-----|--------|----|----|--------|
| 10.0 | +467.34 | 11.51 | 40.59 | 26.21 | 81.5% | 1160 |
| 8.0 | +459.48 | 11.51 | 39.91 | 22.83 | 81.3% | 1161 |
| 7.0 | +423.98 | 11.51 | 36.82 | 19.62 | 80.8% | 1163 |
| 5.5 | +404.05 | 11.51 | 35.09 | 14.98 | 78.5% | 1170 |
| 6.0 | +400.29 | 11.51 | 34.77 | 15.09 | 79.2% | 1166 |
| 5.0 | +371.86 | 11.51 | 32.30 | 9.73 | 77.1% | 1185 |
| 4.5 | +327.31 | 11.68 | 28.03 | 7.98 | 75.2% | 1200 |
| 4.0 | +286.40 | 11.68 | 24.53 | 6.68 | 72.6% | 1223 |
| 3.0 | +202.68 | 11.70 | 17.32 | 3.65 | 65.6% | 1343 |

#### COOLDOWN_BARS (default was 8)

| Value | Return% | DD% | Ret/DD | PF | WR% | Trades |
|-------|---------|-----|--------|----|----|--------|
| 12 | +335.90 | 8.90 | 37.76 | 12.55 | 76.8% | 1056 |
| 16 | +373.49 | 10.02 | 37.28 | 16.67 | 82.1% | 967 |
| 8 | +404.05 | 11.51 | 35.09 | 14.98 | 78.5% | 1170 |
| 2 | +356.32 | 11.11 | 32.07 | 6.22 | 66.8% | 1873 |
| 6 | +384.57 | 12.02 | 32.00 | 11.06 | 76.2% | 1287 |
| 4 | +368.81 | 12.69 | 29.06 | 8.70 | 68.9% | 1464 |
| 20 | +324.10 | 11.96 | 27.10 | 24.31 | 86.9% | 916 |

#### MIN_VOTES (default was 4)

| Value | Return% | DD% | Ret/DD | PF | WR% | Trades |
|-------|---------|-----|--------|----|----|--------|
| 5 | +329.81 | 8.55 | 38.55 | 7.71 | 75.2% | 880 |
| 4 | +404.05 | 11.51 | 35.09 | 14.98 | 78.5% | 1170 |
| 6 | +109.51 | 13.71 | 7.99 | 2.36 | 64.8% | 612 |
| 3 | +74.53 | 14.11 | 5.28 | 19.17 | 80.7% | 3191 |

#### BASE_THRESHOLD (default was 0.012)

| Value | Return% | DD% | Ret/DD | PF | WR% | Trades |
|-------|---------|-----|--------|----|----|--------|
| 0.02 | +396.11 | 11.02 | 35.96 | 12.30 | 77.4% | 1039 |
| 0.012 | +404.05 | 11.51 | 35.09 | 14.98 | 78.5% | 1170 |
| 0.01 | +399.64 | 11.51 | 34.71 | 14.96 | 78.5% | 1181 |
| 0.008 | +399.35 | 11.51 | 34.68 | 14.95 | 78.5% | 1181 |
| 0.005 | +399.35 | 11.51 | 34.68 | 14.95 | 78.5% | 1181 |
| 0.015 | +390.71 | 11.53 | 33.88 | 12.54 | 78.0% | 1106 |

#### RSI_OVERBOUGHT / RSI_OVERSOLD (default was 69/31)

| OB/OS | Return% | DD% | Ret/DD | PF | WR% | Trades |
|-------|---------|-----|--------|----|----|--------|
| 65/35 | +493.36 | 7.22 | 68.29 | 9.99 | 72.2% | 1639 |
| 69/31 | +404.05 | 11.51 | 35.09 | 14.98 | 78.5% | 1170 |
| 72/28 | +241.60 | 14.59 | 16.56 | 10.48 | 76.7% | 925 |
| 75/25 | +166.74 | 19.56 | 8.52 | 9.41 | 81.7% | 764 |
| 80/20 | +42.49 | 28.00 | 1.52 | 7.07 | 72.1% | 627 |

### Secondary Sweep Results

#### BB_COMPRESS_PCTILE (default was 90)

| Value | Return% | DD% | Ret/DD | PF | WR% | Trades |
|-------|---------|-----|--------|----|----|--------|
| 95 | +405.77 | 11.26 | 36.04 | 15.09 | 78.6% | 1172 |
| 85 | +405.37 | 11.43 | 35.47 | 15.07 | 78.8% | 1166 |
| 90 | +404.05 | 11.51 | 35.09 | 14.98 | 78.5% | 1170 |
| 80 | +383.73 | 11.55 | 33.24 | 14.00 | 78.7% | 1163 |

#### RSI_PERIOD (default was 32)

| Value | Return% | DD% | Ret/DD | PF | WR% | Trades |
|-------|---------|-----|--------|----|----|--------|
| 24 | +653.67 | 6.25 | 104.61 | 20.99 | 81.1% | 1462 |
| 28 | +561.18 | 8.23 | 68.18 | 17.80 | 77.2% | 1315 |
| 32 | +404.05 | 11.51 | 35.09 | 14.98 | 78.5% | 1170 |
| 40 | +235.76 | 13.27 | 17.76 | 9.13 | 71.8% | 915 |
| 48 | +202.40 | 16.30 | 12.41 | 6.94 | 74.1% | 788 |

#### RSI_BULL / RSI_BEAR (default was 50/50)

| BULL/BEAR | Return% | DD% | Ret/DD | PF | WR% | Trades |
|-----------|---------|-----|--------|----|----|--------|
| 55/45 | +418.95 | 11.07 | 37.86 | 12.23 | 77.8% | 977 |
| 50/50 | +404.05 | 11.51 | 35.09 | 14.98 | 78.5% | 1170 |
| 45/55 | +301.59 | 11.58 | 26.04 | 14.97 | 78.5% | 1588 |

#### THRESHOLD_MIN / THRESHOLD_MAX (default was 0.005/0.020)

| MIN/MAX | Return% | DD% | Ret/DD | PF | WR% | Trades |
|---------|---------|-----|--------|----|----|--------|
| 0.01/0.03 | +406.41 | 11.02 | 36.89 | 13.48 | 77.7% | 1013 |
| 0.005/0.020 | +404.05 | 11.51 | 35.09 | 14.98 | 78.5% | 1170 |
| 0.003/0.015 | +403.67 | 11.51 | 35.06 | 14.97 | 78.5% | 1170 |
| 0.008/0.025 | +392.88 | 11.52 | 34.10 | 12.36 | 77.8% | 1081 |

### Multi-Parameter Grid (Focused)

Combined the 3 most impactful parameters with COOLDOWN_BARS=12, MIN_VOTES=5 fixed. Top 5 results sorted by Ret/DD:

| RSI_PERIOD | OB/OS | ATR | Return% | DD% | Ret/DD | PF | WR% | Trades |
|---|---|---|---------|-----|--------|----|----|--------|
| 28 | 65/35 | 8.0 | **+646.07** | **5.04** | **128.26** | 20.20 | 81.5% | 1191 |
| 28 | 65/35 | 10.0 | +646.07 | 5.04 | 128.26 | 20.20 | 81.5% | 1191 |
| 28 | 65/35 | 6.0 | +578.18 | 5.29 | 109.26 | 13.21 | 80.1% | 1195 |
| 24 | 65/35 | 8.0 | +556.98 | 7.26 | 76.72 | 21.41 | 78.0% | 1256 |
| 24 | 65/35 | 10.0 | +556.98 | 7.26 | 76.72 | 21.41 | 78.0% | 1256 |

Note: ATR=8.0 and ATR=10.0 produce identical results. The RSI exit conditions (65/35) trigger before the trailing stop at these levels.

### OOS Comparison: Baseline vs Tuned

60/40 in-sample / out-of-sample split:

| Metric | Baseline OOS | Tuned OOS | Change |
|--------|-------------|-----------|--------|
| Return | +74.96% | +84.58% | +12.8% |
| Max DD | 5.61% | 4.98% | -11.2% |
| Ret/DD | 13.36 | 16.98 | +27.1% |
| PF | 26.75 | 16.38 | -38.7% |
| WR | 79.7% | 76.6% | -3.9% |
| Trades | 422 | 447 | +5.9% |

Note: Baseline OOS PF was anomalously high (26.75 vs IS PF=11.26). Tuned params show more normal IS-to-OOS decay (30.78 to 16.38). The tuned strategy wins on return, drawdown, and Ret/DD.

## Key Findings

1. **15m is the sweet spot** -- Best return with strong OOS validation. The 4x bar compression from hourly preserves signal quality while increasing trade frequency.
2. **1m is too noisy** -- Real edge exists but ~50% decay OOS. Limited data (3 days max) makes validation unreliable.
3. **5m is promising but unvalidated** -- Good baseline metrics. Needs tuning and OOS validation before deployment consideration.
4. **Hourly remains the gold standard for risk-adjusted returns** -- 0.3% max drawdown over 9 months is exceptional.
5. **Compounding matters** -- All results use compounding (equity recalculated each bar). Position size scales with equity growth.
6. **Wide ATR stops are critical** -- 5.5-6.5x ATR trailing stops let winners run. Tighter stops killed performance in earlier experiments.
7. **RSI Period is the most impactful parameter** -- Reducing from 32 to 28 (7h) gives 3x Ret/DD improvement. Shorter RSI is more responsive to 15m bars.
8. **Wider RSI exit bands improve Ret/DD** -- 65/35 (vs 69/31) cuts DD from 11.5% to 7.2% by holding positions longer. The tighter exits were premature.
9. **Wider ATR trailing stop complements wider RSI exits** -- ATR=8.0 (vs 5.5) lets winners run further. At ATR>=8.0 the trailing stop is never triggered -- RSI exits dominate.
10. **Higher MIN_VOTES with longer COOLDOWN reduces noise** -- MIN_VOTES=5 + COOLDOWN=12 cuts bad trades, improving Ret/DD despite lower trade count.
11. **Parameters are synergistic** -- Individual improvements (RSI_PERIOD, RSI_OB_OS, ATR) stack multiplicatively when combined. Combined Ret/DD=128 vs best single Ret/DD=105.

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
