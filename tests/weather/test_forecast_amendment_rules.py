"""Weather's part of judging a round's amendment — `forecast_amendment` (#287).

Core judges its own rules and asks each module for its part through `amendment_judged`. Weather's
part is the two forecast refusals, its note and the phases it withdraws, judged from the phases
already drawn, which it reads itself, on the connection core hands it: an amendment's save has
written what it has not yet committed, and the judgement must see it.

Every test pins ``now`` alongside the dates it seeds.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import Round, RoundFormat, RoundStatus, WeatherWindows

ROUND_ID = 31
MOMENT = datetime(2026, 12, 1, 18, 0, tzinfo=timezone.utc)
#: Three days before the round: Phase 1 (five days out) has fallen due, Phase 2 (two days) not.
NOW = MOMENT - timedelta(days=3)
#: The packaged forecast horizons.
DEFAULT_WEATHER = WeatherWindows(phase_1_days=5, phase_2_days=2, phase_3_hours=2)
FIRST_FORECAST_REFUSAL = (
    "The first forecast for this round has already been posted, and it was drawn for its present "
    "circuit. Amend the round's moment as well to put the forecasts back in question."
)


async def _make_db(tmp_path) -> str:
    """Pro's round 1 (id 31) at Monza, on 1 December 2026 at 18:00 UTC, no phase drawn."""
    db_path = os.path.join(str(tmp_path), "forecast_amendment.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 3, '2026-11-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (1, 1, 'Pro', 1, 801)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at, "
            "phase1_done, phase2_done, phase3_done) "
            "VALUES (?, 1, 1, 'NORMAL', 'Monza', ?, 0, 0, 0)",
            (ROUND_ID, MOMENT.isoformat()),
        )
        await db.commit()
    return db_path


async def test_the_drawn_phases_are_read_on_the_connection_handed(tmp_path):
    """Pro's round 1, three days out, its Phase 1 not drawn as committed. On the connection an
    amendment's save holds, Phase 1 is marked drawn and not yet committed, and weather is asked
    on that connection for its part of a change of the round's track alone. It refuses the change
    for the first forecast already posted, as it reads the phase drawn on that connection."""
    db_path = await _make_db(tmp_path)
    from leaguebot.weather.services.forecast_amendment import amendment_judged

    rnd = Round(
        id=ROUND_ID, division_id=1, round_number=1, format=RoundFormat.NORMAL,
        track_name="Monza", scheduled_at=MOMENT, status=RoundStatus.NOT_RUN.value,
    )
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET phase1_done = 1 WHERE id = ?", (ROUND_ID,))
        part = await amendment_judged(
            db, rnd, {"track_name": "Silverstone Circuit"}, moment=MOMENT, now=NOW,
            attendance=None, weather=DEFAULT_WEATHER,
        )
        await db.rollback()

    assert list(part.refusals) == [FIRST_FORECAST_REFUSAL]


# ---------------------------------------------------------------------------
# "Would it have run, were the round always to have stood at its new moment?"
# ---------------------------------------------------------------------------


def _forecasts(changes, *, moment, windows=DEFAULT_WEATHER, drawn=None):
    """The forecasts' part of *changes*, leaving the round at *moment*, judged at NOW."""
    from leaguebot.weather.services.forecast_amendment import judge_forecasts

    return judge_forecasts(changes, moment=moment, now=NOW, windows=windows, drawn=drawn or {})


def test_a_long_delay_puts_every_phase_back_in_question():
    """The amended moment 30 days out, the default horizons, Phase 1 drawn (the round's earlier
    moment is not judged): every phase is armed again."""
    moment = NOW + timedelta(days=30)
    judgement = _forecasts({"scheduled_at": moment}, moment=moment, drawn={1: True})
    assert all(p.rearm for p in judgement.phases.values())


def test_a_delay_can_leave_phase_one_standing_while_the_later_two_are_rearmed():
    """The middle case the rule exists for: one phase keeps, two go back.

    The amended moment four days out, the default horizons, Phase 1 drawn; the round's earlier
    moment is not judged. Phase 1 falls five days before it, which is behind us, so the forecast
    already drawn stands. Phases 2 and 3 have not come round under the new moment, so they are
    armed again. Nothing is refused.
    """
    moment = NOW + timedelta(days=4)
    judgement = _forecasts({"scheduled_at": moment}, moment=moment, drawn={1: True})
    assert not judgement.part.refusals
    assert judgement.phases[1].stands
    assert judgement.phases[2].rearm
    assert judgement.phases[3].rearm


def test_a_phase_falling_exactly_now_counts_as_having_run():
    """The amended moment five days out, the default horizons, no phase drawn, so Phase 1 lands
    exactly now (the round's earlier moment is not judged): Phase 1 stands, as having run, and
    Phase 2 is armed again."""
    moment = NOW + timedelta(days=5)
    judgement = _forecasts({"scheduled_at": moment}, moment=moment)
    assert judgement.phases[1].stands
    assert judgement.phases[2].rearm


def test_no_phases_are_judged_without_configured_horizons():
    """With no forecast horizons, a track change to a round 30 days out judges no phase."""
    judgement = _forecasts(
        {"track_name": "Silverstone Circuit"}, moment=NOW + timedelta(days=30), windows=None
    )
    assert judgement.phases == {}


# ---------------------------------------------------------------------------
# What the track and the format may no longer be
# ---------------------------------------------------------------------------


def test_the_track_may_not_be_amended_once_phase_one_has_run():
    """A round three days out, its Phase 1 drawn, no horizons to put it back in question: its
    track change is refused for the first forecast."""
    judgement = _forecasts(
        {"track_name": "Silverstone Circuit"}, moment=NOW + timedelta(days=3), windows=None,
        drawn={1: True},
    )
    assert any("first forecast" in r for r in judgement.part.refusals)


def test_the_format_may_not_be_amended_once_phase_two_has_run():
    """A round a day out, its Phases 1 and 2 drawn, no horizons: its format change is refused for
    the second forecast."""
    judgement = _forecasts(
        {"format": RoundFormat.SPRINT}, moment=NOW + timedelta(days=1), windows=None,
        drawn={1: True, 2: True},
    )
    assert any("second forecast" in r for r in judgement.part.refusals)


def test_the_format_may_still_be_amended_after_phase_one_alone():
    """A round three days out, its Phase 1 alone drawn, no horizons: its format change is refused
    nothing."""
    judgement = _forecasts(
        {"format": RoundFormat.SPRINT}, moment=NOW + timedelta(days=3), windows=None,
        drawn={1: True},
    )
    assert not judgement.part.refusals
