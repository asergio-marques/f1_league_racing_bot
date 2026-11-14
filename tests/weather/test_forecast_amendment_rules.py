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

import pytest

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


@pytest.mark.xfail(strict=True, reason="#287: weather does not yet judge its own part of an "
                   "amendment (forecast_amendment.amendment_judged)")
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
