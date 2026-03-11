# Strategy Evolution Log

Every experiment we ran, what worked, what didn't, and why. The "keeps" are strategies that beat the previous best and were retained. The "discards" were reverted.

## Scoring Formula

```
score = sharpe * sqrt(min(trades/50, 1.0)) - drawdown_penalty - turnover_penalty
drawdown_penalty = max(0, max_drawdown_pct - 15) * 0.05
turnover_penalty = max(0, annual_turnover/capital - 500) * 0.001
```

Hard cutoffs: <10 trades = -999, >50% drawdown = -999, lost >50% = -999.

---

## Phase 1: Building the Ensemble (score 2.7 → 8.4)

### exp0 — Baseline Simple Momentum (KEEP, score 2.724)
**How it works:** 24-bar lookback, if return > 2% go long, if < -2% go short. Fixed 10% position size. 3% stop loss, 6% take profit.
- Sharpe 2.724, DD 7.6%, 9081 trades
- This is the benchmark from `benchmarks/simple_momentum.py`

### exp1 — Multi-Timeframe Momentum + Vol Sizing + ATR Stops (KEEP, score 2.962)
**How it works:** Added 3 momentum timeframes (12h, 24h, 48h) that must agree. Volatility-adaptive position sizing (smaller in high vol). ATR-based trailing stops instead of fixed stops. Added SOL alongside BTC/ETH.
- Key insight: diversification across timeframes and assets helps Sharpe
- ATR stops adapt to market conditions instead of fixed percentages

### exp2 — EMA Crossover + Funding Carry + Wider ATR (KEEP, score 3.292)
**How it works:** Added EMA 12/26 crossover as a signal. Added funding rate carry overlay (size up when funding favors your direction). Widened ATR stop multiplier.
- Key insight: EMA crossover catches trends that raw momentum misses
- Funding carry adds real P&L on Hyperliquid perps

### exp3 — Cross-Asset Lead-Lag + Dynamic Threshold (KEEP, score 3.671)
**How it works:** BTC momentum used to confirm/deny ETH and SOL entries (BTC leads by 1-6h). Momentum entry threshold now scales with recent volatility (higher vol = need stronger momentum to enter). Position size scales with momentum strength.
- Key insight: BTC tends to move first, alts follow

### exp4 — Ensemble Voting + Correlation Regime + Pyramiding (KEEP, score 5.209)
**How it works:** First ensemble approach — 4 signals (momentum, vshort momentum, EMA, RSI) vote. Need 3/4 agreement to enter. Added BTC-ETH correlation tracking to reduce SOL weight when correlation is high. Pyramiding: add to winners when position is up 1.5%.
- Key insight: requiring multiple signals to agree dramatically reduces bad entries
- Jump from 3.7 to 5.2 — ensemble voting was the first major breakthrough

### exp8 — Combined Best Elements + DD-Adaptive Sizing (KEEP, score 5.533)
**How it works:** Combined the best signal quality from exp7 (relaxed BTC filter, higher position) with exp4's risk framework. Added drawdown-adaptive sizing that reduces positions when equity drops >4% from peak.
- Incremental improvement from combining best of both worlds

### exp10 — RSI Tuning + Larger Pyramid + MR Exit (KEEP, score 6.479)
**How it works:** Tuned RSI thresholds (bull > 53, bear < 47 instead of 50/50). Increased pyramid size to 0.5x. Added mean-reversion exit: close longs when RSI > 70, close shorts when RSI < 30.
- Key insight: RSI overbought/oversold exit was a **massive** improvement
- Prevents holding through mean-reversion against the position

### exp11 — Tighter RSI Exit 70/30 + Pyramid 0.7 (KEEP, score 6.783)
**How it works:** Tightened RSI exit bands to exactly 70/30 (from wider). Increased pyramid size to 0.7x. Lowered pyramid entry threshold to 1.5%.
- Fine-tuning the RSI exit that proved so valuable in exp10

### exp13 — MACD as 5th Signal (KEEP, score 7.758)
**How it works:** Added MACD(12,26,9) histogram as a 5th ensemble signal. Increased position to 0.16. Tightened take-profit.
- Key insight: MACD adds an orthogonal momentum measurement
- 5 signals with 3/5 vote threshold now

