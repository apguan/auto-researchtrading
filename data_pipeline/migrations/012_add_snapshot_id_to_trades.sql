ALTER TABLE trades ADD COLUMN IF NOT EXISTS snapshot_id BIGINT REFERENCES param_snapshots(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_trades_snapshot_id ON trades(snapshot_id);
