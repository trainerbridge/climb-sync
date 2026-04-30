"""Named constants for the sync loop.

Values are locked by CONTEXT.md decisions (D-06 through D-12). Do not tune
these in code review — any change needs a CONTEXT.md amendment first.
"""
from __future__ import annotations

# Write cadence — D-07 locked (matches spike 005 validated rate: 95 writes / 199s / 0 failures)
WRITE_INTERVAL_SECONDS: float = 1.0     # 1 Hz

# EMA smoothing — D-08 Claude's discretion within [0.25, 0.4] band
# 0.3 gives ~95% convergence in ~9 samples (~9s at 1 Hz), matching Climb's
# ~3-5s hardware transit time comfortably (RESEARCH §EMA Recommended Default).
EMA_ALPHA: float = 0.3

# Grade clamp — D-10 locked (Kickr Climb v1 tilt range)
# Applied AFTER EMA, BEFORE encode_grade (RESEARCH §EMA Gotchas).
GRADE_MIN_FRACTION: float = -0.10       # -10%
GRADE_MAX_FRACTION: float = 0.20        # +20%

# Grade-source staleness thresholds — D-11 locked (seconds; monotonic clock)
STALE_WARN_SECONDS: float = 5.0
STALE_OUTAGE_SECONDS: float = 30.0

# Long-outage park threshold: after this many seconds of no S4Z grade, write 0%
# once and pause further writes until S4Z resumes. Refines D-12 (which held the
# last grade indefinitely) so the Climb doesn't stay tilted for hours after
# Zwift/Sauce4Zwift exit. Reset to writing on the first post-outage sample.
LONG_OUTAGE_PARK_SECONDS: float = 300.0

# Workout-mode debounce. ERG/SIM is inferred from S4Z's `state.workoutZone`:
# null → free ride (Zwift drives the Climb directly, we must NOT write or we
# fight Zwift's writes); 1-15 → structured workout (Zwift defaults to ERG and
# stops writing grade, so we take over). The flag can flicker for a frame at
# zone transitions, so require N consecutive consistent samples (~Nx 1 s) before
# flipping mode. Default to "unknown" until the first N samples arrive — never
# write before the mode is known.
WORKOUT_DEBOUNCE_SAMPLES: int = 3

# DIRCON reconnect backoff — D-06 locked (re-exported here for convenience)
DIRCON_BACKOFF_CURVE: tuple[int, ...] = (1, 2, 5, 10, 15, 30)

# S4Z_BACKOFF_CURVE lives in grade.source (single source of truth);
# sync/__init__.py re-exports it for callers who already import from
# climb_sync.sync.
