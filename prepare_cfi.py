"""
CFI Perp Data Generator for Backtesting.

Computes CFI (Cumulative Funding Index) values from existing funding rate data
and generates CFI-anchored perpetual mark prices for backtesting.

CFI v2 formula: I_t = Σ (r_s - k_fixed_hr) where:
  - r_s = funding rate per hour
  - k_fixed_hr = dynamic fixed leg (EMA of funding rate, K2 parameter)

Wire price: P = B + S_eff * (CFI - A)
  - B = baseline price
  - S_eff = scale_s * vol_mult_l (volatility multiplier)
  - A = anchor (re-centers when drift > delta_target)

This file generates CFI perp OHLCV data that can be loaded by the standard
backtest harness via load_data().
"""

import os
import numpy as np
import pandas as pd

# CFI Parameters (BTC Profile H1 defaults from btc-cfi-temp)
CFI_PARAMS = {
    "BTC": {
        "baseline_b0": 75000.0,
        "fixed_leg_initial": 0.000012154,  # ~10.65% APY
        "k2_beta": 0.005952381,            # 7-day time constant
        "vol_mult_l": 20.0,
        "scale_s": 1_000_000.0,
        "delta_target": 0.005,
    },
    "ETH": {
        "baseline_b0": 3500.0,
        "fixed_leg_initial": 0.000010000,  # ~8.76% APY
        "k2_beta": 0.005952381,
        "vol_mult_l": 20.0,
        "scale_s": 1_000_000.0,
        "delta_target": 0.005,
    },
    "SOL": {
        "baseline_b0": 150.0,
        "fixed_leg_initial": 0.000015000,  # ~13.14% APY
        "k2_beta": 0.005952381,
        "vol_mult_l": 20.0,
        "scale_s": 1_000_000.0,
        "delta_target": 0.005,
    },
}

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "autotrader")
DATA_DIR = os.path.join(CACHE_DIR, "data")
CFI_DATA_DIR = os.path.join(CACHE_DIR, "cfi_data")


def compute_cfi_series(funding_rates: np.ndarray, k2_beta: float, fixed_leg_initial: float):
    """
    Compute CFI index values from hourly funding rates.

    Returns (cfi_values, k_fixed_hr_values) arrays.
    """
    n = len(funding_rates)
    cfi = np.zeros(n)
    k_fixed_hr = np.full(n, fixed_leg_initial)

    running_cfi = 0.0
    running_k = fixed_leg_initial

    for i in range(n):
        rate = funding_rates[i]
        # Update K2 dynamic fixed leg
        running_k = (1.0 - k2_beta) * running_k + k2_beta * rate
        # CFI increment = excess funding
        excess = rate - running_k
        running_cfi += excess

        cfi[i] = running_cfi
        k_fixed_hr[i] = running_k

    return cfi, k_fixed_hr


def generate_cfi_ohlcv(symbol: str):
    """
    Generate CFI perp OHLCV data from spot OHLCV + funding rates.

    CFI perp pricing model for backtesting:
    - Mark price = spot_price * exp(vol_mult_bt * CFI_index)
    - vol_mult_bt is calibrated so CFI modulation is realistic (~1-5% price deviation)
    - Funding rate is replaced with excess funding (rate - k_fixed_hr)

    This creates a price series that reflects both spot movement AND the
    cumulative excess funding dynamics that define CFI perp behavior.
    """
    spot_path = os.path.join(DATA_DIR, f"{symbol}_1h.parquet")
    if not os.path.exists(spot_path):
        raise FileNotFoundError(f"Spot data not found: {spot_path}")

    df = pd.read_parquet(spot_path)
    params = CFI_PARAMS[symbol]
    funding_rates = df["funding_rate"].values

    # Compute CFI index
    cfi_values, k_fixed_hr = compute_cfi_series(
        funding_rates,
        params["k2_beta"],
        params["fixed_leg_initial"],
    )

    # Backtest-calibrated volatility multiplier
    # The on-chain S_eff (20M) is for wire price quantization.
    # For backtesting, we want CFI modulation to produce realistic
    # price deviations: vol_mult_bt * max(|CFI|) ≈ 5-10% deviation
    cfi_range = max(abs(cfi_values.min()), abs(cfi_values.max()))
    if cfi_range > 0:
        vol_mult_bt = 0.05 / cfi_range  # target ~5% max deviation
    else:
        vol_mult_bt = 1.0

    # CFI perp mark price = spot * exp(vol_mult_bt * CFI)
    cfi_modulation = np.exp(vol_mult_bt * cfi_values)

    spot_close = df["close"].values
    spot_open = df["open"].values
    spot_high = df["high"].values
    spot_low = df["low"].values

    cfi_close = spot_close * cfi_modulation
    cfi_open = spot_open * cfi_modulation
    cfi_high = spot_high * cfi_modulation
    cfi_low = spot_low * cfi_modulation

    # Ensure OHLCV consistency
    cfi_high = np.maximum(cfi_high, np.maximum(cfi_open, cfi_close))
    cfi_low = np.minimum(cfi_low, np.minimum(cfi_open, cfi_close))

    # Excess funding rate (what CFI perp holders actually pay/receive)
    excess_funding = funding_rates - k_fixed_hr

    # Build CFI perp dataframe
    cfi_df = pd.DataFrame({
        "timestamp": df["timestamp"].values,
        "open": cfi_open,
        "high": cfi_high,
        "low": cfi_low,
        "close": cfi_close,
        "volume": df["volume"].values,
        "funding_rate": excess_funding,  # excess funding, not raw
    })

    # Add metadata columns
    cfi_df["cfi_index"] = cfi_values
    cfi_df["k_fixed_hr"] = k_fixed_hr

    return cfi_df


def generate_all_cfi_data():
    """Generate CFI perp data for all symbols."""
    os.makedirs(CFI_DATA_DIR, exist_ok=True)

    for symbol in ["BTC", "ETH", "SOL"]:
        print(f"  {symbol}: computing CFI perp prices...")
        cfi_df = generate_cfi_ohlcv(symbol)

        filepath = os.path.join(CFI_DATA_DIR, f"{symbol}_1h_cfi.parquet")
        cfi_df.to_parquet(filepath, index=False)

        # Stats
        print(f"    Bars: {len(cfi_df)}")
        print(f"    CFI range: [{cfi_df['cfi_index'].min():.6f}, {cfi_df['cfi_index'].max():.6f}]")
        print(f"    Price range: [{cfi_df['close'].min():.2f}, {cfi_df['close'].max():.2f}]")
        print(f"    Saved to: {filepath}")

    print("\nCFI data generation complete.")


if __name__ == "__main__":
    generate_all_cfi_data()
