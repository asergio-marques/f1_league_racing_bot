"""Catching a calendar up when the weather module is switched on mid-season.

Issue #208. `ModuleCog._catchup_and_schedule_weather` runs when a league enables the weather
module, and it was uncovered. It exists because enabling the module partway through a season
must not leave the rounds that are already inside a forecast horizon without forecasts — a
league that enables it the week of a round expects that round to be forecast, not the next one.

**A phase that is already due is run now; one that is not is left to the scheduler.** The three
horizons are read from the league's own configuration, so a league that forecasts a fortnight
out and one that forecasts three days out catch up to different points from the same calendar.
Each phase is tested either side of its horizon, because an inverted comparison here is
invisible: the rounds still get scheduled and the missing forecasts only show up as silence.

**A phase already done is not run again.** `phase1_done` and its siblings are what stop a
re-enable from re-posting forecasts a division has already seen — and a league toggling the
module off and on again is exactly when this runs.

**A mystery round is caught up on nothing and still scheduled.** Its track is not known, so
there is nothing to forecast; scheduling it anyway is what lets the reveal happen on time.

**Every round is scheduled, caught up or not.** The catch-up is for the past and the scheduler
is for the future, and a round that missed its phase 1 still needs its phase 2 armed.

**Each round is scheduled with its own division's tier and its season's number**, because those
are what the posted forecast is titled with — and they come from a lookup built once over the
divisions rather than from the round, which does not carry them.

**A naive datetime out of SQLite is read as UTC.** Comparing one to an aware "now" raises, and
the raise would abort the catch-up halfway through a calendar, leaving some rounds scheduled and
the rest not.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.module_cog import ModuleCog  # noqa: E402
from models.round import RoundFormat  # noqa: E402

SERVER_ID = 10508
SEASON_ID = 1

#: The defaults a league gets when it has never configured the pipeline.
PHASE_1_DAYS = 7
PHASE_2_DAYS = 3
PHASE_3_HOURS = 6


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _round(
    round_id: int,
    *,
    days_away: float = 30,
    fmt: RoundFormat = RoundFormat.NORMAL,
    division_id: int = 11,
    phase1_done: bool = False,
    phase2_done: bool = False,
    phase3_done: bool = False,
    naive: bool = False,
):
    at = datetime.now(timezone.utc) + timedelta(days=days_away)
    return SimpleNamespace(
        id=round_id,
        division_id=division_id,
        round_number=round_id,
        format=fmt,
        scheduled_at=at.replace(tzinfo=None) if naive else at,
        phase1_done=phase1_done,
        phase2_done=phase2_done,
        phase3_done=phase3_done,
    )


def _division(division_id: int = 11, tier: int = 1):
    return SimpleNamespace(id=division_id, name=f"Division {tier}", tier=tier)


def _make_cog(*, divisions=None, rounds_by_division=None) -> ModuleCog:
    divisions = divisions if divisions is not None else [_division()]
    rounds_by_division = rounds_by_division or {}

    bot = MagicMock()
    bot.db_path = "/tmp/not-read.db"
    bot.season_service = MagicMock()
    bot.season_service.get_divisions = AsyncMock(return_value=divisions)
    bot.season_service.get_division_rounds = AsyncMock(
        side_effect=lambda div_id: rounds_by_division.get(div_id, [])
    )
    bot.scheduler_service = MagicMock()
    bot.scheduler_service.schedule_round = MagicMock(return_value=None)

    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    return cog


def _config(phase_1_days=PHASE_1_DAYS, phase_2_days=PHASE_2_DAYS, phase_3_hours=PHASE_3_HOURS):
    return SimpleNamespace(
        server_id=SERVER_ID,
        phase_1_days=phase_1_days,
        phase_2_days=phase_2_days,
        phase_3_hours=phase_3_hours,
    )


async def _catchup(cog, *, season_number: int = 7, config=None):
    """Run the catch-up with every phase runner stubbed, returning the three stubs."""
    season = SimpleNamespace(id=SEASON_ID, season_number=season_number)
    with patch(
        "services.weather_config_service.get_weather_pipeline_config",
        new=AsyncMock(return_value=config or _config()),
    ), patch("services.phase1_service.run_phase1", new=AsyncMock()) as p1, patch(
        "services.phase2_service.run_phase2", new=AsyncMock()
    ) as p2, patch(
        "services.phase3_service.run_phase3", new=AsyncMock()
    ) as p3:
        await cog._catchup_and_schedule_weather(SERVER_ID, season)
    return p1, p2, p3


def _ran_for(stub) -> list[int]:
    return [call.args[0] for call in stub.await_args_list]


def _scheduled_ids(cog) -> list[int]:
    return [call.args[0].id for call in cog.bot.scheduler_service.schedule_round.call_args_list]


# ---------------------------------------------------------------------------
# What is caught up
# ---------------------------------------------------------------------------


async def test_a_round_beyond_every_horizon_is_caught_up_on_nothing():
    """The scheduler will reach it in its own time; running phase 1 a month early would
    post a forecast nobody can act on and mark the phase done."""
    cog = _make_cog(rounds_by_division={11: [_round(1, days_away=30)]})

    p1, p2, p3 = await _catchup(cog)

    assert _ran_for(p1) == []
    assert _ran_for(p2) == []
    assert _ran_for(p3) == []


async def test_a_round_inside_the_first_horizon_is_caught_up_on_phase_one():
    """A league enabling the module the week of a round expects that round forecast, not
    the next one."""
    cog = _make_cog(rounds_by_division={11: [_round(1, days_away=PHASE_1_DAYS - 1)]})

    p1, p2, p3 = await _catchup(cog)

    assert _ran_for(p1) == [1]
    assert _ran_for(p2) == []
    assert _ran_for(p3) == []


async def test_a_round_inside_the_second_horizon_is_caught_up_on_both():
    """Phase 2 refines phase 1, so a round past both horizons needs both — posting only the
    later one would leave a division with a refinement of a forecast it never saw."""
    cog = _make_cog(rounds_by_division={11: [_round(1, days_away=PHASE_2_DAYS - 1)]})

    p1, p2, p3 = await _catchup(cog)

    assert _ran_for(p1) == [1]
    assert _ran_for(p2) == [1]
    assert _ran_for(p3) == []


async def test_a_round_hours_away_is_caught_up_on_all_three():
    cog = _make_cog(rounds_by_division={11: [_round(1, days_away=0.1)]})

    p1, p2, p3 = await _catchup(cog)

    assert _ran_for(p1) == [1]
    assert _ran_for(p2) == [1]
    assert _ran_for(p3) == [1]


async def test_a_round_already_past_is_caught_up_on_all_three():
    """Enabling the module after a round has run is a league tidying up, and every horizon
    is behind them."""
    cog = _make_cog(rounds_by_division={11: [_round(1, days_away=-2)]})

    p1, p2, p3 = await _catchup(cog)

    assert _ran_for(p1) == [1]
    assert _ran_for(p2) == [1]
    assert _ran_for(p3) == [1]


async def test_the_horizons_come_from_the_leagues_own_configuration():
    """A league that forecasts a fortnight out and one that forecasts three days out catch
    up to different points from the same calendar."""
    cog = _make_cog(rounds_by_division={11: [_round(1, days_away=10)]})

    p1, _, _ = await _catchup(cog, config=_config(phase_1_days=14))

    assert _ran_for(p1) == [1]


async def test_a_shorter_horizon_leaves_the_same_round_alone():
    """The other side of the comparison, because an inverted one is invisible: the rounds
    still get scheduled and the missing forecasts only show up as silence."""
    cog = _make_cog(rounds_by_division={11: [_round(1, days_away=10)]})

    p1, _, _ = await _catchup(cog, config=_config(phase_1_days=3))

    assert _ran_for(p1) == []


@pytest.mark.parametrize(
    "done,expected",
    [
        ({"phase1_done": True}, ([], [1], [1])),
        ({"phase2_done": True}, ([1], [], [1])),
        ({"phase3_done": True}, ([1], [1], [])),
    ],
)
async def test_a_phase_already_done_is_not_run_again(done, expected):
    """These flags are what stop a re-enable re-posting forecasts a division has already
    seen — and toggling the module off and on again is exactly when this runs."""
    cog = _make_cog(rounds_by_division={11: [_round(1, days_away=0.1, **done)]})

    p1, p2, p3 = await _catchup(cog)

    assert (_ran_for(p1), _ran_for(p2), _ran_for(p3)) == expected


async def test_a_round_with_every_phase_done_is_caught_up_on_nothing():
    cog = _make_cog(
        rounds_by_division={
            11: [
                _round(
                    1,
                    days_away=0.1,
                    phase1_done=True,
                    phase2_done=True,
                    phase3_done=True,
                )
            ]
        }
    )

    p1, p2, p3 = await _catchup(cog)

    assert _ran_for(p1) == _ran_for(p2) == _ran_for(p3) == []


async def test_a_mystery_round_is_caught_up_on_nothing():
    """Its track is not known, so there is nothing to forecast."""
    cog = _make_cog(
        rounds_by_division={11: [_round(1, days_away=0.1, fmt=RoundFormat.MYSTERY)]}
    )

    p1, p2, p3 = await _catchup(cog)

    assert _ran_for(p1) == _ran_for(p2) == _ran_for(p3) == []


async def test_a_mystery_round_is_still_scheduled():
    """Scheduling it is what lets the reveal happen on time; skipping it because it has no
    forecasts to catch up would silently drop it from the calendar."""
    cog = _make_cog(
        rounds_by_division={11: [_round(1, days_away=0.1, fmt=RoundFormat.MYSTERY)]}
    )

    await _catchup(cog)

    assert _scheduled_ids(cog) == [1]


async def test_a_naive_round_datetime_is_read_as_utc():
    """SQLite hands back datetimes without a timezone. Comparing one to an aware "now"
    raises, and the raise would abort the catch-up halfway through a calendar — leaving
    some rounds scheduled and the rest not."""
    cog = _make_cog(rounds_by_division={11: [_round(1, days_away=0.1, naive=True)]})

    p1, _, _ = await _catchup(cog)

    assert _ran_for(p1) == [1]
    assert _scheduled_ids(cog) == [1]


# ---------------------------------------------------------------------------
# What is scheduled
# ---------------------------------------------------------------------------


async def test_every_round_is_scheduled(tmp_path):
    """The catch-up is for the past and the scheduler is for the future; a round that
    missed its phase 1 still needs its phase 2 armed."""
    cog = _make_cog(
        rounds_by_division={
            11: [_round(1, days_away=0.1), _round(2, days_away=14), _round(3, days_away=30)]
        }
    )

    await _catchup(cog)

    assert _scheduled_ids(cog) == [1, 2, 3]


async def test_every_division_of_the_season_is_walked():
    """Enabling the module is a server-wide act; catching up one division and not the rest
    would leave the lower tiers silently unforecast."""
    cog = _make_cog(
        divisions=[_division(11, tier=1), _division(12, tier=2)],
        rounds_by_division={11: [_round(1)], 12: [_round(2, division_id=12)]},
    )

    await _catchup(cog)

    assert sorted(_scheduled_ids(cog)) == [1, 2]


async def test_a_round_is_scheduled_with_its_own_divisions_tier():
    """The tier titles the posted forecast, and it comes from a lookup over the divisions
    because the round itself does not carry it."""
    cog = _make_cog(
        divisions=[_division(11, tier=1), _division(12, tier=3)],
        rounds_by_division={11: [_round(1)], 12: [_round(2, division_id=12)]},
    )

    await _catchup(cog)

    tiers = {
        call.args[0].id: call.kwargs["division_tier"]
        for call in cog.bot.scheduler_service.schedule_round.call_args_list
    }
    assert tiers == {1: 1, 2: 3}


async def test_a_round_is_scheduled_with_its_seasons_number():
    cog = _make_cog(rounds_by_division={11: [_round(1)]})

    await _catchup(cog, season_number=9)

    assert (
        cog.bot.scheduler_service.schedule_round.call_args.kwargs["season_number"] == 9
    )


async def test_a_round_whose_division_is_unknown_is_still_scheduled():
    """A round pointing at a division the season does not list is a broken state, and
    dropping it from the calendar silently is worse than scheduling it untitled."""
    cog = _make_cog(rounds_by_division={11: [_round(1, division_id=99)]})

    await _catchup(cog)

    kwargs = cog.bot.scheduler_service.schedule_round.call_args.kwargs
    assert kwargs["division_tier"] == 0
    assert kwargs["season_number"] == 0


async def test_the_horizons_are_passed_to_the_scheduler():
    """The scheduler arms each phase against them, so a catch-up that read one set of
    horizons and scheduled against another would forecast at two different offsets."""
    cog = _make_cog(rounds_by_division={11: [_round(1)]})

    await _catchup(cog, config=_config(phase_1_days=14, phase_2_days=5, phase_3_hours=2))

    kwargs = cog.bot.scheduler_service.schedule_round.call_args.kwargs
    assert kwargs["phase_1_days"] == 14
    assert kwargs["phase_2_days"] == 5
    assert kwargs["phase_3_hours"] == 2


async def test_a_season_with_no_divisions_schedules_nothing():
    """A season enabled before its divisions exist is ordinary during setup, and must not
    raise at a league switching the module on early."""
    cog = _make_cog(divisions=[], rounds_by_division={})

    await _catchup(cog)

    assert _scheduled_ids(cog) == []


async def test_a_division_with_no_rounds_schedules_nothing():
    cog = _make_cog(rounds_by_division={11: []})

    await _catchup(cog)

    assert _scheduled_ids(cog) == []
