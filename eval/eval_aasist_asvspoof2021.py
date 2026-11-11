"""Evaluate AASIST against ASVspoof 2021 LA eval parquet.

Parquet layout (HF SpeechAntiSpoofingBenchmarks mirror):
    path  – str, e.g. "LA_E_1000048.flac"
    audio – struct<bytes: binary, path: string> (raw FLAC bytes)
    label – int: 0 = bonafide, 1 = spoof
    notes – str (JSON metadata incl. attack_id)

AASIST risk convention: risk = 1 - softmax(model(wave))[1]; 1 = spoof, 0 = bonafide.
EER uses bona fide = positive label guarded by scipy brentq interpolation.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
import torch

ROOT        = Path(__file__).resolve().parents[1]
PARQUET_SHARD = ROOT / "eval_data" / "asvspoof2021_la" / "shard_00.parquet"
CSV_OUT     = ROOT / "eval_data" / "asvspoof2021_la" / "aasist_scores.csv"

AASIST_REPO = ROOT / "third_party" / "aasist"
AASIST_CKPT = AASIST_REPO / "models" / "weights" / "AASIST.pth"
BATCH       = 8
CHECKPOINT  = 500
N_SAMPLES   = 64600


def load_aasist():
    sys.path.insert(0, str(AASIST_REPO))
    from importlib import import_module

    AASIST_CONFIG = {
        "architecture": "AASIST",
        "nb_samp": N_SAMPLES,
        "first_conv": 128,
        "filts": [70, [1, 32], [32, 32], [32, 64], [64, 64]],
        "gat_dims": [64, 32],
        "pool_ratios": [0.5, 0.7, 0.5, 0.5],
        "temperatures": [2.0, 2.0, 100.0, 100.0],
    }
    model_mod = import_module("models.AASIST")
    model = model_mod.Model(AASIST_CONFIG).to("cpu")
    model.load_state_dict(torch.load(str(AASIST_CKPT), map_location="cpu"))
    model.eval()
    return model


def prep_window(wave):
    """Reshape one decoded mono waveform to (1, N_SAMPLES) using official pad().

    clovaai/aasist pads short clips by CYCLIC REPETITION, never zeros.
    """
    if wave.ndim > 1:
        wave = wave.mean(axis=-1)
    wave = np.ascontiguousarray(wave, dtype=np.float32)
    if wave.shape[0] < N_SAMPLES:
        num_repeats = int(N_SAMPLES / wave.shape[0]) + 1
        wave = np.tile(wave, num_repeats)[:N_SAMPLES]
    else:
        wave = wave[:N_SAMPLES]
    return torch.as_tensor(wave)


def score_batch(model, windows):
    batch = torch.stack(windows, dim=0)
    with torch.inference_mode():
        _, logits = model(batch)
    probs = torch.softmax(logits, dim=1)
    return list((1.0 - probs[:, 1]).numpy())


def compute_eer_and_breakdown(results, notes):
    from scipy.optimize import brentq
    from sklearn.metrics import roc_curve

    def eer_for(scores, labels):
        fpr, tpr, thresholds = roc_curve(np.asarray(labels), np.asarray(scores), pos_label=1, drop_intermediate=False)
        fnr = 1.0 - tpr
        unique_fpr, rev_idx = np.unique(fpr[::-1], return_index=True)
        idx = len(fpr) - 1 - rev_idx
        ee = brentq(lambda p: np.interp(p, unique_fpr, fnr[idx]) - p, 0.0, 1.0)
        thr = float(np.interp(ee, unique_fpr, thresholds[idx]))
        return ee, thr

    s = [1 - r["risk"] for r in results]   # bonafide = positive class
    l = [1 - r["label"] for r in results]  # bonafide = 1
    eer, thr = eer_for(s, l)
    n_bf = sum(r["label"] == 0 for r in results)
    n_sp = sum(r["label"] == 1 for r in results)

    print("")
    print("=" * 62)
    print(f"  AASIST on ASVspoof 2021 LA eval subset")
    print(f"  Files: {len(results)}  (bonafide={n_bf}, spoof={n_sp})")
    print(f"  EER:        {eer:.2%}")
    print(f"  Threshold:  {thr:.4f}")
    print(f"  Published:  10.51%  (full LA eval, ~14k files)")
    print("=" * 62)

    attacks = {}
    for r, note_json in zip(results, notes):
        try:
            attack = json.loads(note_json).get("attack_id", "unknown")
        except (json.JSONDecodeError, TypeError):
            attack = "unknown"
        attacks.setdefault(attack, []).append(r)

    print("\nPer-attack EER (evaluation partition attacks):")
    for attack in sorted(attacks):
        rows = attacks[attack]
        if len(rows) < 20:
            continue
        bf = sum(r["label"] == 0 for r in rows)
        sp = len(rows) - bf
        if bf == 0 or sp == 0:
            print(f"  {attack:5s}  n={len(rows):>5}  (bf={bf:>4} sp={sp:>4})  EER=skipped (single class)")
            continue
        ee, _ = eer_for([1 - r["risk"] for r in rows], [1 - r["label"] for r in rows])
        print(f"  {attack:5s}  n={len(rows):>5}  (bf={bf:>4} sp={sp:>4})  EER={ee:.2%}")


def read_scores_csv():
    results = []
    with CSV_OUT.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            results.append({"filename": row["filename"], "risk": float(row["score"]), "label": 1 - int(row["label"])})
    return results


def reseed_from_csv():
    """Return {filename: row} already scored in an existing CSV, and rename it.

    The CSV is checkpointed every CHECKPOINT rows; on restart the old file is
    preserved as *.prev and scoring resumes from the next unscored file.
    """
    if not CSV_OUT.is_file():
        return None
    prev = {}
    kept = []
    with CSV_OUT.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["filename"] in prev:
                continue
            prev[row["filename"]] = {
                "filename": row["filename"],
                "risk": float(row["score"]),
                "label": 1 - int(row["label"]),
            }
            kept.append(row)
    backup = CSV_OUT.with_suffix(".csv.prev")
    backup.write_text(CSV_OUT.read_text(encoding="utf-8"), encoding="utf-8")
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["filename", "score", "label"])
        w.writeheader()
        w.writerows(kept)
    return prev


def _write_csv(results):
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["filename", "score", "label"])
        w.writeheader()
        for r in results:
            w.writerow({"filename": r["filename"], "score": r["risk"], "label": 1 - r["label"]})
    print(f"checkpoint written ({len(results)} rows)", flush=True)


def _attach_log(log_path: str) -> None:
    """Tee stdout/stderr to `log_path` too.

    Under `pythonw` (Task Scheduler, no console) stdout is None; the tee then
    writes only to the file, which is how scheduled eval runs keep a log that
    survives the launcher shell's teardown.
    """
    import io as _io

    log_file = open(log_path, "a", encoding="utf-8")

    class _Tee(_io.TextIOBase):
        def __init__(self, *streams):
            super().__init__()
            self._streams = [s for s in streams if s is not None]

        def write(self, s):
            for stream in self._streams:
                try:
                    stream.write(s)
                    stream.flush()
                except (ValueError, OSError):
                    pass
            return len(s)

        def flush(self):
            for stream in self._streams:
                try:
                    stream.flush()
                except (ValueError, OSError):
                    pass

    tee = _Tee(sys.stdout, log_file)
    sys.stdout = tee
    sys.stderr = tee


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="score only N files (0 = all)")
    ap.add_argument("--csv", action="store_true", help="write per-file score CSV")
    ap.add_argument("--from-csv", action="store_true", help="recompute EER/breakdown from existing CSV")
    args = ap.parse_args()

    if args.from_csv:
        if not CSV_OUT.is_file():
            sys.exit(f"CSV not found: {CSV_OUT}")
        results = read_scores_csv()
        table = pq.read_table(str(PARQUET_SHARD))
        notes = table.column("notes").to_pylist()
        note_lookup = dict(zip(table.column("path").to_pylist(), notes))
        ordered = [r["filename"] for r in results]
        notes_sub = [note_lookup.get(p, "{}") for p in ordered]
        compute_eer_and_breakdown(results, notes_sub)
        return

    if not PARQUET_SHARD.is_file():
        sys.exit(f"Parquet not found: {PARQUET_SHARD}")

    table = pq.read_table(str(PARQUET_SHARD))
    paths = table.column("path").to_pylist()
    labels = table.column("label").to_pylist()
    audio_blobs = table.column("audio").to_pylist()
    notes = table.column("notes").to_pylist()
    if args.limit and args.limit < len(paths):
        paths = paths[:args.limit]
        labels = labels[:args.limit]
        audio_blobs = audio_blobs[:args.limit]
        notes = notes[:args.limit]

    n_total = len(paths)
    n_bf = sum(1 for l in labels if l == 0)
    print(f"Loaded {n_total} utterances (bonafide={n_bf}, spoof={n_total - n_bf})", flush=True)
    print(f"PyTorch threads: {torch.get_num_threads()}", flush=True)

    model = load_aasist()
    print("AASIST model loaded — scoring...", flush=True)

    scored = reseed_from_csv() or {}
    print(f"Resuming: {len(scored)} files already scored, {n_total - len(scored)} remaining", flush=True)

    results = list(scored.values())
    windows = []
    t0 = time.time()
    n_skip = 0
    for i, (path, label, blob) in enumerate(zip(paths, labels, audio_blobs), 1):
        if path in scored:
            n_skip += 1
            continue
        wave, _ = sf.read(io.BytesIO(blob["bytes"]), dtype="float32")
        windows.append((path, label, prep_window(wave)))
        if len(windows) == BATCH or i == n_total:
            batch = windows
            windows = []
            scores = score_batch(model, [w for _, _, w in batch])
            for (p, lbl, _), risk in zip(batch, scores):
                results.append({"filename": p, "risk": risk, "label": lbl})
        done = i - n_skip
        if done % 250 == 0 or i == n_total:
            elapsed = time.time() - t0
            print(f"  {done}/{n_total} — {done / elapsed:.0f} files/s — {elapsed:.0f}s", flush=True)
        if args.csv and done % CHECKPOINT == 0:
            _write_csv(results)

    if args.csv:
        _write_csv(results)

    compute_eer_and_breakdown(results, notes)


if __name__ == "__main__":
    if os.getenv("EVAL_LOG"):
        _attach_log(os.getenv("EVAL_LOG"))
    main()