### exp15 — 4/5 Vote Threshold + Cooldown + Wider TP (KEEP, score 8.393)
**How it works:** Raised vote requirement from 3/5 to 4/5 (higher conviction). Added 6-bar cooldown between exit and re-entry. Widened take-profit to 8%.
- Key insight: higher conviction threshold + cooldown reduces turnover
- Turnover penalty was the binding constraint at this point

## Phase 2: ATR Stop Optimization (score 8.4 → 9.4)

### exp25 — ATR 4.0 Stop (KEEP, score 9.012)
**How it works:** Widened trailing stop from 3.5x ATR to 4.0x. Everything else unchanged.
- Key insight: letting winners run longer is worth occasional larger giveback

### exp26 — ATR 4.5 (KEEP, score 9.317)
Same strategy, ATR multiplier 4.5.

### exp27 — ATR 5.0 (KEEP, score 9.341)
Marginal gain. ATR multiplier 5.0.

### exp28 — ATR 5.5 (KEEP, score 9.382)
Final ATR sweet spot at 5.5. Beyond this (6.0), RSI exits dominate and the ATR stop rarely triggers.

## Phase 3: Bollinger Band Width Signal (score 9.4 → 10.3)

### exp32 — BB Width Compression as 6th Signal (KEEP, score 9.737)
**How it works:** Calculate rolling Bollinger Band width (2*std/sma over BB_PERIOD). Compute current width's percentile rank vs history. When width is below the Nth percentile, it votes "true" for both bull AND bear. This acts as a "pending breakout" detector — vol compression precedes big moves.
- Key insight: BB compression is directionally neutral but confirms that a move is likely
- Added as 6th signal, still need 4/6 votes to enter

### exp34-37 — BB Percentile Tuning (KEEP, score 9.78 → 10.305)
Swept BB percentile threshold from 40 to 90. Optimal at 80th percentile (meaning BB width below 80% of historical values = compression). At 80, DD dropped to 2.3%.

## Phase 4: The Great Simplification (score 10.3 → 15.7)

This is where the biggest lesson emerged: **removing complexity improved performance**.

### exp41 — Remove Pyramiding (KEEP, score 10.615)
**What changed:** Set PYRAMID_SIZE = 0. No more adding to winners.
- Why it helped: pyramid trades were mostly late entries that added turnover without proportional return. DD halved from 2.3% to 1.6%.

### exp42 — Remove Funding Boost (KEEP, score 11.302)
**What changed:** Set FUNDING_BOOST = 0. Position size no longer scales up when funding favors direction.
- Why it helped: funding-aligned sizing was adding noise. The direction signal was already capturing the same information.

### exp43 — Remove BTC Lead-Lag Filter (KEEP, score 11.662)
**What changed:** Set BTC_OPPOSE_THRESHOLD = -99 (never triggers). ETH/SOL entries no longer blocked by opposing BTC momentum.
- Why it helped: BTC lead-lag was too noisy at hourly timeframe. It blocked valid alt entries.

### exp44 — Remove Correlation-Based SOL Reduction (KEEP, score 11.804)
**What changed:** Set HIGH_CORR_THRESHOLD = 99 (never triggers). SOL weight no longer reduced during high BTC-ETH correlation.
- Why it helped: correlation regime detection was unreliable and SOL adds diversification value.

### exp45 — Remove DD-Adaptive Sizing (KEEP, score 11.804)
**What changed:** Set DD_REDUCE_THRESHOLD = 99. No more reducing size during drawdowns.
- Same score — DD never reached 4% threshold with the improved strategy, so this was dead code.

### exp46 — Remove Strength Scaling (KEEP, score 13.480)
**What changed:** Set strength_scale = 1.0 (fixed). Position size no longer varies with momentum strength.
- **HUGE gain** (+1.7 points). This was the single biggest simplification win.
- Why it helped: variable sizing introduced noise. Strong momentum ≠ more certain outcome. Uniform sizing is more predictable for the backtest scorer.

