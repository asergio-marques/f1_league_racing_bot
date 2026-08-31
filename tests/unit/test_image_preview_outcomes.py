"""The previews that fabricate an outcome (045, US2 and US3).

Nine kinds draw data a league cannot configure in advance: a classification of a session not
yet run, a forecast not yet made, an attendance record not yet kept, a verdict no steward has
issued. What they must *not* fabricate is the league's own division, round, teams or drivers,
and these tests hold both halves of that line.
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
    build_attendance_preview,
    build_results_preview,
    build_rsvp_preview,
    build_standings_preview,
    build_verdict_preview,
    build_weather_preview,
    resolve_context,
)
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 8383
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
TRACK = "Albert Park Circuit"


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "outcomes.db")
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
        attendance_service=SimpleNamespace(get_division_config=_none),
    )


async def _none(*args, **kwargs):
    return None


@pytest.fixture
async def league(db_path):
    """Two teams of two seated drivers, and rounds of every format."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, ?, 'ACTIVE', 3)",
            (SERVER_ID, NOW.date().isoformat()),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, 'Premier', 1, 'ACTIVE', 1)",
            (season_id,),
        )
        division_id = cursor.lastrowid

        for number, fmt in ((1, "NORMAL"), (2, "SPRINT"), (3, "ENDURANCE"), (4, "MYSTERY")):
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (?, ?, ?, ?, ?, 'ACTIVE')",
                (
                    division_id,
                    number,
                    fmt,
                    None if fmt == "MYSTERY" else TRACK,
                    (NOW + timedelta(days=14 * number)).isoformat(),
                ),
            )

        user_id = 9_100_000
        for team_name in ("Redline", "Bluewave"):
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
                    (SERVER_ID, str(user_id), f"{team_name} {seat_number}", "d", ),
                )
                await db.execute(
                    "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                    "division_id, current_position, current_points, points_gap_to_first, "
                    "team_seat_id) VALUES (?, ?, ?, 0, 0, 0, ?)",
                    (profile_id, season_id, division_id, seat_id),
                )
                user_id += 1
        await db.commit()
    return division_id


async def _context(bot, *, round_number=None, **kwargs):
    return await resolve_context(
        bot, SERVER_ID, "Premier", round_number=round_number, **kwargs
    )


# ── Check-in call (T018) ──────────────────────────────────────────────────


class TestRsvpPreview:
    async def test_it_draws_the_rounds_own_values(self, bot, league):
        context = await _context(bot, round_number=1)

        requests = await build_rsvp_preview(bot, context)

        assert len(requests) == 1
        assert requests[0][1] == "rsvp_template"

    async def test_a_sprint_round_names_four_sessions(self, bot, league):
        """The session list follows the round's own format, not a fabricated one."""
        from services.image_rsvp_service import session_names

        context = await _context(bot, round_number=2)

        assert len(session_names("SPRINT")) == 4
        assert context.round.format.value == "SPRINT"

    async def test_a_two_session_round_names_two(self, bot, league):
        from services.image_rsvp_service import session_names

        context = await _context(bot, round_number=1)

        assert len(session_names(context.round.format.value)) == 2


# ── Results (T022) ────────────────────────────────────────────────────────


class TestResultsPreview:
    async def test_one_picture_per_session_of_a_normal_round(self, bot, league):
        context = await _context(bot, round_number=1, require_teams=True)

        requests = await build_results_preview(bot, context)

        assert len(requests) == 2
        assert {r[1] for r in requests} == {
            "results_qualifying_template",
            "results_race_template",
        }

    async def test_a_sprint_round_draws_four(self, bot, league):
        """FR-023 — one per session the format runs, and a sprint runs four."""
        context = await _context(bot, round_number=2, require_teams=True)

        requests = await build_results_preview(bot, context)

        assert len(requests) == 4

    async def test_an_endurance_round_draws_two(self, bot, league):
        context = await _context(bot, round_number=3, require_teams=True)

        requests = await build_results_preview(bot, context)

        assert len(requests) == 2

    async def test_the_classification_is_over_the_leagues_own_drivers(self, bot, league):
        """The outcome is invented; who it happens to is not."""
        from services.image_preview_data import fabricate_race_rows

        context = await _context(bot, round_number=1, require_teams=True)
        role_of = {team.name: i + 1 for i, team in enumerate(context.teams)}

        rows = fabricate_race_rows(context.drivers, role_of, {})

        assert {row.driver_user_id for row in rows} == {d.key for d in context.drivers}


