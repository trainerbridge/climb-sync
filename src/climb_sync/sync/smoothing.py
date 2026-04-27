"""EMA smoothing + grade clamping — pure functions."""
from __future__ import annotations

from .constants import EMA_ALPHA, GRADE_MIN_FRACTION, GRADE_MAX_FRACTION


def ema_update(previous: float | None, sample: float, alpha: float = EMA_ALPHA) -> float:
    """Apply one EMA step.

    Warmup: if previous is None (no prior sample), returns the sample unchanged.
    This is the "s_0 = x_0" warmup-seed rule — prevents the ramp-from-zero
    artifact at ride start (and on S4Z reconnect, per Pitfall 4 — callers
    reset `previous = None` on reconnect).
    """
    if previous is None:
        return sample
    return alpha * sample + (1.0 - alpha) * previous


def clamp_grade(value: float) -> float:
    """Clamp a grade fraction to the Kickr Climb v1 hardware range [-0.10, +0.20].

    MUST be applied AFTER EMA, BEFORE encode_grade (RESEARCH §EMA Gotchas):
    clamping before smoothing introduces discontinuities in the smoothed signal.
    """
    return max(GRADE_MIN_FRACTION, min(GRADE_MAX_FRACTION, value))
