"""AASIST adapter for the official clovaai/aasist repository."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch

AASIST_INPUT_SAMPLES = 64600
AASIST_CONFIG: dict[str, Any] = {
    "architecture": "AASIST",
    "nb_samp": AASIST_INPUT_SAMPLES,
    "first_conv": 128,
    "filts": [70, [1, 32], [32, 32], [32, 64], [64, 64]],
    "gat_dims": [64, 32],
    "pool_ratios": [0.5, 0.7, 0.5, 0.5],
    "temperatures": [2.0, 2.0, 100.0, 100.0],
}


class AasistScorer:
    """Load and run the official AASIST model from a local repository checkout."""

    def __init__(self, repo_path: str | Path, model_path: str | Path) -> None:
        repo_path = Path(repo_path).resolve()
        model_path = Path(model_path).resolve()
        if not (repo_path / "models" / "AASIST.py").is_file():
            raise FileNotFoundError(
                f"AASIST source not found at {repo_path}; set AASIST_REPO_PATH"
            )
        if not model_path.is_file():
            raise FileNotFoundError(
                f"AASIST checkpoint not found at {model_path}; set AASIST_MODEL_PATH"
            )

        sys.path.insert(0, str(repo_path))
        model_module = importlib.import_module("models.AASIST")
        self._model = model_module.Model(dict(AASIST_CONFIG)).to("cpu")
        state = torch.load(model_path, map_location="cpu")
        self._model.load_state_dict(state)
        self._model.eval()

    @classmethod
    def from_environment(cls) -> "AasistScorer":
        project_root = Path(__file__).resolve().parents[1]
        repo_path = os.getenv("AASIST_REPO_PATH", str(project_root / "third_party" / "aasist"))
        default_model = Path(repo_path) / "models" / "weights" / "AASIST.pth"
        model_path = os.getenv("AASIST_MODEL_PATH", str(default_model))
        return cls(repo_path, model_path)

    @torch.inference_mode()
    def score(self, window_16k: torch.Tensor) -> dict[str, float]:
        if window_16k.ndim != 1:
            raise ValueError(f"expected a mono waveform, got shape {tuple(window_16k.shape)}")
        samples = window_16k.detach().cpu().numpy()
        if samples.shape[0] < AASIST_INPUT_SAMPLES:
            # Official pad(): cyclic repetition, never zero padding.
            num_repeats = int(AASIST_INPUT_SAMPLES / samples.shape[0]) + 1
            samples = np.tile(samples, num_repeats)[:AASIST_INPUT_SAMPLES]
        else:
            samples = samples[:AASIST_INPUT_SAMPLES]
        samples = torch.as_tensor(samples, dtype=torch.float32).unsqueeze(0)

        _, logits = self._model(samples)
        probabilities = torch.softmax(logits, dim=1)[0]
        acoustic_risk = float(1.0 - probabilities[1])
        return {
            "acoustic": max(0.0, min(1.0, acoustic_risk)),
            "prosody": 0.0,
            "speaker": 0.0,
        }
