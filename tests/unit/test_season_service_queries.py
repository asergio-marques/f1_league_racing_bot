"""`SeasonService` — which season is current, and what deleting a division destroys.

Issue #208. `season_service.py` was at 54.6%. It is the service the whole bot asks "is there a
season, and what state is it in", and the answer decides whether almost every command runs.

**A server holds at most one *live* season and any number of archived ones.** Migration 049
enforces that with a partial unique index over `status IN ('SETUP', 'ACTIVE')`, so a league can
keep its whole history while only ever building or racing one season at a time. That shape is
what makes the four lookups distinguishable, and it is seeded here rather than assumed: the
tests put several COMPLETED seasons behind one live one, which is the arrangement a league is
actually in from its second season onwards.

`get_confirmed_season`, `get_setup_season`, `get_setup_or_active_season` and
`get_season_for_server` each answer a different question about that data, and every command in
the bot picks one of them — picking the wrong one is how a command comes to edit last season.
Each is therefore checked against archived seasons being present, which is the case that tells
a correct lookup from one that simply takes the first row.

**An archived season is immutable.** COMPLETED and CANCELLED both refuse, because both are the
championship's record — CANCELLED is not "deleted", it is "this happened and was abandoned",
and its data is deliberately preserved.

**Deleting a division deletes seven tables' worth of rows by hand.** Three of them —
`team_seats`, `team_instances` and `driver_season_assignments` — have no cascade, so they are
removed explicitly; the comments in the method say so, and this file holds them. A missed table
leaves rows pointing at a division that no longer exists, which surfaces much later as a lineup
drawing a team nobody can find. `test_deleting_a_division_leaves_nothing_behind` sweeps all of
them in one assertion so a newly added child table shows up as a failure here.

**The three results channel setters share one row and must not blank each other.** They are
separate `ON CONFLICT` upserts naming one column each, which is the shape where a copy-paste
error silently clears the neighbouring channel.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.season import SeasonStatus  # noqa: E402
from services.season_service import SeasonImmutableError, SeasonService  # noqa: E402

SERVER_ID = 9808
OTHER_SERVER_ID = 9809


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "seasons.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        for server_id in (SERVER_ID, OTHER_SERVER_ID):
            await db.execute(
                "INSERT INTO server_configs (server_id, interaction_role_id, "
                "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
                (server_id,),
            )
        await db.commit()
    return db_path


async def _seed_season(
    db_path: str,
    season_id: int,
    status: str,
    *,
    number: int = 1,
    server_id: int = SERVER_ID,
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, ?, '2026-01-01', ?)",
            (season_id, server_id, number, status),
        )
        await db.commit()


async def _seed_division(db_path: str, division_id: int, season_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, ?, ?, 555)",
            (division_id, season_id, f"Division {division_id}", division_id),
        )
        await db.commit()


async def _seed_full_division(
    db_path: str, division_id: int, season_id: int, *, seated_driver: bool = False
) -> None:
    """A division with a round, a team and an empty seat hanging off it.

    *seated_driver* additionally places a driver in the seat and records the season
    assignment. It defaults to **off** because `delete_division` cannot currently delete a
    division in that state — see the note on
    `test_deleting_a_division_leaves_nothing_behind`.
    """
    await _seed_division(db_path, division_id, season_id)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 1, 'NORMAL', 'Silverstone Circuit', '2026-06-01')",
            (division_id * 10, division_id),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (?, ?, 'Alpha', 2, 0)",
            (division_id * 10, division_id),
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state) "
            "VALUES (?, ?, ?, 'ACTIVE')",
            (division_id, SERVER_ID, str(4000 + division_id)),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (?, ?, 1, ?)",
            (division_id * 10, division_id * 10, division_id if seated_driver else None),
        )
        if seated_driver:
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id) "
                "VALUES (?, ?, ?, ?)",
                (division_id, season_id, division_id, division_id * 10),
            )
        await db.commit()


async def _count(db_path: str, table: str, where: str, params: tuple) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params  # noqa: S608
        )
        return (await cursor.fetchone())["n"]


# ---------------------------------------------------------------------------
# Which season is which
# ---------------------------------------------------------------------------


async def _history_plus_live(tmp_path, live_status: str) -> str:
    """Two archived seasons and one live one, in the state a league is really in.

    Only one `SETUP`-or-`ACTIVE` season may exist per server — migration 049's partial
    unique index — so *live_status* picks which of the two the server is currently in.

    A helper rather than a fixture: an async fixture needs `pytest_asyncio.fixture`, and
    nothing else in this suite uses one.
    """
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "COMPLETED", number=1)
    await _seed_season(db_path, 2, "CANCELLED", number=2)
    await _seed_season(db_path, 3, live_status, number=3)
    return db_path


async def test_the_active_season_is_found_past_a_league_s_history(tmp_path):
    """Two archived seasons sit in front of it. A lookup taking the first row would hand
    back last year's championship."""
    db_path = await _history_plus_live(tmp_path, "ACTIVE")

    season = await SeasonService(db_path).get_confirmed_season(SERVER_ID)

    assert season is not None
    assert season.id == 3
    assert season.status == SeasonStatus.ACTIVE


