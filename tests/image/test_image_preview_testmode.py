"""The `/images test` previews under test mode (046).

Test mode is how a maintainer reaches every image kind without a real league behind it, so
the previews have to hold under it. Two things are covered here.

That a **mock driver is drawn by its mock name** — which was already true of the naming
chain and is pinned as a rule rather than left to chance, because nothing else asserted it.
What actually blocked test mode was that a test season commonly sits in SETUP, and that is
covered by the season widening.

And the nationality of a mock driver, which is only ever the one its signup gave, as a real
driver's is (#600). One with no signup, or whose signup gave none, is drawn without a flag, as a
real posting would draw them, and the reply says so — otherwise a maintainer reads the blank flags
as a broken flag directory. One whose signup gave one is drawn with a flag like anybody else.

Discord is stubbed. Test drivers are created through `test_roster_service`, the same code
`/test-mode roster add` calls, with an ID and a name alone, so the fixture cannot drift from what
test mode really makes; they are then seated as `/driver assign` and the placements' commit leave
a driver, which is not test mode's to do.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

from leaguebot.framework.db.database import get_connection, run_migrations
from leaguebot.core.services.test_mode import test_roster_service
from leaguebot.image.services.config.image_config_service import ImageConfigService
from leaguebot.image.services.preview.image_preview_service import resolve_context as _resolve_context
from leaguebot.core.services.seasons.season_service import SeasonService
from tests.support.saves import create_image_defaults


async def resolve_context(bot, *args, **kwargs):
    """`image_preview_service.resolve_context`, handed the hook signup answers how a driver is
    named, as the command hands it from the bot, so that the names and flags a preview draws are
    signup's."""
    from tests.support.review_workflows import sign_up_driver_names_read

    holder = SimpleNamespace(db_path=bot.db_path)
    sign_up_driver_names_read(holder)
    kwargs.setdefault("driver_names_read", holder.driver_names_read)
    return await _resolve_context(bot, *args, **kwargs)

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
    await create_image_defaults(config_service)
    return SimpleNamespace(
        db_path=db_path,
        season_service=SeasonService(db_path),
        image_config_service=config_service,
    )


async def _seed_test_season(
    db_path, *, status: str = "SETUP", nationalities: tuple[str, ...] = ()
):
    """A division of two teams, seated entirely with test-mode mock drivers.

    *nationalities* is cycled over the mock drivers where given, each given as the nationality an
    approved signup of theirs to the season gave; where it is empty no mock driver has a signup.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES (?, ?, 1)",
            (NOW.date().isoformat(), status),
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
                "scheduled_at, status) VALUES (?, ?, ?, ?, ?, 'NOT_RUN')",
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
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, 2, 0)",
                (division_id, team_name, team_name),
            )
            team_id = cursor.lastrowid
            for seat_number in (1, 2):
                await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number) "
                    "VALUES (?, ?)",
                    (team_id, seat_number),
                )
        await db.commit()

    # Created through the same service `/test-mode roster add` calls, so the fixture cannot
    # drift from what test mode actually creates, then seated as a placement seats a driver.
    for index, name in enumerate(MOCK_NAMES):
        team = "Redline" if index < 2 else "Bluewave"
        async with get_connection(db_path) as db:
            result = await test_roster_service.add_test_driver_on(db, name)
            assert not isinstance(result, str), result
            await _seat(db, result["profile_id"], team, season_id, division_id)
            if nationalities:
                await _signed_up(
                    db, result["discord_user_id"], season_id,
                    nationalities[index % len(nationalities)],
                )
            await db.commit()
    return SimpleNamespace(season_id=season_id, division_id=division_id)


async def _seat(db, profile_id: int, team_name: str, season_id: int, division_id: int) -> None:
    """Seat fake driver *profile_id* in the first free seat of *team_name*, committed, on *db*:
    the seat holds them, their assignment to the division names it, and they are Assigned, as
    `/driver assign` and the placements' commit leave a driver."""
    seat = await (
        await db.execute(
            "SELECT ts.id FROM team_seats ts "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE ti.division_id = ? AND ti.name = ? AND ts.driver_profile_id IS NULL "
            "ORDER BY ts.seat_number LIMIT 1",
            (division_id, team_name),
        )
    ).fetchone()
    await db.execute(
        "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?", (profile_id, seat["id"])
    )
    await db.execute(
        "INSERT INTO driver_season_assignments "
        "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
        "VALUES (?, ?, ?, ?, 1)",
        (profile_id, season_id, division_id, seat["id"]),
    )
    await db.execute(
        "UPDATE driver_profiles SET current_state = 'ASSIGNED' WHERE id = ?", (profile_id,)
    )


async def _signed_up(db, discord_user_id: int, season_id: int, nationality: str | None) -> None:
    """An approved signup to *season_id* by the driver of *discord_user_id*, giving
    *nationality*, on *db*."""
    await db.execute(
        "INSERT INTO signup_records (season_id, discord_user_id, nationality, created_at, "
        "approved) VALUES (?, ?, ?, ?, 1)",
        (season_id, str(discord_user_id), nationality, NOW.isoformat()),
    )


#: What is not yet true of every test here: a fake driver is created with an ID and a name alone.
_NAME_ONLY = "#600: a fake driver is still created seated, from a team and a division"
#: And, of those reading a fake driver's nationality, that it is its signup's.
_SIGNUP_NATIONALITY = (
    "#600: a fake driver is still created seated, and its nationality is still its own "
    "rather than its signup's"
)


# ── T032: mock drivers are drawn by their mock names ──────────────────────


