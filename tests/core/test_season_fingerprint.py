"""The season a review described, and whether it is still that season at the button.

`/season placements-review` fingerprints everything it reports; the Approve button refuses unless the
season still fingerprints the same. That is what lets the approval trust the review's own
render instead of drawing every graphic a second time.

**Every area gets a named case.** An area quietly dropped from the digest is a change that
slips through with nothing failing, so each is changed in turn and asserted to be both
detected *and named*. That is the whole value of the per-area digest over one hash, and it
is the test that stops the coverage rotting.

Each module's own areas are asked of the module, through the hook the builder signs each up to,
and which modules are on is asked of each module's switch reader; so the season here is
fingerprinted with the bot the builder makes, as a review's post fingerprints it.

The artwork on disk is asked of the image module's drawing, which says what the league's artwork
looks like now, and the fingerprint digests the answer. Here the drawing is a stand-in whose answer
each test sets; the answer itself is pinned against image's own drawing in
`tests/image/test_image_drawing_artwork.py`.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.season_fingerprint_service import (
    AREA_LABELS,
    SeasonFingerprint,
    _digest,
)
from tests.support.review_workflows import take_season_fingerprint

SERVER_ID = 8811
_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

#: The modules, by the name each signs its switch reader up under.
_MODULES = ("weather", "signup", "results", "attendance", "images")


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
    """A configured server with one season, one division, a team, a seat and a round, every module
    off, on the bot the builder makes, its image module's drawing a stand-in."""
    from tests.support.change_queue import built_bot

    bot = await built_bot(tmp_path, now=_NOW)
    bot.image_drawing = _ArtworkStandIn()
    path = bot.db_path
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
            "scheduled_at) VALUES (?, 1, 'NORMAL', 'Hungaroring', '2026-06-14T18:00:00+00:00')",
            (division_id,),
        )
        await db.execute("INSERT INTO image_config (id) VALUES (?)", (1,))
        await db.commit()

    return SimpleNamespace(
        path=path, season_id=season_id, division_id=division_id, drawing=bot.image_drawing,
        bot=bot,
    )


async def _take(season) -> SeasonFingerprint:
    return await take_season_fingerprint(season.bot, season.path, season.season_id)


async def _change(season, sql: str, *params) -> None:
    async with get_connection(season.path) as db:
        await db.execute(sql, params)
        await db.commit()


# ── The fingerprint itself ────────────────────────────────────────────────


async def test_every_area_is_covered(season):
    """A missing area is a change that would slip through unnoticed."""
    fingerprint = await _take(season)

    assert set(fingerprint.areas) == set(AREA_LABELS)


async def test_an_unchanged_season_fingerprints_the_same_twice(season):
    """Taken twice with nothing between, it must not report a change nobody made."""
    first = await _take(season)
    second = await _take(season)

    assert first.differs_from(second) == []


async def test_a_fingerprint_that_could_not_be_taken_differs_from_everything(season):
    """A fault refuses an approval rather than waving it through: a fingerprint that could not be
    taken differs from one that was, either way round, and from another that could not be taken
    either, as a fault at both the review and the press would leave them, every area named."""
    empty = SeasonFingerprint({})
    taken = await _take(season)
    every_area = [AREA_LABELS[area] for area in sorted(AREA_LABELS)]

    assert empty.differs_from(taken)
    assert taken.differs_from(empty)
    assert empty.differs_from(SeasonFingerprint({})) == every_area


async def test_a_switch_nobody_signed_up_for_is_raised_not_fingerprinted(season):
    """A module whose switch reader the builder never signed up is a fault in the bot's assembly,
    not a season that could not be read: taking the fingerprint raises it, naming the module,
    rather than giving the empty fingerprint."""
    from leaguebot.core.services.modules.module_service import ModuleService, SwitchNotSignedUp

    season.bot.module_service = ModuleService(season.path)

    with pytest.raises(SwitchNotSignedUp, match="weather"):
        await _take(season)


async def test_an_unreadable_season_yields_an_empty_fingerprint(season):
    """Same reasoning, reached through the real failure path."""
    taken = await take_season_fingerprint(season.bot, "/nonexistent/nowhere.db", 1)

    assert taken.areas == {}


# ── One case per area ─────────────────────────────────────────────────────


