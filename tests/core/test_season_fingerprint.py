"""The season a review described, and whether it is still that season at the button.

`/season placements-review` fingerprints everything it reports; the Approve button refuses unless the
season still fingerprints the same. That is what lets the approval trust the review's own
render instead of drawing every graphic a second time.

**Every area gets a named case.** An area quietly dropped from the digest is a change that
slips through with nothing failing, so each is changed in turn and asserted to be both
detected *and named*. That is the whole value of the per-area digest over one hash, and it
is the test that stops the coverage rotting.

The artwork on disk is the one area core cannot read itself: it asks the image module's drawing
what the league's artwork looks like now, and digests the answer. Here the drawing is a stand-in
whose answer each test sets; the answer itself is pinned against image's own drawing in
`tests/image/test_image_drawing_artwork.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.season_fingerprint_service import (
    AREA_LABELS,
    SeasonFingerprint,
    _digest,
    take_fingerprint,
)

SERVER_ID = 8811

NEEDS_DRAWING = (
    "#283: take_fingerprint is not yet handed the database and the image module's drawing"
)


class _ArtworkStandIn:
    """A stand-in for the image module's drawing, answering the one question the fingerprint asks
    of it: what the league's artwork looks like now. It answers what the test sets, nothing until
    then, and counts the times it is asked."""

    def __init__(self) -> None:
        self.answer: list = []
        self.asked = 0

    async def artwork_signature(self) -> list:
        self.asked += 1
        return self.answer


@pytest.fixture
async def season(tmp_path):
    """A configured server with one season, one division, a team, a seat and a round, and a
    stand-in for the image module's drawing."""
    path = str(tmp_path / "fingerprint.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-03-01', 'SETUP', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, 'Pro', 10, 'ACTIVE', 1)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, 'Redline', 'Redline', 2, 0)",
            (division_id,),
        )
        team_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 1)",
            (team_id,),
        )
        seat_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "former_driver, is_test_driver) VALUES ('123', 'ASSIGNED', 0, 0)"
        )
        profile_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
            "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
            (profile_id, season_id, division_id, seat_id),
        )
        await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, 1, 'NORMAL', 'Hungaroring', '2026-06-14T18:00:00')",
            (division_id,),
        )
        await db.execute("INSERT INTO image_config (id) VALUES (?)", (1,))
        await db.commit()

    return SimpleNamespace(
        path=path, season_id=season_id, division_id=division_id, drawing=_ArtworkStandIn()
    )


async def _take(season) -> SeasonFingerprint:
    return await take_fingerprint(season.path, season.season_id, drawing=season.drawing)


async def _change(season, sql: str, *params) -> None:
    async with get_connection(season.path) as db:
        await db.execute(sql, params)
        await db.commit()


# ── The fingerprint itself ────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_every_area_is_covered(season):
    """A missing area is a change that would slip through unnoticed."""
    fingerprint = await _take(season)

    assert set(fingerprint.areas) == set(AREA_LABELS)


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_an_unchanged_season_fingerprints_the_same_twice(season):
    """Taken twice with nothing between, it must not report a change nobody made."""
    first = await _take(season)
    second = await _take(season)

    assert first.differs_from(second) == []


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_a_fingerprint_that_could_not_be_taken_differs_from_everything(season):
    """A fault refuses an approval rather than waving it through."""
    empty = SeasonFingerprint({})

    assert empty.differs_from(await _take(season))


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_an_unreadable_season_yields_an_empty_fingerprint():
    """Same reasoning, reached through the real failure path."""
    taken = await take_fingerprint("/nonexistent/nowhere.db", 1, drawing=_ArtworkStandIn())

    assert taken.areas == {}


# ── One case per area ─────────────────────────────────────────────────────


