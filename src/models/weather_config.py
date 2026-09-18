from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WeatherPipelineConfig:
    """The league's configurable phase horizons for the weather pipeline.

    Default values match the previously hardcoded schedule_round horizons:
    Phase 1 at T−5 days, Phase 2 at T−2 days, Phase 3 at T−2 hours.
    """

    phase_1_days: int = 5
    phase_2_days: int = 2
    phase_3_hours: int = 2