async def _assert_only(season, before, area: str, sql: str, *params):
    """Change one thing and assert exactly its area is named."""
    await _change(season, sql, *params)

    changed = before.differs_from(await _take(season))

    assert AREA_LABELS[area] in changed, f"{area} went undetected"
    assert changed == [AREA_LABELS[area]], f"{area} also disturbed {changed}"


async def test_the_season_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "season",
        "UPDATE seasons SET game_edition = 26 WHERE id = ?", season.season_id,
    )


async def test_a_stage_moved_is_part_of_the_season_area(season):
    """Confirming a configuration, or mid-season placements, moves the stage and not the
    status: a review of the stage left behind no longer describes the season."""
    before = await _take(season)
    await _assert_only(
        season, before, "season",
        "UPDATE seasons SET stage = 'CONFIGURATION' WHERE id = ?", season.season_id,
    )


async def test_the_divisions_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "divisions",
        "UPDATE divisions SET name = 'Elite' WHERE id = ?", season.division_id,
    )


async def test_the_rounds_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "rounds",
        "UPDATE rounds SET scheduled_at = '2026-06-21T18:00:00+00:00' WHERE division_id = ?",
        season.division_id,
    )


async def test_the_teams_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "teams",
        "UPDATE team_instances SET max_seats = 3 WHERE division_id = ?",
        season.division_id,
    )


async def test_the_drivers_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "drivers",
        "UPDATE driver_season_assignments SET team_seat_id = NULL WHERE season_id = ?",
        season.season_id,
    )


#: A division's channel set or changed, its own or a module's, a seed standing first where the edit
#: changes a row: the seed, the edit, both handed the division.
_CHANNELS = {
    "the division's calendar channel": (
        None, "UPDATE divisions SET calendar_channel_id = 999 WHERE id = ?",
    ),
    "weather's forecast channel": (
        None, "UPDATE divisions SET forecast_channel_id = 555 WHERE id = ?",
    ),
    "results' results channel": (
        None,
        "INSERT INTO division_results_config (division_id, results_channel_id) VALUES (?, 120)",
    ),
    "results' reserves in the standings": (
        "INSERT INTO division_results_config (division_id) VALUES (?)",
        "UPDATE division_results_config SET reserves_in_standings = 0 WHERE division_id = ?",
    ),
    "attendance's RSVP channel": (
        None,
        "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) VALUES (?, '130')",
    ),
}


@pytest.mark.parametrize("edit", sorted(_CHANNELS))
async def test_the_channels_area(season, edit):
    """Pro's calendar channel changed, its weather channel set, its results channel set, its
    reserves left out of the standings, or its RSVP channel set, between the review and the
    button: each is named as the channels changing, and nothing else."""
    seed, sql = _CHANNELS[edit]
    if seed is not None:
        await _change(season, seed, season.division_id)
    before = await _take(season)
    await _assert_only(season, before, "channels", sql, season.division_id)


@pytest.mark.parametrize("module", ["weather", "signup", "results", "attendance", "images"])
async def test_the_modules_area(season, module):
    """Any module switched on between the review and the button is named as which modules are
    enabled changing. Attendance's and images' switches stand in their own configurations, which
    the review reports too, so those are named with it."""
    from tests.support.season_league import set_module_flag

    before = await _take(season)
    await set_module_flag(season.path, module, True)

    changed = before.differs_from(await _take(season))

    also = {"attendance": ["attendance"], "images": ["images"]}.get(module, [])
    assert changed == [AREA_LABELS[area] for area in sorted(["modules", *also])]


async def test_the_modules_area_is_answered_by_each_module_s_switch_reader(season):
    """Every module's flag in the database stays off; each module's switch reader is a stand-in
    answering what the test sets. Images' reader answering on between the review and the button
    is named as which modules are enabled changing, and nothing else: the fingerprint asks each of
    the five modules' readers, all of them on the one connection it holds, and reads no flag
    itself."""
    answers = dict.fromkeys(_MODULES, False)
    handed: list[tuple[str, object]] = []
    for module in _MODULES:
        async def reader(db, module=module) -> bool:
            handed.append((module, db))
            return answers[module]
        season.bot.module_service.register_switch(module, reader)

    before = await _take(season)
    asked = list(handed)
    handed.clear()
    answers["images"] = True
    changed = before.differs_from(await _take(season))

    assert changed == [AREA_LABELS["modules"]]
    for each in (asked, handed):
        assert sorted(module for module, _db in each) == sorted(_MODULES)
        assert len({id(db) for _module, db in each}) == 1


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


