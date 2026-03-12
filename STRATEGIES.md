# Strategy Evolution Log

Every experiment we ran, what worked, what didn't, and why. The "keeps" are strategies that beat the previous best and were retained. The "discards" were reverted.

## Scoring Formula

```
score = sharpe * sqrt(min(trades / 50, 1.0)) - drawdown_penalty - turnover_penalty

where:
  sharpe        = mean(daily_returns) / std(daily_returns) * sqrt(365)
  drawdown_penalty = max(0, max_drawdown_pct - 15) * 0.05
  turnover_penalty = max(0, annual_turnover / capital - 500) * 0.001

Hard cutoffs (automatic -999):
  num_trades < 10
  max_drawdown > 50%
  final_equity < 50% of initial
```

---

## Mathematical Primitives

These building blocks are referenced throughout the strategies below.

### EMA (Exponential Moving Average)
```
alpha = 2 / (span + 1)
EMA[0] = price[0]
EMA[t] = alpha * price[t] + (1 - alpha) * EMA[t-1]
```

### RSI (Relative Strength Index)
```
deltas[i]  = close[i] - close[i-1]    for last (period+1) closes
gains[i]   = max(deltas[i], 0)
losses[i]  = max(-deltas[i], 0)
avg_gain   = mean(gains)
avg_loss   = mean(losses)
RS         = avg_gain / avg_loss
RSI        = 100 - 100 / (1 + RS)
```

### ATR (Average True Range)
```
TR[i] = max(high[i] - low[i],
            |high[i] - close[i-1]|,
            |low[i] - close[i-1]|)
ATR = mean(TR) over lookback window
```

### MACD (Moving Average Convergence Divergence)
```
MACD_line    = EMA(close, fast_period) - EMA(close, slow_period)
signal_line  = EMA(MACD_line, signal_period)
histogram    = MACD_line - signal_line
```

### Bollinger Band Width Percentile
```
For each bar i in [BB_PERIOD*2 .. N]:
  window     = close[i-BB_PERIOD : i]
  SMA        = mean(window)
  sigma      = std(window)
  BB_width_i = 2 * sigma / SMA

percentile = 100 * count(BB_width <= BB_width_current) / count(BB_width_all)
```

### Realized Volatility
```
log_returns[i] = ln(close[i] / close[i-1])    over last VOL_LOOKBACK bars
realized_vol   = std(log_returns)
```

### Dynamic Momentum Threshold
```
vol_ratio     = realized_vol / TARGET_VOL      (TARGET_VOL = 0.015)
dyn_threshold = BASE_THRESHOLD * (0.5 + vol_ratio * 0.5)
dyn_threshold = clamp(dyn_threshold, 0.006, 0.025)
```

---

## Phase 1: Building the Ensemble (score 2.7 → 8.4)

### exp0 — Baseline Simple Momentum (KEEP, score 2.724)

**Math:**
```
ret_24h = (close[t] - close[t-24]) / close[t-24]

Entry:
  Long  if ret_24h > 0.02
  Short if ret_24h < -0.02

Position size = 0.10 * equity   (fixed 10%)

Exit:
  Stop loss:   PnL < -3%
  Take profit: PnL > +6%
```
- Sharpe 2.724, DD 7.6%, 9081 trades
- Benchmark from `benchmarks/simple_momentum.py`

### exp1 — Multi-Timeframe Momentum + Vol Sizing + ATR Stops (KEEP, score 2.962)

**Math:**
```
ret_12h = (close[t] - close[t-12]) / close[t-12]
ret_24h = (close[t] - close[t-24]) / close[t-24]
ret_48h = (close[t] - close[t-48]) / close[t-48]

Entry (all must agree):
  Long  if ret_12h > threshold AND ret_24h > threshold*0.8 AND ret_48h > 0
  Short if ret_12h < -threshold AND ret_24h < -threshold*0.8 AND ret_48h < 0

Position sizing (vol-adaptive):
  vol_scale = TARGET_VOL / realized_vol
  size = equity * BASE_POSITION_PCT * weight * vol_scale

ATR trailing stop:
  For longs:  stop = peak_price_since_entry - ATR_MULT * ATR
              exit if close < stop
  For shorts: stop = trough_price_since_entry + ATR_MULT * ATR
              exit if close > stop
```
- Added SOL (BTC/ETH/SOL with weights 0.40/0.35/0.25)
- ATR stops adapt to market conditions instead of fixed %