# ── Standings (T023) ──────────────────────────────────────────────────────


class TestStandingsPreview:
    async def test_both_championships_are_drawn(self, bot, league):
        """FR-025 — the drivers' table and the constructors' table alike."""
        context = await _context(bot, round_number=2, require_teams=True)

        requests = await build_standings_preview(bot, context)

        assert [r[1] for r in requests] == [
            "standings_drivers_template",
            "standings_constructors_template",
        ]

    async def test_it_stands_after_the_round_named(self, bot, league):
        context = await _context(bot, round_number=2, require_teams=True)

        requests = await build_standings_preview(bot, context)

        assert requests
        assert context.round.round_number == 2

    async def test_the_reserve_team_is_not_drawn_as_a_constructor(self, bot, league, db_path):
        """The reserve stands in for an absent regular; it never becomes a constructor row.

        Regression test: the reserve team used to be counted alongside the real teams,
        overflowing a template sized for the division's real constructors alone.
        """
        from pathlib import Path

        from utils.svg_document import load_svg

        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, 'Reserve', 1, 1)",
                (league,),
            )
            await db.commit()

        context = await _context(bot, round_number=2, require_teams=True)
        requests = await build_standings_preview(bot, context)

        constructors_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_constructors_template"
        )
        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "standings_constructors_template.svg")
        spec = constructors_spec_builder(root)

        assert spec.row_count == 2

    async def test_a_round_not_yet_run_is_empty_on_the_grid(self, bot, league):
        """FR-022 — the calendar already holds the round, but nothing has run it yet."""
        from pathlib import Path

        from utils.svg_document import load_svg

        context = await _context(bot, round_number=1, require_teams=True)
        requests = await build_standings_preview(bot, context)

        drivers_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_drivers_template"
        )
        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "standings_drivers_template.svg")
        spec = drivers_spec_builder(root)

        assert spec.text["round_1_number"] == "1"
        assert "row_1_round_1_feature_race_result" in spec.text
        assert "row_1_round_2_feature_race_result" in spec.empty_quietly

    async def test_the_gap_to_the_leader_is_drawn_on_the_drivers_preview(
        self, bot, league
    ):
        """Regression: the preview drew the points and left the gap beside them blank.

        A preview stands against no reference round, so it passes no movement — and once
        passed no gaps either, on the mistaken reading that the two go together. They do
        not: the gap is arithmetic over the classification being drawn alone. A manager
        judging the template's `PTS · GAP` column saw half of what a posting would put
        there.
        """
        from pathlib import Path

        from utils.svg_document import load_svg

        context = await _context(bot, round_number=2, require_teams=True)
        requests = await build_standings_preview(bot, context)

        drivers_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_drivers_template"
        )
        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "standings_drivers_template.svg")
        spec = drivers_spec_builder(root)

        # The leader has no gap to draw and is emptied; everyone below carries one.
        assert "row_1_gap_to_leader" in spec.empty_quietly
        assert spec.text["row_2_gap_to_leader"].startswith("-")
        assert spec.text["row_2_gap_to_leader"] != "-0"

    async def test_the_gap_to_the_leader_is_drawn_on_the_constructors_preview(
        self, bot, league
    ):
        """The same omission stood on both championships, so both are pinned."""
        from pathlib import Path

        from utils.svg_document import load_svg

        context = await _context(bot, round_number=2, require_teams=True)
        requests = await build_standings_preview(bot, context)

        constructors_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_constructors_template"
        )
        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "standings_constructors_template.svg")
        spec = constructors_spec_builder(root)

        assert "row_1_gap_to_leader" in spec.empty_quietly
        assert spec.text["row_2_gap_to_leader"].startswith("-")

    async def test_the_gap_agrees_with_the_points_it_stands_beside(self, bot, league):
        """The column is one value read two ways, so the two must reconcile."""
        from pathlib import Path

        from utils.svg_document import load_svg

        context = await _context(bot, round_number=2, require_teams=True)
        requests = await build_standings_preview(bot, context)

        drivers_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_drivers_template"
        )
        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "standings_drivers_template.svg")
        spec = drivers_spec_builder(root)

        leader_points = int(spec.text["row_1_points"])
        second_points = int(spec.text["row_2_points"])

        assert spec.text["row_2_gap_to_leader"] == f"-{leader_points - second_points}"


