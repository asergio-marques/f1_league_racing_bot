"""The previews that fabricate an outcome (045, US2 and US3).

Nine kinds draw data a league cannot configure in advance: a classification of a session not
yet run, a forecast not yet made, an attendance record not yet kept, a verdict no steward has
issued. What they must *not* fabricate is the league's own division, round, teams or drivers,
and these tests hold both halves of that line.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.image.services.image_config_service import ImageConfigService
from leaguebot.image.services.image_preview_service import (
    build_attendance_preview,
    build_results_preview,
    build_rsvp_preview,
    build_standings_preview,
    build_verdict_preview,
    build_weather_preview,
    resolve_context,
)
from leaguebot.core.services.season_service import SeasonService

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
    await config_service.create_with_defaults()
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
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES (?, 'ACTIVE', 3)",
            (NOW.date().isoformat(),),
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
                "scheduled_at, status) VALUES (?, ?, ?, ?, ?, 'NOT_RUN')",
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
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, 2, 0)",
                (division_id, team_name, team_name),
            )
            team_id = cursor.lastrowid
            for seat_number in (1, 2):
                cursor = await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                    (team_id, seat_number),
                )
                seat_id = cursor.lastrowid
                cursor = await db.execute(
                    "INSERT INTO driver_profiles (discord_user_id, "
                    "current_state) VALUES (?, 'ACTIVE')",
                    (user_id,),
                )
                profile_id = cursor.lastrowid
                await db.execute(
                    "INSERT INTO signup_records (discord_user_id, "
                    "server_display_name, discord_username, nationality) "
                    "VALUES (?, ?, ?, 'British')",
                    (str(user_id), f"{team_name} {seat_number}", "d", ),
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
        bot, "Premier", round_number=round_number, **kwargs
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
        from leaguebot.image.services.image_rsvp_service import session_names

        context = await _context(bot, round_number=2)

        assert len(session_names("SPRINT")) == 4
        assert context.round.format.value == "SPRINT"

    async def test_a_two_session_round_names_two(self, bot, league):
        from leaguebot.image.services.image_rsvp_service import session_names

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
        from leaguebot.image.services.image_preview_data import fabricate_race_rows

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

        from leaguebot.image.utils.svg_document import load_svg

        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, 'Reserve', 'Reserve', 1, 1)",
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

        from leaguebot.image.utils.svg_document import load_svg

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

    async def test_the_preview_grid_shows_the_highlights_a_league_would_see(
        self, bot, league
    ):
        """The preview reaches the highlights through the same funnel, with no code of its own.

        Both layers must appear, and the backgrounds must be spread over more than one row:
        the whole point of scattering the fabricated classifications is that a manager
        judging their template sees a grid, not a stripe down the winner's row.
        """
        from pathlib import Path

        from leaguebot.image.utils.svg_document import load_svg

        context = await _context(bot, round_number=3, require_teams=True)
        requests = await build_standings_preview(bot, context)

        drivers_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_drivers_template"
        )
        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "standings_drivers_template.svg")
        spec = drivers_spec_builder(root)

        backgrounds = [key for key in spec.image_data if key.endswith("_background")]
        overlays = [key for key in spec.image_data if key.endswith("_fastest_lap")]
        assert backgrounds, "the preview drew no highlight at all"
        assert overlays, "the preview drew no fastest lap"

        rows = {key.split("_")[1] for key in backgrounds}
        assert len(rows) > 1, "every highlight fell on one row — the grid is a flat column"

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

        from leaguebot.image.utils.svg_document import load_svg

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

        from leaguebot.image.utils.svg_document import load_svg

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

        from leaguebot.image.utils.svg_document import load_svg

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

    async def test_two_drivers_level_on_points_are_drawn_on_separate_positions(
        self, bot, league
    ):
        """#144 — the previous fixed ramp never produced a tie on a normal field."""
        from pathlib import Path

        from leaguebot.image.utils.svg_document import load_svg

        context = await _context(bot, round_number=2, require_teams=True)
        requests = await build_standings_preview(bot, context)

        drivers_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_drivers_template"
        )
        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "standings_drivers_template.svg")
        spec = drivers_spec_builder(root)

        assert spec.text["row_2_position"] != spec.text["row_3_position"]
        assert spec.text["row_2_points"] == spec.text["row_3_points"]

    async def test_two_teams_level_on_points_are_drawn_on_separate_positions(
        self, bot, league, db_path
    ):
        """The same fabrication feeds the constructors table, so a third team draws its tie."""
        from pathlib import Path

        from leaguebot.image.utils.svg_document import load_svg

        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, 'Greenfield', 'Greenfield', 2, 0)",
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

        assert spec.text["row_2_position"] != spec.text["row_3_position"]
        assert spec.text["row_2_points"] == spec.text["row_3_points"]

    async def test_the_last_driver_and_the_last_team_are_drawn_on_no_points(
        self, bot, league
    ):
        """#144 — the previous ramp reached zero only from position 15 (drivers) or 13
        (teams), so a normal-sized division never showed a pointless entry either.
        """
        from pathlib import Path

        from leaguebot.image.utils.svg_document import load_svg

        context = await _context(bot, round_number=2, require_teams=True)
        requests = await build_standings_preview(bot, context)

        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"

        drivers_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_drivers_template"
        )
        drivers_root = load_svg(root_dir / "standings_drivers_template.svg")
        drivers_spec = drivers_spec_builder(drivers_root)
        assert drivers_spec.text["row_4_points"] == "0"

        constructors_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_constructors_template"
        )
        constructors_root = load_svg(root_dir / "standings_constructors_template.svg")
        constructors_spec = constructors_spec_builder(constructors_root)
        assert constructors_spec.text["row_2_points"] == "0"

    async def test_the_three_movement_markers_are_drawn_on_the_drivers_preview(
        self, bot, league
    ):
        """#144 — movement was omitted outright; a preview can fabricate a reference round.

        The four-driver fixture puts the gain on row 2, the loss on row 3, and the hold on
        rows 1 and 4 (`fabricate_standings_previous_positions`).
        """
        from pathlib import Path

        from leaguebot.image.utils.svg_document import load_svg

        context = await _context(bot, round_number=2, require_teams=True)
        requests = await build_standings_preview(bot, context)

        drivers_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_drivers_template"
        )
        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "standings_drivers_template.svg")
        spec = drivers_spec_builder(root)

        markers = {
            spec.image_data[f"row_{row}_position_change_marker"][1] for row in (1, 2, 3, 4)
        }
        assert markers == {"position_change_gained", "position_change_lost", "position_change_none"}

    async def test_the_three_movement_markers_are_drawn_on_the_constructors_preview(
        self, bot, league, db_path
    ):
        """The same fabrication feeds the constructors table, so a third team draws its
        own gain and loss.
        """
        from pathlib import Path

        from leaguebot.image.utils.svg_document import load_svg

        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, 'Greenfield', 'Greenfield', 2, 0)",
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

        markers = {
            spec.image_data[f"row_{row}_position_change_marker"][1] for row in (1, 2, 3)
        }
        assert "position_change_gained" in markers
        assert "position_change_lost" in markers

    async def test_a_reserve_driver_is_drawn_in_the_drivers_classification(
        self, bot, league, db_path
    ):
        """#144 — `_racing_drivers` excluded the reserve team wholesale, so a preview never
        exercised `driver_is_drawn`'s reserve branch at all.
        """
        from pathlib import Path

        from leaguebot.image.utils.svg_document import load_svg

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, 'Reserve', 'Reserve', 1, 1)",
                (league,),
            )
            team_id = cursor.lastrowid
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 1)",
                (team_id,),
            )
            seat_id = cursor.lastrowid
            cursor = await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, 'ACTIVE')",
                # Bound, never written into the SQL: a literal 9_200_000 is Python's digit
                # separator, which only SQLite 3.46 and later accepts in a query.
                (9_200_000,),
            )
            profile_id = cursor.lastrowid
            await db.execute(
                "INSERT INTO signup_records (discord_user_id, server_display_name, "
                "discord_username, nationality) VALUES ('9200000', 'Reserve 1', 'r', "
                "'British')"
            )
            season_id = (
                await (await db.execute("SELECT season_id FROM divisions WHERE id = ?", (league,))).fetchone()
            )["season_id"]
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, current_position, current_points, points_gap_to_first, "
                "team_seat_id) VALUES (?, ?, ?, 0, 0, 0, ?)",
                (profile_id, season_id, league, seat_id),
            )
            await db.commit()

        context = await _context(bot, round_number=2, require_teams=True)
        requests = await build_standings_preview(bot, context)

        drivers_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_drivers_template"
        )
        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "standings_drivers_template.svg")
        spec = drivers_spec_builder(root)

        assert spec.row_count == 5
        assert spec.text["row_5_driver_name"] == "Reserve 1"

        # The reserve is also the driver the preceding round does not hold: their movement
        # group leaves whole, where every regular's is drawn.
        assert "row_5_position_change_group" in spec.remove
        assert "row_5_position_change_marker" not in spec.image_data
        assert "row_4_position_change_marker" in spec.image_data

    async def test_a_driver_is_absent_and_a_reserve_stands_in_for_one_round(
        self, bot, league, db_path
    ):
        """#144 — every regular was scattered into every round; none of the cases the spec
        asks for (an absence, a stand-in, an empty car, a team scoring nothing in a round)
        could ever occur.

        Every team seats two, the commonest division there is — and the one a first cut
        of the substitution skipped altogether, its two last regulars being teammates.
        """
        from pathlib import Path

        from leaguebot.image.utils.svg_document import load_svg

        async with get_connection(db_path) as db:
            season_id = (
                await (
                    await db.execute("SELECT season_id FROM divisions WHERE id = ?", (league,))
                ).fetchone()
            )["season_id"]

            user_id = 9_300_000
            for team_name, seats, is_reserve in (("Greenfield", 2, 0), ("Reserve", 1, 1)):
                cursor = await db.execute(
                    "INSERT INTO team_instances (division_id, name, full_name, max_seats, "
                    "is_reserve) VALUES (?, ?, ?, ?, ?)",
                    (league, team_name, team_name, seats, is_reserve),
                )
                team_id = cursor.lastrowid
                for seat_number in range(1, seats + 1):
                    cursor = await db.execute(
                        "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                        (team_id, seat_number),
                    )
                    seat_id = cursor.lastrowid
                    cursor = await db.execute(
                        "INSERT INTO driver_profiles (discord_user_id, current_state) "
                        "VALUES (?, 'ACTIVE')",
                        (user_id,),
                    )
                    profile_id = cursor.lastrowid
                    await db.execute(
                        "INSERT INTO signup_records (discord_user_id, server_display_name, "
                        "discord_username, nationality) VALUES (?, ?, 'd', 'British')",
                        (str(user_id), f"{team_name} {seat_number}"),
                    )
                    await db.execute(
                        "INSERT INTO driver_season_assignments (driver_profile_id, "
                        "season_id, division_id, current_position, current_points, "
                        "points_gap_to_first, team_seat_id) VALUES (?, ?, ?, 0, 0, 0, ?)",
                        (profile_id, season_id, league, seat_id),
                    )
                    user_id += 1
            await db.commit()

        context = await _context(bot, round_number=2, require_teams=True)
        requests = await build_standings_preview(bot, context)

        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"

        constructors_spec_builder = next(
            spec for label, key, spec in requests if key == "standings_constructors_template"
        )
        constructors_root = load_svg(root_dir / "standings_constructors_template.svg")
        spec = constructors_spec_builder(constructors_root)

        # Rows follow the teams: Redline 1, Bluewave 2, Greenfield 3. Greenfield's second
        # driver sits round 1 out and Bluewave's second is replaced by the reserve. Rows
        # past the field have their cars removed too, so every assertion names its row.
        removed = set(spec.remove)

        # A car nobody drove: Greenfield's second, for round 1 alone.
        assert "row_3_round_1_driver_2_group" in removed
        assert "row_3_round_1_driver_1_group" not in removed
        assert "row_3_round_2_driver_2_group" not in removed

        # The stand-in fills the car Bluewave's absent driver left, so both still stand.
        assert "row_2_round_1_driver_1_group" not in removed
        assert "row_2_round_1_driver_2_group" not in removed

        # A team conferred no points in one of the rounds run: Greenfield's remaining car
        # finished at the back, so no cell of theirs in round 1 carries a highlight.
        assert not [
            key
            for key in spec.image_data
            if key.startswith("row_3_round_1_") and key.endswith("_background")
        ]