### exp2 — EMA Crossover + Funding Carry + Wider ATR (KEEP, score 3.292)

**Math:**
```
New signal — EMA crossover:
  ema_bull = EMA(close, 12) > EMA(close, 26)
  ema_bear = EMA(close, 12) < EMA(close, 26)

Funding carry overlay:
  avg_funding = mean(funding_rate[-24:])
  funding_mult = 1.0 + FUNDING_BOOST   if funding favors direction
               = 1.0                    otherwise

  "Favors direction" means:
    Going long  AND avg_funding < 0  (shorts pay longs)
    Going short AND avg_funding > 0  (longs pay shorts)

  final_size = base_size * funding_mult
```
- EMA crossover catches trends that raw momentum misses
- Funding carry is real P&L on Hyperliquid perps (you earn the funding rate)

### exp3 — Cross-Asset Lead-Lag + Dynamic Threshold (KEEP, score 3.671)

**Math:**
```
BTC lead-lag filter:
  btc_mom = (BTC_close[t] - BTC_close[t-24]) / BTC_close[t-24]

  For ETH/SOL entries:
    Block bull entry if btc_mom < BTC_OPPOSE_THRESHOLD  (BTC bearish)
    Block bear entry if btc_mom > -BTC_OPPOSE_THRESHOLD (BTC bullish)

Dynamic threshold (replaces fixed 2%):
  vol_ratio = realized_vol / 0.015
  threshold = 0.012 * (0.5 + vol_ratio * 0.5)
  threshold = clamp(threshold, 0.006, 0.025)

  In high vol: threshold rises → need stronger momentum to enter
  In low vol:  threshold falls → enter on weaker signals

Strength-scaled sizing:
  mom_strength = |ret_short| / threshold
  strength_scale = min(mom_strength, 2.0)
  size = base_size * strength_scale
```

### exp4 — Ensemble Voting + Correlation Regime + Pyramiding (KEEP, score 5.209)

**Math:**
```
4-signal ensemble (first version):
  Signal 1: Momentum      → ret_12h > threshold
  Signal 2: V-Short Mom   → ret_6h  > threshold * 0.5
  Signal 3: EMA Crossover → EMA(12) > EMA(26)
  Signal 4: RSI(14)       → RSI > 53 (bull), RSI < 47 (bear)

  bull_votes = count(Signal_i = bullish)
  bear_votes = count(Signal_i = bearish)

  Enter long  if bull_votes >= 3  (out of 4)
  Enter short if bear_votes >= 3  (out of 4)

Correlation regime:
  btc_rets = diff(ln(BTC_close[-72:]))
  eth_rets = diff(ln(ETH_close[-72:]))
  corr = pearson(btc_rets, eth_rets)

  If corr > HIGH_CORR_THRESHOLD:
    SOL_weight *= 0.5   (SOL adds less diversification when correlated)

Pyramiding (add to winners):
  If in_position AND PnL > 1.5%:
    target += base_size * PYRAMID_SIZE   (add to winner)
    Only pyramids once per entry.
```
- **Jump from 3.7 to 5.2** — ensemble voting was the first major breakthrough
- Requiring multiple signals to agree dramatically reduces false entries

### exp8 — Combined Best Elements + DD-Adaptive Sizing (KEEP, score 5.533)

**Math:**
```
Drawdown-adaptive sizing:
  peak_equity = max(equity_history)
  current_dd  = (peak_equity - equity) / peak_equity

  if current_dd > DD_REDUCE_THRESHOLD (4%):
    dd_scale = max(0.5, 1.0 - (current_dd - 0.04) * 5)
  else:
    dd_scale = 1.0

  size = base_size * dd_scale
```
- Combined best signal quality from exp7 with exp4's risk framework

### exp10 — RSI Tuning + Larger Pyramid + MR Exit (KEEP, score 6.479)

