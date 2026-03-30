CREATE TABLE IF NOT EXISTS risk_events (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    details TEXT NOT NULL,
    action_taken TEXT
);

CREATE INDEX IF NOT EXISTS idx_risk_events_timestamp ON risk_events(timestamp);

DO $$
BEGIN
    ALTER TABLE risk_events ENABLE ROW LEVEL SECURITY;
EXCEPTION WHEN others THEN NULL;
END $$;

DO $$
BEGIN
    CREATE POLICY "Allow all access" ON risk_events FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