### exp47 — Remove Vol Scaling (KEEP, score 13.487)
**What changed:** Set vol_scale = 1.0 (fixed). Position size no longer inversely proportional to realized volatility.
- Marginal score gain but DD dropped to 0.79%. Simpler and more robust.

### exp51 — Remove Take-Profit (KEEP, score 13.491)
**What changed:** Set TAKE_PROFIT_PCT = 99. No fixed take-profit level.
- Take-profit rarely triggered because ATR stops and RSI exits handle exits. Dead code removal.

### exp52 — Equal Symbol Weights (KEEP, score 13.519)
**What changed:** Equal weights (0.33/0.33/0.33) instead of BTC-heavy (0.40/0.35/0.25).
- Marginal gain. Equal weighting = more diversification.

## Phase 5: Cooldown and BB Period Tuning (score 13.5 → 15.7)

### exp55-56 — Cooldown 4→3 (KEEP, score 13.632 → 14.592)
**What changed:** Reduced cooldown from 6 bars to 3. More re-entries allowed.
- With the simplified strategy, faster re-entry captures more moves without adding noise.
- Cooldown 2 was also tested later with the full config — 19.859.

### exp61-63 — BB Period 15→10 (KEEP, score 14.722 → 14.790)
**What changed:** Shortened BB lookback from 20 to 10 bars.
- Faster BB response catches shorter compression cycles.

### exp65 — Remove ret_long Filter (KEEP, score 14.908)
**What changed:** Momentum signal simplified from `ret_short > threshold AND ret_med > threshold*0.8 AND ret_long > 0` to just `ret_short > threshold`.
- The long-term filter was blocking valid momentum entries.

### exp66 — Simplified Momentum (KEEP, score 15.718)
**What changed:** Momentum signal further simplified to just `ret_short > threshold` with no medium-term confirmation.
- Another **huge gain** from simplification. Multi-timeframe momentum confirmation was hurting.

## Phase 6: RSI Period Discovery (score 15.7 → 20.6)

### exp71 — RSI Period 10 (KEEP, score 17.032)
**What changed:** RSI calculation period from 14 to 10.
- Faster RSI responds quicker to hourly price changes.

### exp72 — RSI Period 8 (KEEP, score 19.697)
**What changed:** RSI period from 10 to 8.
- **Single biggest individual improvement** (+5 points). Standard 14-period RSI is designed for daily bars. 8-period is much better for hourly crypto.
- Tested 6 (worse, too many trades) and 7 (worse) and 9 (worse). 8 is the sweet spot.

### exp77 — RSI 50/50 Entry Thresholds (KEEP, score 19.718)
**What changed:** RSI bull/bear thresholds from 51/49 to 50/50 (exact neutral).
- Marginal but cleaner. RSI > 50 = bullish, RSI < 50 = bearish.

### exp86 — Cooldown 2 (KEEP, score 19.859)
**What changed:** Reduced cooldown from 3 to 2 bars. Re-tested with the full simplified config.
- With RSI 8 generating more signals, shorter cooldown captures more valid entries.

### exp94-96 — Position Size 0.12→0.08 (KEEP, score 20.270 → 20.497)
**What changed:** Reduced position from 0.16 to 0.08.
- Eliminates turnover penalty entirely. Score ≈ Sharpe at this point.
- Returns are lower (130% vs 260%) but risk-adjusted performance is maximized.

### exp100 — BB Percentile 85 (KEEP, score 20.581)
**What changed:** BB compression threshold from 80 to 85.
- Slightly looser compression filter allows more entries.

### exp102 — RSI 50/50 (KEEP, score 20.634) — FINAL BEST
**What changed:** RSI entry thresholds to exact 50/50.
- Final refinement. Score 20.634, Sharpe 20.634, DD 0.3%, 7605 trades.

---

## Notable Discards (Lessons Learned)