class TestMockDriversAreDrawnByTheirMockName:
    """FR-026."""

    @pytest.mark.xfail(strict=True, reason=_NAME_ONLY)
    @pytest.mark.parametrize("status", ["SETUP", "ACTIVE"])
    async def test_every_mock_name_is_drawn(self, bot, db_path, status):
        await _seed_test_season(db_path, status=status)

        context = await resolve_context(bot, "Division 1", kind="lineup")

        assert sorted(d.display_name for d in context.drivers) == sorted(MOCK_NAMES)

    @pytest.mark.xfail(strict=True, reason=_NAME_ONLY)
    async def test_the_seats_carry_the_mock_names_too(self, bot, db_path):
        """The lineup draws the seats, not the flat list, so both must be right."""
        await _seed_test_season(db_path)

        context = await resolve_context(bot, "Division 1", kind="lineup")

        seated = [
            seat.server_display_name or seat.test_display_name
            for team in context.teams
            for seat in team.seats
        ]
        assert sorted(n for n in seated if n) == sorted(MOCK_NAMES)

    @pytest.mark.xfail(strict=True, reason=_NAME_ONLY)
    @pytest.mark.parametrize(
        "kind", ["results", "standings", "attendance", "verdict"]
    )
    async def test_the_outcome_kinds_draw_the_mock_names(self, bot, db_path, kind):
        await _seed_test_season(db_path)

        context = await resolve_context(
            bot, "Division 1", round_number=1, kind=kind
        )

        assert sorted(d.display_name for d in context.drivers) == sorted(MOCK_NAMES)

    @pytest.mark.xfail(strict=True, reason=_NAME_ONLY)
    async def test_a_mock_driver_is_never_invented_over(self, bot, db_path):
        """FR-027 — a mock driver is a seated driver, not an empty seat."""
        await _seed_test_season(db_path)

        context = await resolve_context(bot, "Division 1", kind="lineup")

        assert context.fabricated_drivers is False
        assert all(not d.fabricated for d in context.drivers)


# ── T034: the nationality tally ───────────────────────────────────────────


class TestTheNationalityTally:
    """FR-028. A mock driver is drawn under the nationality its signup gave, or none."""

    @pytest.mark.xfail(strict=True, reason=_NAME_ONLY)
    async def test_mock_drivers_are_counted(self, bot, db_path):
        await _seed_test_season(db_path)

        context = await resolve_context(bot, "Division 1", kind="lineup")

        assert context.nationality_collected is True
        assert context.drivers_without_nationality == len(MOCK_NAMES)

    @pytest.mark.xfail(strict=True, reason=_NAME_ONLY)
    async def test_they_are_drawn_without_a_flag_rather_than_given_one(
        self, bot, db_path
    ):
        await _seed_test_season(db_path)

        context = await resolve_context(bot, "Division 1", kind="lineup")

        assert all(d.nationality in (None, "") for d in context.drivers)

    @pytest.mark.xfail(strict=True, reason=_NAME_ONLY)
    async def test_nothing_is_counted_where_the_league_collects_no_nationality(
        self, bot, db_path
    ):
        await _seed_test_season(db_path)
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO signup_module_settings (id, nationality_required) "
                "VALUES (?, 0)",
                (1,),
            )
            await db.commit()

        context = await resolve_context(bot, "Division 1", kind="lineup")

        assert context.nationality_collected is False
        assert context.drivers_without_nationality == 0

    @pytest.mark.xfail(strict=True, reason=_SIGNUP_NATIONALITY)
    async def test_a_driver_who_records_one_is_not_counted(self, bot, db_path):
        """The tally is of drivers drawn without a flag, not of test drivers: one mock driver's
        signup gave British, and only the other three are counted."""
        seeded = await _seed_test_season(db_path)
        async with get_connection(db_path) as db:
            profile = await (
                await db.execute(
                    "SELECT discord_user_id FROM driver_profiles "
                    "WHERE is_test_driver = 1 ORDER BY id LIMIT 1",
                )
            ).fetchone()
            await _signed_up(db, profile["discord_user_id"], seeded.season_id, "British")
            await db.commit()

        context = await resolve_context(bot, "Division 1", kind="lineup")

        assert context.drivers_without_nationality == len(MOCK_NAMES) - 1

    @pytest.mark.xfail(strict=True, reason=_SIGNUP_NATIONALITY)
    async def test_a_roster_created_with_nationalities_is_drawn_with_flags(
        self, bot, db_path
    ):
        """A mock driver whose signup gave a nationality is drawn with its flag, as any driver
        is: four mock drivers, their signups giving British, Dutch, Brazilian and British."""
        await _seed_test_season(db_path, nationalities=("British", "Dutch", "Brazilian"))

        context = await resolve_context(bot, "Division 1", kind="lineup")

        assert context.drivers_without_nationality == 0
        assert sorted(d.nationality for d in context.drivers) == [
            "Brazilian",
            "British",
            "British",
            "Dutch",
        ]

    @pytest.mark.xfail(strict=True, reason=_SIGNUP_NATIONALITY)
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

        context = await resolve_context(bot, "Division 1", kind="lineup")

        assert context.nationality_collected is False
        assert context.drivers_without_nationality == 0
        assert all(d.nationality is None for d in context.drivers)


# ── T035: test mode changes what exists, never how it is read ─────────────


class TestTestModeChangesNoReading:
    """FR-029."""

    async def test_the_preview_reads_no_test_mode_flag_at_all(self):
        """The strongest form of FR-029: the flag is not consulted anywhere.

        A companion case asserting that a season-less server fabricated its league
        identically either way went with the fabrication itself, withdrawn 2026-09-06:
        a preview now names its division and round and draws the league's own data.
        """
        import inspect

        from leaguebot.image.services.preview import image_preview_service

        assert "test_mode_active" not in inspect.getsource(image_preview_service)
