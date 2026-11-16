"""The kinds of weather's forecast changes, and how a forecast clean-up names what it deletes."""

from __future__ import annotations

from collections.abc import Iterable


#: The kinds of weather's forecast changes. Core names none of them: weather reads what is still
#: unfinished of each for its own steps of `/test-mode advance`, and asks for them itself.
FORECAST = "weather.forecast"
FORECAST_CLEAN_UP = "weather.forecast.clean_up"

def forecasts_named(phases: Iterable[int]) -> str:
    """The forecasts a clean-up deletes, named by their phases as every text of the clean-up
    names them: "the Phase 2 forecast", "the Phase 1 and 2 forecasts", or "the forecast" where no
    phase is known. Here so that weather's changes, its restart and its advance step name the
    clean-up alike, the phase it really deletes and never one that is not standing."""
    known = sorted(set(phases))
    if not known:
        return "the forecast"
    if len(known) == 1:
        return f"the Phase {known[0]} forecast"
    listed = ", ".join(str(phase) for phase in known[:-1])
    return f"the Phase {listed} and {known[-1]} forecasts"