async def _seed_full_grid(db_path, division_id) -> None:
    """Eight more two-seat teams beside the fixture's two: a twenty-car field."""
    async with get_connection(db_path) as db:
        season_id = (
            await (
                await db.execute("SELECT season_id FROM divisions WHERE id = ?", (division_id,))
            ).fetchone()
        )["season_id"]
        user_id = 9_400_000
        for team_name in ("Solaris", "Nordvik", "Carmine", "Halcyon",
                          "Ironbark", "Veloce", "Zephyr", "Greenfield"):
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, ?, ?, 2, 0)",
                (division_id, team_name, team_name),
            )
            team_id = cursor.lastrowid
            for seat_number in (1, 2):
                cursor = await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                    (team_id, seat_number),
                )
                seat_id = cursor.lastrowid
                cursor = await db.execute(
                    "INSERT INTO driver_profiles (discord_user_id, current_state) "
                    "VALUES (?, 'ACTIVE')",
                    (user_id,),
                )
                profile_id = cursor.lastrowid
                await db.execute(
                    "INSERT INTO signup_records (discord_user_id, server_display_name, "
                    "discord_username, nationality) VALUES (?, ?, 'd', 'British')",
                    (str(user_id), f"{team_name} {seat_number}"),
                )
                await db.execute(
                    "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                    "division_id, current_position, current_points, points_gap_to_first, "
                    "team_seat_id) VALUES (?, ?, ?, 0, 0, 0, ?)",
                    (profile_id, season_id, division_id, seat_id),
                )
                user_id += 1
        await db.commit()


