"""The lineup preview draws the league's own teams and drivers (045, US1).

The lineup is the kind that most needed this change: a lineup template names its fields
after real teams, so a preview against invented teams could never tell a manager whether
their template was right. These tests assert the drawing carries the seeded roster.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.image_config_service import ImageConfigService  # noqa: E402
from services.image_preview_service import (  # noqa: E402
    build_lineup_preview,
    resolve_context,
)
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 7272
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "lineup.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
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


async def _seed(db_path, *, seat_drivers: bool, teams=("Redline", "Bluewave")):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, ?, 'ACTIVE', 2)",
            (SERVER_ID, NOW.date().isoformat()),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, 'Premier', 1, 'ACTIVE', 1)",
            (season_id,),
        )
        division_id = cursor.lastrowid

        user_id = 8_000_001
        for team_name in teams:
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, ?, 2, 0)",
                (division_id, team_name),
            )
            team_id = cursor.lastrowid
            for seat_number in (1, 2):
                cursor = await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                    (team_id, seat_number),
                )
                seat_id = cursor.lastrowid
                if not seat_drivers:
                    continue
                cursor = await db.execute(
                    "INSERT INTO driver_profiles (server_id, discord_user_id, "
                    "current_state) VALUES (?, ?, 'ACTIVE')",
                    (SERVER_ID, user_id),
                )
                profile_id = cursor.lastrowid
                await db.execute(
                    "INSERT INTO signup_records (server_id, discord_user_id, "
                    "server_display_name, discord_username, nationality) "
                    "VALUES (?, ?, ?, ?, 'British')",
                    (
                        SERVER_ID,
                        str(user_id),
                        f"{team_name} Driver {seat_number}",
                        f"d{user_id}",
                    ),
                )
                await db.execute(
                    "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                    "division_id, current_position, current_points, points_gap_to_first, "
                    "team_seat_id) VALUES (?, ?, ?, 0, 0, 0, ?)",
                    (profile_id, season_id, division_id, seat_id),
                )
                user_id += 1

        # Every division holds a reserve team; it must not be counted as a real team.
        await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, 'Reserve', 2, 1)",
            (division_id,),
        )
        await db.commit()
    return division_id


# ── A seated division ─────────────────────────────────────────────────────


class TestASeatedDivision:
    async def test_the_drawing_carries_the_leagues_own_team_names(self, bot, db_path):
        """SC-004 — the reason a lineup template can finally be checked at all."""
        await _seed(db_path, seat_drivers=True)
        context = await resolve_context(
            bot, SERVER_ID, "Premier", require_teams=True
        )

        requests = await build_lineup_preview(bot, context)

        assert len(requests) == 1
        assert requests[0][1] == "lineup_template"
        assert {t.name for t in context.teams} == {"Redline", "Bluewave", "Reserve"}

    async def test_the_drawing_carries_the_leagues_own_driver_names(self, bot, db_path):
        await _seed(db_path, seat_drivers=True)
        context = await resolve_context(
            bot, SERVER_ID, "Premier", require_teams=True
        )

        names = {d.display_name for d in context.drivers}

        assert names == {
            "Redline Driver 1",
            "Redline Driver 2",
            "Bluewave Driver 1",
            "Bluewave Driver 2",
        }
        assert context.fabricated_drivers is False

    async def test_the_resolved_drawing_names_the_division(self, bot, db_path):
        from services.image_lineup_service import resolve_drawing

        await _seed(db_path, seat_drivers=True)
        context = await resolve_context(
            bot, SERVER_ID, "Premier", require_teams=True
        )

        drawing = resolve_drawing(
            division_name=context.division_name,
            division_tier=context.division_tier,
            season_number=context.season_number,
            teams=context.teams,
            display_names=context.display_names,
            nationality_collected=context.nationality_collected,
        )

        assert drawing.division_name == "Premier"
        assert {team.display_name for team in drawing.teams} >= {"Redline", "Bluewave"}


# ── An unseated division ──────────────────────────────────────────────────


class TestAnUnseatedDivision:
    async def test_every_seat_is_filled_with_an_invented_driver(self, bot, db_path):
        """FR-018 — a league that has configured teams but seated nobody still gets a picture."""
        await _seed(db_path, seat_drivers=False)
        context = await resolve_context(
            bot, SERVER_ID, "Premier", require_teams=True
        )

        assert context.fabricated_drivers is True
        # Two teams of two seats. The reserve team is seeded with no seat, and a seat is
        # what a driver is fabricated for.
        assert len(context.drivers) == 4
        assert all(d.fabricated for d in context.drivers)

    async def test_the_invented_drivers_reach_the_seats_themselves(self, bot, db_path):
        """The lineup draws seats, not the flat list; an unoccupied seat would draw empty."""
        await _seed(db_path, seat_drivers=False)
        context = await resolve_context(
            bot, SERVER_ID, "Premier", require_teams=True
        )

        for team in context.teams:
            for seat in team.seats:
                assert seat.discord_user_id is not None
                assert seat.server_display_name

    async def test_the_drawing_shows_them_as_occupied_seats(self, bot, db_path):
        from services.image_lineup_service import resolve_drawing

        await _seed(db_path, seat_drivers=False)
        context = await resolve_context(
            bot, SERVER_ID, "Premier", require_teams=True
        )

        drawing = resolve_drawing(
            division_name=context.division_name,
            division_tier=context.division_tier,
            season_number=context.season_number,
            teams=context.teams,
            display_names=context.display_names,
            nationality_collected=context.nationality_collected,
        )

        occupied = [
            seat
            for team in drawing.teams
            for seat in team.seats
            if getattr(seat, "occupied", False)
        ]
        assert occupied, "an unseated division drew no occupied seat"
        assert all(seat.driver_name for seat in occupied)

    async def test_the_teams_drawn_are_still_the_leagues_own(self, bot, db_path):
        """Only the drivers are invented. The teams are never fabricated."""
        await _seed(db_path, seat_drivers=False, teams=("Vermilion", "Cobalt"))
        context = await resolve_context(
            bot, SERVER_ID, "Premier", require_teams=True
        )

        assert {t.name for t in context.teams} == {"Vermilion", "Cobalt", "Reserve"}
        assert {d.team_name for d in context.drivers} == {"Vermilion", "Cobalt"}


# ── The league's own artwork ──────────────────────────────────────────────


class TestItUsesTheLeaguesOwnDirectories:
    async def test_the_spec_is_built_against_the_configured_directories(
        self, bot, db_path
    ):
        """FR-035, and the faults ride along so the reply can name them (FR-038)."""
        import services.image_lineup_service as lineup_service

        await _seed(db_path, seat_drivers=True)
        await bot.image_config_service.set_field(
            SERVER_ID, "flag_directory", "resources/teams"
        )
        context = await resolve_context(
            bot, SERVER_ID, "Premier", require_teams=True
        )

        seen = {}
        original = lineup_service.build_fill_spec

        def _capture(drawing, root, *, asset_directories=None):
            seen["directories"] = asset_directories
            return SimpleNamespace(asset_directory_faults={})

        lineup_service.build_fill_spec = _capture
        try:
            requests = await build_lineup_preview(bot, context)
            spec = requests[0][2](object())
        finally:
            lineup_service.build_fill_spec = original

        assert seen["directories"]["flag"].name == "teams"
        assert spec.asset_directory_faults == {}
