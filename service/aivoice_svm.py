"""MFCC + linear-SVM voice detector, mirroring WaliMuhammadAhmad/AIVoice-Detection.

The upstream repository (`github.com/WaliMuhammadAhmad/AIVoice-Detection`) trains a
linear SVC on the file-level mean of 13 MFCCs (librosa, n_fft=2048, hop_length=512).
Labels: 0 = real human voice, 1 = AI-generated voice.

This module wraps that trained model so it can run inside the ensemble scorer on the
same 4 s / 16 kHz windows the rest of the pipeline sees. Raw decoding is done with
soundfile (never librosa.load) so the scorer has no file I/O of its own.
"""

from __future__ import annotations

import io
from pathlib import Path

import joblib
import librosa
import numpy as np
import soundfile as sf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = PROJECT_ROOT / "models" / "aivoice" / "svm_model.pkl"
DEFAULT_SCALER_PATH = PROJECT_ROOT / "models" / "aivoice" / "scaler.pkl"


class SvMfccScorer:
    """Mean-MFCC -> linear SVM risk score. High risk = AI-generated voice."""

    def __init__(self, model_path: str | Path, scaler_path: str | Path) -> None:
        model_path = Path(model_path)
        scaler_path = Path(scaler_path)
        if not model_path.is_file() or not scaler_path.is_file():
            raise FileNotFoundError(
                f"AIVoice SVM artifacts not found ({model_path}, {scaler_path})"
            )
        self._model = joblib.load(model_path)
        self._scaler = joblib.load(scaler_path)
        self._n_mfcc = 13

    @classmethod
    def from_environment(cls) -> "SvMfccScorer":
        import os

        model_path = os.getenv("AIVOICE_SVM_MODEL", str(DEFAULT_MODEL_PATH))
        scaler_path = os.getenv("AIVOICE_SVM_SCALER", str(DEFAULT_SCALER_PATH))
        return cls(model_path, scaler_path)

    def _extract_mfcc_mean(self, window_16k: np.ndarray, sr: int) -> np.ndarray | None:
        """File-level feature of the upstream repo: mean of 13 MFCC frames."""
        if window_16k.ndim != 1:
            window_16k = window_16k.mean(axis=-1)
        if window_16k.shape[0] == 0:
            return None
        mfccs = librosa.feature.mfcc(
            y=window_16k,
            sr=sr,
            n_mfcc=self._n_mfcc,
            n_fft=2048,
            hop_length=512,
        )
        return np.mean(mfccs.T, axis=0)

    def score(self, window_16k, sr: int = 16000) -> dict[str, float]:
        """Return {'svm': risk} where 1.0 = confident AI-generated voice."""
        if isinstance(window_16k, (list, tuple)):
            window_16k = np.asarray(window_16k, dtype=np.float32)
        elif hasattr(window_16k, "detach"):  # torch tensor
            window_16k = window_16k.detach().cpu().numpy()
        window_16k = np.asarray(window_16k, dtype=np.float32)

        features = self._extract_mfcc_mean(window_16k, sr)
        if features is None:
            return {"svm": 0.5}
        scaled = self._scaler.transform(features.reshape(1, -1))
        decision = float(self._model.decision_function(scaled)[0])
        # sign > 0  => class 1 (fake). Map decision value to a 0..1 risk.
        risk = 1.0 / (1.0 + np.exp(-decision))
        return {"svm": max(0.0, min(1.0, float(risk)))}

    @staticmethod
    def decode_wav_bytes(flac_or_wav: bytes) -> tuple[np.ndarray, int]:
        """Decode raw audio bytes with soundfile; returns mono float32 + sr."""
        audio, sr = sf.read(io.BytesIO(flac_or_wav), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=-1)
        return np.asarray(audio, dtype=np.float32), sr


if __name__ == "__main__":
    # Selfcheck: score the bundled human/bot demo clips.
    scorer = SvMfccScorer.from_environment()
    for path in ("demo/human_voice.wav", "demo/bot_voice.wav", "demo/sample.wav"):
        with sf.SoundFile(Path(__file__).resolve().parents[1] / path) as f:
            wave = f.read(dtype="float32")
            sr = f.samplerate
        out = scorer.score(wave, sr)
        print(f"{path}: svm_risk={out['svm']:.3f}")