"""The `/images test` previews under test mode (046).

Test mode is how a maintainer reaches every image kind without a real league behind it, so
the previews have to hold under it. Two things are covered here.

That a **mock driver is drawn by its mock name** — which was already true of the naming
chain and is pinned as a rule rather than left to chance, because nothing else asserted it.
What actually blocked test mode was that a test season commonly sits in SETUP, and that is
covered by the season widening.

And the nationality of a mock driver. One created without a nationality records none and
is drawn without a flag, as a real posting would draw them, and the reply says so —
otherwise a maintainer reads the blank flags as a broken flag directory. One created with
a nationality carries it in place of the signup record it has not got, and is drawn with a
flag like anybody else.

Discord is stubbed. Test drivers are created through `test_roster_service`, the same code
`/test-mode roster add` calls, so the fixture cannot drift from what test mode really makes.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services import test_roster_service  # noqa: E402
from services.image_config_service import ImageConfigService  # noqa: E402
from services.image_preview_service import resolve_context  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 8484

#: Pinned alongside every seeded date.
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

MOCK_NAMES = ("Mock Alpha", "Mock Bravo", "Mock Charlie", "Mock Delta")


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "testmode.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


@pytest.fixture
async def bot(db_path):
    config_service = ImageConfigService(db_path)
    await config_service.create_with_defaults(SERVER_ID)
    return SimpleNamespace(
        db_path=db_path,
        season_service=SeasonService(db_path),
        image_config_service=config_service,
    )


async def _seed_test_season(
    db_path, *, status: str = "SETUP", nationalities: tuple[str, ...] = ()
):
    """A division of two teams, seated entirely with test-mode mock drivers.

    *nationalities* is cycled over the mock drivers where given, in the forms a maintainer
    may type; where it is empty every mock driver is created without one.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, ?, ?, 1)",
            (SERVER_ID, NOW.date().isoformat(), status),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, "
            "forecast_channel_id, status, tier) VALUES (?, 'Division 1', 1, NULL, "
            "'ACTIVE', 1)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        for number, fmt in ((1, "NORMAL"), (2, "SPRINT"), (3, "MYSTERY")):
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (?, ?, ?, ?, ?, 'ACTIVE')",
                (
                    division_id,
                    number,
                    fmt,
                    None if fmt == "MYSTERY" else "Albert Park Circuit",
                    (NOW + timedelta(days=7 * number)).isoformat(),
                ),
            )
        for team_name in ("Redline", "Bluewave"):
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, ?, 2, 0)",
                (division_id, team_name),
            )
            team_id = cursor.lastrowid
            for seat_number in (1, 2):
                await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number) "
                    "VALUES (?, ?)",
                    (team_id, seat_number),
                )
        await db.commit()

    # Seated through the same service `/test-mode roster add` calls, so the fixture cannot
    # drift from what test mode actually creates.
    for index, name in enumerate(MOCK_NAMES):
        team = "Redline" if index < 2 else "Bluewave"
        result = await test_roster_service.add_test_driver(
            SERVER_ID,
            name,
            team,
            "Division 1",
            db_path,
            nationality=nationalities[index % len(nationalities)] if nationalities else None,
        )
        assert not isinstance(result, str), result
    return SimpleNamespace(season_id=season_id, division_id=division_id)


# ── T032: mock drivers are drawn by their mock names ──────────────────────


class TestMockDriversAreDrawnByTheirMockName:
    """FR-026."""

    @pytest.mark.parametrize("status", ["SETUP", "ACTIVE"])
    async def test_every_mock_name_is_drawn(self, bot, db_path, status):
        await _seed_test_season(db_path, status=status)

        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="lineup")

        assert sorted(d.display_name for d in context.drivers) == sorted(MOCK_NAMES)

    async def test_the_seats_carry_the_mock_names_too(self, bot, db_path):
        """The lineup draws the seats, not the flat list, so both must be right."""
        await _seed_test_season(db_path)

        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="lineup")

        seated = [
            seat.server_display_name or seat.test_display_name
            for team in context.teams
            for seat in team.seats
        ]
        assert sorted(n for n in seated if n) == sorted(MOCK_NAMES)

    @pytest.mark.parametrize(
        "kind", ["results", "standings", "attendance", "verdict"]
    )
    async def test_the_outcome_kinds_draw_the_mock_names(self, bot, db_path, kind):
        await _seed_test_season(db_path)

        context = await resolve_context(
            bot, SERVER_ID, "Division 1", round_number=1, kind=kind
        )

        assert sorted(d.display_name for d in context.drivers) == sorted(MOCK_NAMES)

    async def test_a_mock_driver_is_never_invented_over(self, bot, db_path):
        """FR-027 — a mock driver is a seated driver, not an empty seat."""
        await _seed_test_season(db_path)

        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="lineup")

        assert context.fabricated_drivers is False
        assert all(not d.fabricated for d in context.drivers)