**Math:**
```
RSI entry thresholds (tuned):
  rsi_bull = RSI > 53   (instead of 50 — require slight bullish tilt)
  rsi_bear = RSI < 47   (require slight bearish tilt)

Mean-reversion exit (NEW — massive improvement):
  If long  AND RSI > 70:  exit to flat  (overbought)
  If short AND RSI < 30:  exit to flat  (oversold)

  Rationale: RSI > 70 means price has moved far from recent mean.
  The probability of reversion increases. Exit before the pullback.

Pyramid size increased:
  PYRAMID_SIZE = 0.5  (add 50% of base size to winners)
```
- RSI overbought/oversold exit was the second major breakthrough
- Prevents holding through mean-reversion pullbacks

### exp11 — Tighter RSI Exit 70/30 + Pyramid 0.7 (KEEP, score 6.783)

**Math:**
```
RSI exit bands tightened to exact 70/30.
PYRAMID_SIZE = 0.7 (add 70% of base size).
PYRAMID_THRESHOLD = 1.5% (pyramid earlier).
```

### exp13 — MACD as 5th Signal (KEEP, score 7.758)

**Math:**
```
5-signal ensemble:
  Signal 1: Momentum      → ret_12h > dyn_threshold
  Signal 2: V-Short Mom   → ret_6h  > dyn_threshold * 0.5
  Signal 3: EMA Crossover → EMA(12) > EMA(26)
  Signal 4: RSI(14)       → RSI > 53
  Signal 5: MACD(12,26,9) → histogram > 0

  MACD histogram = EMA(close, 12) - EMA(close, 26) - EMA(MACD_line, 9)

  bull_votes = count(signals = bullish)
  Enter if bull_votes >= 3 (out of 5)

  Position increased to 0.16.
```
- MACD adds an orthogonal momentum measurement (rate of change of trend, not just trend)

### exp15 — 4/5 Vote Threshold + Cooldown + Wider TP (KEEP, score 8.393)

**Math:**
```
Higher conviction:
  MIN_VOTES = 4  (out of 5)  — up from 3/5

Cooldown mechanism:
  exit_bar[symbol] = bar_count at time of exit
  in_cooldown = (current_bar - exit_bar[symbol]) < COOLDOWN_BARS

  If in_cooldown: no new entries allowed for that symbol.
  COOLDOWN_BARS = 6

  Rationale: prevents rapid cycling (exit → immediate re-enter)
  which inflates turnover and incurs the turnover penalty.

Take-profit widened to 8%.
```
- Higher conviction + cooldown reduces turnover (the binding constraint at this score level)

## Phase 2: ATR Stop Optimization (score 8.4 → 9.4)

### exp25 — ATR 4.0 Stop (KEEP, score 9.012)

**Math:**
```
ATR trailing stop (widened):
  ATR = mean(True_Range[-24:])

  For longs:
    peak = max(close since entry)
    stop = peak - 4.0 * ATR
    exit if close < stop

  For shorts:
    trough = min(close since entry)
    stop = trough + 4.0 * ATR
    exit if close > stop

Previously ATR_MULT was 3.5. Wider stop lets winners run longer.
```

### exp26-28 — ATR 4.5 → 5.0 → 5.5 (KEEP, score 9.317 → 9.341 → 9.382)

```
ATR_STOP_MULT swept: 4.5, 5.0, 5.5
Sweet spot at 5.5.

At 6.0+, RSI overbought/oversold exits trigger first in almost all cases,
making the ATR stop redundant. 5.5 is the last value where ATR stops
still contribute meaningful exits.
```

## Phase 3: Bollinger Band Width Signal (score 9.4 → 10.3)

### exp32 — BB Width Compression as 6th Signal (KEEP, score 9.737)

