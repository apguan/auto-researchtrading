# autotrader-cfi

Autonomous trading strategy research on **CFI-anchored perpetual futures**.

## Context

This is a continuation of the autotrader project, now running on CFI (Custom Financial Index) perp pricing instead of spot-referenced perps. The six-signal strategy achieved Sharpe 21.4 on spot perps. On CFI perps, it scores **Sharpe 21.1** (val) and **18.6** (test, OOS).

The focus of this run is NOT signal discovery (already solved) — it's **exit strategy optimization, signal check intervals, and dynamic stop losses**. The core insight: the real edge isn't just which signals to use, but how to exit, when to check, and how to set stops dynamically.

## Baseline

```
CFI Perp Baseline (current strategy.py):
  Val:  score=21.124  sharpe=21.124  return=+79.7%  dd=0.43%
  Test: score=18.583  sharpe=18.583  return=+63.8%  dd=0.28%
```

## Setup

1. **Branch**: `git checkout -b autotrader/cfi-exits-<tag>` from council/adversarial-pass-1.
2. **Read**: `strategy.py`, `prepare_cfi.py`, `backtest_cfi.py`, `prepare.py`.
3. **Verify data**: `ls ~/.cache/autotrader/cfi_data/`
4. **Initialize**: `echo -e "commit\tscore\tsharpe\tmax_dd\tstatus\tdescription" > results_cfi.tsv`
5. **Go**.

## Experimentation

Each experiment runs: `uv run backtest_cfi.py`

The backtest uses CFI perp mark prices (spot * exp(vol_mult * CFI_index)) with excess funding rates (rate - k_fixed_hr). Same symbols (BTC, ETH, SOL), same intervals (1h), same scoring.

**What you CAN do:**
- Modify `strategy.py` — the ONLY file you edit.
- Focus on exit mechanisms, stop-loss dynamics, signal timing.

**What you CANNOT do:**
- Modify `prepare.py`, `prepare_cfi.py`, `backtest_cfi.py`.
- Install new packages.
- Change the 6-signal entry ensemble (keep 4/6 majority vote).

**One parameter per experiment. No exceptions.**

## Output format

```
grep "^score:" run.log
```

Use the VAL SET score as the optimization target.

## Results TSV

Log to `results_cfi.tsv`:
```
commit	score	sharpe	max_dd	status	description
```

## The experiment loop

LOOP FOREVER:

1. Look at git state and results_cfi.tsv
2. Modify `strategy.py` — one parameter change focused on exits/stops/intervals
3. git commit
4. `uv run backtest_cfi.py > run.log 2>&1`
5. `grep "^score:\|^sharpe:\|^max_drawdown_pct:" run.log` (use VAL SET line)
6. If empty → crashed. `tail -n 50 run.log`, fix.
7. Record in results_cfi.tsv
8. If VAL score improved: keep
9. If worse: `git reset --hard HEAD~1`

## Strategy Research Directions

### Tier 1 — Exit Strategy (HIGHEST PRIORITY)

These are the primary research directions. The user believes exit strategy is the key differentiator:

1. **Dynamic ATR stop-loss based on vol regime**
   - ATR multiplier scales with realized volatility
   - Low vol → tighter stops (3.5x ATR), high vol → wider (7.5x ATR)
   - Linear interpolation between regimes
   - Test: vary regime thresholds and ATR range

2. **Trailing profit target that tightens**
   - As unrealized PnL grows, tighten the trailing stop
   - Tightening rate: reduce ATR mult by X per 1% unrealized gain
   - Floor: minimum ATR mult (never tighter than Y)
   - Note: with 8% position size, per-trade PnL is small — may need larger positions first

3. **Time-based exits**
   - Maximum hold duration before forced reassessment
   - If position held > N bars without new signal confirmation → exit
   - Prevents stale positions in ranging markets

4. **Partial exit laddering**
   - Scale out at multiple profit targets (e.g., 25% at 2x ATR, 50% at 4x ATR, 100% at signal flip)
   - Requires tracking position layers

5. **RSI exit calibration by regime**
   - Current: fixed RSI 69/31 exit thresholds
   - Dynamic: RSI exit thresholds scale with volatility or trend strength
   - In strong trends: widen exit thresholds (75/25)
   - In ranging: tighten exit thresholds (60/40)

### Tier 2 — Signal Interval Optimization

6. **Multi-timeframe signal checking**
   - Use different lookback windows for fast vs slow signals
   - Fast signals (momentum, vshort): shorter window for responsiveness
   - Slow signals (EMA, MACD): longer window for noise filtering
   - Can simulate sub-bar by using different lookback periods

7. **Adaptive interval selection**
   - Signal check frequency based on volatility
   - High vol → check more frequently (shorter lookback)
   - Low vol → check less frequently (longer lookback)
   - Implement as dynamic window lengths, not actual interval changes

8. **Signal staleness decay**
   - Reduce vote weight for signals unchanged over N bars
   - A signal that's been bullish for 100 bars is less informative than one that just flipped
   - Weight = 1.0 / (1 + bars_since_flip * decay_rate)

### Tier 3 — Dynamic Stop Losses

9. **Regime-aware SL**
   - Classify vol into low/normal/high using percentiles
   - Different stop parameters per regime
   - Low vol: tight ATR, high min_votes; High vol: wide ATR, lower threshold

10. **Asymmetric SL (long vs short)**
    - Crypto has upside skew — shorts should have tighter stops
    - Long stop: ATR_STOP_MULT * atr (current)
    - Short stop: ATR_STOP_MULT * SHORT_STOP_MULT * atr (tighter)

11. **Momentum-adjusted SL**
    - When momentum is fading (e.g., momentum return declining), tighten stop
    - When momentum is accelerating, widen stop
    - Adjust ATR mult by momentum derivative

### Tier 4 — Position Sizing Refinements

12. **Kelly-based position sizing**
    - Use recent win rate and profit factor to compute optimal size
    - Kelly fraction = (pf * wr - (1-wr)) / pf
    - Apply fractional Kelly (e.g., 0.25x Kelly) for safety

13. **Volatility-normalized sizing**
    - Target constant dollar risk per trade
    - Size = target_risk / (ATR * ATR_STOP_MULT)
    - Prevents oversized positions in high vol

## Council Mode

After 5 consecutive no-improvement experiments, enter Council Mode as specified in program.md. Generate proposals from different philosophies, anonymize, peer review, execute top-ranked.

## NEVER STOP

Once the loop begins, run autonomously. If you stall, think harder. Focus on exits first, then intervals, then dynamic SLs.
