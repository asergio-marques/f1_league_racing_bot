from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WeatherPipelineConfig:
    """The league's configurable phase horizons for the weather pipeline.

    The defaults are the packaged horizons: Phase 1 at T−5 days, Phase 2 at T−2 days, Phase 3 at
    T−2 hours. Core arms nothing by them: a round's forecasts are armed at the league's own
    horizons (`forecast_leads`).
    """

    phase_1_days: int = 5
    phase_2_days: int = 2
    phase_3_hours: int = 2


@dataclass(frozen=True)
class WeatherWindows:
    """The three forecast phase horizons, as `weather_pipeline_config` stores them."""

    phase_1_days: int
    phase_2_days: int
    phase_3_hours: int
