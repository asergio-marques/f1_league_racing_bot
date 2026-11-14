"""What a module hands the weather picture to draw: the words of a forecast, already composed.

The image module draws what a module hands it and imports no other module (architecture.md, "How
modules and core fit together"), so the forecast's words come from the weather module and are
never composed here: the phase's description, the likelihood of rain as text, and each session's
label, drawn weather type and slot labels, in run order. These are plain data.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ForecastSession:
    """One session of the round as the forecast words it: its label, the weather type drawn for
    it (None before Phase 2), its slots' labels and their summary (both empty before Phase 3)."""

    label: str
    weather_type: str | None = None
    slots: tuple[str, ...] = ()
    summary: str | None = None


@dataclass(frozen=True)
class ForecastValues:
    """The words of one phase's forecast: its description, the likelihood of rain as text (None
    where the draw holds none) and, from Phase 2 on, its sessions in the order they are run."""

    description: str
    rain_probability: str | None = None
    sessions: tuple[ForecastSession, ...] = ()
