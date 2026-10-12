"""Weather's draws of a phase, recorded for the tests of a change that asks for them (#439).

An amendment that brings a round forward draws each phase now due at once. Until slice 6 it ran
weather's phase runner (`run_phaseN`); from slice 6 its `draw_forecast` job makes the draw
(`draw_phaseN_on`) and its `post_forecast` job posts it. `recorded_draws` records either alike, so
that a test reading which phases were drawn holds on both sides of the change.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest


def recorded_draws(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Record `(phase, round id)` in `ran` for each phase drawn; a phase in `failing` raises its
    fault instead, as a fault of the bot's own.

    A draw (`draw_phaseN_on`), where weather has it, is recorded and then made as weather makes it,
    patched in its own module and in `forecast_changes` where that holds the same function. A phase
    runner (`run_phaseN`), where weather still has it, is recorded and not run.
    """
    record = SimpleNamespace(ran=[], failing={})
    try:
        changes: Any = importlib.import_module("leaguebot.weather.services.forecast_changes")
    except ModuleNotFoundError:
        changes = None
    for number in (1, 2, 3):
        module = importlib.import_module(f"leaguebot.weather.services.phase{number}_service")

        if hasattr(module, f"run_phase{number}"):
            async def _run(rid: int, *_args: Any, _number: int = number, **_kwargs: Any) -> None:
                if _number in record.failing:
                    raise record.failing[_number]
                record.ran.append((_number, rid))

            monkeypatch.setattr(module, f"run_phase{number}", _run)

        name = f"draw_phase{number}_on"
        drawing = getattr(module, name, None)
        if drawing is None:
            continue

        async def _draw(db: Any, rid: int, *args: Any, _number: int = number,
                        _drawing: Any = drawing, **kwargs: Any) -> Any:
            if _number in record.failing:
                raise record.failing[_number]
            record.ran.append((_number, rid))
            return await _drawing(db, rid, *args, **kwargs)

        monkeypatch.setattr(module, name, _draw)
        if changes is not None and getattr(changes, name, None) is drawing:
            monkeypatch.setattr(changes, name, _draw)
    return record
