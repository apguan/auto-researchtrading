ALTER TABLE param_snapshots ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_param_snapshots_is_active ON param_snapshots(is_active) WHERE is_active = TRUE;

DROP TABLE IF EXISTS active_params CASCADE;
