-- SIH26104 normalized schema design (PostgreSQL syntax, reference only).
-- The runtime database is SQLite (`db/voiceclone.sqlite3`); the live DDL is
-- maintained in service/api.py `_init_sqlite` (CREATE TABLE IF NOT EXISTS +
-- the audio_chunks layer-column migration). Keep both tables in sync.
-- Deliberately stores derived features and scores only; raw audio is never persisted.

CREATE TABLE calls (
    id              SERIAL PRIMARY KEY,
    external_ref    VARCHAR(128),
    source_type     VARCHAR(20) NOT NULL CHECK (source_type IN ('upload', 'stream')),
    started_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMP,
    status          VARCHAR(20) NOT NULL DEFAULT 'in_progress',
    language        VARCHAR(20)
);

CREATE TABLE audio_chunks (
    id              SERIAL PRIMARY KEY,
    call_id         INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    start_ms        INTEGER NOT NULL,
    end_ms          INTEGER NOT NULL,
    raw_score       FLOAT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE risk_scores (
    id              SERIAL PRIMARY KEY,
    call_id         INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    risk_score      FLOAT NOT NULL,
    confidence_tier VARCHAR(10) NOT NULL,
    computed_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE alerts (
    id              SERIAL PRIMARY KEY,
    call_id         INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    risk_score_id   INTEGER REFERENCES risk_scores(id),
    severity        VARCHAR(10) NOT NULL,
    message         TEXT,
    raised_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    acknowledged    BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged_by VARCHAR(100),
    acknowledged_at TIMESTAMP
);

CREATE TABLE case_reviews (
    id              SERIAL PRIMARY KEY,
    call_id         INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    reviewer        VARCHAR(100) NOT NULL,
    decision        VARCHAR(30) NOT NULL,
    notes           TEXT,
    reviewed_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audio_chunks_call_id ON audio_chunks(call_id);
CREATE INDEX idx_risk_scores_call_id ON risk_scores(call_id);
CREATE INDEX idx_alerts_call_ack ON alerts(call_id, acknowledged);
CREATE INDEX idx_calls_status ON calls(status);
