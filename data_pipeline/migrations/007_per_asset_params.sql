-- 007_per_asset_params.sql
-- Add per-symbol tuning support: symbol column on param_snapshots and active_params.

-- Add symbol column to param_snapshots
ALTER TABLE param_snapshots ADD COLUMN IF NOT EXISTS symbol TEXT NOT NULL DEFAULT 'ALL';
CREATE INDEX IF NOT EXISTS idx_param_snapshots_symbol ON param_snapshots(symbol);

-- Add symbol column to active_params
ALTER TABLE active_params ADD COLUMN IF NOT EXISTS symbol TEXT NOT NULL DEFAULT 'ALL';

-- Drop old single-column unique index and create composite (period, symbol)
DROP INDEX IF EXISTS idx_active_params_period;
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_params_period_symbol ON active_params(period, symbol);
