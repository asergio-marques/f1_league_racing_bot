"""The kinds of weather's forecast changes, and how a forecast clean-up names what it deletes."""

from __future__ import annotations

from collections.abc import Iterable


#: The kinds of weather's forecast changes. Core names none of them: weather reads what is still
#: unfinished of each for its own steps of `/test-mode advance`, and asks for them itself.
FORECAST = "weather.forecast"
FORECAST_CLEAN_UP = "weather.forecast.clean_up"

def forecasts_named(phases: Iterable[int]) -> str:
    """What a clean-up deletes, named by its phases as every text of the clean-up names them:
    "the Phase 2 forecast", "the Phase 1 and 2 forecasts", or "the forecast" where no phase is
    known. A mystery round's notice (phase 0) is "the mystery round notice", never a phase, and
    stands first beside any forecast: "the mystery round notice and the Phase 3 forecast". Here so
    that weather's changes, its restart and its advance step name the clean-up alike, the phase it
    really deletes and never one that is not standing."""
    known = sorted(set(phases))
    notice = 0 in known
    forecasts = [phase for phase in known if phase != 0]
    if not forecasts:
        return "the mystery round notice" if notice else "the forecast"
    if len(forecasts) == 1:
        named = f"the Phase {forecasts[0]} forecast"
    else:
        listed = ", ".join(str(phase) for phase in forecasts[:-1])
        named = f"the Phase {listed} and {forecasts[-1]} forecasts"
    return f"the mystery round notice and {named}" if notice else named