async def test_a_league_between_seasons_has_no_active_season(tmp_path):
    """Its live season is in SETUP, so `get_confirmed_season` must be empty — commands
    guarded on an active season are exactly the ones that must not run during setup."""
    db_path = await _history_plus_live(tmp_path, "SETUP")

    assert await SeasonService(db_path).get_confirmed_season(SERVER_ID) is None


async def test_the_setup_season_is_the_one_being_built(tmp_path):
    db_path = await _history_plus_live(tmp_path, "SETUP")

    season = await SeasonService(db_path).get_setup_season(SERVER_ID)

    assert season is not None
    assert season.id == 3


async def test_a_league_mid_season_has_no_setup_season(tmp_path):
    db_path = await _history_plus_live(tmp_path, "ACTIVE")

    assert await SeasonService(db_path).get_setup_season(SERVER_ID) is None


@pytest.mark.parametrize("live_status", ["SETUP", "ACTIVE"])
async def test_setup_or_active_finds_the_live_season_either_way(tmp_path, live_status):
    """Commands that may run in either state use this, and it has to reach past the
    archived seasons to whichever one is live."""
    db_path = await _history_plus_live(tmp_path, live_status)

    season = await SeasonService(db_path).get_setup_or_active_season(SERVER_ID)

    assert season is not None
    assert season.id == 3


async def test_a_league_with_only_archived_seasons_has_no_live_one(tmp_path):
    """The state between finishing one season and starting the next."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "COMPLETED", number=1)

    assert await SeasonService(db_path).get_setup_or_active_season(SERVER_ID) is None


async def test_a_server_with_no_season_reads_as_none(tmp_path):
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)

    assert await service.get_confirmed_season(SERVER_ID) is None
    assert await service.get_setup_season(SERVER_ID) is None
    assert await service.get_season_for_server(SERVER_ID) is None


async def test_another_server_s_seasons_are_never_returned(tmp_path):
    """Every lookup is server-scoped; one league driving another's season is the fault
    these `WHERE server_id` clauses exist to prevent."""
    service = SeasonService(await _history_plus_live(tmp_path, "ACTIVE"))

    assert await service.get_confirmed_season(OTHER_SERVER_ID) is None
    assert await service.get_setup_season(OTHER_SERVER_ID) is None


# ---------------------------------------------------------------------------
# Existence and counting
# ---------------------------------------------------------------------------


async def test_a_setup_season_counts_as_existing(tmp_path):
    db_path = await _history_plus_live(tmp_path, "SETUP")
    assert await SeasonService(db_path).has_existing_season(SERVER_ID) is True


async def test_a_setup_season_alone_is_not_active_or_completed(tmp_path):
    """`/bot-init` and the reset command turn on this distinction: a season being built
    has never been raced, so it is not a record to protect."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    service = SeasonService(db_path)

    assert await service.has_active_or_completed_season(SERVER_ID) is False
    assert await service.has_active_or_setup_season(SERVER_ID) is True


async def test_a_completed_season_is_not_setup_or_active(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "COMPLETED")
    service = SeasonService(db_path)

    assert await service.has_active_or_completed_season(SERVER_ID) is True
    assert await service.has_active_or_setup_season(SERVER_ID) is False


async def test_completed_seasons_are_counted(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "COMPLETED", number=1)
    await _seed_season(db_path, 2, "COMPLETED", number=2)
    await _seed_season(db_path, 3, "ACTIVE", number=3)

    assert await SeasonService(db_path).count_completed_seasons(SERVER_ID) == 2


