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

    async def test_an_approved_season_outranks_a_later_pending_one(self, service, db_path):
        """A-002. The season a league is running is the one its templates are judged by."""
        await _seed(db_path, "ACTIVE", 4)
        await _seed(db_path, "SETUP", 5)

        season = await service.get_previewable_season(SERVER_ID)

        assert season.season_number == 4
        assert season.status is SeasonStatus.ACTIVE

    async def test_the_precedence_holds_whichever_order_the_rows_were_written_in(
        self, service, db_path
    ):
        """The SETUP row is written *first* here, so a query relying on insertion order
        would return it. `get_setup_or_active_season` is exactly that query."""
        await _seed(db_path, "SETUP", 5)
        await _seed(db_path, "ACTIVE", 4)

        assert (await service.get_previewable_season(SERVER_ID)).status is SeasonStatus.ACTIVE

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
