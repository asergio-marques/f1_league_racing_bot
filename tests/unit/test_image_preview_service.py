"""Resolution and refusal shared by every `/images test` preview (045).

The point of the feature is that a preview draws the league's *own* data. These tests seed
a real season, division, rounds, teams and drivers, and assert that what comes back is what
was seeded — and that every refusal fires on its own condition, in the contracted order,
before any render is attempted.
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
from services.image_config_service import ImageConfigService  # noqa: E402
from services.image_preview_service import (  # noqa: E402
    REASON_MYSTERY_ROUND,
    REASON_NO_DIVISION,
    REASON_NO_ROUND,
    REASON_NO_ROUNDS,
    REASON_NO_SEASON,
    REASON_NO_TEAMS,
    REASON_NOT_MYSTERY_ROUND,
    DirectoryFault,
    PreviewContext,
    PreviewDriver,
    PreviewRefused,
    build_attendance_preview,
    build_calendar_preview,
    resolve_asset_directories,
    resolve_context,
)
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 4242

#: Pinned, so a seeded future round stays in the future for as long as this suite lives.
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "preview.db")
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
    """A stand-in carrying only what the preview service reaches for.

    No Discord anywhere: the service takes a bot-shaped object and touches three services
    and the database, which is the whole of its dependency surface.
    """
    config_service = ImageConfigService(db_path)
    await config_service.create_with_defaults(SERVER_ID)
    return SimpleNamespace(
        db_path=db_path,
        season_service=SeasonService(db_path),
        image_config_service=config_service,
    )


async def _seed_season(db_path, *, season_number: int = 1, status: str = "ACTIVE") -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, ?, ?, ?)",
            (SERVER_ID, NOW.date().isoformat(), status, season_number),
        )
        await db.commit()
        return cursor.lastrowid


async def _seed_division(db_path, season_id: int, name: str, *, tier: int = 1) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id, "
            "status, tier) VALUES (?, ?, ?, ?, 'ACTIVE', ?)",
            (season_id, name, 1, None, tier),
        )
        await db.commit()
        return cursor.lastrowid


async def _seed_round(
    db_path, division_id: int, number: int, *, fmt: str = "NORMAL", track: str = "Monza"
) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, track_name, "
            "scheduled_at, status) VALUES (?, ?, ?, ?, ?, 'ACTIVE')",
            (
                division_id,
                number,
                fmt,
                None if fmt == "MYSTERY" else track,
                (NOW + timedelta(days=7 * number)).isoformat(),
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def _seed_team(
    db_path, division_id: int, name: str, *, seats: int = 2, reserve: bool = False
) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, ?, ?, ?)",
            (division_id, name, seats, int(reserve)),
        )
        team_id = cursor.lastrowid
        for seat_number in range(1, seats + 1):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                (team_id, seat_number),
            )
        await db.commit()
        return team_id


async def _seat_driver(
    db_path,
    season_id: int,
    division_id: int,
    team_id: int,
    seat_number: int,
    *,
    name: str,
    nationality: str | None = "British",
) -> int:
    user_id = 700_000 + seat_number + team_id * 10
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) "
            "VALUES (?, ?, 'ACTIVE')",
            (SERVER_ID, user_id),
        )
        profile_id = cursor.lastrowid
        # signup_records is keyed by (server_id, discord_user_id) — it holds no profile id.
        await db.execute(
            "INSERT INTO signup_records (server_id, discord_user_id, server_display_name, "
            "discord_username, nationality) VALUES (?, ?, ?, ?, ?)",
            (SERVER_ID, str(user_id), name, name.lower(), nationality),
        )
        seat = await (
            await db.execute(
                "SELECT id FROM team_seats WHERE team_instance_id = ? AND seat_number = ?",
                (team_id, seat_number),
            )
        ).fetchone()
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
            "division_id, current_position, current_points, points_gap_to_first, "
            "team_seat_id) VALUES (?, ?, ?, 0, 0, 0, ?)",
            (profile_id, season_id, division_id, seat["id"]),
        )
        await db.commit()
        return profile_id


@pytest.fixture
async def league(db_path):
    """One ordinary league: a division of two teams, four seated drivers, three rounds."""
    season_id = await _seed_season(db_path)
    division_id = await _seed_division(db_path, season_id, "Division 1")
    await _seed_division(db_path, season_id, "Division 2", tier=2)
    for number, fmt in ((1, "NORMAL"), (2, "SPRINT"), (3, "MYSTERY")):
        await _seed_round(db_path, division_id, number, fmt=fmt)
    red = await _seed_team(db_path, division_id, "Redline")
    blue = await _seed_team(db_path, division_id, "Bluewave")
    await _seed_team(db_path, division_id, "Reserve", seats=2, reserve=True)
    await _seat_driver(db_path, season_id, division_id, red, 1, name="Alice Ardent")
    await _seat_driver(db_path, season_id, division_id, red, 2, name="Bruno Bellini")
    await _seat_driver(db_path, season_id, division_id, blue, 1, name="Carla Costa")
    await _seat_driver(db_path, season_id, division_id, blue, 2, name="Dieter Damm")
    return SimpleNamespace(season_id=season_id, division_id=division_id)


# ── T003: the shapes ──────────────────────────────────────────────────────


class TestShapes:
    def test_a_preview_driver_is_not_fabricated_by_default(self):
        driver = PreviewDriver(
            key=1, display_name="Ada", team_name="Redline", seat_number=1
        )
        assert driver.fabricated is False
        assert driver.nationality is None

    def test_a_context_starts_with_empty_collections_and_collects_nationality(self):
        context = PreviewContext(
            server_id=1,
            season_number=1,
            division_id=1,
            division_name="D",
            division_tier=1,
        )
        assert context.teams == []
        assert context.drivers == []
        assert context.directory_faults == []
        assert context.round is None
        assert context.nationality_collected is True
        assert context.fabricated_drivers is False

    def test_two_contexts_do_not_share_their_collections(self):
        """A mutable default shared across contexts would leak one preview into the next."""
        a = PreviewContext(
            server_id=1, season_number=1, division_id=1, division_name="A", division_tier=1
        )
        b = PreviewContext(
            server_id=1, season_number=1, division_id=2, division_name="B", division_tier=1
        )
        a.drivers.append(
            PreviewDriver(key=1, display_name="Ada", team_name="T", seat_number=1)
        )
        assert b.drivers == []


# ── T004/T005: season, division and round resolution ──────────────────────


class TestResolution:
    async def test_the_named_division_resolves_with_its_own_identity(self, bot, league):
        context = await resolve_context(bot, SERVER_ID, "Division 1")

        assert context.division_id == league.division_id
        assert context.division_name == "Division 1"
        assert context.division_tier == 1
        assert context.season_number == 1

    async def test_the_name_is_matched_without_regard_to_case_or_padding(self, bot, league):
        context = await resolve_context(bot, SERVER_ID, "  division 1  ")
        assert context.division_id == league.division_id

    async def test_a_round_resolves_by_its_number(self, bot, league):
        context = await resolve_context(bot, SERVER_ID, "Division 1", round_number=2)

        assert context.round is not None
        assert context.round.round_number == 2

    async def test_no_round_is_resolved_where_none_is_asked_for(self, bot, league):
        context = await resolve_context(bot, SERVER_ID, "Division 1")
        assert context.round is None


# ── T006/T007: teams, drivers and nationality ─────────────────────────────


class TestTeamsAndDrivers:
    async def test_the_divisions_own_teams_and_drivers_are_drawn(self, bot, league):
        context = await resolve_context(bot, SERVER_ID, "Division 1")

        assert {t.name for t in context.teams} == {"Redline", "Bluewave", "Reserve"}
        assert {d.display_name for d in context.drivers} == {
            "Alice Ardent",
            "Bruno Bellini",
            "Carla Costa",
            "Dieter Damm",
        }
        assert context.fabricated_drivers is False
        assert all(d.fabricated is False for d in context.drivers)

    async def test_a_partly_seated_division_leaves_its_empty_seats_empty(
        self, bot, db_path
    ):
        """FR-020 — drawn as it stands, because that is what its posting would look like."""
        season_id = await _seed_season(db_path)
        division_id = await _seed_division(db_path, season_id, "Half")
        team = await _seed_team(db_path, division_id, "Redline", seats=2)
        await _seat_driver(db_path, season_id, division_id, team, 1, name="Only Driver")

        context = await resolve_context(bot, SERVER_ID, "Half")

        assert context.fabricated_drivers is False
        assert [d.display_name for d in context.drivers] == ["Only Driver"]

    async def test_a_wholly_unseated_division_fabricates_every_seat(self, bot, db_path):
        """FR-018 — an empty grid would tell a manager nothing about their template."""
        season_id = await _seed_season(db_path)
        division_id = await _seed_division(db_path, season_id, "Empty")
        await _seed_team(db_path, division_id, "Redline", seats=2)
        await _seed_team(db_path, division_id, "Bluewave", seats=2)

        context = await resolve_context(bot, SERVER_ID, "Empty")

        assert context.fabricated_drivers is True
        assert len(context.drivers) == 4
        assert all(d.fabricated for d in context.drivers)
        assert len({d.display_name for d in context.drivers}) == 4

    async def test_a_fabricated_driver_carries_a_nationality_where_the_league_collects(
        self, bot, db_path
    ):
        season_id = await _seed_season(db_path)
        division_id = await _seed_division(db_path, season_id, "Empty")
        await _seed_team(db_path, division_id, "Redline", seats=2)

        context = await resolve_context(bot, SERVER_ID, "Empty")

        assert context.nationality_collected is True
        assert all(d.nationality for d in context.drivers)

    async def test_no_nationality_is_given_where_the_league_does_not_collect_one(
        self, bot, db_path
    ):
        """FR-019 — a league that switched collection off configured a graphic with no flags."""
        season_id = await _seed_season(db_path)
        division_id = await _seed_division(db_path, season_id, "Empty")
        await _seed_team(db_path, division_id, "Redline", seats=2)
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO signup_module_settings (server_id, nationality_required, "
                "time_type, time_image_required) VALUES (?, 0, 'TIME_TRIAL', 1)",
                (SERVER_ID,),
            )
            await db.commit()

        context = await resolve_context(bot, SERVER_ID, "Empty")

        assert context.nationality_collected is False
        assert all(d.nationality is None for d in context.drivers)

    async def test_a_seated_drivers_own_nationality_is_kept(self, bot, league):
        context = await resolve_context(bot, SERVER_ID, "Division 1")
        assert all(d.nationality == "British" for d in context.drivers)


# ── T008: the nine refusals, in their contracted order ────────────────────


class TestRefusals:
    async def test_no_season_is_no_longer_refused_but_fabricated(self, bot, db_path):
        """046 withdraws the refusal 045 raised here.

        A server with no season draws an invented league instead. The calendar draws no
        team, so it is drawn even on a server that has configured none.
        """
        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="calendar")

        assert context.fabricated_league is True

    async def test_an_archived_season_is_not_drawn_from(self, bot, db_path):
        """A-001 — the preview checks what the league is about to run.

        Such a server holds no previewable season, so it falls to the fabricated league
        rather than drawing the archived one. The assertion that matters is that the
        archived division's name is *not* what comes back.
        """
        season_id = await _seed_season(db_path, status="ARCHIVED")
        await _seed_division(db_path, season_id, "Old Division")

        context = await resolve_context(bot, SERVER_ID, "Old Division", kind="calendar")

        assert context.fabricated_league is True
        assert context.division_name != "Old Division"

    async def test_an_unknown_division_is_refused_and_the_known_ones_named(
        self, bot, league
    ):
        with pytest.raises(PreviewRefused) as caught:
            await resolve_context(bot, SERVER_ID, "Nonexistent")
        assert caught.value.reason == REASON_NO_DIVISION
        assert "Division 1" in caught.value.message

    async def test_a_division_with_no_round_is_refused_for_the_calendar(self, bot, db_path):
        season_id = await _seed_season(db_path)
        await _seed_division(db_path, season_id, "Bare")

        with pytest.raises(PreviewRefused) as caught:
            await resolve_context(bot, SERVER_ID, "Bare", require_rounds=True)
        assert caught.value.reason == REASON_NO_ROUNDS

    async def test_an_unknown_round_number_is_refused(self, bot, league):
        with pytest.raises(PreviewRefused) as caught:
            await resolve_context(bot, SERVER_ID, "Division 1", round_number=99)
        assert caught.value.reason == REASON_NO_ROUND

    async def test_a_division_with_only_a_reserve_team_is_refused(self, bot, db_path):
        season_id = await _seed_season(db_path)
        division_id = await _seed_division(db_path, season_id, "Reserves Only")
        await _seed_round(db_path, division_id, 1)
        await _seed_team(db_path, division_id, "Reserve", reserve=True)

        with pytest.raises(PreviewRefused) as caught:
            await resolve_context(
                bot, SERVER_ID, "Reserves Only", round_number=1, require_teams=True
            )
        assert caught.value.reason == REASON_NO_TEAMS

    async def test_a_mystery_round_is_refused_a_forecast(self, bot, league):
        with pytest.raises(PreviewRefused) as caught:
            await resolve_context(
                bot, SERVER_ID, "Division 1", round_number=3, require_mystery=False
            )
        assert caught.value.reason == REASON_MYSTERY_ROUND

    async def test_a_plain_round_is_refused_the_mystery_notice(self, bot, league):
        with pytest.raises(PreviewRefused) as caught:
            await resolve_context(
                bot, SERVER_ID, "Division 1", round_number=1, require_mystery=True
            )
        assert caught.value.reason == REASON_NOT_MYSTERY_ROUND

    async def test_a_mystery_round_passes_the_mystery_notice(self, bot, league):
        context = await resolve_context(
            bot, SERVER_ID, "Division 1", round_number=3, require_mystery=True
        )
        assert context.round.round_number == 3

    # ── Ordering (FR-014) ─────────────────────────────────────────────────

    async def test_a_wrong_round_on_a_teamless_division_reports_the_round(
        self, bot, db_path
    ):
        """The round is checked before the team list, so the first fault is the one named."""
        season_id = await _seed_season(db_path)
        division_id = await _seed_division(db_path, season_id, "Teamless")
        await _seed_round(db_path, division_id, 1)

        with pytest.raises(PreviewRefused) as caught:
            await resolve_context(
                bot, SERVER_ID, "Teamless", round_number=99, require_teams=True
            )
        assert caught.value.reason == REASON_NO_ROUND

    async def test_a_mistyped_division_with_a_wrong_round_reports_the_division(
        self, bot, league
    ):
        with pytest.raises(PreviewRefused) as caught:
            await resolve_context(bot, SERVER_ID, "Nope", round_number=99)
        assert caught.value.reason == REASON_NO_DIVISION


# ── T009: nothing renders before a refusal ────────────────────────────────


class TestNothingRendersBeforeARefusal:
    async def test_no_render_is_attempted_on_a_refusal(self, bot, league):
        """FR-015 — a fault of configuration is never reported as a failure to render."""

        class ExplodingRenderService:
            async def render(self, *args, **kwargs):  # pragma: no cover - must not run
                raise AssertionError("a render was attempted before the refusal")

        bot.image_render_service = ExplodingRenderService()

        with pytest.raises(PreviewRefused):
            await resolve_context(bot, SERVER_ID, "Nonexistent")


# ── T010: the league's own asset directories ──────────────────────────────


class TestAssetDirectories:
    async def test_the_leagues_configured_directories_are_resolved(self, bot):
        """FR-035 — never the packaged directories the withdrawn command hardcoded."""
        directories, faults = await resolve_asset_directories(bot, SERVER_ID)

        assert set(directories) >= {"flag", "track", "team", "weather"}
        assert faults == []
        assert directories["flag"].name == "flags"

    async def test_a_league_pointing_a_class_elsewhere_is_followed(self, bot):
        """The configured value is read, not a hardcoded default.

        The withdrawn command resolved `resources/defaults/flags` whatever the league had set. The
        directory is pointed at another real folder inside the project root — a league
        cannot point outside it, which the containment test below covers.
        """
        await bot.image_config_service.set_field(
            SERVER_ID, "flag_directory", "resources/defaults/teams"
        )

        directories, faults = await resolve_asset_directories(bot, SERVER_ID)

        assert directories["flag"].name == "teams"
        assert not [f for f in faults if f.asset_class == "flag"]

    async def test_a_path_escaping_the_project_root_is_reported_with_its_reason(self, bot):
        """FR-038 — not silently omitted and then called an unconfigured class."""
        await bot.image_config_service.set_field(
            SERVER_ID, "flag_directory", "../../elsewhere"
        )

        directories, faults = await resolve_asset_directories(bot, SERVER_ID)

        assert "flag" not in directories
        fault = next(f for f in faults if f.asset_class == "flag")
        assert fault.configured_value == "../../elsewhere"
        assert fault.reason

    async def test_a_directory_that_does_not_exist_is_reported_but_still_passed_on(
        self, bot
    ):
        await bot.image_config_service.set_field(
            SERVER_ID, "flag_directory", "resources/not_a_real_directory"
        )

        directories, faults = await resolve_asset_directories(bot, SERVER_ID)

        assert "flag" in directories
        fault = next(f for f in faults if f.asset_class == "flag")
        assert "does not exist" in fault.reason

    async def test_the_context_carries_the_directories_and_their_faults(self, bot, league):
        context = await resolve_context(bot, SERVER_ID, "Division 1")

        assert context.asset_directories
        assert isinstance(context.directory_faults, list)
        assert all(isinstance(f, DirectoryFault) for f in context.directory_faults)


# ── The context carries the calendar (046 T005/T006) ──────────────────────


class TestContextCarriesTheCalendar:
    """`context.rounds` is resolved once and is what the builders draw.

    Three builders used to re-query the calendar by ``division_id``. A fabricated league
    has no division row, so a re-query would have handed it an empty calendar rather than
    the one it invented — which is why the context has to be self-sufficient.
    """

    async def test_resolve_context_carries_the_divisions_rounds(self, bot, league):
        context = await resolve_context(bot, SERVER_ID, "Division 1")

        assert [r.round_number for r in context.rounds] == [1, 2, 3]

    async def test_the_calendar_draws_from_the_context_not_the_database(
        self, bot, league
    ):
        """Emptying `context.rounds` empties the calendar, though the division holds three.

        The calendar refuses an empty round list outright, so the refusal *is* the proof:
        a builder still querying by ``division_id`` would have found the three seeded
        rounds and drawn them happily.
        """
        from services.image_calendar_service import CalendarDataError

        context = await resolve_context(bot, SERVER_ID, "Division 1")
        assert len(context.rounds) == 3
        context.rounds = []

        with pytest.raises(CalendarDataError):
            await build_calendar_preview(bot, context)

    async def test_the_calendar_draws_exactly_the_rounds_the_context_holds(
        self, bot, league
    ):
        """And the positive case: two rounds on the context, two rounds drawn.

        The seeded track name is replaced with one the shipped `tracks` table carries,
        because the calendar resolves a round's country and grand prix name through it and
        refuses a name it cannot find.
        """
        context = await resolve_context(bot, SERVER_ID, "Division 1")
        context.rounds = [r for r in context.rounds if r.round_number in (1, 2)]
        for entry in context.rounds:
            entry.track_name = "Albert Park Circuit"

        requests = await build_calendar_preview(bot, context)

        assert len(requests) == 1
        assert requests[0][1] == "calendar_template"

    async def test_the_attendance_sheet_draws_from_the_context_not_the_database(
        self, bot, league
    ):
        context = await resolve_context(bot, SERVER_ID, "Division 1", round_number=1)
        seen = list(context.rounds)
        context.rounds = [r for r in seen if r.round_number == 1]

        requests = await build_attendance_preview(bot, context)

        assert requests
        # One heading per round of `context.rounds`, not per round of the division.
        assert len(context.rounds) == 1

    async def test_no_builder_re_queries_the_division_calendar(self):
        """The only `get_division_rounds` call left is the one in `resolve_context`."""
        import inspect

        from services import image_preview_service

        source = inspect.getsource(image_preview_service)
        assert source.count("get_division_rounds(") == 1


# ── Which season a preview draws (046 US1) ────────────────────────────────


async def _seed_league(db_path, *, status: str, season_number: int = 1, name="Division 1"):
    """The `league` fixture's shape, at a chosen season status."""
    season_id = await _seed_season(db_path, season_number=season_number, status=status)
    division_id = await _seed_division(db_path, season_id, name)
    for number, fmt in ((1, "NORMAL"), (2, "SPRINT"), (3, "MYSTERY")):
        await _seed_round(db_path, division_id, number, fmt=fmt)
    red = await _seed_team(db_path, division_id, "Redline")
    await _seed_team(db_path, division_id, "Reserve", seats=2, reserve=True)
    await _seat_driver(db_path, season_id, division_id, red, 1, name="Alice Ardent")
    await _seat_driver(db_path, season_id, division_id, red, 2, name="Bruno Bellini")
    return SimpleNamespace(season_id=season_id, division_id=division_id)


