"""The division and season writes in `SeasonService` that nothing else exercised.

Issue #208. `duplicate_division` and `load_all_setup_seasons` were uncovered at the service
level — the cogs that call them are tested with the service stubbed, which is the right shape for
the cogs and left the SQL itself unread. The divisions and rounds they copy and read are laid down
by SQL, as the setup commands' saves write them.

**A tier is unique within a season.** `duplicate_division` refuses a tier another division of the
season holds and a negative one.

**A duplicated division copies the calendar, shifted, and nothing else.** Every round is
copied with the same format and track and its time moved by the offset; the phase flags start
at zero, because a copy of a round whose phase 1 has run is a new round nobody has forecast.
The copy is then renumbered by date, which matters when a negative offset moves rounds past
each other's positions in a copy that also drops the source's numbering.

**Setup seasons are rebuilt from the database on start-up.** `load_all_setup_seasons` is what
turns the tables back into the in-memory pending configuration after a restart, and only
seasons still in SETUP are read — an active season has no pending configuration to rebuild.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.services.season_service import SeasonService

SERVER_ID = 13408
SEASON_ID = 1


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name="season_divisions", status="SETUP") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, game_edition, start_date, "
            "status) VALUES (?, 5, 25, '2026-01-01', ?)",
            (SEASON_ID, status),
        )
        await db.commit()
    return db_path


async def _division(db_path: str, name: str, role_id: int, *, forecast_channel_id=None,
                    tier: int) -> int:
    """A division of season 5 laid down by SQL, its id returned."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id, tier) "
            "VALUES (?, ?, ?, ?, ?)",
            (SEASON_ID, name, role_id, forecast_channel_id, tier),
        )
        await db.commit()
        return cursor.lastrowid


async def _source(db_path: str, *, tier=1) -> int:
    """Pro (tier *tier*, role 555, forecast channel 600) with round 1 at Silverstone on 1 March
    and a sprint round 2 at Monza on 8 March, both at 18:00 UTC; its id returned."""
    div_id = await _division(db_path, "Pro", 555, forecast_channel_id=600, tier=tier)
    async with get_connection(db_path) as db:
        for number, fmt, track, at in (
            (1, RoundFormat.NORMAL, "Silverstone", datetime(2026, 3, 1, 18, tzinfo=timezone.utc)),
            (2, RoundFormat.SPRINT, "Monza", datetime(2026, 3, 8, 18, tzinfo=timezone.utc)),
        ):
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, "
                "scheduled_at) VALUES (?, ?, ?, ?, ?)",
                (div_id, number, fmt.value, track, at.isoformat()),
            )
        await db.commit()
    return div_id


# ---------------------------------------------------------------------------
# duplicate_division
# ---------------------------------------------------------------------------


async def test_a_duplicate_copies_every_round(tmp_path):
    db_path = await _make_db(tmp_path, name="dup_rounds")
    service = SeasonService(db_path)
    source = await _source(db_path)

    copy = await service.duplicate_division(source, "Am", 556, tier=2)

    rounds = await service.get_division_rounds(copy.id)
    assert [(r.round_number, r.format, r.track_name) for r in rounds] == [
        (1, RoundFormat.NORMAL, "Silverstone"),
        (2, RoundFormat.SPRINT, "Monza"),
    ]


async def test_a_duplicate_carries_its_own_name_role_and_tier(tmp_path):
    db_path = await _make_db(tmp_path, name="dup_identity")
    service = SeasonService(db_path)
    source = await _source(db_path)

    copy = await service.duplicate_division(source, "Am", 556, forecast_channel_id=601, tier=2)

    assert (copy.name, copy.mention_role_id, copy.forecast_channel_id, copy.tier) == (
        "Am", 556, 601, 2
    )
    assert copy.season_id == SEASON_ID