# ── T034: the nationality tally ───────────────────────────────────────────


class TestTheNationalityTally:
    """FR-028. A mock driver records the nationality it was created with, or none."""

    async def test_mock_drivers_are_counted(self, bot, db_path):
        await _seed_test_season(db_path)

        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="lineup")

        assert context.nationality_collected is True
        assert context.drivers_without_nationality == len(MOCK_NAMES)

    async def test_they_are_drawn_without_a_flag_rather_than_given_one(
        self, bot, db_path
    ):
        await _seed_test_season(db_path)

        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="lineup")

        assert all(d.nationality in (None, "") for d in context.drivers)

    async def test_nothing_is_counted_where_the_league_collects_no_nationality(
        self, bot, db_path
    ):
        await _seed_test_season(db_path)
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO signup_module_settings (server_id, nationality_required) "
                "VALUES (?, 0)",
                (SERVER_ID,),
            )
            await db.commit()

        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="lineup")

        assert context.nationality_collected is False
        assert context.drivers_without_nationality == 0

    async def test_a_driver_who_records_one_is_not_counted(self, bot, db_path):
        """The tally is of drivers drawn without a flag, not of test drivers."""
        await _seed_test_season(db_path)
        async with get_connection(db_path) as db:
            profile = await (
                await db.execute(
                    "SELECT id FROM driver_profiles "
                    "WHERE server_id = ? AND is_test_driver = 1 LIMIT 1",
                    (SERVER_ID,),
                )
            ).fetchone()
            await db.execute(
                "UPDATE driver_profiles SET test_nationality = 'British' WHERE id = ?",
                (profile["id"],),
            )
            await db.commit()

        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="lineup")

        assert context.drivers_without_nationality == len(MOCK_NAMES) - 1

    async def test_a_roster_created_with_nationalities_is_drawn_with_flags(
        self, bot, db_path
    ):
        """The whole point of the column: a mock driver has a flag of its own."""
        await _seed_test_season(db_path, nationalities=("british", "Dutch", "brazil"))

        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="lineup")

        assert context.drivers_without_nationality == 0
        # Stored canonically, whatever form the roster command was given.
        assert sorted(d.nationality for d in context.drivers) == [
            "Brazilian",
            "British",
            "British",
            "Dutch",
        ]

    async def test_the_test_mode_switch_stands_in_while_test_mode_is_on(
        self, bot, db_path
    ):
        """Switching it off draws no flag at all, and says nothing about it (XIV.4)."""
        await _seed_test_season(db_path, nationalities=("British",))
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE server_configs SET test_mode_active = 1, "
                "test_mode_nationality_required = 0 WHERE server_id = ?",
                (SERVER_ID,),
            )
            await db.commit()

        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="lineup")

        assert context.nationality_collected is False
        assert context.drivers_without_nationality == 0
        assert all(d.nationality is None for d in context.drivers)


# ── T035: test mode changes what exists, never how it is read ─────────────


class TestTestModeChangesNoReading:
    """FR-029."""

    @pytest.mark.parametrize("flag", [0, 1])
    async def test_a_season_less_server_fabricates_the_same_either_way(
        self, bot, db_path, flag
    ):
        import random

        from services.image_preview_league import build_fabricated_context

        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE server_configs SET test_mode_active = ? WHERE server_id = ?",
                (flag, SERVER_ID),
            )
            await db.execute(
                "INSERT INTO default_teams (server_id, name, max_seats, is_reserve) "
                "VALUES (?, 'Redline', 2, 0)",
                (SERVER_ID,),
            )
            await db.commit()

        context = await build_fabricated_context(
            bot, SERVER_ID, kind="lineup", rng=random.Random(11), now=NOW
        )

        assert context.fabricated_league is True
        assert [t.name for t in context.teams] == ["Redline"]

    async def test_the_preview_reads_no_test_mode_flag_at_all(self):
        """The strongest form of FR-029: the flag is not consulted anywhere."""
        import inspect

        from services import image_preview_league, image_preview_service

        for module in (image_preview_service, image_preview_league):
            assert "test_mode_active" not in inspect.getsource(module)