**Math:**
```
Bollinger Band width calculation:
  For each bar i:
    window = close[i - BB_PERIOD : i]     (BB_PERIOD = 20 initially)
    SMA_i  = mean(window)
    std_i  = std(window)
    width_i = 2 * std_i / SMA_i

  Current width percentile:
    all_widths = [width_i for i in lookback]
    pctile = 100 * count(all_widths <= width_current) / len(all_widths)

  BB compression signal:
    bb_compressed = (pctile < THRESHOLD)   (THRESHOLD = 50 initially)

  Key property: bb_compressed is DIRECTIONALLY NEUTRAL.
    It votes TRUE for BOTH bull_votes AND bear_votes.
    It says "a breakout is likely" without saying which direction.

6-signal ensemble:
  Signal 1: Momentum      → ret_12h > dyn_threshold
  Signal 2: V-Short Mom   → ret_6h  > dyn_threshold * 0.5
  Signal 3: EMA Crossover → EMA(12) > EMA(26)
  Signal 4: RSI(14)       → RSI > threshold
  Signal 5: MACD(12,26,9) → histogram > 0
  Signal 6: BB Compression → pctile < THRESHOLD  (votes for BOTH sides)

  bull_votes = sum([mom_bull, vshort_bull, ema_bull, rsi_bull, macd_bull, bb_compressed])
  bear_votes = sum([mom_bear, vshort_bear, ema_bear, rsi_bear, macd_bear, bb_compressed])
  MIN_VOTES = 4 (out of 6)
```
- Vol compression (Bollinger squeeze) precedes large price moves
- Adding it as directionally neutral means it only boosts entries when 3 other directional signals already agree

### exp34-37 — BB Percentile Tuning (KEEP, score 9.78 → 10.305)

```
Swept BB_THRESHOLD: 40, 50, 60, 70, 80, 90
Optimal: 80th percentile

bb_compressed = (pctile < 80)

Meaning: current BB width is narrower than 80% of its history = compressed.
At 80, DD dropped to 2.3%. Higher thresholds let more entries through,
lower thresholds are too restrictive.
```

## Phase 4: The Great Simplification (score 10.3 → 15.7)

The biggest lesson: **removing complexity improved performance**.

### exp41 — Remove Pyramiding (KEEP, score 10.615)

**Math change:**
```
Before: if PnL > 1.5% AND not already pyramided:
           target += base_size * 0.7
After:  PYRAMID_SIZE = 0  (this code path never executes)
```
- Pyramid trades were late entries adding turnover without return. DD halved (2.3% → 1.6%).

### exp42 — Remove Funding Boost (KEEP, score 11.302)

**Math change:**
```
Before: funding_mult = 1.0 + 0.3  if funding favors direction
After:  FUNDING_BOOST = 0  → funding_mult always = 1.0
```
- Funding-aligned sizing was noise. The directional signals already incorporated the same information.

### exp43 — Remove BTC Lead-Lag Filter (KEEP, score 11.662)

**Math change:**
```
Before: Block ETH/SOL bull entry if BTC_24h_return < -0.01
After:  BTC_OPPOSE_THRESHOLD = -99 (never triggers)
```
- BTC lead-lag too noisy at hourly timeframe. Blocked valid alt entries.

### exp44 — Remove Correlation-Based SOL Reduction (KEEP, score 11.804)

**Math change:**
```
Before: if pearson(BTC_rets, ETH_rets, 72bars) > 0.8:
           SOL_weight *= 0.5
After:  HIGH_CORR_THRESHOLD = 99 (never triggers)
```
- Correlation regime detection was unreliable. SOL adds diversification value unconditionally.

### exp45 — Remove DD-Adaptive Sizing (KEEP, score 11.804)

**Math change:**
```
Before: if current_drawdown > 4%:
           dd_scale = max(0.5, 1.0 - (dd - 0.04) * 5)
After:  DD_REDUCE_THRESHOLD = 99 (never triggers, dd_scale always 1.0)
```
- DD never reached 4% with the improved strategy. Dead code.

### exp46 — Remove Strength Scaling (KEEP, score 13.480)

**Math change:**
```
Before: strength_scale = min(|ret_short| / threshold, 2.0)
         → stronger momentum = larger position
After:  strength_scale = 1.0 (fixed)
         → all entries are equal size regardless of momentum magnitude
```
- **+1.7 points.** Single biggest simplification win.
- Strong momentum ≠ more certain outcome. Variable sizing introduced noise.

### exp47 — Remove Vol Scaling (KEEP, score 13.487)

**Math change:**
```
Before: vol_scale = TARGET_VOL / realized_vol
         → high vol = smaller position, low vol = larger position
After:  vol_scale = 1.0 (fixed)
```
- DD dropped to 0.79%. Simpler and more robust.

### exp51 — Remove Take-Profit (KEEP, score 13.491)