class TestWhichSeasonIsDrawn:
    """FR-001 to FR-005. A season pending approval is drawn exactly as an approved one."""

    async def test_a_season_pending_approval_is_drawn(self, bot, db_path):
        await _seed_league(db_path, status="SETUP", season_number=1)

        context = await resolve_context(bot, SERVER_ID, "Division 1")

        assert context.season_number == 1
        assert context.season_pending_approval is True
        assert [r.round_number for r in context.rounds] == [1, 2, 3]

    async def test_a_pending_season_carries_its_teams_and_seated_drivers(
        self, bot, db_path
    ):
        """FR-002 — no substitution and no fabrication on account of status alone."""
        await _seed_league(db_path, status="SETUP")

        context = await resolve_context(bot, SERVER_ID, "Division 1")

        assert sorted(d.display_name for d in context.drivers) == [
            "Alice Ardent",
            "Bruno Bellini",
        ]
        assert context.fabricated_drivers is False

    async def test_an_approved_season_is_not_flagged_as_pending(self, bot, league):
        context = await resolve_context(bot, SERVER_ID, "Division 1")

        assert context.season_pending_approval is False

    async def test_a_preview_never_faces_a_choice_of_two_live_seasons(self, bot, db_path):
        """The server cannot hold both, so the preview has one season to resolve against.

        This replaced a test asserting that an ACTIVE season outranked a later SETUP one.
        A server now holds at most one live season — migration 049 enforces it with a
        partial unique index — so the precedence it pinned arbitrates a state that cannot
        occur, and what is worth pinning instead is that the state is refused.
        """
        await _seed_league(db_path, status="ACTIVE", season_number=4, name="Running")

        with pytest.raises(aiosqlite.IntegrityError):
            await _seed_league(
                db_path, status="SETUP", season_number=5, name="NextYear"
            )

        context = await resolve_context(bot, SERVER_ID, "Running")
        assert context.season_number == 4
        assert context.season_pending_approval is False

    @pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
    async def test_a_finished_season_is_not_drawn(self, bot, db_path, status):
        """FR-005 — a server holding only such seasons holds none for previewing, and
        therefore draws a fabricated league numbered on from the one that finished."""
        await _seed_league(db_path, status=status, season_number=2)

        context = await resolve_context(bot, SERVER_ID, "Division 1", kind="calendar")

        assert context.fabricated_league is True
        # And the number counts on from the season that finished.
        assert context.season_number == 3


