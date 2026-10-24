"""Weather's draws of a phase, recorded for the tests of a change that asks for them (#439).

An amendment that brings a round forward draws each phase now due at once: its `draw_forecast` job
makes the draw (`draw_phaseN_on`) and its `post_forecast` job posts it. `recorded_draws` records
each draw, so that a test reads which phases were drawn. It imports weather's `forecast_changes` and
reads each `draw_phaseN_on`.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest


def recorded_draws(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Record `(phase, round id)` in `ran` for each phase drawn; a phase in `failing` raises its
    fault instead, as a fault of the bot's own.

    Each draw (`draw_phaseN_on`) is recorded and then made as weather makes it, patched in its
    own module and in `forecast_changes` where that holds the same function.
    """
    from leaguebot.weather.services import forecast_changes

    record = SimpleNamespace(ran=[], failing={})
    for number in (1, 2, 3):
        module = importlib.import_module(f"leaguebot.weather.services.phase{number}_service")
        name = f"draw_phase{number}_on"
        drawing = getattr(module, name)

        async def _draw(db: Any, rid: int, *args: Any, _number: int = number,
                        _drawing: Any = drawing, **kwargs: Any) -> Any:
            if _number in record.failing:
                raise record.failing[_number]
            record.ran.append((_number, rid))
            return await _drawing(db, rid, *args, **kwargs)

        monkeypatch.setattr(module, name, _draw)
        if getattr(forecast_changes, name, None) is drawing:
            monkeypatch.setattr(forecast_changes, name, _draw)
    return record
