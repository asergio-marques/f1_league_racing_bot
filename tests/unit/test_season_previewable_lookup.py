"""The two season lookups the `/images test` previews rest on (046).

`get_previewable_season` decides which season a preview draws, and its precedence is the
whole point of it — the deliberately unordered `get_setup_or_active_season` cannot be used.
`get_previous_season_number` gives a fabricated league its season number.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.season import SeasonStatus  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 5151
OTHER_SERVER = 6262

#: Pinned, so a seeded date cannot drift into the past as the suite ages.
START = date(2026, 3, 1)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "seasons.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        # Both servers, because `seasons.server_id` is a foreign key and the isolation
        # tests seed a season on the second one.
        for server_id in (SERVER_ID, OTHER_SERVER):
            await db.execute(
                "INSERT OR IGNORE INTO server_configs (server_id, interaction_role_id, "
                "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
                (server_id,),
            )
        await db.commit()
    return path


@pytest.fixture
def service(db_path):
    return SeasonService(db_path)


async def _seed(db_path, status: str, number: int, *, server_id: int = SERVER_ID) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, ?, ?, ?)",
            (server_id, START.isoformat(), status, number),
        )
        await db.commit()
        return cursor.lastrowid


# ── get_previewable_season ────────────────────────────────────────────────


class TestPreviewableSeason:
    async def test_no_season_at_all_returns_none(self, service):
        assert await service.get_previewable_season(SERVER_ID) is None

    async def test_an_active_season_is_returned(self, service, db_path):
        await _seed(db_path, "ACTIVE", 3)

        season = await service.get_previewable_season(SERVER_ID)

        assert season is not None
        assert season.season_number == 3
        assert season.status is SeasonStatus.ACTIVE

    async def test_a_season_pending_approval_is_returned_when_nothing_is_approved(
        self, service, db_path
    ):
        await _seed(db_path, "SETUP", 1)

        season = await service.get_previewable_season(SERVER_ID)

        assert season is not None
        assert season.season_number == 1
        assert season.status is SeasonStatus.SETUP

    @pytest.mark.parametrize(
        "first, second", [("ACTIVE", "SETUP"), ("SETUP", "ACTIVE"), ("SETUP", "SETUP")]
    )
    async def test_a_server_cannot_hold_two_live_seasons(self, db_path, first, second):
        """The state the preview used to arbitrate is now refused by the database.

        A live season is one that is approved or pending approval, and a server holds at
        most one. Migration 049 enforces it with a partial unique index rather than
        leaving it to the command that starts a season — every reader of "the season of
        this server" depends on there being exactly one, and `/season setup`'s own guard
        cannot speak for the stored data.
        """
        await _seed(db_path, first, 4)

        with pytest.raises(aiosqlite.IntegrityError):
            await _seed(db_path, second, 5)

    @pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
    async def test_a_finished_season_is_never_previewable(self, service, db_path, status):
        await _seed(db_path, status, 2)

        assert await service.get_previewable_season(SERVER_ID) is None

    async def test_another_servers_season_is_not_returned(self, service, db_path):
        await _seed(db_path, "ACTIVE", 9, server_id=OTHER_SERVER)

        assert await service.get_previewable_season(SERVER_ID) is None


# ── get_previous_season_number ────────────────────────────────────────────


class TestPreviousSeasonNumber:
    async def test_a_server_that_has_never_held_a_season_is_zero(self, service):
        assert await service.get_previous_season_number(SERVER_ID) == 0

    async def test_the_highest_committed_number_is_returned(self, service, db_path):
        for number in (1, 2, 3, 4):
            await _seed(db_path, "COMPLETED", number)

        assert await service.get_previous_season_number(SERVER_ID) == 4

    async def test_a_cancelled_season_still_counts(self, service, db_path):
        """Its number was issued and must not be handed out twice."""
        await _seed(db_path, "COMPLETED", 1)
        await _seed(db_path, "CANCELLED", 2)

        assert await service.get_previous_season_number(SERVER_ID) == 2

    async def test_an_active_season_counts(self, service, db_path):
        await _seed(db_path, "ACTIVE", 7)

        assert await service.get_previous_season_number(SERVER_ID) == 7

    async def test_a_season_pending_approval_does_not_count(self, service, db_path):
        """It holds a provisional number; nothing is committed until it is approved."""
        await _seed(db_path, "COMPLETED", 4)
        await _seed(db_path, "SETUP", 5)

        assert await service.get_previous_season_number(SERVER_ID) == 4

    async def test_only_a_pending_season_still_reads_zero(self, service, db_path):
        await _seed(db_path, "SETUP", 1)

        assert await service.get_previous_season_number(SERVER_ID) == 0

    async def test_another_servers_seasons_do_not_count(self, service, db_path):
        await _seed(db_path, "COMPLETED", 9, server_id=OTHER_SERVER)

        assert await service.get_previous_season_number(SERVER_ID) == 0


# ── get_previewable_divisions ─────────────────────────────────────────────


async def _seed_division(db_path, season_id: int, name: str, tier: int) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, tier, status, mention_role_id) "
            "VALUES (?, ?, ?, 'ACTIVE', ?)",
            (season_id, name, tier, 1000 + tier),
        )
        await db.commit()
        return cursor.lastrowid


class TestPreviewableDivisions:
    """One connection doing what `get_previewable_season` + `get_divisions` did in two.

    The autocomplete this feeds has three seconds and cannot be deferred, so halving the
    connect/PRAGMA/close cost is the point. The precedence rules are the same ones
    `TestPreviewableSeason` above pins; these tests check the pairing, not restate them.
    """

    async def test_no_season_at_all_offers_nothing(self, service):
        """Empty rather than an error — a season-less server draws a fabricated league."""
        assert await service.get_previewable_divisions(SERVER_ID) == []

    async def test_it_returns_the_active_seasons_divisions(self, service, db_path):
        season_id = await _seed(db_path, "ACTIVE", 3)
        await _seed_division(db_path, season_id, "Premier", 1)
        await _seed_division(db_path, season_id, "Academy", 2)

        divisions = await service.get_previewable_divisions(SERVER_ID)

        assert [d.name for d in divisions] == ["Premier", "Academy"]

    async def test_the_one_live_season_is_the_one_whose_divisions_are_offered(
        self, service, db_path
    ):
        """An archived season's divisions are never offered beside the live one's.

        This replaced a test that seeded an ACTIVE season beside a SETUP one to prove
        which won. A server can no longer hold both, so what remains worth pinning is
        that the archive does not leak into the completion.
        """
        done = await _seed(db_path, "COMPLETED", 3)
        live = await _seed(db_path, "SETUP", 4)
        await _seed_division(db_path, done, "Last Season Division", 1)
        await _seed_division(db_path, live, "This Season Division", 1)

        divisions = await service.get_previewable_divisions(SERVER_ID)

        assert [d.name for d in divisions] == ["This Season Division"]

    async def test_a_season_pending_approval_is_used_when_none_is_approved(
        self, service, db_path
    ):
        pending = await _seed(db_path, "SETUP", 1)
        await _seed_division(db_path, pending, "Pending Division", 1)

        divisions = await service.get_previewable_divisions(SERVER_ID)

        assert [d.name for d in divisions] == ["Pending Division"]

    async def test_a_completed_season_is_not_previewable(self, service, db_path):
        done = await _seed(db_path, "COMPLETED", 2)
        await _seed_division(db_path, done, "Last Season", 1)

        assert await service.get_previewable_divisions(SERVER_ID) == []

    async def test_another_servers_divisions_are_not_offered(self, service, db_path):
        theirs = await _seed(db_path, "ACTIVE", 1, server_id=OTHER_SERVER)
        await _seed_division(db_path, theirs, "Their Division", 1)

        assert await service.get_previewable_divisions(SERVER_ID) == []

    async def test_it_matches_the_pair_it_replaced(self, service, db_path):
        """The guard against the combined query drifting from the two it stands in for."""
        season_id = await _seed(db_path, "ACTIVE", 3)
        await _seed_division(db_path, season_id, "Premier", 1)
        await _seed_division(db_path, season_id, "Academy", 2)

        season = await service.get_previewable_season(SERVER_ID)
        separately = await service.get_divisions(season.id)
        combined = await service.get_previewable_divisions(SERVER_ID)

        assert [d.name for d in combined] == [d.name for d in separately]
        assert [d.id for d in combined] == [d.id for d in separately]
        assert [d.tier for d in combined] == [d.tier for d in separately]

    async def test_a_shorter_lock_wait_can_be_asked_for(self, service, db_path):
        """The autocomplete path passes one; it must be accepted and still work."""
        season_id = await _seed(db_path, "ACTIVE", 3)
        await _seed_division(db_path, season_id, "Premier", 1)

        divisions = await service.get_previewable_divisions(SERVER_ID, timeout=1.0)

        assert [d.name for d in divisions] == ["Premier"]
