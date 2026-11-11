"""Flask API for the live voice-cloning detection workflow.

REST endpoints, WebSocket push, SQLite persistence (derived scores only; raw
audio is never persisted), and - when legacy Supabase is configured - an
async buffered flush of score points.

Contract notes (see Reference/api-contract.md):
  * WebSocket messages and /session/<call_id>/risk follow the frozen shape
    {"call_id","t","score","layers","context_flags","state"}.
  * AASIST needs 16 kHz raw audio. The acquisition rate is negotiated per
    source: /ws/live browser ingest streams wideband 16 kHz (default 16000,
    override to 8000 only when explicitly requested), while AudioSocket /
    file feeders stay 8 kHz and are upsampled 8 -> 16 kHz before any model
    input. AASIST must never see a narrowband waveform (highest-risk defect,
    keep the invariant explicit).
  * Analysis endpoints return a clear `inconclusive` status for short or
    silent audio rather than a fabricated score (master prompt sec. 9).
"""

from __future__ import annotations

import json
import io
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
import wave
from pathlib import Path
from uuid import uuid4

from contextlib import contextmanager
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask import send_from_directory
from flask_sock import Sock
import numpy as np
import torch

import file_feeder
from streaming_scorer import (
    StreamingScorer, W_ACOUSTIC, W_SPEAKER, W_PROSODY,
    SPEECH_VOICED_MIN, NON_SPEECH_MAX, _is_non_speech, gate_fused,
)
from layers import EnsembleScorer

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
if os.getenv("LOG_FILE"):
    logging.getLogger().addHandler(
        logging.FileHandler(os.getenv("LOG_FILE"), encoding="utf-8")
    )
logger = logging.getLogger("api")

