"""The division and season writes in `SeasonService` that nothing else exercised.

Issue #208. `duplicate_division`, `add_division`'s tier checks and `load_all_setup_seasons` were
uncovered at the service level — the cogs that call them are tested with the service stubbed, which is the right shape for the cogs and left the
SQL itself unread.

**A tier is unique within a season, and zero means "not yet tiered".** Both `add_division` and
`duplicate_division` refuse a tier another division of the season holds and a negative one, but
a tier of zero is not checked at all — setup lays down untiered divisions before a manager
orders them, and refusing a second zero would block laying down a second division.

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
import sys
from datetime import date, datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.core.models.round import RoundFormat  # noqa: E402
from leaguebot.core.services.season_service import SeasonService  # noqa: E402

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


async def _source(service: SeasonService, *, tier=1):
    div = await service.add_division(SEASON_ID, "Pro", 555, forecast_channel_id=600, tier=tier)
    await service.add_round(
        div.id, 1, RoundFormat.NORMAL, "Silverstone", datetime(2026, 3, 1, 18, tzinfo=timezone.utc)
    )
    await service.add_round(
        div.id, 2, RoundFormat.SPRINT, "Monza", datetime(2026, 3, 8, 18, tzinfo=timezone.utc)
    )
    return div


# ---------------------------------------------------------------------------
# add_division
# ---------------------------------------------------------------------------


async def test_a_division_is_added_with_its_tier(tmp_path):
    service = SeasonService(await _make_db(tmp_path))

    div = await service.add_division(SEASON_ID, "Pro", 555, forecast_channel_id=600, tier=1)

    assert (div.name, div.tier, div.mention_role_id, div.forecast_channel_id) == (
        "Pro", 1, 555, 600
    )
    assert [d.name for d in await service.get_divisions(SEASON_ID)] == ["Pro"]


async def test_a_negative_tier_is_refused(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="add_negative"))

    with pytest.raises(ValueError, match="Tier must be >= 1"):
        await service.add_division(SEASON_ID, "Pro", 555, tier=-1)


async def test_a_tier_already_held_is_refused(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="add_dup"))
    await service.add_division(SEASON_ID, "Pro", 555, tier=1)

    with pytest.raises(ValueError, match="tier 1 already exists"):
        await service.add_division(SEASON_ID, "Am", 556, tier=1)


async def test_several_untiered_divisions_may_be_laid_down(tmp_path):
    """Setup lays them down before a manager orders them; zero is not checked."""
    service = SeasonService(await _make_db(tmp_path, name="add_zero"))

    await service.add_division(SEASON_ID, "Pro", 555)
    await service.add_division(SEASON_ID, "Am", 556)

    assert len(await service.get_divisions(SEASON_ID)) == 2


# ---------------------------------------------------------------------------
# duplicate_division
# ---------------------------------------------------------------------------


async def test_a_duplicate_copies_every_round(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="dup_rounds"))
    source = await _source(service)

    copy = await service.duplicate_division(source.id, "Am", 556, tier=2)

    rounds = await service.get_division_rounds(copy.id)
    assert [(r.round_number, r.format, r.track_name) for r in rounds] == [
        (1, RoundFormat.NORMAL, "Silverstone"),
        (2, RoundFormat.SPRINT, "Monza"),
    ]


async def test_a_duplicate_carries_its_own_name_role_and_tier(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="dup_identity"))
    source = await _source(service)

    copy = await service.duplicate_division(source.id, "Am", 556, forecast_channel_id=601, tier=2)

    assert (copy.name, copy.mention_role_id, copy.forecast_channel_id, copy.tier) == (
        "Am", 556, 601, 2
    )
    assert copy.season_id == SEASON_ID


async def test_the_offset_moves_every_round(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="dup_offset"))
    source = await _source(service)

    copy = await service.duplicate_division(source.id, "Am", 556, day_offset=1, hour_offset=1.5)

    first = (await service.get_division_rounds(copy.id))[0]
    assert first.scheduled_at.replace(tzinfo=timezone.utc) == datetime(
        2026, 3, 2, 19, 30, tzinfo=timezone.utc
    )


async def test_a_negative_offset_moves_rounds_back(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="dup_back"))
    source = await _source(service)

    copy = await service.duplicate_division(source.id, "Am", 556, day_offset=-2)

    first = (await service.get_division_rounds(copy.id))[0]
    assert first.scheduled_at.date() == date(2026, 2, 27)


async def test_the_copys_phase_flags_start_clear(tmp_path):
    """A copy of a round whose phase 1 has run is a new round nobody has forecast."""
    db_path = await _make_db(tmp_path, name="dup_flags")
    service = SeasonService(db_path)
    source = await _source(service)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET phase1_done = 1, phase2_done = 1 WHERE division_id = ?",
            (source.id,),
        )
        await db.commit()

    copy = await service.duplicate_division(source.id, "Am", 556)

    assert all(
        not (r.phase1_done or r.phase2_done or r.phase3_done)
        for r in await service.get_division_rounds(copy.id)
    )


async def test_the_source_is_untouched(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="dup_source"))
    source = await _source(service)

    await service.duplicate_division(source.id, "Am", 556, day_offset=7)

    rounds = await service.get_division_rounds(source.id)
    assert rounds[0].scheduled_at.date() == date(2026, 3, 1)


async def test_duplicating_onto_a_held_tier_is_refused(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="dup_tier"))
    source = await _source(service, tier=1)

    with pytest.raises(ValueError, match="tier 1 already exists"):
        await service.duplicate_division(source.id, "Am", 556, tier=1)

    assert len(await service.get_divisions(SEASON_ID)) == 1


async def test_duplicating_onto_a_negative_tier_is_refused(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="dup_negative"))
    source = await _source(service)

    with pytest.raises(ValueError, match="Tier must be >= 1"):
        await service.duplicate_division(source.id, "Am", 556, tier=-2)


async def test_a_division_with_no_rounds_duplicates_empty(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="dup_empty"))
    source = await service.add_division(SEASON_ID, "Pro", 555, tier=1)

    copy = await service.duplicate_division(source.id, "Am", 556, tier=2)

    assert await service.get_division_rounds(copy.id) == []


# ---------------------------------------------------------------------------
# Rebuilding setup seasons on start-up
# ---------------------------------------------------------------------------


async def test_a_setup_season_is_rebuilt_with_its_divisions_and_rounds(tmp_path):
    service = SeasonService(await _make_db(tmp_path, name="load_setup"))
    await _source(service)

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
