"""Core's template reads run off the event loop, so the bot keeps answering while they parse.

A template's parse takes a good part of a second on a Raspberry Pi, and on the loop it would
leave the bot answering nothing until it finished. The season review's capacity checks, the
round guard and the placement guards each parse one configured template, and each hands the
parse to a worker thread with its reading of the tree, so that only counts and names come back:
a tree parsed in a worker thread never leaves that thread (`load_svg`).

Each test runs its entry point twice against the templates the bot ships: once as it stands,
and once with the parse replaced by a `Held` one (`tests/support/off_the_loop.py`), which waits
for a coroutine on the loop to release it. The two must answer alike, and the held parse must
never have found the loop held. These checks swallow their own faults, so a parse on the loop
would show as `held_loop` and as an answer that differs, never as an error.
`tests/image/test_image_sweeps_off_the_loop.py` holds the image module to the same.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.placement_service import PlacementService
from leaguebot.image.utils import svg_document
from tests.support.off_the_loop import Held, answered_while

#: The templates the bot ships.
SHIPPED = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"

SEASON_ID = 1
DIVISION_ID = 1


def _reports() -> dict[str, Any]:
    """Every shipped template, reported valid at its own path."""
    return {
        path.stem: SimpleNamespace(valid=True, reason=None, resolved_path=path)
        for path in sorted(SHIPPED.glob("*.svg"))
    }


async def _season(tmp_path) -> str:
    """A season of one division holding sixty sprint rounds: more than any shipped template
    draws, so each capacity check has something to say."""
    db_path = str(tmp_path / "league.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'SETUP')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        for number in range(1, 61):
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, scheduled_at, format) "
                "VALUES (?, ?, '2026-02-01T18:00:00+00:00', 'SPRINT')",
                (DIVISION_ID, number),
            )
        await db.commit()
    return db_path


def _cog(db_path: str, *, aspects: tuple[str, ...]) -> SeasonCog:
    """The season cog, the image module on with *aspects* switched on, over thirty teams."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    bot.image_config_service.get_config = AsyncMock(return_value=object())
    bot.image_config_service.is_aspect_enabled = AsyncMock(
        side_effect=lambda aspect: aspect in aspects
    )
    bot.image_validity_service.template_reports = AsyncMock(return_value=_reports())
    bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(id=DIVISION_ID, name="Pro", tier=1)]
    )
    bot.team_service.get_division_teams = AsyncMock(
        return_value=[{"name": f"Team {n}", "seats": []} for n in range(1, 31)]
    )
    return SeasonCog(bot)


async def _held_alike(monkeypatch, entry) -> tuple[Any, Held]:
    """What *entry* answers as it stands, checked against what it answers with its parses
    held until the loop releases them; returns the answer and the `Held` parse."""
    expected = await entry()
    held = Held(svg_document.load_svg)
    monkeypatch.setattr(svg_document, "load_svg", held)

    answer = await answered_while(held, entry)

    assert held.calls, "the entry point parsed no template"
    assert held.held_loop == 0
    assert answer == expected
    return answer, held


#: Each season check, the outputs switched on for it, how it is called, and whether the
#: shipped templates leave it something to say about sixty rounds and thirty teams.
SEASON_CHECKS = [
    ("lineup", ("lineup",), lambda cog: cog._lineup_problems(SEASON_ID), True),
    ("calendar-round", ("calendar",), lambda cog: cog._calendar_round_overflow(61), True),
    ("calendar-review", ("calendar",), lambda cog: cog._calendar_capacity_warning(SEASON_ID),
     True),
    ("attendance-sheet", ("attendance",),
     lambda cog: cog._attendance_capacity_warning(SEASON_ID), True),
    ("check-in", ("rsvp",), lambda cog: cog._attendance_capacity_warning(SEASON_ID), None),
    ("standings", ("standings",), lambda cog: cog._attendance_capacity_warning(SEASON_ID),
     True),
]


@pytest.mark.parametrize(
    "aspects,check,says", [case[1:] for case in SEASON_CHECKS], ids=[c[0] for c in SEASON_CHECKS]
)
async def test_a_season_check_parses_its_template_off_the_loop(
    tmp_path, monkeypatch, aspects, check, says
):
    """The lineup, calendar, attendance sheet, check-in and standings checks of a season and
    its rounds parse their template in a worker thread, and say what they said before."""
    cog = _cog(await _season(tmp_path), aspects=aspects)

    answer, _held = await _held_alike(monkeypatch, lambda: check(cog))

    if says:
        assert answer


async def _service(tmp_path, monkeypatch, *, enabled: str, reserve: bool) -> PlacementService:
    """The placement service of the sixty-round season, the output *enabled* names on, the
    team placed into a reserve team or not as *reserve* says."""
    import leaguebot.image.services.image_attendance_post as attendance_post
    import leaguebot.image.services.image_lineup_post as lineup_post
    import leaguebot.image.services.image_standings_post as standings_post

    db_path = await _season(tmp_path)
    for module, name in (
        (lineup_post, "lineup_enabled"),
        (attendance_post, "attendance_enabled"),
        (standings_post, "standings_enabled"),
    ):
        monkeypatch.setattr(module, name, AsyncMock(return_value=name == enabled))
    bot = MagicMock()
    bot.image_validity_service.template_reports = AsyncMock(return_value=_reports())
    service = PlacementService(db_path, bot)
    monkeypatch.setattr(service, "_is_reserve_team", AsyncMock(return_value=reserve))
    return service


#: Each placement guard, the output it guards, and whether the team placed into is reserve.
PLACEMENT_GUARDS = [
    ("reserve", "lineup_enabled", True,
     lambda service: service._guard_reserve_capacity(DIVISION_ID, "Reserve", adding=99)),
    ("attendance-sheet", "attendance_enabled", False,
     lambda service: service._guard_sheet_capacity(DIVISION_ID, adding=99)),
    ("standings", "standings_enabled", False,
     lambda service: service._guard_standings_capacity(DIVISION_ID, "Team 1", adding=99)),
]


@pytest.mark.parametrize(
    "enabled,reserve,guard", [case[1:] for case in PLACEMENT_GUARDS],
    ids=[c[0] for c in PLACEMENT_GUARDS],
)
async def test_a_placement_guard_measures_its_template_off_the_loop(
    tmp_path, monkeypatch, enabled, reserve, guard
):
    """The reserve, attendance sheet and standings guards parse and measure their template in
    a worker thread, and refuse ninety-nine more drivers as they did before."""
    service = await _service(tmp_path, monkeypatch, enabled=enabled, reserve=reserve)

    async def refusal() -> str:
        with pytest.raises(ValueError) as refused:
            await guard(service)
        return str(refused.value)

    answer, _held = await _held_alike(monkeypatch, refusal)

    assert "was **not** assigned" in answer