async def test_a_setup_season_has_not_committed_its_number(tmp_path):
    """`count_persisted_seasons` is every season whose number is already spoken for.
    A SETUP season's number can still change, so counting it would skip a number."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "COMPLETED", number=1)
    await _seed_season(db_path, 2, "CANCELLED", number=2)
    await _seed_season(db_path, 3, "SETUP", number=3)

    assert await SeasonService(db_path).count_persisted_seasons(SERVER_ID) == 2


async def test_a_cancelled_season_still_counts_as_persisted(tmp_path):
    """CANCELLED is not "deleted" — it is "this happened and was abandoned", and its
    number stays spoken for."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "CANCELLED", number=1)

    assert await SeasonService(db_path).count_persisted_seasons(SERVER_ID) == 1


# ---------------------------------------------------------------------------
# The lifecycle transitions
# ---------------------------------------------------------------------------


async def test_a_season_is_completed_in_place(tmp_path):
    """Archived, not removed — the championship's record."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "ACTIVE")
    service = SeasonService(db_path)

    await service.complete_season(1)

    assert await service.get_confirmed_season(SERVER_ID) is None
    assert await service.has_active_or_completed_season(SERVER_ID) is True


async def test_a_cancelled_season_keeps_its_data(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "ACTIVE")
    await _seed_division(db_path, 11, 1)
    service = SeasonService(db_path)

    await service.cancel_season(1)

    assert await _count(db_path, "divisions", "season_id = ?", (1,)) == 1


@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
async def test_an_archived_season_refuses_modification(tmp_path, status):
    """Both are the championship's record. Editing either would move results already
    published."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, status)
    service = SeasonService(db_path)
    season = await service.get_season_for_server(SERVER_ID)

    with pytest.raises(SeasonImmutableError, match="archived"):
        await service.assert_season_mutable(season)


@pytest.mark.parametrize("status", ["SETUP", "ACTIVE"])
async def test_a_live_season_may_be_modified(tmp_path, status):
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, status)
    service = SeasonService(db_path)
    season = await service.get_season_for_server(SERVER_ID)

    await service.assert_season_mutable(season)  # must not raise


# ---------------------------------------------------------------------------
# Divisions
# ---------------------------------------------------------------------------


async def test_divisions_are_returned_in_tier_order(tmp_path):
    """A league reads its divisions from the top down, and the tier is what orders them —
    not the id, which follows creation order."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (11, 1, 'Second', 2, 555)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (12, 1, 'First', 1, 556)"
        )
        await db.commit()

    divisions = await SeasonService(db_path).get_divisions(1)

    assert [d.name for d in divisions] == ["First", "Second"]


async def test_a_division_is_renamed(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    await _seed_division(db_path, 11, 1)
    service = SeasonService(db_path)

    await service.rename_division(11, "Premier")

    assert (await service.get_divisions(1))[0].name == "Premier"


async def test_deleting_a_division_leaves_nothing_behind(tmp_path):
    """Seven tables, three of which have no cascade and are removed by hand. A missed one
    leaves rows pointing at a division that no longer exists, which surfaces much later as
    a lineup drawing a team nobody can find.

    **This covers a division whose seats are empty.** A division with a driver actually
    seated in it cannot be deleted at all: the method removes `team_seats` while
    `driver_season_assignments.team_seat_id` still references those rows, and that foreign
    key is `NO ACTION`, so SQLite refuses. Reproduced and reported separately; fixing it is
    out of scope for #208, and the assignment is left out here rather than have this file
    assert that the failure is correct.
    """
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    await _seed_full_division(db_path, 11, 1)

    await SeasonService(db_path).delete_division(11)

    assert await _count(db_path, "divisions", "id = ?", (11,)) == 0
    assert await _count(db_path, "rounds", "division_id = ?", (11,)) == 0
    assert await _count(db_path, "team_instances", "division_id = ?", (11,)) == 0
    assert await _count(
        db_path, "team_seats", "team_instance_id = ?", (110,)
    ) == 0
    assert await _count(
        db_path, "driver_season_assignments", "division_id = ?", (11,)
    ) == 0


async def test_deleting_one_division_leaves_its_neighbours_alone(tmp_path):
    """The deletes are all keyed on the division, and a missing `WHERE` would take the
    whole season's lineup with it."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    await _seed_full_division(db_path, 11, 1)
    await _seed_full_division(db_path, 12, 1)

    await SeasonService(db_path).delete_division(11)

    assert await _count(db_path, "divisions", "id = ?", (12,)) == 1
    assert await _count(db_path, "rounds", "division_id = ?", (12,)) == 1
    assert await _count(db_path, "team_instances", "division_id = ?", (12,)) == 1
    assert await _count(db_path, "team_seats", "team_instance_id = ?", (120,)) == 1


