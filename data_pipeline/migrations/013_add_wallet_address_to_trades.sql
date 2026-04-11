ALTER TABLE trades ADD COLUMN IF NOT EXISTS wallet_address TEXT;
CREATE INDEX IF NOT EXISTS idx_trades_wallet_address ON trades(wallet_address);