**Math change:**
```
Before: if PnL > 8%: exit to flat
After:  TAKE_PROFIT_PCT = 99 (never triggers)
```
- ATR trailing stops and RSI exits handle all exits. Fixed TP was dead code.

### exp52 — Equal Symbol Weights (KEEP, score 13.519)

**Math change:**
```
Before: weights = {BTC: 0.40, ETH: 0.35, SOL: 0.25}
After:  weights = {BTC: 0.33, ETH: 0.33, SOL: 0.33}

size = equity * 0.08 * 0.33  ≈ 2.64% of equity per symbol
```
- Equal weighting = maximum diversification across uncorrelated assets.

## Phase 5: Cooldown and BB Period Tuning (score 13.5 → 15.7)

### exp55-56 — Cooldown 6→3 (KEEP, score 13.632 → 14.592)

**Math change:**
```
COOLDOWN_BARS = 3  (from 6)

in_cooldown = (bar_count - exit_bar[symbol]) < 3
```
- With simplified strategy, faster re-entry captures more moves without adding noise.

### exp61-63 — BB Period 20→10 (KEEP, score 14.722 → 14.790)

**Math change:**
```
BB_PERIOD = 10  (from 20)

BB width now computed over 10-bar rolling window instead of 20.
Faster response catches shorter compression cycles.
At hourly bars, 10 bars = 10 hours, vs 20 = almost a full day.
```

### exp65 — Remove ret_long Filter (KEEP, score 14.908)

**Math change:**
```
Momentum signal (before):
  mom_bull = ret_12h > threshold AND ret_24h > threshold*0.8 AND ret_48h > 0
  (Three-timeframe agreement required)

Momentum signal (after):
  mom_bull = ret_12h > threshold
  (Only 12h return matters)
```
- The 24h and 48h filters were blocking valid entries.

### exp66 — Simplified Momentum (KEEP, score 15.718)

**Math change:**
```
Previously both momentum signals required multi-timeframe confirmation.
Now simplified to just:

  Signal 1 (Momentum):      ret_12h > dyn_threshold
  Signal 2 (V-Short Mom):   ret_6h  > dyn_threshold * 0.5

No medium-term or long-term confirmation needed.
```
- Another huge gain from simplification. Multi-timeframe momentum confirmation was net harmful.

## Phase 6: RSI Period Discovery (score 15.7 → 20.6)

### exp71 — RSI Period 10 (KEEP, score 17.032)

**Math change:**
```
RSI_PERIOD = 10  (from 14)

RSI = f(close[-11:])   instead of f(close[-15:])
Faster response to hourly price changes.
```

### exp72 — RSI Period 8 (KEEP, score 19.697)

**Math change:**
```
RSI_PERIOD = 8

deltas = diff(close[-(8+1):])        ← only last 9 closes
gains  = where(deltas > 0, deltas, 0)
losses = where(deltas < 0, -deltas, 0)
RS     = mean(gains) / mean(losses)
RSI    = 100 - 100 / (1 + RS)

Standard RSI uses period=14 (designed for daily bars).
At hourly bars, 14 periods = 14 hours ≈ 0.6 days.
Period 8 = 8 hours ≈ 0.33 days — much more responsive.
```
- **Single biggest individual improvement** (+5 points from 14.8 → 19.7)
- Tested 6 (too twitchy, too many trades), 7 (worse), 9 (worse). 8 is the sweet spot.

### exp77 — RSI 50/50 Entry Thresholds (KEEP, score 19.718)

**Math change:**
```
Before: rsi_bull = RSI > 51, rsi_bear = RSI < 49
After:  rsi_bull = RSI > 50, rsi_bear = RSI < 50

Exactly neutral. RSI above midline = bullish, below = bearish.
No dead zone.
```

### exp86 — Cooldown 2 (KEEP, score 19.859)

**Math change:**
```
COOLDOWN_BARS = 2

in_cooldown = (bar_count - exit_bar[symbol]) < 2
With RSI(8) generating more signals, shorter cooldown captures more valid entries.
```

### exp94-96 — Position Size 0.16→0.08 (KEEP, score 20.270 → 20.497)