async def test_the_test_mode_area(season):
    """Confirming a configuration fixes test mode, so switching it after the report is a
    change to what would be confirmed (issue #220)."""
    before = await _take(season)
    await _assert_only(
        season, before, "test mode",
        "UPDATE server_configs SET test_mode_active = 1 WHERE server_id = ?", SERVER_ID,
    )


async def test_a_team_added_to_the_server_list_is_part_of_the_team_list_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "team list",
        "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
        "VALUES ('Bluestreak', 'Bluestreak', 2, 0)",
    )


async def test_a_team_role_repointed_is_part_of_the_team_list_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "team list",
        "INSERT INTO team_role_configs (team_name, role_id, updated_at) "
        "VALUES ('Redline', 4242, '2026-09-17')",
    )


async def test_the_points_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "points",
        "INSERT INTO season_points_links (season_id, config_name) VALUES (?, 'Standard')",
        season.season_id,
    )


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


async def test_the_signup_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "signup",
        "INSERT INTO signup_module_config (id, signup_channel_id) VALUES (1, 77)",
    )


@pytest.mark.parametrize("column", ["base_role_id", "driver_role_id"])
async def test_changing_a_league_role_is_named_as_the_roles_changing(season, column):
    """The two roles are the league's (issue #276), not the signup configuration: a change
    to one names them, and does not tell a league its signup settings moved."""
    before = await _take(season)
    await _assert_only(
        season, before, "roles", f"UPDATE server_configs SET {column} = 4242",
    )


async def test_the_attendance_area(season):
    """Changed on an existing row, its module switch left as it was, so the attendance
    configuration alone is named."""
    await _change(
        season,
        "INSERT INTO attendance_config (id, rsvp_notice_days) VALUES (1, 5)",
    )
    before = await _take(season)
    await _assert_only(
        season, before, "attendance",
        "UPDATE attendance_config SET rsvp_notice_days = 9",
    )


async def test_the_weather_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "weather",
        "INSERT INTO weather_pipeline_config (id, phase_1_days) VALUES (1, 7)",
    )


async def test_the_images_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "images",
        "UPDATE image_config SET date_format = 'YYYY_MM_DD'",
    )


async def test_an_aspect_toggle_is_part_of_the_images_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "images",
        "INSERT INTO image_aspect_toggles (aspect, enabled) VALUES ('calendar', 1)",
    )


# ── Each module's own areas ───────────────────────────────────────────────
#
# Core reads no module's table: each module hands the fingerprint its own areas, on the
# connection the fingerprint holds, through the hook the builder signs each up to.


#: The areas each module hands the fingerprint, by the module.
_MODULE_AREAS = {
    "weather": {"channels", "weather"},
    "results": {"channels", "points"},
    "attendance": {"channels", "attendance"},
    "signup": {"signup"},
    "images": {"images"},
}

#: An edit to one module's own configuration: the module, the one area of its own it changes, a
#: seed standing first where the edit changes a row, and the edit. `:division` and `:season` are
#: the season's division and the season.
_MODULE_EDITS = {
    "weather's forecast channel": (
        "weather", "channels", None,
        "UPDATE divisions SET forecast_channel_id = 555 WHERE id = :division",
    ),
    "weather's phases": (
        "weather", "weather", None,
        "INSERT INTO weather_pipeline_config (id, phase_1_days) VALUES (1, 7)",
    ),
    "results' results channel": (
        "results", "channels", None,
        "INSERT INTO division_results_config (division_id, results_channel_id) "
        "VALUES (:division, 120)",
    ),
    "results' reserves in the standings": (
        "results", "channels",
        "INSERT INTO division_results_config (division_id) VALUES (:division)",
        "UPDATE division_results_config SET reserves_in_standings = 0 "
        "WHERE division_id = :division",
    ),
    "results' points configuration": (
        "results", "points", None,
        "INSERT INTO season_points_links (season_id, config_name) VALUES (:season, 'Standard')",
    ),
    "attendance's RSVP channel": (
        "attendance", "channels", None,
        "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
        "VALUES (:division, '130')",
    ),
    "attendance's notice": (
        "attendance", "attendance",
        "INSERT INTO attendance_config (id, rsvp_notice_days) VALUES (1, 5)",
        "UPDATE attendance_config SET rsvp_notice_days = 9",
    ),
    "signup's channel": (
        "signup", "signup", None,
        "INSERT INTO signup_module_config (id, signup_channel_id) VALUES (1, 77)",
    ),
    "signup's settings": (
        "signup", "signup", None,
        "INSERT INTO signup_module_settings (id, nationality_required) VALUES (1, 0)",
    ),
    "signup's availability slots": (
        "signup", "signup", None,
        "INSERT INTO signup_availability_slots (day_of_week, time_hhmm) VALUES (3, '20:00')",
    ),
    "image's date format": (
        "images", "images", None, "UPDATE image_config SET date_format = 'YYYY_MM_DD'",
    ),
    "image's aspect toggles": (
        "images", "images", None,
        "INSERT INTO image_aspect_toggles (aspect, enabled) VALUES ('calendar', 1)",
    ),
}


