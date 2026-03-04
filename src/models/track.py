"""Track registry: 27 F1 circuits with their base rain-probability factor (Btrack)."""

from __future__ import annotations

from typing import Final

TRACKS: Final[dict[str, float]] = {
    "Bahrain": 0.05,
    "Saudi Arabia": 0.05,
    "Australia": 0.10,
    "Japan": 0.25,
    "China": 0.25,
    "Miami": 0.15,
    "Imola": 0.25,
    "Monaco": 0.25,
    "Canada": 0.30,
    "Barcelona": 0.20,
    "Madrid": 0.15,
    "Austria": 0.25,
    "United Kingdom": 0.30,
    "Hungary": 0.25,
    "Belgium": 0.30,
    "Netherlands": 0.25,
    "Monza": 0.15,
    "Azerbaijan": 0.10,
    "Singapore": 0.20,
    "Texas": 0.10,
    "Mexico": 0.05,
    "Brazil": 0.30,
    "Las Vegas": 0.05,
    "Qatar": 0.05,
    "Abu Dhabi": 0.05,
    "Portugal": 0.10,
    "Turkey": 0.10,
}


def get_btrack(name: str) -> float:
    """Return Btrack for *name*; raise ValueError for unknown circuits."""
    try:
        return TRACKS[name]
    except KeyError:
        raise ValueError(
            f"Unknown track {name!r}. Valid options: {sorted(TRACKS)}"
        ) from None
