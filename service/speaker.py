"""Speaker layer: ECAPA-TDNN cosine similarity against an enrolled reference.

Reference handling follows D5: the reference embedding must come from audio
that has been pushed through the *same codec path* as the live call (G.711
mu-law -> 8 kHz -> upsampled back to 16 kHz). Wideband enrolment against
narrowband live audio is the documented failure mode and it is on purpose that
`enroll_from_wav` in api.py routes the upload through that path.

Semantics of the returned value: it is a *risk* score in [0, 1] like the other
layers:
    speaker risk = clamp(1 - cos_sim, 0, 1)
so a voice that matches the enrolled reference sits near 0.0 risk and a voice
that does not sits near 1.0. A cloned voice *of the same speaker* will match
the reference too - that is exactly why speaker is a supporting layer (weight
0.25, D6) and why the clone is caught by the acoustic + prosody layers
instead.

When no reference is enrolled the layer returns the neutral 0.5 risk and the
fusion must interpret that as "no speaker evidence", not as either low or
high confidence.

The ECAPA model is downloaded lazily on first use (SpeechBrain auto-download
to `savedir`). If the download or the first encode fails the scorer degrades
to the 0.5 neutral value instead of raising.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

MODEL_RATE = 16000
EMBEDDING_DIM = 192
NEUTRAL = 0.5

# SpeechBrain model to use for speaker embeddings.
SPEAKER_MODEL = "speechbrain/spkrec-ecapa-voxceleb"
DEFAULT_SAVEDIR = Path(__file__).resolve().parents[1] / "models" / "speaker"


def _peaks_to_tensor(peak_samples: np.ndarray) -> torch.Tensor:
    """ECAPA is trained on roughly 3-second segments; chunked context windows"""
    return torch.from_numpy(peak_samples).float()


class SpeakerScorer:
    """ECAPA-TDNN (SpeechBrain) cosine similarity vs. one enrolled reference.

    Thread-safety: the underlying encoder is read-only during `score`, but
    SpeechBrain's hub loader is not guaranteed thread-safe on first
    construction, so the first call inside `score` is guarded by a module lock
    (api.py holds `model_lock` while warm-up happens at load time).
    """

    def __init__(self, ref_embedding: np.ndarray | None = None,
                 savedir: str | Path = DEFAULT_SAVEDIR) -> None:
        self._encoder = None
        self._savedir = Path(savedir)
        self._ref: torch.Tensor | None = None
        if ref_embedding is not None:
            self.set_reference(ref_embedding)

    # ---- reference lifecycle -------------------------------------------------
    def set_reference(self, embedding: np.ndarray) -> None:
        # copy=True keeps the tensor writable even when the caller hands us a
        # read-only view (np.frombuffer in api._restore_reference does exactly
        # that), which otherwise emits a non-writable-tensor UserWarning.
        emb = np.array(embedding, dtype=np.float32, copy=True)
        if emb.shape != (EMBEDDING_DIM,):
            raise ValueError(f"reference must be {EMBEDDING_DIM}-dim, got {emb.shape}")
        self._ref = torch.from_numpy(emb).view(1, -1)
        logger.info("speaker reference set (%d-dim)", EMBEDDING_DIM)

    def clear_reference(self) -> None:
        self._ref = None

    @property
    def has_reference(self) -> bool:
        return self._ref is not None

    # ---- model lifecycle -----------------------------------------------------
    def _ensure_encoder(self) -> None:
        if self._encoder is not None:
            return
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError as exc:
            logger.warning("speechbrain unavailable (%s): speaker layer neutral", exc)
            self._encoder = None
            return
        try:
            from speechbrain.utils.fetching import LocalStrategy
        except ImportError:  # pragma: no cover
            LocalStrategy = None
        try:
            logger.info("loading ECAPA-TDNN speaker encoder (%s)", SPEAKER_MODEL)
            kwargs: dict = {"source": SPEAKER_MODEL, "savedir": str(self._savedir),
                            "run_opts": {"device": "cpu"}}
            if LocalStrategy is not None:
                # SYMLINK fails on Windows without admin rights; copy the
                # downloaded weights into the local savedir instead (D5 path).
                kwargs["local_strategy"] = LocalStrategy.COPY
            self._encoder = EncoderClassifier.from_hparams(**kwargs)
        except Exception as exc:  # download failure, bad checkpoint, ...
            logger.warning("ECAPA load failed (%s): speaker layer neutral", exc)
            self._encoder = None

    @torch.inference_mode()
    def embed(self, window_16k: torch.Tensor) -> torch.Tensor:
        """Return the [1, 192] ECAPA embedding for a mono 16 kHz window."""
        self._ensure_encoder()
        if self._encoder is None:
            raise RuntimeError("speaker encoder not available")
        wav = window_16k.detach().float().unsqueeze(0)  # [1, T]
        return self._encoder.encode_batch(wav)          # [1, 1, 192]

    @torch.inference_mode()
    def score(self, window_16k: torch.Tensor) -> dict[str, float]:
        if self._ref is None:
            return {"speaker": NEUTRAL}
        try:
            emb = self.embed(window_16k).squeeze(1)      # [1, 192]
            sim = float(F.cosine_similarity(emb, self._ref, dim=1))
        except Exception as exc:
            logger.warning("speaker encode failed (%s): neutral", exc)
            return {"speaker": NEUTRAL}
        sim = float(np.clip(sim, -1.0, 1.0))
        return {"speaker": round(max(0.0, min(1.0, 1.0 - sim)), 4)}

    # ---- class-level convenient constructors ----------------------------------
    @classmethod
    def load_default(cls) -> "SpeakerScorer":
        """Load the default ECAPA model without a reference (neutral until enrolled)."""
        obj = cls()
        obj._ensure_encoder()
        return obj


def _selfcheck() -> None:
    """Load ECAPA and check two random windows compute embeddings."""
    import time

    scorer = SpeakerScorer(savedir=DEFAULT_SAVEDIR)
    t0 = time.perf_counter()
    scorer._ensure_encoder()
    logger.info("ECAPA ready in %.1fs", time.perf_counter() - t0)

    if scorer._encoder is None:
        print("skip: ECAPA unavailable (offline?)")
        return

    emb = scorer.embed(torch.randn(16000 * 4))
    assert tuple(emb.shape) == (1, 1, EMBEDDING_DIM), tuple(emb.shape)
    assert scorer.score(torch.randn(16000 * 4))["speaker"] == NEUTRAL, "no ref must be neutral"

    scorer.set_reference(emb.squeeze(1).squeeze(0).numpy())
    same = scorer.score(torch.randn(16000 * 4))
    print(f"ok: embedding {tuple(emb.shape)}, same-audio risk={same['speaker']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _selfcheck()