**Math change:**
```
BASE_POSITION_PCT = 0.08  (from 0.16)

size = equity * 0.08 * 0.33 = 2.64% of equity per symbol

Annual turnover drops proportionally → turnover penalty = 0.
Score ≈ pure Sharpe ratio now.
Returns: 130% (vs 260% at 0.16) but risk-adjusted is maximized.
```

### exp100 — BB Percentile 85 (KEEP, score 20.581)

**Math change:**
```
bb_compressed = (pctile < 85)  (from 80)

More permissive: BB width below 85th percentile = "compressed."
Allows more entries through the BB filter gate.
```

### exp102 — RSI 50/50 (KEEP, score 20.634)

**Math change:**
```
RSI_BULL = 50, RSI_BEAR = 50 (confirmed optimal from exp77)
Score 20.634, Sharpe 20.634, DD 0.3%, 7605 trades.
```

## Phase 7: Fine-Tuning the Ensemble (score 20.6 → 21.4)

With the core strategy locked in, this phase swept every remaining parameter looking for marginal gains. ~150 experiments ran autonomously.

### exp119 — BB Percentile 80 (KEEP, score 20.714)

**Math change:**
```
BB_PERIOD = 8 (from 10, found in exp115)
bb_compressed = (pctile < 80)  (re-tuned with new BB_PERIOD)
```

### exp123 — EMA 10/22 (KEEP, score 20.775)

**Math change:**
```
EMA_FAST = 10  (from 12)
EMA_SLOW = 22  (from 26)

Faster EMA crossover responds quicker.
EMA(10) vs EMA(22) instead of EMA(12) vs EMA(26).
```

### exp136 — VOL_LOOKBACK 36 (KEEP, score 20.794)

**Math change:**
```
VOL_LOOKBACK = 36  (from 48)
realized_vol = std(diff(ln(closes[-36:])))

Shorter vol lookback → more responsive to recent volatility changes.
```

### exp140 — Vol Scaling 0.4+0.6 (KEEP, score 20.852)

**Math change:**
```
dyn_threshold = BASE_THRESHOLD * (0.4 + vol_ratio * 0.6)
                                  ↑ was 0.5    ↑ was 0.5

Slightly more weight on vol_ratio, slightly lower floor.
Threshold responds more to current volatility.
```

### exp145-153 — EMA_FAST Sweep 9→7, EMA_SLOW 24 (KEEP, score 20.865 → 20.924)

**Math change:**
```
Swept EMA_FAST: 9 (20.865), 8 (20.875), 7 (20.899)
  EMA_FAST 6 was worse (20.874). Sweet spot: 7.

Then swept EMA_SLOW with EMA_FAST=7:
  20 (20.577), 22 (tested), 24 (20.924 ← new best), 26 (pending)

Final: EMA(7) vs EMA(24)
  alpha_fast = 2/8 = 0.25  (very responsive)
  alpha_slow = 2/25 = 0.08 (smooth trend)
```

### exp167 — BB Compression Threshold 90 (KEEP, score 20.957)

**Math change:**
```
bb_compressed = (pctile < 90)  (from 80)

Very permissive: only filters out the top 10% widest BB readings.
In practice, BB compression votes "yes" most of the time,
acting as a mild quality gate rather than a strong filter.
```

### exp186 — MACD_SLOW 22 (KEEP, score 20.995)

**Math change:**
```
MACD_SLOW = 22  (from 26)
MACD histogram = EMA(close, 12) - EMA(close, 22) - EMA(MACD_line, 9)

Faster MACD slow EMA matches the faster EMA crossover (7/24).
```

### exp191 — EMA_SLOW 26 (KEEP, score 21.050)

**Math change:**
```
EMA_SLOW = 26  (from 24, re-tested with other changes)
With MACD_SLOW=22 and EMA_FAST=7, the wider EMA gap works better.
```

### exp200 — V-Short Mult 0.7 (KEEP, score 21.077)

**Math change:**
```
vshort_bull = ret_6h > dyn_threshold * 0.7  (from 0.5)
vshort_bear = ret_6h < -dyn_threshold * 0.7

Higher threshold for v-short signal reduces false positives.
```

### exp205 — Vol Scaling 0.3+0.7 (KEEP, score 21.243)

