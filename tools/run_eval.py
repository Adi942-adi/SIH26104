"""One-shot ASVspoof 2021 LA evaluation runner (task ``SIH26104_eval``).

The dataset parquet is not shipped with the repository. When it is absent this
script logs a clear message and exits 0 instead of crash-looping a scheduled
task. When present it runs the existing checkpointed evaluator:

    eval/eval_aasist_asvspoof2021.py --csv

which resumes from ``eval_data/asvspoof2021_la/aasist_scores.csv`` and prints
EER / breakdown. Output is tee'd to ``logs/eval.log``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
EVAL_SCRIPT = ROOT / "eval" / "eval_aasist_asvspoof2021.py"
PARQUET = ROOT / "eval_data" / "asvspoof2021_la" / "shard_00.parquet"
LOG_DIR = ROOT / "logs"


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "eval.log"
    if not PARQUET.is_file():
        message = (
            f"ASVspoof parquet not found: {PARQUET}\n"
            "The evaluation dataset is not part of the repository; download "
            "shard_00.parquet into eval_data/asvspoof2021_la/ before running "
            "the SIH26104_eval task.\n"
        )
        if sys.stderr is not None:
            print(message, file=sys.stderr, flush=True)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(message)
        return 0
    python = VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable)
    env = {**os.environ, "EVAL_LOG": str(log_path)}
    return subprocess.call(
        [str(python), str(EVAL_SCRIPT), "--csv"],
        cwd=str(ROOT),
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


if __name__ == "__main__":
    raise SystemExit(main())
