CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    size DOUBLE PRECISION NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    fee DOUBLE PRECISION NOT NULL,
    pnl DOUBLE PRECISION,
    strategy_signal TEXT,
    order_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);

DO $$
BEGIN
    ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
EXCEPTION WHEN others THEN NULL;
END $$;

DO $$
BEGIN
    CREATE POLICY "Allow all access" ON trades FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