**Math change:**
```
dyn_threshold = BASE_THRESHOLD * (0.3 + vol_ratio * 0.7)
                                  ↑ was 0.4    ↑ was 0.6
dyn_threshold = clamp(dyn_threshold, 0.005, 0.020)

Even more dynamic: lower floor (0.3 vs 0.4) means threshold adapts more
to volatility. Floor/ceiling also tightened from [0.006, 0.025] to [0.005, 0.020].
```

### exp217 — MACD_FAST 14 (KEEP, score 21.249)

**Math change:**
```
MACD_FAST = 14  (from 12)
MACD histogram = EMA(close, 14) - EMA(close, 22) - EMA(MACD_line, 9)

Narrower MACD gap (14 vs 22 instead of 12 vs 22).
MACD becomes more of a convergence detector than divergence.
```

### exp225 — BB_PERIOD 7 (KEEP, score 21.347)

**Math change:**
```
BB_PERIOD = 7  (from 8)
BB width computed over 7-bar rolling window.
7 hours of compression detection — very fast response.
```

### exp246 — MACD_SLOW 23 (KEEP, score 21.364)

**Math change:**
```
MACD_SLOW = 23  (from 22)
MACD histogram = EMA(close, 14) - EMA(close, 23) - EMA(MACD_line, 9)
Marginal widening of MACD gap.
```

### exp251 — RSI Exit 69/31 (KEEP, score 21.402) — CURRENT BEST

**Math change:**
```
RSI_OVERBOUGHT = 69  (from 70)
RSI_OVERSOLD   = 31  (from 30)

Exit longs 1 RSI point earlier (69 instead of 70).
Exit shorts 1 RSI point earlier (31 instead of 30).
Slightly earlier exits reduce drawdown on mean-reversion.
Score 21.402, Sharpe 21.402, DD 0.3%, 7949 trades.
```

### Phase 7 Notable Discards

| Experiment | Score | What Failed | Lesson |
|-----------|-------|-------------|--------|
| exp106 | 20.206 | BASE_THRESHOLD 0.015 | Higher threshold too restrictive |
| exp109 | 16.786 | MIN_VOTES 3/6 | Too many entries, massive turnover |
| exp112 | 16.847 | RSI_PERIOD 12 | Slower RSI much worse for hourly |
| exp124 | 16.323 | MIN_VOTES 5/6 | Too restrictive, misses valid entries |
| exp155-156 | 20.485-20.798 | Various MACD_FAST/SLOW | Most MACD tweaks were marginal |
| exp162 | 20.346 | COOLDOWN_BARS 3 | Re-entry speed matters with fast RSI |
| exp168 | 20.741 | BB threshold 95 | Too permissive, BB signal becomes meaningless |
| exp170 | 20.403 | RSI exit 68/32 | One step too far, too many exits |
| exp223-224 | 18.4-19.2 | Remove v-short signal | V-short momentum is essential to ensemble |
| exp238 | 21.330 | Log returns for momentum | No improvement over simple returns |

---

## Notable Discards (Lessons Learned)

| Experiment | Score | What Failed | Math Lesson |
|-----------|-------|-------------|-------------|
| exp5 | 3.495 | Removed take-profit | Before RSI exits existed, TP was the only profit-capture mechanism |
| exp9 | 3.485 | Fading stop: `stop = entry - ATR * (1 - pnl/0.05)` | Tightening stops as PnL grew killed winners early |
| exp14 | 3.222 | Position 0.20 + wide vol_scale | `turnover_penalty = max(0, turnover/capital - 500) * 0.001` becomes dominant |
| exp17 | -2.125 | ATR 3.0 + no flip | 3.0 * ATR too tight for hourly crypto. No-flip = exit flat, miss reversal |
| exp21 | 6.269 | Vol regime bins (low/normal/high/extreme) | Regime transitions too noisy at hourly. Whipsawed between regimes |
| exp22 | 8.096 | ADX > 25 trend filter | ADX lagged too much at hourly, filtered valid entries |
| exp60 | 7.618 | Exit to flat instead of signal flip | Signal flip captures reversal immediately: `target = -size` vs `target = 0` |
| exp78 | 17.627 | RSI exit at 65/35 (tighter than 70/30) | More exits = more turnover. `turnover_penalty` binding |
| exp81 | 17.547 | ROC(12) instead of MACD | `ROC = (close - close[-12]) / close[-12]` too noisy without signal-line smoothing |
| exp90 | 5.991 | RSI oversold threshold = 10 | Shorts never exited → held through massive reversals |
| exp99 | 19.750 | Stochastic %K instead of RSI | `%K = 100 * (close - low_14) / (high_14 - low_14)` — bounded by recent range, less smooth than RSI |

