"""Three-layer ensemble: acoustic (AASIST) + prosody (Praat) + speaker (ECAPA).

This is the composite that `streaming_scorer.LayerScorer` actually runs. Each
layer exposes `score(window_16k) -> flat dict` and keeps its own state (the
speaker reference lives in `SpeakerScorer`; session fusion state lives in
`streaming_scorer`, never here). The fused weights live in
`streaming_scorer` per D6.

A secondary detector ships alongside the acoustic layer: a linear-SVM over
mean MFCCs (WaliMuhammadAhmad/AIVoice-Detection). It is not merged into the
frozen acoustic score — its verdict is only surfaced as a consensus flag on
`context_flags` (`ai_detectors_agree`) so a weak detector can never drag a
genuine caller toward BLOCK.

The ensemble is the single choke point so that a layer that fails to load
(no checkpoint, no reference, offline ECAPA download) degrades to a sensible
neutral value instead of either crashing the scorer loop or silently dropping
the layer.
"""

from __future__ import annotations

import logging

import torch

from aasist_adapter import AasistScorer
from aivoice_svm import SvMfccScorer
from prosody import ProsodyScorer
from speaker import SpeakerScorer

logger = logging.getLogger(__name__)

# Consensus gate: both detectors must clear 0.55 before we claim agreement.
SVM_AGREE_AT = 0.55
AASIST_AGREE_AT = 0.55


class EnsembleScorer:
    """Compose the three layer scorers behind the `LayerScorer` protocol.

    `.score()` returns the frozen {acoustic, prosody, speaker} keys plus an
    optional `"svm"` diagnostic and a `"ai_consensus"` boolean. Callers that
    must keep the wire format exact (streaming_scorer) ignore everything but
    the three layer keys.
    """

    def __init__(
        self,
        acoustic: AasistScorer | None = None,
        prosody: ProsodyScorer | None = None,
        speaker: SpeakerScorer | None = None,
        svm: SvMfccScorer | None = None,
    ) -> None:
        self.acoustic = acoustic if acoustic is not None else AasistScorer.from_environment()
        self.prosody = prosody if prosody is not None else ProsodyScorer()
        self.speaker = speaker if speaker is not None else SpeakerScorer()
        self.svm = svm

    @classmethod
    def from_environment(cls) -> "EnsembleScorer":
        acoustic: AasistScorer | None = None
        try:
            acoustic = AasistScorer.from_environment()
        except (FileNotFoundError, ImportError, RuntimeError) as exc:
            logger.warning("AASIST unavailable (%s); acoustic layer degraded", exc)
        svm: SvMfccScorer | None = None
        try:
            svm = SvMfccScorer.from_environment()
        except (FileNotFoundError, ImportError, RuntimeError) as exc:
            logger.warning("AIVoice SVM unavailable (%s); secondary detector disabled", exc)
        return cls(acoustic, ProsodyScorer(), SpeakerScorer(), svm)

    def score(self, window_16k: torch.Tensor) -> dict[str, float]:
        layers: dict[str, float] = {"acoustic": 0.5, "prosody": 0.5, "speaker": 0.5}
        if self.acoustic is not None:
            layers["acoustic"] = self.acoustic.score(window_16k)["acoustic"]
        layers.update(self.prosody.score(window_16k))
        layers.update(self.speaker.score(window_16k))

        result: dict[str, float] = {
            "acoustic": round(float(layers["acoustic"]), 4),
            "prosody": round(float(layers["prosody"]), 4),
            "speaker": round(float(layers["speaker"]), 4),
            "voiced_ratio": round(float(layers.get("voiced_ratio", 1.0)), 4),
            "silence_ratio": round(float(layers.get("silence_ratio", 1.0)), 4),
            "f0_cv": round(float(layers.get("f0_cv", 1.0)), 4),
        }
        if self.svm is not None:
            svm_risk = self.svm.score(window_16k)["svm"]
            result["svm"] = round(svm_risk, 4)
            result["ai_consensus"] = bool(
                result["acoustic"] > AASIST_AGREE_AT and svm_risk > SVM_AGREE_AT
            )
        return result


def _selfcheck() -> None:
    scorer = EnsembleScorer.from_environment()
    window = torch.randn(16000 * 4)
    layers = scorer.score(window)
    assert set(layers) >= {"acoustic", "prosody", "speaker"}, layers
    assert all(0.0 <= v <= 1.0 for v in layers.values()), layers
    print(f"ok: ensemble layers acoustic={layers['acoustic']:.3f} "
          f"prosody={layers['prosody']:.3f} speaker={layers['speaker']:.3f}"
          + (f" svm={layers.get('svm', 0.0):.3f}" if "svm" in layers else ""))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _selfcheck()