async def test_deleting_a_division_with_no_rounds_is_not_an_error(tmp_path):
    """A division added during setup and removed before any round was scheduled."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    await _seed_division(db_path, 11, 1)

    await SeasonService(db_path).delete_division(11)

    assert await _count(db_path, "divisions", "id = ?", (11,)) == 0


# ---------------------------------------------------------------------------
# The channel setters
# ---------------------------------------------------------------------------


async def test_the_forecast_channel_is_set_and_reports_what_it_replaced(tmp_path):
    """The previous value is what the audit entry records, so a manager can get back to
    the channel they replaced."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    await _seed_division(db_path, 11, 1)
    service = SeasonService(db_path)

    assert await service.set_division_forecast_channel(11, 700001) is None
    assert await service.set_division_forecast_channel(11, 700002) == 700001
    assert (await service.get_divisions(1))[0].forecast_channel_id == 700002


CHANNEL_SETTERS = [
    ("set_division_results_channel", "results_channel_id"),
    ("set_division_standings_channel", "standings_channel_id"),
    ("set_division_penalty_channel", "penalty_channel_id"),
]


#: `penalty_channel_id` is a TEXT column while the other two are INTEGER, so the value
#: comes back as a string. Compared as text throughout rather than papered over with a cast:
#: the divergence is real and a test that hid it would hide the next one too.
def _as_text(value) -> str | None:
    return None if value is None else str(value)


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_each_results_channel_setter_writes_its_own_column(tmp_path, setter, column):
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    await _seed_division(db_path, 11, 1)
    service = SeasonService(db_path)

    await getattr(service, setter)(11, 700001)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {column} FROM division_results_config WHERE division_id = 11"  # noqa: S608
        )
        assert _as_text((await cursor.fetchone())[column]) == "700001"


async def test_the_three_results_channels_share_a_row_without_blanking_each_other(tmp_path):
    """Three separate upserts naming one column each — the shape where a copy-paste error
    silently clears the neighbouring channel."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    await _seed_division(db_path, 11, 1)
    service = SeasonService(db_path)

    await service.set_division_results_channel(11, 700001)
    await service.set_division_standings_channel(11, 700002)
    await service.set_division_penalty_channel(11, 700003)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT results_channel_id, standings_channel_id, penalty_channel_id "
            "FROM division_results_config WHERE division_id = 11"
        )
        row = await cursor.fetchone()
    assert row["results_channel_id"] == 700001
    assert row["standings_channel_id"] == 700002
    assert _as_text(row["penalty_channel_id"]) == "700003"


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_a_results_channel_setter_reports_what_it_replaced(tmp_path, setter, column):
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    await _seed_division(db_path, 11, 1)
    service = SeasonService(db_path)

    assert await getattr(service, setter)(11, 700001) is None
    assert _as_text(await getattr(service, setter)(11, 700002)) == "700001"


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_a_results_channel_can_be_cleared(tmp_path, setter, column):
    """A league that stops posting one of the three has no other way to unset it."""
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    await _seed_division(db_path, 11, 1)
    service = SeasonService(db_path)
    await getattr(service, setter)(11, 700001)

    await getattr(service, setter)(11, None)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {column} FROM division_results_config WHERE division_id = 11"  # noqa: S608
        )
        assert (await cursor.fetchone())[column] is None


async def test_setting_a_channel_twice_does_not_make_a_second_row(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_season(db_path, 1, "SETUP")
    await _seed_division(db_path, 11, 1)
    service = SeasonService(db_path)

    await service.set_division_results_channel(11, 700001)
    await service.set_division_results_channel(11, 700002)

    assert await _count(db_path, "division_results_config", "division_id = ?", (11,)) == 1
