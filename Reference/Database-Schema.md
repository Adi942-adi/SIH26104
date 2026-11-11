# Database Schema — SQLite

Local file `db/voiceclone.sqlite3`, created and migrated by `service/api.py` on startup. Only derived scores are stored — raw audio is never written (CLAUDE.md hard rule 1). The dashboard reads it through the REST endpoints; there are no database realtime subscriptions.

## Tables

### `calls`
| Column | Type | Note |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| external_ref | VARCHAR(128) | AudioSocket UUID, feeder/scorer call_id, or upload name |
| source_type | VARCHAR(20) NOT NULL | `upload` / `stream` / `live` |
| started_at | TIMESTAMP default CURRENT_TIMESTAMP | |
| ended_at | TIMESTAMP null | |
| status | VARCHAR(20) default `in_progress` | `in_progress` / `completed` / `failed` / `ended` |
| language | VARCHAR(20) null | `demo` marks `/demo` streams |

### `audio_chunks`
One row per scored window (derived scores only, never audio).

| Column | Type |
|---|---|
| id | INTEGER PK AUTOINCREMENT |
| call_id | INTEGER FK → calls(id) ON DELETE CASCADE |
| chunk_index | INTEGER |
| start_ms, end_ms | INTEGER |
| raw_score | FLOAT |
| acoustic, prosody, speaker | FLOAT default 0.0 |
| created_at | TIMESTAMP default CURRENT_TIMESTAMP |

Index: `idx_audio_chunks_call_id (call_id)`.

### `risk_scores`
| Column | Type | Note |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| call_id | INTEGER FK → calls(id) ON DELETE CASCADE | |
| risk_score | FLOAT | fused 0–1 score |
| confidence_tier | VARCHAR(10) | `low` / `medium` / `high` |
| computed_at | TIMESTAMP default CURRENT_TIMESTAMP | |

Index: `idx_risk_scores_call_id (call_id)`.

### `alerts`
| Column | Type | Note |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| call_id | INTEGER FK → calls(id) ON DELETE CASCADE | |
| risk_score_id | INTEGER FK → risk_scores(id) | |
| severity | VARCHAR(10) | `flag` (medium) / `block` (high) |
| message | TEXT | |
| raised_at | TIMESTAMP default CURRENT_TIMESTAMP | |
| acknowledged | BOOLEAN default 0 | |
| acknowledged_by, acknowledged_at | VARCHAR(100), TIMESTAMP | |

Index: `idx_alerts_call_ack (call_id, acknowledged)`.

### `case_reviews`
| Column | Type |
|---|---|
| id | INTEGER PK AUTOINCREMENT |
| call_id | INTEGER FK → calls(id) ON DELETE CASCADE |
| reviewer | VARCHAR(100) |
| decision | VARCHAR(30) |
| notes | TEXT |
| reviewed_at | TIMESTAMP default CURRENT_TIMESTAMP |

### `speaker_references`
| Column | Type | Note |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| speaker | VARCHAR(100) | |
| embedding | BLOB | 192 float32 values; audio is never stored |
| enrolled_at | TIMESTAMP default CURRENT_TIMESTAMP | |

The most recent row is restored on startup so enrolment survives a restart.

## Write pattern — this matters for latency
Scorer messages are buffered in memory and flushed by one background thread every 1 s (`SQLITE_FLUSH_SECONDS`). Each flush opens a short-lived connection and closes it in a `finally` block, so scoring never blocks on disk I/O and file handles do not leak.

## Legacy Supabase
If `ENABLE_LEGACY_SUPABASE_FLUSH=true` and credentials are present, score points are also buffered and flushed asynchronously every 2 s (`PG_FLUSH_SECONDS`). Off by default; SQLite is the source of truth for the demo, and no write path is allowed to stall the scorer loop.
