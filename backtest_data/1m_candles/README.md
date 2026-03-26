# 1-Minute Candle Data

1-minute OHLCV + funding rate data for BTC, ETH, SOL, XRP perpetual futures on Hyperliquid.

## Data

| Symbol | Bars | Price Range | Change |
|--------|------|-------------|--------|
| BTC | 2,881 | $70,355 → $68,738 | -2.30% |
| ETH | 2,881 | $2,148.80 → $2,059.80 | -4.14% |
| SOL | 2,881 | $90.44 → $86.36 | -4.50% |
| XRP | 2,881 | $1.41 → $1.36 | -3.91% |

- **Period**: 2026-03-24 22:25 UTC to 2026-03-26 22:26 UTC (48 hours)
- **Interval**: 1 minute
- **Columns**: `timestamp`, `open`, `high`, `low`, `close`, `volume`, `funding_rate`
- **Source**: Hyperliquid `candleSnapshot` + `fundingHistory` REST endpoints

## Files

```
BTC_1m.parquet  (95K)
ETH_1m.parquet  (75K)
SOL_1m.parquet  (99K)
XRP_1m.parquet  (77K)
BTC_1m.csv / ETH_1m.csv / SOL_1m.csv / XRP_1m.csv  (human-readable)
```

## Running the Backtest

```bash
# Against this data (default)
uv run backtest_1m.py

# Download fresh data instead
uv run backtest_1m.py --download --hours 48

# Custom data directory
uv run backtest_1m.py --data-dir ./backtest_data/1m_candles
```

## Backtest Results

Strategy: 6-signal ensemble (momentum, EMA, RSI, MACD, BB compression, very-short momentum) with ATR trailing stops.

| Metric | Value |
|--------|-------|
| Initial capital | $10,000 |
| Bars processed | 2,882 |
| Trades | 3,307 |
| Closed trades | 1,475 |
| Win rate | 62.1% |
| Profit factor | 4.75 |
| Gross PnL | $175.13 |
| Net return | -1.88% |
| Max drawdown | 1.89% |
| Sharpe (annualized) | -134.29 |
| Final equity | $9,812.39 |
| Runtime | 105.6s |

### Key Takeaway

The strategy is directionally profitable (62.1% win rate, $175 gross PnL, 4.75x profit factor) but loses money net because it was tuned for hourly bars. At 1-minute resolution, the same lookback windows (6 bars = 6 minutes instead of 6 hours) trigger excessive noise trades. The 3,307 round-trips over 48h generate ~$355 in taker fees, wiping out the edge.

### Equity Curve

```
  0%: $  10,000.00
 25%: $   9,948.59
 50%: $   9,904.09
 75%: $   9,849.17
100%: $   9,812.39
```

### Per-Trade Stats (closed trades)

| Metric | Value |
|--------|-------|
| Avg PnL | $0.12 |
| Best PnL | $2.29 |
| Worst PnL | -$0.67 |

### Backtest Configuration

- Funding scaled for 1m bars: divided by 480 (8h / 1min)
- Sharpe annualized from minute returns: `sqrt(525,600)`
- Fees: 5bps taker + 1bps slippage per trade
- Position sizing: `equity * 0.08 * weight` per signal (equal 0.25 weight across 4 symbols)
