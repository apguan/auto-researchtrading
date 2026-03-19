Inspired by Karpathy's autoresearch, I let Claude self-evolve trading strategies on Hyperliquid perps—not tweaking one parameter, but autonomously discovering, testing, and discarding entire strategy architectures.

**103 fully automated experiments, zero human intervention:**
- Score: 2.724 → 21.402 (7.9x improvement)
- Sharpe ratio: 2.7 → 21.4
- Max drawdown: 7.6% → 0.3%
- Strategy complexity: went DOWN (the AI learned to simplify)

Loop: backtest → score → mutate strategy.py → commit → backtest → keep or revert → repeat.

The AI runs its own experiment loop on a RunPod A100. It modifies its own trading strategy code, backtests it against 9 months of hourly crypto data, evaluates the score, and decides whether to keep or revert—all without a human touching anything. Simultaneously, a second loop optimizes GPT pretraining hyperparameters on the same GPU.

**The biggest discovery wasn't what it added—it was what it removed.**

Phase 1-3: The AI built up a 6-signal ensemble with pyramiding, funding carry, BTC lead-lag filters, correlation regime detection, volatility-adaptive sizing, drawdown-adaptive positions... classic quant complexity.

Phase 4: "The Great Simplification." The AI systematically removed every clever feature it had built. Each removal *improved* performance. Removing momentum strength scaling alone added +1.7 Sharpe. The AI independently discovered that uniform sizing beats sophisticated sizing, that multi-timeframe confirmation hurts, and that the fanciest features were just adding noise.

Phase 5-7: Fine-tuning. RSI period 8 instead of the textbook 14 was the single biggest individual gain (+5 Sharpe). The AI figured out that standard indicator periods are designed for daily bars—hourly crypto needs faster signals.

**What's interesting about the related work:**
- Karpathy's autoresearch optimizes model training hyperparameters
- VibeHQ evolves multi-agent collaboration protocols
- Gastown orchestrates 20-30 Claude Codes simultaneously

These are all evolving *how agents work*. What we're doing is evolving *domain knowledge*—the AI is becoming a better quant researcher through pure trial and error. It doesn't know finance theory. It just runs experiments and follows the score.

**The strategy it converged on is remarkably elegant:**
- 6 signals vote (momentum, v-short momentum, EMA crossover, RSI, MACD, Bollinger Band compression)
- Need 4/6 agreement to enter
- RSI overbought/oversold exits (the AI's most important discovery)
- ATR trailing stops at 5.5x (let winners run)
- Signal flip on reversal (never exit to flat)
- Equal weight BTC/ETH/SOL, fixed 8% position size

Every "smart" feature—pyramiding, funding carry, BTC lead-lag, correlation filters, variable sizing—was tried, kept temporarily, then permanently removed when the AI realized simplicity wins.

**What happens if it keeps running:**
- Cross-asset strategies that adapt signal weights per market regime
- Self-discovering new technical indicators from raw price data
- Strategies that evolve differently for each asset based on its characteristics
- Eventually: strategies too complex for humans to design, but empirically validated through thousands of automated backtests

The full evolution log with math for every strategy is open source. 103 experiments, every keep, every discard, every lesson learned—all generated autonomously.

Score 2.724 → 21.402. The AI taught itself to be a quant.