async def test_the_offset_moves_every_round(tmp_path):
    db_path = await _make_db(tmp_path, name="dup_offset")
    service = SeasonService(db_path)
    source = await _source(db_path)

    copy = await service.duplicate_division(source, "Am", 556, day_offset=1, hour_offset=1.5)

    first = (await service.get_division_rounds(copy.id))[0]
    assert first.scheduled_at.replace(tzinfo=timezone.utc) == datetime(
        2026, 3, 2, 19, 30, tzinfo=timezone.utc
    )


async def test_a_negative_offset_moves_rounds_back(tmp_path):
    db_path = await _make_db(tmp_path, name="dup_back")
    service = SeasonService(db_path)
    source = await _source(db_path)

    copy = await service.duplicate_division(source, "Am", 556, day_offset=-2)

    first = (await service.get_division_rounds(copy.id))[0]
    assert first.scheduled_at.date() == date(2026, 2, 27)


async def test_the_copys_phase_flags_start_clear(tmp_path):
    """A copy of a round whose phase 1 has run is a new round nobody has forecast."""
    db_path = await _make_db(tmp_path, name="dup_flags")
    service = SeasonService(db_path)
    source = await _source(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET phase1_done = 1, phase2_done = 1 WHERE division_id = ?",
            (source,),
        )
        await db.commit()

    copy = await service.duplicate_division(source, "Am", 556)

    assert all(
        not (r.phase1_done or r.phase2_done or r.phase3_done)
        for r in await service.get_division_rounds(copy.id)
    )


async def test_the_source_is_untouched(tmp_path):
    db_path = await _make_db(tmp_path, name="dup_source")
    service = SeasonService(db_path)
    source = await _source(db_path)

    await service.duplicate_division(source, "Am", 556, day_offset=7)

    rounds = await service.get_division_rounds(source)
    assert rounds[0].scheduled_at.date() == date(2026, 3, 1)


async def test_duplicating_onto_a_held_tier_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, name="dup_tier")
    service = SeasonService(db_path)
    source = await _source(db_path, tier=1)

    with pytest.raises(ValueError, match="tier 1 already exists"):
        await service.duplicate_division(source, "Am", 556, tier=1)

    assert len(await service.get_divisions(SEASON_ID)) == 1


async def test_duplicating_onto_a_negative_tier_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, name="dup_negative")
    service = SeasonService(db_path)
    source = await _source(db_path)

    with pytest.raises(ValueError, match="Tier must be >= 1"):
        await service.duplicate_division(source, "Am", 556, tier=-2)


async def test_a_division_with_no_rounds_duplicates_empty(tmp_path):
    db_path = await _make_db(tmp_path, name="dup_empty")
    service = SeasonService(db_path)
    source = await _division(db_path, "Pro", 555, tier=1)

    copy = await service.duplicate_division(source, "Am", 556, tier=2)

    assert await service.get_division_rounds(copy.id) == []


# ---------------------------------------------------------------------------
# Rebuilding setup seasons on start-up
# ---------------------------------------------------------------------------


async def test_a_setup_season_is_rebuilt_with_its_divisions_and_rounds(tmp_path):
    db_path = await _make_db(tmp_path, name="load_setup")
    service = SeasonService(db_path)
    await _source(db_path)

    [season] = await service.load_all_setup_seasons()

    assert season["season_id"] == SEASON_ID
    assert season["season_number"] == 5
    assert season["game_edition"] == 25
    assert season["start_date"] == date(2026, 1, 1)
    [division] = season["divisions"]
    assert (division["name"], division["role_id"], division["channel_id"], division["tier"]) == (
        "Pro", 555, 600, 1
    )
    assert [r["round_number"] for r in division["rounds"]] == [1, 2]
    assert division["rounds"][1]["format"] == RoundFormat.SPRINT


async def test_an_active_season_is_not_rebuilt(tmp_path):
    """It has no pending configuration to rebuild."""
    service = SeasonService(await _make_db(tmp_path, name="load_active", status="ACTIVE"))

    assert await service.load_all_setup_seasons() == []


async def test_a_setup_season_with_no_divisions_rebuilds_empty(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="load_empty"))

    [season] = await service.load_all_setup_seasons()

    assert season["divisions"] == []
