"""Prosody layer: F0 contour + pause/rhythm analysis with praat-parselmouth.

Heuristic, honest, and auditable. This layer is a *supporting* signal (fusion
weight 0.15 by D6) and must never gate a call on its own, so the risk mapping
is deliberately conservative:

    prosody risk = 0.45 * monotone_cv + 0.25 * flat_contour
                  + 0.15 * silence_deviation + 0.15 * rate_deviation

Where:
  `monotone_cv`     rises when F0 coefficient-of-variation collapses (TTS
                    tends to be over-stable vs. expressive human F0)
  `flat_contour`    rises when the linear slope of the F0 contour approaches
                    zero (a human speaker never holds one pitch line)
  `silence_deviation` rises when the silence ratio leaves the plausible
                    10-45% range for conversational speech
  `rate_deviation`  rises when the estimated syllable rate leaves the
                    plausible 2.2-6.5 syl/s band

All band edges above are *documented priors*, not learned values (D6). They
are wide so a genuine caller stays near the floor.

Decision D4: jitter and shimmer are measured here and returned in
`prosody_diagnostics`, but contributed weight 0 to the risk. At 8 kHz
telephony they are corrupted by mu-law quantisation.

`score()` is pure and stateless: given one fixed-length 16 kHz window tensor
it returns the same dict every time. There is no session state in this class.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import torch

try:
    import parselmouth
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    parselmouth = None

logger = logging.getLogger(__name__)

MODEL_RATE = 16000
F0_FLOOR = 75.0
F0_CEILING = 500.0

# Documented-prior band edges (see decision D6). Wide on purpose.
CV_KNEE = 0.16              # F0 coefficient of variation below this ...
MONOTONE_EXPONENT = 0.16    # ... is treated as increasingly flat
SLOPE_KNEE = 0.12           # |F0 slope|/mean below this per second = flat
SILENCE_BAND = (0.10, 0.45)
RATE_BAND = (2.2, 6.5)
MIN_VOICED_RATIO = 0.15
MIN_DURATION = 1.5


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


class ProsodyScorer:
    """F0 contour + rhythm risk from a 16 kHz mono window tensor."""

    def score(self, window_16k: torch.Tensor) -> dict[str, float]:
        if window_16k.ndim != 1:
            raise ValueError(f"expected mono waveform, got shape {tuple(window_16k.shape)}")
        return self._score_numpy(window_16k.detach().cpu().numpy().astype(np.float64))

    def _score_numpy(self, samples: np.ndarray) -> dict[str, float]:
        diagnostics: dict[str, float] = {
            "jitter_pct": 0.0,
            "shimmer_pct": 0.0,
            "f0_mean_hz": 0.0,
            "f0_cv": 0.0,
            "silence_ratio": 0.0,
            "syllables_per_sec": 0.0,
        }
        if parselmouth is None:
            return {"prosody": 0.5, "voiced_ratio": 1.0, "silence_ratio": 1.0, "f0_cv": 1.0,
                    "prosody_diagnostics": diagnostics}

        duration = samples.size / MODEL_RATE
        if duration < MIN_DURATION:
            return {"prosody": 0.5, "voiced_ratio": 1.0, "silence_ratio": 1.0, "f0_cv": 1.0,
                    "prosody_diagnostics": diagnostics}

        sound = parselmouth.Sound(samples, sampling_frequency=MODEL_RATE)

        # ---- Pitch contour -------------------------------------------------
        try:
            pitch = sound.to_pitch(time_step=0.01, pitch_floor=F0_FLOOR, pitch_ceiling=F0_CEILING)
            f0 = pitch.selected_array["frequency"]
        except Exception as exc:  # pragma: no cover - Praat edge behaviour
            logger.warning("prosody: pitch extraction failed: %s", exc)
            return {"prosody": 0.5, "voiced_ratio": 1.0, "silence_ratio": 1.0, "f0_cv": 1.0,
                    "prosody_diagnostics": diagnostics}

        f0 = np.asarray(f0, dtype=np.float64)
        f0[~np.isfinite(f0)] = 0.0
        voiced = f0 > 0.0
        voiced_ratio = float(voiced.mean())

        if voiced_ratio < MIN_VOICED_RATIO:
            # Not enough voiced content to judge prosody. Stay neutral.
            return {"prosody": 0.5, "voiced_ratio": round(voiced_ratio, 4),
                    "silence_ratio": 0.5, "f0_cv": 1.0,
                    "prosody_diagnostics": diagnostics}

        f0_v = f0[voiced]
        f0_mean = float(f0_v.mean()) or 1e-9
        f0_sd = float(f0_v.std())
        f0_cv = f0_sd / f0_mean

        # ---- F0 contour slope ---------------------------------------------
        times_v = pitch.xs()[voiced]
        slope = np.polyfit(times_v, f0_v, 1)[0] if times_v.size > 2 else 0.0
        slope_norm = abs(slope) / (f0_mean * max(1.0, times_v[-1] - times_v[0])) if times_v.size > 2 else 0.0

        # ---- Silence ratio (intensity-based, Praat convention) ------------
        try:
            intensity = sound.to_intensity(minimum_pitch=100.0)
            inten = np.asarray(intensity.values).ravel()
            inten = inten[np.isfinite(inten)]
            if inten.size > 0:
                silence_ratio = float(np.mean(inten < (inten.mean() - 30.0)))
                syllable_rate = self._syllable_rate(inten, duration)
            else:  # pragma: no cover
                silence_ratio, syllable_rate = 0.5, 0.0
        except Exception as exc:  # pragma: no cover
            logger.warning("prosody: intensity analysis failed: %s", exc)
            silence_ratio, syllable_rate = 0.5, 0.0

        # ---- Jitter / shimmer (diagnostic only, weight 0 per D4) ----------
        try:
            pulse = sound.to_point_process("pitch")
            jitter = float(pulse.get_jitter_local(0.0, duration, 0.0001, 0.02, 1.3)) * 100.0
        except Exception:
            jitter = 0.0
        try:
            pulse = sound.to_point_process("pitch")
            shimmer = float(pulse.get_shimmer_local(0.0, duration, 0.0001, 0.02, 1.3, 1.6)) * 100.0
        except Exception:
            shimmer = 0.0

        diagnostics.update({
            "jitter_pct": round(jitter, 3),
            "shimmer_pct": round(shimmer, 3),
            "f0_mean_hz": round(f0_mean, 1),
            "f0_cv": round(f0_cv, 4),
            "silence_ratio": round(silence_ratio, 4),
            "syllables_per_sec": round(syllable_rate, 3),
        })

        monotone = _clamp((CV_KNEE - f0_cv) / CV_KNEE)
        flat = _clamp((SLOPE_KNEE - slope_norm) / SLOPE_KNEE)
        lo, hi = SILENCE_BAND
        if silence_ratio < lo:
            silence_dev = _clamp((lo - silence_ratio) / lo)
        elif silence_ratio > hi:
            silence_dev = _clamp((silence_ratio - hi) / 0.30)
        else:
            silence_dev = 0.0
        rlo, rhi = RATE_BAND
        if syllable_rate > 0.0:
            if syllable_rate < rlo:
                rate_dev = _clamp((rlo - syllable_rate) / rlo)
            elif syllable_rate > rhi:
                rate_dev = _clamp((syllable_rate - rhi) / 2.0)
            else:
                rate_dev = 0.0
        else:
            rate_dev = 0.0

        risk = 0.45 * monotone + 0.25 * flat + 0.15 * silence_dev + 0.15 * rate_dev

        return {"prosody": round(_clamp(risk), 4),
                "voiced_ratio": round(voiced_ratio, 4),
                "silence_ratio": round(silence_ratio, 4),
                "f0_cv": round(f0_cv, 4),
                "prosody_diagnostics": diagnostics}

    @staticmethod
    def _syllable_rate(inten: np.ndarray, duration: float) -> float:
        """Count syllable-like nuclei as local intensity maxima above the mean.

        This is the standard De Jong & Wempe (2009) recipe made vectorisable:
        we need the number of candidates, not their exact locations, and the
        peak-counting loop is short (n_frames ~ duration * 10).
        """
        mean = float(inten.mean())
        if duration <= 0.0:
            return 0.0
        n = 0
        for i in range(1, inten.size - 1):
            if inten[i] > mean and inten[i] >= inten[i - 1] and inten[i] > inten[i + 1]:
                n += 1
        return n / duration


def _selfcheck() -> None:
    """Deterministic checks: a rising monotone sine must score above a fluttery one."""
    import math as _math

    rng = np.random.default_rng(7)

    def sine_with(freq: float, rms: float, seconds: float, wobble: float) -> np.ndarray:
        n = int(MODEL_RATE * seconds)
        t = np.arange(n) / MODEL_RATE
        f0_t = freq + wobble * np.sin(2 * _math.pi * 0.5 * t)
        phase = np.cumsum(2 * _math.pi * f0_t / MODEL_RATE)
        return rms * np.sin(phase)

    scorer = ProsodyScorer()

    flat = scorer._score_numpy(sine_with(120.0, 0.02, 4.0, 0.0))
    wobble = scorer._score_numpy(sine_with(120.0, 0.02, 4.0, 25.0))
    silence = scorer._score_numpy(np.zeros(int(MODEL_RATE * 4.0), dtype=np.float64))

    assert flat["prosody"] > 0.5, flat["prosody"]
    assert wobble["prosody"] < flat["prosody"], (wobble["prosody"], flat["prosody"])
    assert silence["prosody"] == 0.5, silence["prosody"]
    assert flat["prosody_diagnostics"]["jitter_pct"] >= 0.0
    print(f"ok: flat sine risk={flat['prosody']:.3f} > wobble "
          f"risk={wobble['prosody']:.3f}, silence neutral=0.5")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _selfcheck()