---

## Final Strategy: Complete Mathematical Description (exp251, score 21.402)

### Parameters
```python
SYMBOLS     = ["BTC", "ETH", "SOL"]     # equal weight 0.33 each
POSITION    = 0.08                       # 8% of equity per symbol
ATR_MULT    = 5.5                        # trailing stop width
RSI_PERIOD  = 8                          # fast RSI
RSI_OB/OS   = 69/31                     # overbought/oversold exit thresholds
EMA_FAST    = 7                          # fast EMA period
EMA_SLOW    = 26                         # slow EMA period
MACD        = (14, 23, 9)               # fast, slow, signal
BB_PERIOD   = 7                          # BB width lookback
BB_THRESH   = 90                         # BB compression percentile threshold
COOLDOWN    = 2                          # bars between exit and re-entry
MIN_VOTES   = 4                          # out of 6 signals
THRESHOLD   = 0.012                      # base momentum threshold
VOL_LOOKBACK = 36                        # realized vol window
```

### Per-Bar Logic (for each symbol)

**Step 1: Compute indicators**
```
closes      = history["close"]
realized_vol = std(diff(ln(closes[-36:])))
vol_ratio    = realized_vol / 0.015
dyn_thresh   = clamp(0.012 * (0.3 + vol_ratio * 0.7), 0.005, 0.020)

ret_6h      = (closes[-1] - closes[-6]) / closes[-6]
ret_12h     = (closes[-1] - closes[-12]) / closes[-12]
ema_7       = EMA(closes, 7)[-1]
ema_26      = EMA(closes, 26)[-1]
rsi         = RSI(closes, 8)
macd_hist   = MACD(closes, 14, 23, 9)
bb_pctile   = BB_width_percentile(closes, 7)
```

**Step 2: Vote**
```
                        BULL condition            BEAR condition
Signal 1 (Momentum):   ret_12h > dyn_thresh      ret_12h < -dyn_thresh
Signal 2 (V-Short):    ret_6h > dyn_thresh*0.7   ret_6h < -dyn_thresh*0.7
Signal 3 (EMA):        ema_7 > ema_26            ema_7 < ema_26
Signal 4 (RSI):        rsi > 50                  rsi < 50
Signal 5 (MACD):       macd_hist > 0             macd_hist < 0
Signal 6 (BB Comp):    bb_pctile < 90            bb_pctile < 90    ← SAME (neutral)

bull_votes = sum(bull conditions)
bear_votes = sum(bear conditions)
bullish    = (bull_votes >= 4)
bearish    = (bear_votes >= 4)
```

**Step 3: Entry**
```
in_cooldown = (current_bar - last_exit_bar[symbol]) < 2

if no_position AND NOT in_cooldown:
    if bullish:  target = +equity * 0.08 * 0.33
    if bearish:  target = -equity * 0.08 * 0.33
```

**Step 4: Exit (priority order)**
```
1. ATR Trailing Stop:
   For longs:  peak = max(peak, close)
               if close < peak - 5.5 * ATR(24): exit
   For shorts: trough = min(trough, close)
               if close > trough + 5.5 * ATR(24): exit

2. RSI Mean-Reversion Exit:
   If long  AND RSI(8) > 69: exit
   If short AND RSI(8) < 31: exit

3. Signal Flip (replaces exit + new entry in one step):
   If long  AND bearish AND NOT in_cooldown: target = -size  (flip to short)
   If short AND bullish AND NOT in_cooldown: target = +size  (flip to long)
```

**Step 5: Position update**
```
if |target - current_position| > $1:
    emit Signal(symbol, target_position=target)

    if new entry: record entry_price, peak_price, ATR_at_entry
    if exit:      record exit_bar for cooldown, clear tracking state
    if flip:      record new entry state
```