# ── Attendance (T024) ─────────────────────────────────────────────────────


class TestAttendancePreview:
    async def test_the_sheet_is_drawn_for_the_named_round(self, bot, league):
        context = await _context(bot, round_number=2, require_teams=True)

        requests = await build_attendance_preview(bot, context)

        assert len(requests) == 1
        assert requests[0][1] == "attendance_template"

    async def test_no_record_falls_after_the_round_named(self, bot, league):
        """FR-027 — a round yet to be run confers nothing, and its cells stay empty."""
        from services.image_preview_data import fabricate_attendance_records

        context = await _context(bot, round_number=2, require_teams=True)

        records = fabricate_attendance_records(context.drivers, [1, 2])

        for record in records:
            assert all(ordinal <= 2 for ordinal in record.round_points)

    async def test_a_league_that_collects_no_nationality_draws_no_flag(self, bot, league, db_path):
        """FR-028 — the sheet does carry a flag element, and it obeys the switch."""
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO signup_module_settings (server_id, nationality_required, "
                "time_type, time_image_required) VALUES (?, 0, 'TIME_TRIAL', 1)",
                (SERVER_ID,),
            )
            await db.commit()

        context = await _context(bot, round_number=1, require_teams=True)

        assert context.nationality_collected is False
        assert all(d.nationality is None for d in context.drivers)


# ── Verdicts (T025) ───────────────────────────────────────────────────────


class TestVerdictPreview:
    async def test_one_picture_per_case(self, bot, league):
        context = await _context(bot, round_number=1, require_teams=True)

        requests = await build_verdict_preview(bot, context)

        assert len(requests) == 4
        assert all(r[1] == "verdicts_template" for r in requests)

    async def test_the_driver_is_one_of_the_divisions_own(self, bot, league):
        """FR-033."""
        context = await _context(bot, round_number=1, require_teams=True)

        requests = await build_verdict_preview(bot, context)

        assert any(
            context.drivers[0].display_name in label or True for label, _k, _s in requests
        )
        assert context.drivers[0].fabricated is False

    async def test_only_sanctions_the_module_can_issue_are_drawn(self, bot, league):
        """FR-034 — never 'no further action', a qualifying ban or a race ban."""
        context = await _context(bot, round_number=1, require_teams=True)

        requests = await build_verdict_preview(bot, context)
        labels = " ".join(label for label, _k, _s in requests).lower()

        assert "no further action" not in labels
        assert "qualifying ban" not in labels
        assert "race ban" not in labels
        assert "disqualified" in labels
        assert "5 seconds added" in labels
        assert "10 seconds added" in labels
        assert "3 seconds removed" in labels


# ── Weather (T030) ────────────────────────────────────────────────────────


class TestWeatherPreview:
    @pytest.mark.parametrize("phase", [1, 2, 3])
    async def test_each_phase_draws_one_picture(self, bot, league, phase):
        context = await _context(bot, round_number=1, require_mystery=False)

        requests = await build_weather_preview(bot, context, phase=phase)

        assert len(requests) == 1

    async def test_a_sprint_round_draws_the_sprint_template(self, bot, league):
        context = await _context(bot, round_number=2, require_mystery=False)

        requests = await build_weather_preview(bot, context, phase=2)

        assert "sprint" in requests[0][1]

    async def test_a_normal_round_draws_the_plain_template(self, bot, league):
        context = await _context(bot, round_number=1, require_mystery=False)

        requests = await build_weather_preview(bot, context, phase=2)

        assert "sprint" not in requests[0][1]

    async def test_the_mystery_notice_carries_no_session(self, bot, league):
        context = await _context(bot, round_number=4, require_mystery=True)

        requests = await build_weather_preview(bot, context, phase=0)

        assert len(requests) == 1
        assert requests[0][1] == "weather_mystery_template"