class TestRefusalsStillFireOnAPendingSeason:
    """FR-006. None of feature 045's six refusals is weakened by the season widening."""

    async def test_an_unknown_division_is_refused(self, bot, db_path):
        await _seed_league(db_path, status="SETUP")

        with pytest.raises(PreviewRefused) as excinfo:
            await resolve_context(bot, SERVER_ID, "Nope")
        assert excinfo.value.reason == REASON_NO_DIVISION

    async def test_an_absent_round_is_refused(self, bot, db_path):
        await _seed_league(db_path, status="SETUP")

        with pytest.raises(PreviewRefused) as excinfo:
            await resolve_context(bot, SERVER_ID, "Division 1", round_number=99)
        assert excinfo.value.reason == REASON_NO_ROUND

    async def test_a_division_with_no_round_is_refused_for_the_calendar(
        self, bot, db_path
    ):
        season_id = await _seed_season(db_path, status="SETUP")
        await _seed_division(db_path, season_id, "Empty")

        with pytest.raises(PreviewRefused) as excinfo:
            await resolve_context(bot, SERVER_ID, "Empty", require_rounds=True)
        assert excinfo.value.reason == REASON_NO_ROUNDS

    async def test_a_division_with_only_the_reserve_team_is_refused(self, bot, db_path):
        season_id = await _seed_season(db_path, status="SETUP")
        division_id = await _seed_division(db_path, season_id, "Bare")
        await _seed_round(db_path, division_id, 1)
        await _seed_team(db_path, division_id, "Reserve", seats=2, reserve=True)

        with pytest.raises(PreviewRefused) as excinfo:
            await resolve_context(bot, SERVER_ID, "Bare", require_teams=True)
        assert excinfo.value.reason == REASON_NO_TEAMS

    async def test_a_forecast_asked_of_a_mystery_round_is_refused(self, bot, db_path):
        await _seed_league(db_path, status="SETUP")

        with pytest.raises(PreviewRefused) as excinfo:
            await resolve_context(
                bot, SERVER_ID, "Division 1", round_number=3, require_mystery=False
            )
        assert excinfo.value.reason == REASON_MYSTERY_ROUND

    async def test_a_mystery_notice_asked_of_an_ordinary_round_is_refused(
        self, bot, db_path
    ):
        await _seed_league(db_path, status="SETUP")

        with pytest.raises(PreviewRefused) as excinfo:
            await resolve_context(
                bot, SERVER_ID, "Division 1", round_number=1, require_mystery=True
            )
        assert excinfo.value.reason == REASON_NOT_MYSTERY_ROUND