async def _assert_only(season, before, area: str, sql: str, *params):
    """Change one thing and assert exactly its area is named."""
    await _change(season, sql, *params)

    changed = before.differs_from(await _take(season))

    assert AREA_LABELS[area] in changed, f"{area} went undetected"
    assert changed == [AREA_LABELS[area]], f"{area} also disturbed {changed}"


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_season_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "season",
        "UPDATE seasons SET game_edition = 26 WHERE id = ?", season.season_id,
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_a_stage_moved_is_part_of_the_season_area(season):
    """Confirming a configuration, or mid-season placements, moves the stage and not the
    status: a review of the stage left behind no longer describes the season."""
    before = await _take(season)
    await _assert_only(
        season, before, "season",
        "UPDATE seasons SET stage = 'CONFIGURATION' WHERE id = ?", season.season_id,
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_divisions_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "divisions",
        "UPDATE divisions SET name = 'Elite' WHERE id = ?", season.division_id,
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_rounds_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "rounds",
        "UPDATE rounds SET scheduled_at = '2026-06-21T18:00:00' WHERE division_id = ?",
        season.division_id,
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_teams_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "teams",
        "UPDATE team_instances SET max_seats = 3 WHERE division_id = ?",
        season.division_id,
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_drivers_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "drivers",
        "UPDATE driver_season_assignments SET team_seat_id = NULL WHERE season_id = ?",
        season.season_id,
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_channels_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "channels",
        "UPDATE divisions SET calendar_channel_id = 999 WHERE id = ?",
        season.division_id,
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_modules_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "modules",
        "UPDATE server_configs SET weather_module_enabled = 1 WHERE server_id = ?",
        SERVER_ID,
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_a_signup_settled_after_the_report_is_part_of_the_unsettled_signups_area(season):
    """Issue #220: the review names every unsettled signup, and a change among them is a
    change to what would be confirmed."""
    await _change(
        season,
        "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
        "VALUES (900, '900', 'PENDING_ADMIN_APPROVAL')",
    )
    before = await _take(season)
    await _assert_only(
        season, before, "unsettled signups",
        "UPDATE driver_profiles SET current_state = 'UNASSIGNED' WHERE id = 900",
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_test_mode_area(season):
    """Confirming a configuration fixes test mode, so switching it after the report is a
    change to what would be confirmed (issue #220)."""
    before = await _take(season)
    await _assert_only(
        season, before, "test mode",
        "UPDATE server_configs SET test_mode_active = 1 WHERE server_id = ?", SERVER_ID,
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_a_team_added_to_the_server_list_is_part_of_the_team_list_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "team list",
        "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
        "VALUES ('Bluestreak', 'Bluestreak', 2, 0)",
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_a_team_role_repointed_is_part_of_the_team_list_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "team list",
        "INSERT INTO team_role_configs (team_name, role_id, updated_at) "
        "VALUES ('Redline', 4242, '2026-09-17')",
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_points_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "points",
        "INSERT INTO season_points_links (season_id, config_name) VALUES (?, 'Standard')",
        season.season_id,
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
@pytest.mark.parametrize("edit", ["a points entry", "a fastest-lap row"])
async def test_a_server_points_entry_of_an_attached_configuration_changes_the_points_area(
    season, edit,
):
    """The approval copies the attached configurations' server entries and fastest-lap rows onto
    the season, so an edit to either between the review and the approval is a change to the
    points; the same edit to a configuration the season does not score on is not (#439)."""
    async with get_connection(season.path) as db:
        ids = {}
        for name in ("Standard", "Sprint Heavy"):
            cursor = await db.execute(
                "INSERT INTO points_config_store (config_name) VALUES (?)", (name,)
            )
            ids[name] = cursor.lastrowid
            await db.execute(
                "INSERT INTO points_config_entries (config_id, session_type, position, points) "
                "VALUES (?, 'FEATURE_RACE', 1, 25)",
                (ids[name],),
            )
            await db.execute(
                "INSERT INTO points_config_fl (config_id, session_type, fl_points, "
                "fl_position_limit) VALUES (?, 'FEATURE_RACE', 1, 10)",
                (ids[name],),
            )
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, 'Standard')",
            (season.season_id,),
        )
        await db.commit()
    sql = (
        "UPDATE points_config_entries SET points = 30 WHERE config_id = ?"
        if edit == "a points entry"
        else "UPDATE points_config_fl SET fl_points = 2 WHERE config_id = ?"
    )
    before = await _take(season)

    await _change(season, sql, ids["Sprint Heavy"])
    assert before.differs_from(await _take(season)) == []

    await _assert_only(season, before, "points", sql, ids["Standard"])


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_signup_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "signup",
        "INSERT INTO signup_module_config (id, signup_channel_id) VALUES (1, 77)",
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
@pytest.mark.parametrize("column", ["base_role_id", "driver_role_id"])
async def test_changing_a_league_role_is_named_as_the_roles_changing(season, column):
    """The two roles are the league's (issue #276), not the signup configuration: a change
    to one names them, and does not tell a league its signup settings moved."""
    before = await _take(season)
    await _assert_only(
        season, before, "roles", f"UPDATE server_configs SET {column} = 4242",
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_attendance_area(season):
    """Changed on an existing row, so the module switch it shares with `modules` is not
    also disturbed — inserting the row would move both, correctly but less precisely."""
    await _change(
        season,
        "INSERT INTO attendance_config (id, rsvp_notice_days) VALUES (1, 5)",
    )
    before = await _take(season)
    await _assert_only(
        season, before, "attendance",
        "UPDATE attendance_config SET rsvp_notice_days = 9",
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_weather_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "weather",
        "INSERT INTO weather_pipeline_config (id, phase_1_days) VALUES (1, 7)",
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_the_images_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "images",
        "UPDATE image_config SET date_format = 'YYYY_MM_DD'",
    )


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_an_aspect_toggle_is_part_of_the_images_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "images",
        "INSERT INTO image_aspect_toggles (aspect, enabled) VALUES ('calendar', 1)",
    )


# ── The artwork on disk ───────────────────────────────────────────────────
#
# The one area a database read cannot see. A template edited or deleted between the review
# and the button would otherwise pass, which is exactly what the withdrawn render caught.


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_a_changed_answer_from_the_drawing_is_named_as_the_artwork_alone(season):
    """The fingerprint digests the drawing's answer as it stands, so a review drawn before the
    image module answered compares equal after; and a template edited, deleted or added to between
    the review and the button, which changes the answer, is named as the templates and artwork on
    disk changing, and nothing else, since no setting changed."""
    season.drawing.answer = [["calendar_template", ["calendar_template.svg", 1024, 1767225600]]]
    before = await _take(season)

    assert before.areas["artwork"] == _digest(season.drawing.answer)

    season.drawing.answer = [["calendar_template", ["calendar_template.svg", 1040, 1767229200]]]
    changed = before.differs_from(await _take(season))

    assert changed == [AREA_LABELS["artwork"]]
    assert season.drawing.asked == 2


# ── Several changes at once ───────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=NEEDS_DRAWING)
async def test_two_changed_areas_are_both_named(season):
    before = await _take(season)

    await _change(
        season, "UPDATE divisions SET name = 'Elite' WHERE id = ?", season.division_id
    )
    await _change(
        season,
        "UPDATE rounds SET track_name = 'Albert Park' WHERE division_id = ?",
        season.division_id,
    )

    changed = before.differs_from(await _take(season))

    assert AREA_LABELS["divisions"] in changed
    assert AREA_LABELS["rounds"] in changed