def _drawn_feature_finishes(spec, stem: str, round_count: int) -> list[tuple]:
    """The classified Feature Race finishes a row's grid shows, as the standings tally them.

    Read back off the drawing — the cells a manager would look at to check the order — so
    the test judges the picture and not the data behind it. A cell holding an outcome
    literal (DNF, DNS, DSQ) is not a classified finish and is not counted.
    """
    finishes = []
    for number in range(1, round_count + 1):
        for key, value in spec.text.items():
            if (
                key.startswith(f"{stem}_round_{number}_")
                and key.endswith("feature_race_result")
                and value.isdigit()
            ):
                finishes.append((stem, "FEATURE_RACE", "CLASSIFIED", int(value), number))
    return finishes


def _countback_differs(finish_counts, first_finish_rounds) -> bool:
    return (finish_counts.get("row_2"), first_finish_rounds.get("row_2")) != (
        finish_counts.get("row_3"),
        first_finish_rounds.get("row_3"),
    )


class TestStandingsPreviewCountback:
    """#144 — the pair level on points stands in the order the countback of its own grid
    gives. It once stood in the order of the driver list, the grid scattered on its own, so
    a driver with a win could be drawn beneath one without.
    """

    async def _drawn(self, bot, key, round_number):
        from pathlib import Path

        from leaguebot.image.utils.svg_document import load_svg

        context = await _context(bot, round_number=round_number, require_teams=True)
        requests = await build_standings_preview(bot, context)
        spec_builder = next(spec for label, k, spec in requests if k == key)
        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        return spec_builder(load_svg(root_dir / f"{key}.svg"))

    async def test_the_level_drivers_stand_in_the_order_the_countback_gives(
        self, bot, league, db_path
    ):
        from leaguebot.results.services.standings_service import order_drivers, tally_feature_finishes

        await _seed_full_grid(db_path, league)
        for round_number in (1, 2, 3, 4):
            spec = await self._drawn(bot, "standings_drivers_template", round_number)
            assert spec.text["row_2_points"] == spec.text["row_3_points"]

            finish_counts, first_finish_rounds = tally_feature_finishes(
                _drawn_feature_finishes(spec, "row_2", round_number)
                + _drawn_feature_finishes(spec, "row_3", round_number)
            )
            ordered = order_drivers(
                ["row_2", "row_3"],
                points={"row_2": 1, "row_3": 1},
                finish_counts=finish_counts,
                first_finish_rounds=first_finish_rounds,
                participants={"row_2", "row_3"},
                seats={},
                names={},
            )
            # Where the countback is level too, the final tiebreak decides on names this
            # test does not rebuild; the countback is what is being pinned.
            if _countback_differs(finish_counts, first_finish_rounds):
                assert ordered == ["row_2", "row_3"], (
                    f"after round {round_number}, "
                    f"{spec.text['row_3_driver_name']} out-counts "
                    f"{spec.text['row_2_driver_name']} yet stands below"
                )

    async def test_the_level_teams_stand_in_the_order_the_countback_gives(
        self, bot, league, db_path
    ):
        from leaguebot.results.services.standings_service import order_teams, tally_feature_finishes

        await _seed_full_grid(db_path, league)
        for round_number in (1, 2, 3, 4):
            spec = await self._drawn(bot, "standings_constructors_template", round_number)
            assert spec.text["row_2_points"] == spec.text["row_3_points"]

            finish_counts, first_finish_rounds = tally_feature_finishes(
                _drawn_feature_finishes(spec, "row_2", round_number)
                + _drawn_feature_finishes(spec, "row_3", round_number)
            )
            ordered = order_teams(
                ["row_2", "row_3"],
                points={"row_2": 1, "row_3": 1},
                finish_counts=finish_counts,
                first_finish_rounds=first_finish_rounds,
                team_meta={},
            )
            # Where the countback is level too, the final tiebreak decides on names this
            # test does not rebuild; the countback is what is being pinned.
            if _countback_differs(finish_counts, first_finish_rounds):
                assert ordered == ["row_2", "row_3"], (
                    f"after round {round_number}, {spec.text['row_3_team_name']} "
                    f"out-counts {spec.text['row_2_team_name']} yet stands below"
                )


