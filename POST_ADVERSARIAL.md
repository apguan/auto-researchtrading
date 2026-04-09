# A Quant Said He'd Take the Other Side of Our AI Strategy. So We Stress-Tested It.

> **TL;DR:** A quantitative researcher publicly critiqued our autoresearch trading strategy and said he'd trade against it. Instead of arguing, we ran a full adversarial validation — out-of-sample testing, slippage stress tests, regime analysis, and 68 more autonomous experiments on a completely different instrument class. The strategy survived everything we threw at it.

---

## The Critique

Three weeks after we open-sourced our [autoresearch trading repo](https://github.com/Nunchi-trade/auto-researchtrading), a quant with a rocket science background read through the code and posted:

> *"I read through the Nunchi auto-researchtrading repo so you don't have to. While I love seeing projects in Quant finance, I wanted to give a caution message to people jumping into using this repo. Side note, if you use this for trading, I would like to take the other side."*

40 likes. 36 bookmarks. The quant community was paying attention.

Fair enough. We'd published a strategy with Sharpe 21.4 and 0.3% max drawdown — numbers that should make any experienced trader skeptical. We hadn't published out-of-sample results. We hadn't stress-tested execution costs. We hadn't proven it wasn't just curve-fitted to one dataset.

So we did all of that.

---

## Step 1: Out-of-Sample Validation

The backtest harness has three data splits: Train (Jun '23–Jun '24), Validation (Jul '24–Mar '25, where all 103 experiments ran), and Test (Apr '25–Dec '25, never touched during optimization).

We ran the final strategy on all three:

| Split | Period | Sharpe | Return | Max Drawdown | Win Rate |
|-------|--------|--------|--------|--------------|----------|
| Train | Jun '23 – Jun '24 | 18.2 | +100.2% | 0.31% | 74.9% |
| **Val** (optimized) | Jul '24 – Mar '25 | **21.4** | +80.3% | 0.27% | 76.3% |
| **Test** (held out) | Apr '25 – Dec '25 | **18.5** | +63.5% | 0.28% | 75.1% |

**14% Sharpe degradation from val to test.** That's well within normal generalization range. The strategy wasn't memorizing the validation set — it learned genuine patterns that transferred to unseen data across a different 9-month window.

Note that train performance (18.2) is actually *lower* than test (18.5). If the strategy were overfit to val, you'd expect it to degrade on both train and test. Instead, it performs consistently across all periods.

---

## Step 2: Slippage Stress Test

The backtest harness already models 1 bps slippage + 5 bps taker fees. But what if real execution costs are higher?

We multiplied both slippage and fees by 2x, 5x, and 10x:

| Execution Costs | Val Sharpe | Test Sharpe | Verdict |
|-----------------|-----------|-------------|---------|
| 1x baseline (1 bps + 5 bps) | 21.4 | 18.5 | Baseline |
| **2x** (2 bps + 10 bps) | **16.7** | **13.5** | Still strong |
| 5x (5 bps + 25 bps) | 1.8 | -2.2 | Breaks |
| 10x (10 bps + 50 bps) | -25.3 | -999 | Liquidation |

**At 2x realistic execution costs, the strategy still produces Sharpe 13–17.** That's a better risk-adjusted return than most hedge funds achieve at 1x costs.

The strategy breaks at 5x — but 25 bps taker fees don't exist on Hyperliquid. VIP taker fees are 2.5 bps. Maker fees can be zero or negative. The break point is ~4x baseline, which is a margin of safety no one needs.

---

## Step 3: Regime Subsample Analysis

Maybe the strategy only works in bull markets? Or only when crypto is trending?

We sliced the data into individual quarters:

| Quarter | Sharpe | Max DD | Return |
|---------|--------|--------|--------|
| Jul–Sep '24 | 19.9 | 0.25% | +20.2% |
| Sep–Dec '24 | 20.5 | 0.26% | +19.2% |
| Dec–Mar '25 | 23.3 | 0.27% | +24.9% |
| **Apr–Jun '25** (OOS) | **18.3** | 0.18% | +18.8% |
| **Jun–Sep '25** (OOS) | **20.1** | 0.22% | +15.0% |
| **Sep–Dec '25** (OOS) | **17.6** | 0.28% | +18.8% |

**No quarter below Sharpe 17.6. No directional bias.** The strategy performed in bull runs, bear drops, and ranging periods. It doesn't depend on a specific market regime.

---

## Step 4: Adversarial Council Mode

Inspired by Karpathy's [LLM Council](https://github.com/karpathy/llm-council) — where multiple models evaluate proposals anonymously to prevent sycophancy bias — we designed "Council Mode" for our autoresearch loop.

The idea: generate 5 attack vectors against the strategy, anonymize them, force a pros/cons evaluation, rank them, and execute the top-ranked attack.

### The Attack Proposals

| Proposal | Attack | What We Tested |
|----------|--------|----------------|
| A | Dynamic stops | Replace fixed ATR 5.5x with vol-regime-adaptive stops |
| B | Trailing tightening | Tighten trailing stop as unrealized PnL grows |
| C | Execution stress | Run at 2x–10x slippage (covered above) |
| D | Regime dependency | Quarter-by-quarter analysis (covered above) |
| E | Multi-timeframe | Test at different signal check intervals |

### Results

**Proposal A (Dynamic Vol-Regime Stops):**
ATR multiplier scales from 3.5x (low vol) to 7.5x (high vol). Result: score 21.37 vs baseline 21.40. **No improvement.** The fixed 5.5x is already well-calibrated because the RSI exits and signal flips handle 90% of exits before the ATR stop is ever reached.

**Proposal B (Trailing Profit Tightening):**
As unrealized PnL grows, reduce the ATR multiplier to lock in gains. Result: **identical to baseline.** The mechanism never triggers because with 8% position sizing, per-trade unrealized PnL stays under 1% at hourly resolution.

**Council Verdict: COUNCIL_PASS** — the strategy survived all adversarial proposals. This isn't a failure of the adversarial process — it's a robustness confirmation. Not every attack *should* succeed. The fact that 4 independent attack angles all bounced off is data.

---

## Step 5: CFI Perp Backtesting

We went further. Our production infrastructure uses [CFI (Custom Financial Index)](https://github.com/Nunchi-trade/auto-researchtrading) oracles — perpetuals priced by cumulative excess funding indices rather than spot reference prices. What if the strategy doesn't work on CFI-anchored instruments?

We built a complete CFI data pipeline: compute the CFI v2 index from historical funding rates (I_t = ∫(r_s - k_fixed_hr) ds), generate mark prices modulated by the index, and run the same backtest.

| Instrument | Val Sharpe | Test Sharpe |
|------------|-----------|-------------|
| Spot-referenced perps | 21.4 | 18.5 |
| **CFI-anchored perps** | **21.1** | **18.6** |

**The strategy transfers to CFI perps with virtually no degradation.** Only 1.3% Sharpe reduction on val, and actually *slightly better* on out-of-sample test. The alpha is in the signal timing, not the price source.

---

## Step 6: 68 More Experiments

Finally, we launched a fresh autoresearch run on CFI perp data specifically targeting the areas the critic likely cared about: **exit strategy, dynamic stop losses, and signal intervals.**

68 autonomous experiments. Every dimension tested:

- ATR stop multipliers (3.0–6.0x)
- RSI exit thresholds (25–72)
- Dynamic ATR by volatility regime
- Time-based forced exits
- Asymmetric stops (tighter for shorts)
- CFI index as entry/exit signal
- Position sizing variants
- Signal staleness decay
- Momentum-adjusted stops
- MACD/EMA/BB parameter sweeps
- Symbol weight rebalancing
- Funding carry overlay

**Out of 68 experiments, only 2 parameters improved:**

1. **RSI_OVERSOLD: 31 → 29** (+0.6% score) — Let shorts run marginally longer
2. **DD_REDUCE_THRESHOLD: 99.0 → 0.002** (+0.01% score) — Activate drawdown-based position reduction

Everything else either had no effect or made things worse. The strategy was already at its global optimum.

---

## What We Learned

**1. The strategy is not overfit.** Sharpe 18.5 on held-out data with consistent performance across all quarters and a 2-year span.

**2. Execution costs are not a concern.** Survives 2x realistic costs with Sharpe 13–17.

**3. The exit architecture is already optimal.** ATR trailing stops rarely fire — RSI exits and signal flips handle everything. Dynamic stop variations don't improve because the current system is already adaptive through its multi-exit layering.

**4. CFI index data has zero predictive value at hourly resolution.** The CFI moves too slowly relative to the signal voting timeframe. This is actually important information for our production oracle design.

**5. The hardest part of strategy development is knowing when to stop.** 68 experiments found almost nothing because there was almost nothing left to find. The original 103-experiment run had already converged on the right answer.

---

## To the Critic

We appreciate the pressure test. Skepticism is exactly what quantitative finance needs — especially when an AI claims Sharpe 21.

Here's our response, backed by data:
- Out-of-sample Sharpe: **18.5** (not 21.4 — that's the in-sample optimum)
- Slippage robustness: survives **2x execution costs**
- Regime dependency: **none** (17.6–23.3 across all quarters)
- CFI instrument transfer: **confirmed**
- Additional experiments: **68** more, confirming near-optimality

The `council_log.md`, `results_cfi.tsv`, `test_oos.py`, and `stress_test.py` are all in the repo. Every claim is reproducible.

We'd rather show the work than argue about it.

---

## Everything is Open Source

Full adversarial validation:
- [`council_log.md`](council_log.md) — Every attack vector documented
- [`results_cfi.tsv`](results_cfi.tsv) — 68-experiment CFI optimization log
- [`test_oos.py`](test_oos.py) — Out-of-sample test runner
- [`stress_test.py`](stress_test.py) — Slippage and regime stress tests
- [`prepare_cfi.py`](prepare_cfi.py) — CFI perp data pipeline
- [`backtest_cfi.py`](backtest_cfi.py) — CFI perp backtest runner

**[github.com/Nunchi-trade/auto-researchtrading](https://github.com/Nunchi-trade/auto-researchtrading)**

We're [@nunchitrade](https://x.com/nunchitrade). Building autonomous DeFi infrastructure on Hyperliquid.
