-- Migration 011: Add dry_run column to trades
--
-- Dry run trades shared the same `trades` table as live trades.
-- The daily loss kill switch (risk_controller.get_daily_pnl) counted all
-- trades equally, so dry run PnL could trigger (or mask) the live kill switch.
--
-- Previously dry run trades were identified by a "dry-" prefix in order_id.
-- Add a dedicated boolean column and backfill from the legacy convention.

ALTER TABLE trades ADD COLUMN IF NOT EXISTS dry_run BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE trades SET dry_run = TRUE WHERE order_id LIKE '%dry%';

-- Strip legacy prefix (order matters: longer first)
UPDATE trades SET order_id = REPLACE(order_id, 'dry-trigger-', '') WHERE order_id LIKE 'dry-trigger-%';
UPDATE trades SET order_id = REPLACE(order_id, 'dry-', '') WHERE order_id LIKE 'dry-%';