async def _handed(season) -> list:
    """What each module hands the fingerprint of the season, on a connection held open."""
    async with get_connection(season.path) as db:
        return await season.bot.fingerprint_areas_read.call(db, season.season_id)


async def _edit(season, sql: str) -> None:
    async with get_connection(season.path) as db:
        await db.execute(sql, {"division": season.division_id, "season": season.season_id})
        await db.commit()


async def test_each_module_hands_its_own_areas_whatever_its_switch(season):
    """Every module is off. Asked for the season, weather hands its channels and its
    configuration, results its channels and its points, attendance its channels and its
    configuration, signup its configuration and images its configuration: each module its own
    areas, its switch off or not."""
    handed = await _handed(season)

    assert sorted(sorted(each) for each in handed) == sorted(
        sorted(areas) for areas in _MODULE_AREAS.values()
    )


@pytest.mark.parametrize("edit", sorted(_MODULE_EDITS))
async def test_an_edit_to_a_module_s_configuration_changes_what_that_module_hands_alone(
    season, edit,
):
    """Every module off. One module's own configuration is edited: Pro's weather channel or the
    weather phases; Pro's results channel, its reserves left out of the standings, or a points
    configuration attached to the season; Pro's RSVP channel or attendance's notice days; the
    signup channel, settings or an availability slot; the image date format or an aspect toggle.
    What that module hands changes, in that one area alone, and what every other module hands
    does not."""
    module, area, seed, sql = _MODULE_EDITS[edit]
    if seed is not None:
        await _edit(season, seed)
    before = await _handed(season)

    await _edit(season, sql)
    after = await _handed(season)

    changed = [
        (set(was), {key for key in was if was[key] != now.get(key)})
        for was, now in zip(before, after)
        if was != now
    ]
    assert changed == [(_MODULE_AREAS[module], {area})]


@pytest.mark.parametrize("fault", [
    "a module's read fails", "a module's row lacks a column", "no module hands its areas",
])
async def test_a_module_s_areas_not_read_leave_an_empty_fingerprint_logged(
    season, caplog, fault,
):
    """A module whose read fails while the fingerprint is taken (the database refusing, or a row
    without the column it names, an `IndexError` as `sqlite3.Row` raises), or a bot whose modules
    hand no areas at all, gives an empty fingerprint, which differs from every other, so an
    approval is refused rather than waved through; the fault is logged on the host."""
    from leaguebot.core.utils.hooks import AwaitedHook

    if fault != "no module hands its areas":
        error = (
            sqlite3.OperationalError("no such table: image_config")
            if fault == "a module's read fails" else IndexError("No item with that key")
        )

        async def broken(*_args, **_kwargs):
            raise error

        season.bot.fingerprint_areas_read.subscribe(broken)
    else:
        season.bot.fingerprint_areas_read = AwaitedHook()

    with caplog.at_level(logging.ERROR):
        taken = await _take(season)

    assert taken.areas == {}
    assert any(
        record.levelno == logging.ERROR
        and record.name == "leaguebot.core.services.season_fingerprint_service"
        for record in caplog.records
    )


# ── The artwork on disk ───────────────────────────────────────────────────
#
# The one area a database read cannot see. A template edited or deleted between the review
# and the button would otherwise pass, which is exactly what the withdrawn render caught.


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
