-- Migration 010: Drop dead columns from param_snapshots
--
-- These params are either:
--   (a) Not in ACTIVE_PARAMS (never tracked by the 1h strategy)
--   (b) Effectively disabled (zeroed/sentinel values, code paths exist
--       but the params are never meaningfully tuned)
--
-- Dropped columns:
--   THRESHOLD_MIN, THRESHOLD_MAX, BB_COMPRESS_PCTILE  (never in strategy.py)
--   FUNDING_BOOST, PYRAMID_SIZE                        (permanently 0.0)
--   BTC_OPPOSE_THRESHOLD, HIGH_CORR_THRESHOLD         (permanently -99.0 / 99.0)
--   DD_REDUCE_THRESHOLD                               (permanently 99.0)

ALTER TABLE param_snapshots DROP COLUMN IF EXISTS THRESHOLD_MIN;
ALTER TABLE param_snapshots DROP COLUMN IF EXISTS THRESHOLD_MAX;
ALTER TABLE param_snapshots DROP COLUMN IF EXISTS BB_COMPRESS_PCTILE;
ALTER TABLE param_snapshots DROP COLUMN IF EXISTS FUNDING_BOOST;
ALTER TABLE param_snapshots DROP COLUMN IF EXISTS PYRAMID_SIZE;
ALTER TABLE param_snapshots DROP COLUMN IF EXISTS BTC_OPPOSE_THRESHOLD;
ALTER TABLE param_snapshots DROP COLUMN IF EXISTS HIGH_CORR_THRESHOLD;
ALTER TABLE param_snapshots DROP COLUMN IF EXISTS DD_REDUCE_THRESHOLD;