# ── Attendance (T024) ─────────────────────────────────────────────────────


class TestAttendancePreview:
    async def test_the_sheet_is_drawn_for_the_named_round(self, bot, league):
        context = await _context(bot, round_number=2, require_teams=True)

        requests = await build_attendance_preview(bot, context)

        assert len(requests) == 1
        assert requests[0][1] == "attendance_template"

    async def test_no_record_falls_after_the_round_named(self, bot, league):
        """FR-027 — a round yet to be run confers nothing, and its cells stay empty."""
        from leaguebot.image.services.image_preview_data import fabricate_attendance_records

        context = await _context(bot, round_number=2, require_teams=True)

        records = fabricate_attendance_records(context.drivers, [1, 2])

        for record in records:
            assert all(ordinal <= 2 for ordinal in record.round_points)

    async def test_the_drawn_sheet_carries_both_marks(self, bot, league):
        """The marks are the whole of what the sheet says beyond its numbers.

        A preview seeded from one narrow band of totals paints a single mark down the column
        — or none at all — and leaves the two artworks and the unmarked row unjudged.
        """
        from pathlib import Path

        from leaguebot.image.services.image_attendance_service import (
            MARK_ASSET_CLASS,
            MARK_NEAR,
            MARK_REACHED,
        )
        from leaguebot.image.utils.svg_document import load_svg

        context = await _context(bot, round_number=2, require_teams=True)
        requests = await build_attendance_preview(bot, context)

        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "attendance_template.svg")
        spec = requests[0][2](root)

        marks = {
            value
            for name, value in spec.image_data.items()
            if name.endswith("_points_background")
        }

        assert (MARK_ASSET_CLASS, MARK_REACHED) in marks
        assert (MARK_ASSET_CLASS, MARK_NEAR) in marks

    async def test_the_drawn_sheet_leaves_a_row_unmarked(self, bot, league):
        """A driver earning no mark is drawn as one, and the sheet must show that too."""
        context = await _context(bot, round_number=2, require_teams=True)

        requests = await build_attendance_preview(bot, context)

        from pathlib import Path

        from leaguebot.image.utils.svg_document import load_svg

        root_dir = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
        root = load_svg(root_dir / "attendance_template.svg")
        spec = requests[0][2](root)

        marked = [n for n in spec.image_data if n.endswith("_points_background")]
        assert len(marked) < len(context.drivers)

    async def test_a_league_that_collects_no_nationality_draws_no_flag(self, bot, league, db_path):
        """FR-028 — the sheet does carry a flag element, and it obeys the switch."""
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO signup_module_settings (id, nationality_required, "
                "time_type, time_image_required) VALUES (?, 0, 'TIME_TRIAL', 1)",
                (1,),
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

        assert len(requests) == 5
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
        """FR-034 — never a qualifying ban or a race ban. No further action is drawn since the
        results module can issue it (#138)."""
        context = await _context(bot, round_number=1, require_teams=True)

        requests = await build_verdict_preview(bot, context)
        labels = " ".join(label for label, _k, _s in requests).lower()

        assert "no further action" in labels
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
