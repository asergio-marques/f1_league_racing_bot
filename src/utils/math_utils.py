"""Weather computation formulas for the F1 League Weather Randomizer Bot.

All formula references are from specs/001-league-weather-bot/plan.md
and the source specification document.

Notation:
  Rpc   – Rain probability coefficient (0.0–1.0, fractional)
  Ir    – Rainy slots count  (out of 1000)
  Im    – Mixed slots count  (out of 1000)
  Is    – Sunny slots count  (out of 1000)
  Prain – Rpc value (used as Prain in Phase 3 formulas)

Phase 3 weather types (from spec):
  Clear, Light Cloud, Overcast, Wet, Very Wet
"""

from __future__ import annotations

import logging
import math

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 1 – Rain Probability Coefficient
# ---------------------------------------------------------------------------

def compute_rpc(btrack: float, rand1: float, rand2: float) -> float:
    """Compute Rpc = round((Btrack * R1 * R2) / 3.025, 2), clamped to [0.0, 1.0].

    rand1 and rand2 are expected in the range [1, 98] (integer dice).
    Intermediate calculation is unclamped; clamping occurs at the end.
    """
    raw = (btrack * rand1 * rand2) / 3.025
    result = round(raw, 2)
    if result < 0.0 or result > 1.0:
        log.warning(
            "compute_rpc: raw result %s outside [0.0, 1.0] (btrack=%s, r1=%s, r2=%s); clamping.",
            result, btrack, rand1, rand2,
        )
    return max(0.0, min(1.0, result))


# ---------------------------------------------------------------------------
# Phase 2 – Slot Distribution
# ---------------------------------------------------------------------------

def compute_ir(rpc: float) -> int:
    """Rainy slots: floor((1000 * Rpc * (1 + Rpc)^2) / 5)."""
    return math.floor((1000 * rpc * (1 + rpc) ** 2) / 5)


def compute_im(rpc: float, ir: int) -> int:
    """Mixed slots: floor(1000 * Rpc) - Ir."""
    return max(0, math.floor(1000 * rpc) - ir)


def compute_is(im: int, ir: int) -> int:
    """Sunny slots: 1000 - Im - Ir."""
    return 1000 - im - ir


def build_slot_pool(ir: int, im: int, is_: int) -> list[str]:
    """Build a 1000-entry pool of 'rain', 'mixed', 'sunny' strings.

    If the sum < 1000 (floating-point edge), pad with 'mixed' entries.
    """
    pool = (["rain"] * ir) + (["mixed"] * im) + (["sunny"] * is_)
    deficit = 1000 - len(pool)
    if deficit > 0:
        pool += ["mixed"] * deficit
    return pool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clamp_weight(value: float) -> float:
    """Clamp a weight to [0.0, ∞); negative weights become 0."""
    return max(0.0, value)


# ---------------------------------------------------------------------------
# Phase 3 – Per-slot-type weather weights
#
# Weather types (from spec): Clear, Light Cloud, Overcast, Wet, Very Wet
# Weights are NOT differentiated by session type — only by slot type.
# All results are clamped to a minimum of 0 per spec.
# ---------------------------------------------------------------------------

def weights_sunny(prain: float) -> dict[str, float]:
    """Phase 3 weight map for a SUNNY slot session."""
    return {
        "Clear":       clamp_weight(60 - (60 * prain ** 0.8)),
        "Light Cloud": clamp_weight(25 + (25 * prain ** 2)),
        "Overcast":    clamp_weight(15 + (80 * prain ** 4)),
        "Wet":         0.0,
        "Very Wet":    0.0,
    }


def weights_mixed(prain: float) -> dict[str, float]:
    """Phase 3 weight map for a MIXED slot session."""
    return {
        "Clear":       clamp_weight(20 - (20 * prain ** 0.4)),
        "Light Cloud": clamp_weight(40 + (20 * prain) - (70 * prain ** 1.2)),
        "Overcast":    clamp_weight(40 + (30 * prain) - (70 * prain ** 1.7)),
        "Wet":         clamp_weight((80 * prain) - (40 * prain ** 2)),
        "Very Wet":    clamp_weight((10 * prain ** 1.5) + (35 * prain ** 3)),
    }


def weights_rain(prain: float) -> dict[str, float]:
    """Phase 3 weight map for a RAIN slot session."""
    return {
        "Clear":       0.0,
        "Light Cloud": 0.0,
        "Overcast":    0.0,
        "Wet":         clamp_weight(100 - (40 * prain ** 2) - (13 * prain ** 4)),
        "Very Wet":    clamp_weight((5 * prain ** 2) + (40 * prain ** 0.8)),
    }


def get_phase3_weights(slot_type: str, prain: float) -> dict[str, float]:
    """Return the weight dict for a Phase 3 draw given *slot_type* and *prain*.

    Args:
        slot_type: 'rain', 'mixed', or 'sunny'
        prain: Rpc (0.0–1.0)
    """
    match slot_type:
        case "rain":
            return weights_rain(prain)
        case "mixed":
            return weights_mixed(prain)
        case "sunny":
            return weights_sunny(prain)
        case _:
            raise ValueError(f"Unknown slot type: {slot_type!r}")


def draw_weighted(weights: dict[str, float], rng: "random.Random | None" = None) -> str:
    """Draw one weather label using the provided (pre-clamped) weights.

    Falls back to equal-weight selection if all weights are zero.
    """
    import random as _random

    rng = rng or _random

    labels = list(weights.keys())
    values = list(weights.values())
    total = sum(values)

    if total <= 0.0:
        log.warning("All Phase 3 weights are zero; falling back to equal distribution.")
        return rng.choice(labels)

    return rng.choices(labels, weights=values, k=1)[0]