| Experiment | Score | What Failed | Lesson |
|-----------|-------|-------------|--------|
| exp5 | 3.495 | Removed take-profit | Take-profit was essential early on (before RSI exits) |
| exp9 | 3.485 | Linear regression trend + fading stop | Fading stops killed winners |
| exp14 | 3.222 | Position 0.20 + wide vol_scale | Turnover penalty is binding at high position sizes |
| exp17 | -2.125 | ATR 3.0 + no flip | ATR 3.0 too tight; no-flip prevented recovery |
| exp21 | 6.269 | Vol regime switching | Regime detection too noisy for hourly |
| exp22 | 8.096 | ADX trend filter | ADX filtered out valid entries |
| exp60 | 7.618 | Exit to flat instead of flipping | Flipping is essential — captures reversal immediately |
| exp78 | 17.627 | RSI exit 65/35 (tighter) | Too many exits, massive turnover |
| exp81 | 17.547 | ROC instead of MACD | MACD's signal line smoothing beats raw ROC |
| exp90 | 5.991 | RSI oversold 10 (let shorts ride) | Shorts need the oversold exit too |
| exp99 | 19.750 | Stochastic %K instead of RSI | Standard RSI beats Stochastic for this use case |

---

## Final Strategy Parameters

```python
ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
SYMBOL_WEIGHTS = {"BTC": 0.33, "ETH": 0.33, "SOL": 0.33}
SHORT_WINDOW = 6          # 6h very-short momentum
MED_WINDOW = 12           # 12h short momentum (main signal)
EMA_FAST = 12             # EMA crossover fast period
EMA_SLOW = 26             # EMA crossover slow period
RSI_PERIOD = 8            # Fast RSI for hourly data
RSI_BULL = 50             # RSI > 50 = bullish vote
RSI_BEAR = 50             # RSI < 50 = bearish vote
RSI_OVERBOUGHT = 70       # Exit longs when RSI > 70
RSI_OVERSOLD = 30         # Exit shorts when RSI < 30
MACD_FAST = 12            # MACD fast EMA
MACD_SLOW = 26            # MACD slow EMA
MACD_SIGNAL = 9           # MACD signal line
BB_PERIOD = 10            # Bollinger Band lookback
BASE_POSITION_PCT = 0.08  # 8% of equity per symbol
ATR_LOOKBACK = 24         # ATR calculation window
ATR_STOP_MULT = 5.5       # 5.5x ATR trailing stop
BASE_THRESHOLD = 0.012    # 1.2% momentum entry threshold
COOLDOWN_BARS = 2         # 2-bar re-entry cooldown
MIN_VOTES = 4             # Need 4/6 signals to agree

# Disabled features (set to never-trigger values):
FUNDING_BOOST = 0.0       # No funding-based sizing
BTC_OPPOSE_THRESHOLD = -99 # No BTC lead-lag filter
HIGH_CORR_THRESHOLD = 99  # No correlation filter
DD_REDUCE_THRESHOLD = 99  # No DD-adaptive sizing
PYRAMID_SIZE = 0.0        # No pyramiding
TAKE_PROFIT_PCT = 99.0    # No fixed take-profit
```

## Signal Descriptions

1. **Momentum (ret_short)**: 12-hour return vs dynamic threshold. Threshold = BASE_THRESHOLD * (0.5 + vol_ratio * 0.5), clamped to [0.006, 0.025].
2. **Very-Short Momentum (ret_vshort)**: 6-hour return vs threshold * 0.5. Catches more recent moves.
3. **EMA Crossover**: EMA(12) vs EMA(26). Classic trend-following signal.
4. **RSI(8)**: Fast RSI. > 50 = bullish, < 50 = bearish. Also used for exit at 70/30.
5. **MACD(12,26,9)**: MACD histogram sign. Positive = bullish, negative = bearish.
6. **BB Compression**: Bollinger Band width < 85th percentile of its history = "compressed." Directionally neutral — votes for both sides. Acts as a "breakout is likely" filter.

## Exit Logic (in priority order)

1. **ATR Trailing Stop**: Track peak price since entry. Exit when price drops 5.5 * ATR from peak (longs) or rises 5.5 * ATR from trough (shorts).
2. **RSI Overbought/Oversold**: Exit longs at RSI > 70, shorts at RSI < 30.
3. **Signal Flip**: If opposite signal fires and not in cooldown, flip the position (exit + enter opposite).