SUPABASE_URL = os.getenv("VITE_SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("VITE_SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")
PG_FLUSH_SECONDS = 2.0
SQLITE_FLUSH_SECONDS = 1.0

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # reject >16 MB uploads cleanly (413)
sock = Sock(app)
state_lock = threading.RLock()
clients: set = set()
latest: dict[str, dict] = {}
pg_buf: list[dict] = []
sqlite_buf: list[dict] = []
model_lock = threading.Lock()
scorer: EnsembleScorer | None = None

# Streaming sessions tracked for SQLite persistence: call_db_id keyed by
# stream call_id, plus per-call alert dedup (one alert per escalation run).
stream_calls: dict[str, int] = {}
alerts_raised: set[int] = set()
# Per-call persistence counters. The live /ws/live path hops every 0.25 s,
# while file/AudioSocket sources hop every 0.5 s, so chunk_index cannot be
# derived from t (it would collide); keep a monotonic counter per call.
stream_chunk_counts: dict[str, int] = {}
stream_last_t: dict[str, float] = {}


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin in {"http://127.0.0.1:5173", "http://localhost:5173"}:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.errorhandler(413)
@app.errorhandler(400)
@app.errorhandler(422)
def _apierror(exc):
    """Keep the API's error surface JSON even for framework-level rejections."""
    return jsonify({"status": "inconclusive", "error": getattr(exc, "description", str(exc))}), exc.code if hasattr(exc, "code") else 400


@app.route("/api/<path:unused>", methods=["OPTIONS"])
def api_options(unused):
    return ("", 204)


SQLITE_PATH = Path(__file__).resolve().parents[1] / "db" / "voiceclone.sqlite3"


@contextmanager
def _db():
    """SQLite connection context manager: commits on success, closes always.

    A bare `with sqlite3.connect(...)` commits/rolls back the transaction but
    does NOT close the connection; under the 1 s flush loop that leaks one file
    handle per tick. Closing here keeps every call site leak-free.
    """
    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _init_sqlite() -> None:
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_ref VARCHAR(128), source_type VARCHAR(20) NOT NULL,
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP, status VARCHAR(20) NOT NULL DEFAULT 'in_progress',
                language VARCHAR(20)
            );
            CREATE TABLE IF NOT EXISTS audio_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL, start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL, raw_score FLOAT NOT NULL,
                acoustic FLOAT NOT NULL DEFAULT 0.0,
                prosody FLOAT NOT NULL DEFAULT 0.0,
                speaker FLOAT NOT NULL DEFAULT 0.0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS risk_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
                risk_score FLOAT NOT NULL, confidence_tier VARCHAR(10) NOT NULL,
                computed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
                risk_score_id INTEGER REFERENCES risk_scores(id), severity VARCHAR(10) NOT NULL,
                message TEXT, raised_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                acknowledged BOOLEAN NOT NULL DEFAULT 0, acknowledged_by VARCHAR(100),
                acknowledged_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS case_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
                reviewer VARCHAR(100) NOT NULL, decision VARCHAR(30) NOT NULL,
                notes TEXT, reviewed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS speaker_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                speaker VARCHAR(100) NOT NULL,
                embedding BLOB NOT NULL,
                enrolled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_audio_chunks_call_id ON audio_chunks(call_id);
            CREATE INDEX IF NOT EXISTS idx_risk_scores_call_id ON risk_scores(call_id);
            CREATE INDEX IF NOT EXISTS idx_alerts_call_ack ON alerts(call_id, acknowledged);
            CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
        """)
        # Migrate pre-layer databases: add per-layer chunk columns if missing.
        for column in ("acoustic", "prosody", "speaker"):
            try:
                connection.execute(f"ALTER TABLE audio_chunks ADD COLUMN {column} FLOAT NOT NULL DEFAULT 0.0")
            except sqlite3.OperationalError:
                pass  # already present


_init_sqlite()


def _ok_message(call_id: str) -> dict:
    return {"call_id": call_id, "t": 0.0, "score": 0.0,
            "layers": {"acoustic": 0.0, "prosody": 0.0, "speaker": 0.0},
            "context_flags": [], "state": "OK"}


def _score_row(message: dict) -> dict:
    return {"call_id": message["call_id"], "t": message["t"], "score": message["score"],
            "acoustic": message["layers"]["acoustic"], "prosody": message["layers"]["prosody"],
            "speaker": message["layers"]["speaker"], "context_flags": message["context_flags"],
            "state": message["state"]}


def _get_scorer() -> EnsembleScorer:
    global scorer
    with model_lock:
        if scorer is None:
            scorer = EnsembleScorer.from_environment()
            _restore_reference(scorer)
        return scorer


def _get_speaker():
    """Return the shared SpeakerScorer so /api/enroll can update its reference."""
    return _get_scorer().speaker


def _restore_reference(ensemble: EnsembleScorer) -> None:
    """Load the most recently enrolled reference from SQLite on startup so a
    restart (Testing.md T12) keeps speaker comparison working."""
    try:
        with _db() as connection:
            row = connection.execute(
                "SELECT embedding FROM speaker_references ORDER BY enrolled_at DESC LIMIT 1"
            ).fetchone()
        if row is not None:
            embedding = np.frombuffer(row["embedding"], dtype=np.float32)
            ensemble.speaker.set_reference(embedding)
            logger.info("restored enrolled speaker reference from SQLite")
    except Exception as exc:
        logger.warning("speaker reference restore failed: %s", exc)


def _tier(risk_score: float) -> str:
    # Same 0.55 / 0.80 boundaries as the frozen API contract, expressed on the
    # 0-100 persistence scale. Keeping these in sync means a score persisted as
    # "high" is exactly the score the live state calls BLOCK (and never before).
    if risk_score >= float(os.getenv("RISK_THRESHOLD_HIGH", "80")):
        return "high"
    if risk_score >= float(os.getenv("RISK_THRESHOLD_MEDIUM", "55")):
        return "medium"
    return "low"


def _fused_score(layers: dict[str, float]) -> float:
    """Same convex combination as streaming_scorer; never re-learned (D6)."""
    return (
        W_ACOUSTIC * layers["acoustic"]
        + W_SPEAKER * layers["speaker"]
        + W_PROSODY * layers["prosody"]
    )


def _read_audio(file_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(file_bytes), "rb") as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise ValueError("audio must be 16-bit mono WAV")
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        if source.getframerate() != 16000:
            positions = np.linspace(0, len(samples) - 1, max(1, int(len(samples) * 16000 / source.getframerate())))
            samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
        return samples


def _split_windows(samples: np.ndarray, window: int) -> list[np.ndarray]:
    return [samples[start:start + window] for start in range(0, len(samples) - window + 1, window)]


# ---------------------------------------------------------------------------
# SQLite persistence for streams (both /session and /demo).
# ---------------------------------------------------------------------------

def _insert_stream_chunk(call_db_id: int, chunk_index: int, start_ms: int, end_ms: int,
                         score: float, layers: dict[str, float]) -> None:
    with state_lock:
        sqlite_buf.append({
            "kind": "chunk", "call_db_id": call_db_id,
            "chunk_index": chunk_index, "start_ms": start_ms, "end_ms": end_ms,
            "raw_score": score,
            "acoustic": layers["acoustic"], "prosody": layers["prosody"],
            "speaker": layers["speaker"],
        })


def _insert_stream_risk(call_db_id: int, risk_score: float, tier: str) -> None:
    with state_lock:
        sqlite_buf.append({"kind": "risk", "call_db_id": call_db_id,
                           "risk_score": risk_score, "tier": tier})


def _persist(sqlite_rows: list[dict]) -> None:
    """Apply buffered rows inside one transaction. Raw audio is never stored."""
    with _db() as connection:
        for row in sqlite_rows:
            if row["kind"] == "chunk":
                connection.execute(
                    "INSERT INTO audio_chunks (call_id, chunk_index, start_ms, end_ms, "
                    "raw_score, acoustic, prosody, speaker) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (row["call_db_id"], row["chunk_index"], row["start_ms"], row["end_ms"],
                     row["raw_score"], row["acoustic"], row["prosody"], row["speaker"]),
                )
            elif row["kind"] == "risk":
                cursor = connection.execute(
                    "INSERT INTO risk_scores (call_id, risk_score, confidence_tier) VALUES (?, ?, ?)",
                    (row["call_db_id"], row["risk_score"], row["tier"]),
                )
                risk_id = cursor.lastrowid
                if row.get("tier") in ("medium", "high") and row["call_db_id"] not in alerts_raised:
                    if row["tier"] == "high":
                        severity, message = "block", "High spoof likelihood detected"
                    else:
                        severity, message = "flag", "Elevated spoof likelihood detected"
                    cursor = connection.execute(
                        "INSERT INTO alerts (call_id, risk_score_id, severity, message) VALUES (?, ?, ?, ?)",
                        (row["call_db_id"], risk_id, severity, message),
                    )
                    if cursor.lastrowid:
                        # Keep the call status untouched here: feeder completion
                        # (in _start_audio_thread) owns the 'completed' transition.
                        alerts_raised.add(row["call_db_id"])


def _sqlite_flush_loop() -> None:
    while True:
        time.sleep(SQLITE_FLUSH_SECONDS)
        with state_lock:
            if not sqlite_buf:
                continue
            rows = sqlite_buf[:]
            sqlite_buf.clear()
        try:
            _persist(rows)
        except Exception as exc:
            logger.warning("sqlite flush of %d rows failed: %s", len(rows), exc)


def _make_supabase():
    if os.getenv("ENABLE_LEGACY_SUPABASE_FLUSH", "false").lower() != "true":
        return None
    if not (SUPABASE_URL and SUPABASE_KEY):
        logger.warning("Supabase credentials missing - legacy flush disabled")
        return None
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as exc:
        logger.warning("Supabase init failed (%s) - legacy flush disabled", exc)
        return None


supabase = _make_supabase()


def _insert_session(call_id: str, payload: dict) -> None:
    if not supabase:
        return
    try:
        supabase.table("call_session").insert({
            "call_id": call_id, "codec": "ulaw",
            "origin_number": payload.get("origin_number"), "final_state": "OK",
        }).execute()
    except Exception as exc:
        logger.warning("call_session insert failed: %s", exc)


def _flush_loop() -> None:
    while True:
        time.sleep(PG_FLUSH_SECONDS)
        if not supabase:
            continue
        with state_lock:
            if not pg_buf:
                continue
            rows = pg_buf[:]
            pg_buf.clear()
        try:
            supabase.table("score_point").insert(rows).execute()
        except Exception as exc:
            logger.warning("score_point flush of %d rows failed: %s", len(rows), exc)


def _emit(message: dict) -> None:
    _persist_stream_message(message)
    with state_lock:
        latest[message["call_id"]] = message
        pg_buf.append(_score_row(message))
        dead = set()
        for client in clients:
            try:
                client.send(json.dumps(message))
            except Exception:
                dead.add(client)
        clients.difference_update(dead)


def _persist_stream_message(message: dict) -> None:
    """Persist one live score message to SQLite (called on every emission)."""
    call_id = message["call_id"]
    call_db_id = stream_calls.get(call_id)
    if call_db_id is None:
        return
    t = float(message["t"])
    with state_lock:
        chunk_index = stream_chunk_counts.get(call_id, 0)
        stream_chunk_counts[call_id] = chunk_index + 1
        previous_t = stream_last_t.get(call_id)
        stream_last_t[call_id] = t
    end_ms = int(t * 1000)
    hop_ms = end_ms if previous_t is None else max(0, int((t - previous_t) * 1000))
    start_ms = max(0, end_ms - hop_ms)
    _insert_stream_chunk(call_db_id, chunk_index, start_ms, end_ms,
                         float(message["score"]), message["layers"])
    _insert_stream_risk(call_db_id, float(message["score"]) * 100.0, _tier(float(message["score"]) * 100.0))


def _start_audio_thread(call_id: str, wav_path: Path, payload: dict) -> None:
    def run() -> None:
        shared = _get_scorer()
        scorer_session = StreamingScorer(
            call_id, _emit, scorer=shared,
            context_flags=payload.get("context_flags", ["unknown_origin"]),
        )
        final_status = "completed"
        try:
            file_feeder.feed(wav_path, scorer_session.feed)
        except Exception as exc:
            logger.error("call %s feeder failed: %s", call_id, exc)
            final_status = "failed"
        finally:
            with _db() as connection:
                call_db_id = stream_calls.get(call_id)
                if call_db_id is not None:
                    connection.execute(
                        "UPDATE calls SET status = ?, ended_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (final_status, call_db_id),
                    )
            with state_lock:
                stream_chunk_counts.pop(call_id, None)
                stream_last_t.pop(call_id, None)
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------
# Built dashboard (single serving process: no separate Vite dev server needed)
# ---------------------------------------------------------------------------

_DIST = Path(__file__).resolve().parents[1] / "dashboard" / "dist"


@app.get("/")
def dashboard_index():
    return send_from_directory(_DIST, "index.html")


@app.get("/assets/<path:filename>")
def dashboard_assets(filename):
    return send_from_directory(_DIST / "assets", filename)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    # Never call _get_scorer() here: the first call cold-loads AASIST + ECAPA +
    # SVM (tens of seconds), so health probes timed out and supervisors started
    # duplicate API processes. Report the cached state instead.
    models_loaded = scorer is not None
    return jsonify({"status": "ok", "service": "voice-cloning-detection",
                    "models_loaded": models_loaded,
                    "database": "sqlite",
                    "legacy_supabase": bool(supabase),
                    "speaker_reference": bool(
                        models_loaded and scorer.speaker.has_reference)})


@app.post("/api/enroll")
def enroll_speaker():
    """Enrol a speaker reference THROUGH the same codec path as live audio (D5).

    Accepts a mono WAV; the audio is pushed through the G.711 mu-law -> 8 kHz
    -> upsample back to 16 kHz round trip used by the live call, then embedded
    with ECAPA. Only the 192-dim embedding is persisted - raw audio is never
    kept.
    """
    uploaded = request.files.get("audio") or request.files.get("file")
    if uploaded is None:
        return jsonify({"status": "inconclusive", "error": "audio WAV file is required"}), 400
    speaker_name = (request.form.get("speaker") or "default").strip() or "default"

    import tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(uploaded.read())
            tmp_path = Path(tmp.name)
        try:
            pcm_8k = file_feeder.transcode_ulaw(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    except (ValueError, wave.Error, subprocess.CalledProcessError, FileNotFoundError) as exc:
        return jsonify({"status": "inconclusive", "error": str(exc)}), 400

    samples8 = np.frombuffer(pcm_8k, dtype="<i2").astype(np.float32) / 32768.0
    if samples8.size < 16000:
        return jsonify({"status": "inconclusive", "error": "enrolment needs at least one second of audio"}), 422
    window_16k = torch.from_numpy(_upsample_8k_to_16k(samples8))

    speaker_layer = _get_speaker()
    try:
        embedding = speaker_layer.embed(window_16k).squeeze(0).squeeze(0).numpy().astype(np.float32)
        if np.max(np.abs(embedding)) < 1e-9:
            return jsonify({"status": "inconclusive", "error": "could not extract a voiceprint from this audio"}), 422
    except RuntimeError as exc:
        return jsonify({"status": "inconclusive", "error": f"speaker model unavailable: {exc}"}), 503

    speaker_layer.set_reference(embedding)
    with _db() as connection:
        cursor = connection.execute(
            "INSERT INTO speaker_references (speaker, embedding) VALUES (?, ?)",
            (speaker_name, embedding.tobytes()),
        )
    return jsonify({"speaker": speaker_name, "embedding_dim": int(embedding.size),
                    "enrolled": True, "reference_id": cursor.lastrowid})


@app.get("/api/enroll")
def get_enrolled():
    with _db() as connection:
        row = connection.execute("SELECT * FROM speaker_references ORDER BY enrolled_at DESC LIMIT 1").fetchone()
    speaker = _get_scorer().speaker
    enrolled = bool(speaker.has_reference)
    from_db = False
    if not enrolled and row is not None:
        embedding = np.frombuffer(row["embedding"], dtype=np.float32)
        speaker.set_reference(embedding)
        enrolled = True
        from_db = True
    return jsonify({"enrolled": enrolled,
                    "speaker": row["speaker"] if row else None,
                    "from_db": from_db})


@app.post("/api/analyze")
def analyze_audio():
    uploaded = request.files.get("audio") or request.files.get("file")
    if uploaded is None:
        return jsonify({"status": "inconclusive", "error": "audio WAV file is required"}), 400
    try:
        samples = _read_audio(uploaded.read())
    except (ValueError, EOFError, wave.Error) as exc:
        return jsonify({"status": "inconclusive", "error": str(exc)}), 400
    if len(samples) < 16000 or float(np.max(np.abs(samples), initial=0.0)) < 0.001:
        return jsonify({"status": "inconclusive", "error": "audio must contain at least one second of speech"}), 422

    external_ref = request.form.get("call_id")
    with _db() as connection:
        cursor = connection.execute(
            "INSERT INTO calls (external_ref, source_type, language) VALUES (?, 'upload', ?)",
            (external_ref, request.form.get("language")),
        )
        call_db_id = cursor.lastrowid

    ensemble = _get_scorer()
    window = 64600  # AASIST input samples
    chunks: list[dict] = []
    fused_list: list[float] = []
    for part in _split_windows(samples, window) or [samples]:
        tensor = torch.from_numpy(part)
        layers = ensemble.score(tensor)
        # Non-speech gate (music / tones / silence must not read as a cloned
        # voice): same rule as streaming_scorer, applied to the upload path.
        non_speech = _is_non_speech(layers)
        score = round(gate_fused(_fused_score(layers), layers), 4)
        chunks.append({"raw_score": score, **layers})
        fused_list.append(score)

    risk_score = round(float(np.mean(fused_list)) * 100.0, 1)
    tier = _tier(risk_score)
    with _db() as connection:
        for index, chunk in enumerate(chunks):
            connection.execute(
                "INSERT INTO audio_chunks (call_id, chunk_index, start_ms, end_ms, raw_score, "
                "acoustic, prosody, speaker) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (call_db_id, index, index * 1000, (index + 1) * 1000,
                 chunk["raw_score"], chunk["acoustic"], chunk["prosody"], chunk["speaker"]),
            )
        cursor = connection.execute(
            "INSERT INTO risk_scores (call_id, risk_score, confidence_tier) VALUES (?, ?, ?)",
            (call_db_id, risk_score, tier),
        )
        risk_id = cursor.lastrowid
        alert = None
        if tier in ("medium", "high"):
            severity = "block" if tier == "high" else "flag"
            message = "High spoof likelihood detected" if tier == "high" else "Elevated spoof likelihood detected"
            cursor = connection.execute(
                "INSERT INTO alerts (call_id, risk_score_id, severity, message) VALUES (?, ?, ?, ?)",
                (call_db_id, risk_id, severity, message),
            )
            alert = {"id": cursor.lastrowid, "severity": severity, "message": message}
        connection.execute("UPDATE calls SET status = 'completed', ended_at = CURRENT_TIMESTAMP WHERE id = ?",
                           (call_db_id,))

    return jsonify({"call_id": call_db_id, "chunk_scores": [c["raw_score"] for c in chunks],
                    "chunks": chunks, "risk_score": risk_score,
                    "confidence_tier": tier, "status": "completed",
                    "alert": alert, "speaker_reference": bool(_get_scorer().speaker.has_reference)})


@app.get("/api/calls")
def list_calls():
    with _db() as connection:
        rows = connection.execute("SELECT * FROM calls ORDER BY started_at DESC LIMIT 100").fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/calls/<int:call_id>")
def call_detail(call_id: int):
    with _db() as connection:
        call = connection.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        if call is None:
            return jsonify({"error": "call not found"}), 404
        chunks = connection.execute("SELECT * FROM audio_chunks WHERE call_id = ? ORDER BY chunk_index", (call_id,)).fetchall()
        scores = connection.execute("SELECT * FROM risk_scores WHERE call_id = ? ORDER BY computed_at", (call_id,)).fetchall()
        alerts = connection.execute("SELECT * FROM alerts WHERE call_id = ? ORDER BY raised_at", (call_id,)).fetchall()
        reviews = connection.execute("SELECT * FROM case_reviews WHERE call_id = ? ORDER BY reviewed_at", (call_id,)).fetchall()
    return jsonify({"call": dict(call), "chunks": [dict(row) for row in chunks],
                    "risk_scores": [dict(row) for row in scores], "alerts": [dict(row) for row in alerts],
                    "reviews": [dict(row) for row in reviews]})


@app.get("/api/alerts")
def list_alerts():
    acknowledged = request.args.get("acknowledged", "false").lower() == "true"
    with _db() as connection:
        rows = connection.execute("SELECT * FROM alerts WHERE acknowledged = ? ORDER BY raised_at DESC", (int(acknowledged),)).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/alerts/<int:alert_id>/acknowledge")
def acknowledge_alert(alert_id: int):
    payload = request.get_json(silent=True) or {}
    analyst = payload.get("acknowledged_by")
    if not analyst:
        return jsonify({"error": "acknowledged_by is required"}), 400
    with _db() as connection:
        cursor = connection.execute(
            "UPDATE alerts SET acknowledged = 1, acknowledged_by = ?, acknowledged_at = CURRENT_TIMESTAMP WHERE id = ?",
            (analyst, alert_id),
        )
        if cursor.rowcount == 0:
            return jsonify({"error": "alert not found"}), 404
    return jsonify({"id": alert_id, "acknowledged": True, "acknowledged_by": analyst})


@app.post("/api/calls/<int:call_id>/review")
def submit_review(call_id: int):
    payload = request.get_json(silent=True) or {}
    reviewer = payload.get("reviewer")
    decision = payload.get("decision")
    allowed = {"confirmed_spoof", "confirmed_genuine", "needs_more_evidence"}
    if not reviewer or decision not in allowed:
        return jsonify({"error": "reviewer and a valid decision are required"}), 400
    with _db() as connection:
        if connection.execute("SELECT 1 FROM calls WHERE id = ?", (call_id,)).fetchone() is None:
            return jsonify({"error": "call not found"}), 404
        cursor = connection.execute(
            "INSERT INTO case_reviews (call_id, reviewer, decision, notes) VALUES (?, ?, ?, ?)",
            (call_id, reviewer, decision, payload.get("notes")),
        )
    return jsonify({"id": cursor.lastrowid, "call_id": call_id, "decision": decision}), 201


# ---------------------------------------------------------------------------
# Streaming endpoints (feeder / AudioSocket compatible)
# ---------------------------------------------------------------------------

def _upsample_8k_to_16k(samples_8k: np.ndarray) -> np.ndarray:
    """Single source of truth for the 8->16 kHz upsample shared by the
    enrolment and stream paths. Must always run before any model input."""
    import torch.nn.functional as F
    tensor = torch.from_numpy(samples_8k).float()
    out = F.interpolate(tensor.view(1, 1, -1), size=tensor.numel() * 2,
                        mode="linear", align_corners=False).view(-1)
    return out.numpy()


@app.post("/session")
def create_session():
    payload = request.get_json(silent=True) or {}
    wav_path = payload.get("wav_path")
    if not wav_path:
        return jsonify({"error": "wav_path is required"}), 400
    # Resolve relative paths against the project root so callers are not
    # dependent on the server process's working directory.
    candidate = Path(wav_path)
    if not candidate.is_absolute():
        candidate = Path(__file__).resolve().parents[1] / candidate
    if not candidate.is_file():
        return jsonify({"error": f"wav file not found: {wav_path}"}), 404
    call_id = payload.get("call_id") or f"feeder-{uuid4().hex[:8]}"
    with _db() as connection:
        cursor = connection.execute(
            "INSERT INTO calls (external_ref, source_type, status) VALUES (?, 'stream', 'in_progress')",
            (call_id,),
        )
        stream_calls[call_id] = cursor.lastrowid
    with state_lock:
        latest.setdefault(call_id, _ok_message(call_id))
    if supabase:
        threading.Thread(target=_insert_session, args=(call_id, payload), daemon=True).start()
    _start_audio_thread(call_id, candidate, payload)
    return jsonify({"call_id": call_id}), 202


@app.post("/demo")
def start_demo():
    """Start a bundled demo stream so the dashboard has a live data source.

    `source` selects the clip: "sample" (bundled synthetic-voice sample, default),
    "human" (ASVspoof 2021 bonafide speech), or "bot" (ASVspoof 2021
    voice-conversion spoof). Everything flows through the identical
    8 kHz G.711 codec path the scorer always sees.
    """
    body = request.get_json(silent=True) or {}
    source = str(body.get("source", "sample") or "sample")
    # Allowlist keeps the demo self-contained: no filesystem traversal.
    clips = {
        "sample": "sample.wav",
        "human": "human_long.wav",
        "bot": "bot_8s.wav",
    }
    if source not in clips:
        source = "sample"
    demo_path = Path(__file__).resolve().parents[1] / "demo" / clips[source]
    if not demo_path.is_file():
        return jsonify({"error": f"demo/{clips[source]} is missing"}), 404
    call_id = f"demo-{uuid4().hex[:8]}"
    with _db() as connection:
        cursor = connection.execute(
            "INSERT INTO calls (external_ref, source_type, status, language) "
            "VALUES (?, 'stream', 'in_progress', 'demo')",
            (call_id,),
        )
        stream_calls[call_id] = cursor.lastrowid
    with state_lock:
        latest[call_id] = _ok_message(call_id)
    _start_audio_thread(call_id, demo_path, {"context_flags": ["unknown_origin", "high_value_txn"]})
    return jsonify({"call_id": call_id, "source": source}), 202


@app.get("/session/<call_id>/risk")
def get_risk(call_id: str):
    with state_lock:
        message = latest.get(call_id) or _ok_message(call_id)
    return jsonify(message)


@sock.route("/ws")
def websocket_stream(ws):
    with state_lock:
        clients.add(ws)
    try:
        while ws.receive() is not None:
            pass
    finally:
        with state_lock:
            clients.discard(ws)


@sock.route("/ws/live")
def websocket_live(ws):
    """Ingest live microphone PCM and score it through the identical
    ring-buffer pipeline the file / AudioSocket feeds use.

    First frame may be JSON text {"start": true, "call_id": "...", "rate": N} so
    the UI can key on the broadcast stream and choose the ingest rate; it
    defaults to a self-generated id at 16 kHz. Binary frames are linear s16le
    mono at the negotiated rate (any size; buffered into 320-byte chunks).
    Chunks are scored as-is: the browser already produced the telephony-grade
    signal, and the AudioSocket payload is linear PCM too (0x10 type). A forced
    G.711 re-quantisation measurably flips the AASIST acoustic layer to 1.0 on
    natural speech (false positives), so it is deliberately absent. The live
    browser path streams wideband 16 kHz so AASIST sees its native input
    distribution; 8 kHz stays for AudioSocket / feeder sources. Raw audio is
    never persisted.
    """
    first = ws.receive()
    call_id = f"mic-{uuid4().hex[:8]}"
    source_rate = 16000
    if isinstance(first, str):
        try:
            body = json.loads(first)
        except (ValueError, TypeError):
            body = {}
        asked = str(body.get("call_id") or "").strip()
        if asked:
            call_id = re.sub(r"[^A-Za-z0-9_\-]", "-", asked)[:64]
        if body.get("rate") == 8000:
            source_rate = 8000
    elif first is None:
        return

    shared = _get_scorer()
    scorer_session = StreamingScorer(
        call_id, _emit, scorer=shared,
        context_flags=["unknown_origin", "live_mic"],
        src_rate=source_rate,
    )
    with _db() as connection:
        cursor = connection.execute(
            "INSERT INTO calls (external_ref, source_type, status, language) "
            "VALUES (?, 'live', 'in_progress', 'live')",
            (call_id,),
        )
        stream_calls[call_id] = cursor.lastrowid
    with state_lock:
        latest[call_id] = _ok_message(call_id)
    logger.info("live mic session %s started", call_id)

    pending = bytearray(first if isinstance(first, bytes) else b"")
    index = 0
    try:
        while True:
            frame = ws.receive()
            if frame is None:
                break
            if isinstance(frame, str):
                try:
                    if json.loads(frame).get("stop"):
                        break
                except (ValueError, TypeError):
                    pass
                continue
            pending.extend(frame)
            while len(pending) >= file_feeder.CHUNK_BYTES:
                chunk = bytes(pending[:file_feeder.CHUNK_BYTES])
                del pending[:file_feeder.CHUNK_BYTES]
                scorer_session.feed(index, chunk)
                index += 1
    finally:
        with _db() as connection:
            call_db_id = stream_calls.get(call_id)
            if call_db_id is not None:
                connection.execute(
                    "UPDATE calls SET status = 'ended', ended_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (call_db_id,),
                )
        with state_lock:
            stream_chunk_counts.pop(call_id, None)
            stream_last_t.pop(call_id, None)
        logger.info("live mic session %s ended (%d frames)", call_id, index)


threading.Thread(target=_flush_loop, daemon=True).start()
threading.Thread(target=_sqlite_flush_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)