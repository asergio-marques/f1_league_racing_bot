"""The entry point's settings and its builder (architecture.md, "Start-up runs once, in one
function", step 1, and "Services stay attached to the bot, and are built in one place").

The three settings are read once, in `main`, from the environment `load_dotenv` has filled:
`read_settings` takes a plain mapping, so it is tested without a bot or the process's environment.
`build` makes the bot and every service complete, and registers every change type and timer kind
before anything starts; a test calls it on paths under `tmp_path` through
`tests/support/change_queue.built_bot`.
"""
from __future__ import annotations

import inspect
import logging
from datetime import datetime, timedelta, timezone

import pytest

from tests.support.change_queue import built_bot

_SET_ONLY_WHERE_KNOWN = {"running_version", "running_version_date"}


@pytest.mark.parametrize(
    ("environ", "db_path", "jobstore_path"),
    [
        pytest.param({"BOT_TOKEN": "t"}, "bot.db", None, id="the database defaults to bot.db"),
        pytest.param({"BOT_TOKEN": "t", "DB_PATH": "/league/league.db"}, "/league/league.db", None,
                     id="an unset job store sits beside the database"),
        pytest.param({"BOT_TOKEN": "t", "DB_PATH": "/league/league.db", "SCHEDULER_DB_PATH": ""},
                     "/league/league.db", None, id="an empty job store sits beside the database"),
        pytest.param({"BOT_TOKEN": "t", "DB_PATH": "/league/league.db",
                      "SCHEDULER_DB_PATH": "/jobs/jobs.db"},
                     "/league/league.db", "/jobs/jobs.db", id="an explicit job store is honoured"),
        pytest.param({"DB_PATH": "/league/league.db"}, None, None,
                     id="a missing token is refused by name"),
    ],
)
def test_read_settings(environ, db_path, jobstore_path):
    """`read_settings` reads the token, the database and the job store from a plain mapping,
    resolving the job store once; with no `BOT_TOKEN` it raises `MissingBotToken`, naming it."""
    from leaguebot.__main__ import MissingBotToken, read_settings
    from leaguebot.core.services.scheduler_service import default_jobstore_path

    if db_path is None:
        with pytest.raises(MissingBotToken, match="BOT_TOKEN"):
            read_settings(environ)
        return
    settings = read_settings(environ)
    assert settings.token == "t"
    assert settings.db_path == db_path
    assert settings.jobstore_path == (jobstore_path or default_jobstore_path(db_path))


#: When the built bot's change queue takes "now" to be.
_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


async def test_build_returns_every_service_complete(tmp_path):
    """`build` on paths under `tmp_path` gives a `LeagueBot` with every service it declares set,
    on the settings' paths, and the wizard service holding the bot from its constructor, with no
    `set_bot` left to call."""
    from leaguebot.core.utils.league_bot import LeagueBot

    bot = await built_bot(tmp_path, now=_NOW)

    assert isinstance(bot, LeagueBot)
    declared = set(inspect.get_annotations(LeagueBot))
    assert sorted(name for name in declared if not hasattr(bot, name)) == []
    assert sorted(
        name for name in declared - _SET_ONLY_WHERE_KNOWN if getattr(bot, name) is None
    ) == []
    assert bot.db_path == str(tmp_path / "bot.db")
    assert bot.scheduler_service.jobstore_path == str(tmp_path / "jobs.db")
    assert bot.wizard_service._bot is bot
    assert not hasattr(bot.wizard_service, "set_bot")


async def test_the_builder_hands_the_capacity_guards_and_the_lineups_the_image_module_s_drawing(
    tmp_path,
):
    """`build` makes the image module's answer to core's drawing protocol once, declares it on the
    bot, and hands that same answer to the template capacity service and the lineup poster:
    without it, every capacity guard would let every placement through, and no lineup would be
    drawn. `PlacementService` is handed the capacity service and the team-role service the bot
    declares, the same objects, so its seats are guarded and its roles granted by what every other
    caller uses. It keeps no lineup posting of its own: a move or a sack names the divisions
    whose lineup is owed, and the poster posts them. The drawing is handed the bot's database,
    which is all a lineup's drawing reads beyond the image module's own services, and none of the
    four services is handed the bot, since each posts or grants through the guild or member it is
    given. Attendance's hook is handed the placement, team-role and lineup services the bot
    declares: its keywords for the last two are optional, so only this test sees one left out,
    which would leave a sanction's roles unchanged and its lineup unposted."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    bot = await built_bot(tmp_path, now=_NOW)

    assert isinstance(bot.image_drawing, ImageDrawing)
    assert bot.template_capacity_service._drawing is bot.image_drawing
    assert bot.lineup_poster._drawing is bot.image_drawing
    assert bot.placement_service._capacity is bot.template_capacity_service
    assert bot.placement_service._roles is bot.team_role_service
    assert not hasattr(bot.placement_service, "_drawing")
    assert not hasattr(bot.placement_service, "_refresh_lineup_post")
    assert bot.image_drawing._db_path == bot.db_path
    assert bot.attendance_after_review._placement is bot.placement_service
    assert bot.attendance_after_review._roles is bot.team_role_service
    assert bot.attendance_after_review._lineups is bot.lineup_poster
    for service in (bot.placement_service, bot.template_capacity_service, bot.lineup_poster,
                    bot.team_role_service):
        assert not hasattr(service, "_bot")


async def test_the_builder_hands_every_change_that_posts_a_calendar_the_image_module_s_drawing(
    tmp_path,
):
    """Every change that posts a division's calendar (a season's approval, the three
    cancellations, results' round ended not held, and `/division calendar-sync`) carries a
    `post_calendar` job that draws through the image module's drawing the builder made, the one on
    the bot, and no other change carries one."""
    bot = await built_bot(tmp_path, now=_NOW)

    handed = {}
    for kind, change_type in bot.change_queue._types.items():
        step = change_type.steps.get("post_calendar")
        if step is not None:
            held = [cell.cell_contents for cell in step.run.__closure__ or ()]
            handed[kind] = any(each is bot.image_drawing for each in held)

    assert handed == {
        "season.approve": True,
        "season.round.cancel": True,
        "season.division.cancel": True,
        "season.cancel": True,
        "results.submission.all_cancelled": True,
        "calendar.sync": True,
    }


async def test_the_builder_hands_the_approval_and_the_confirmation_the_drawing_s_rasteriser_check(
    tmp_path, monkeypatch,
):
    """A season's approval and a mid-season confirmation of placements each judge the host's
    drawing program again as they run. The builder hands both the image module's drawing's own
    check, the one the reviews ask, so the three ask it one way and cannot disagree."""
    from leaguebot.core.services.seasons import mid_season_confirm_change, season_approval_change

    handed = {}

    def _capturing(name, made):
        def _make(**kwargs):
            handed[name] = kwargs["rasteriser_fault"]
            return made(**kwargs)

        return _make

    monkeypatch.setattr(
        season_approval_change, "season_approval_change",
        _capturing("approval", season_approval_change.season_approval_change),
    )
    monkeypatch.setattr(
        mid_season_confirm_change, "mid_season_confirm_change",
        _capturing("confirmation", mid_season_confirm_change.mid_season_confirm_change),
    )

    bot = await built_bot(tmp_path, now=_NOW)

    assert handed == {
        "approval": bot.image_drawing.rasteriser_fault,
        "confirmation": bot.image_drawing.rasteriser_fault,
    }


async def test_the_review_s_calendar_picture_is_drawn_through_the_image_module_s_drawing(
    tmp_path, monkeypatch,
):
    """The review asks for Pro's calendar picture of season 3: the builder's drawing of review
    pictures draws it through the image module's drawing the builder attached to the bot, and
    hands back the PNG drawn."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from leaguebot.__main__ import _draw_review_picture
    from leaguebot.core.services.rounds import calendar_post_service
    from tests.support.calendar_drawing import CalendarDrawingStub, draw_calendars_with

    bot = await built_bot(tmp_path, now=_NOW)
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")
    drawing = draw_calendars_with(bot, monkeypatch, CalendarDrawingStub(picture=png))
    monkeypatch.setattr(bot.season_service, "get_division",
                        AsyncMock(return_value=SimpleNamespace(id=11, name="Pro", tier=1)))
    monkeypatch.setattr(bot.season_service, "get_division_rounds", AsyncMock(return_value=[]))
    monkeypatch.setattr(calendar_post_service, "tracks_by_name", AsyncMock(return_value={}))

    drawn = await _draw_review_picture(
        bot, {"kind": "calendar", "division_id": 11, "season_number": 3})

    assert drawn == png
    assert drawing.asked == [{"division": "Pro", "tracks": [], "season_number": 3}]


async def test_the_review_s_lineup_picture_is_drawn_through_the_image_module_s_drawing(
    tmp_path, monkeypatch,
):
    """The review asks for Pro's lineup picture with its uncommitted drivers in it: the builder's
    drawing of review pictures draws it through the image module's drawing the builder attached to
    the bot (`draw_review_lineup`), handed the league's server, the division and the uncommitted
    drivers, and hands back the PNG drawn; it never reaches past the drawing to image's renderer."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from leaguebot.__main__ import _draw_review_picture
    from leaguebot.core.services.modules.image_drawing import LineupPicture
    from leaguebot.image.services import image_lineup_post

    bot = await built_bot(tmp_path, now=_NOW)
    png = tmp_path / "lineup.png"
    png.write_bytes(b"\x89PNG")
    guild = SimpleNamespace(id=1)
    monkeypatch.setattr(bot.config_service, "get_league_server_id", AsyncMock(return_value=1))
    monkeypatch.setattr(bot, "get_guild", lambda server_id: guild if server_id == 1 else None)
    asked: list[tuple[object, int, bool]] = []

    async def draw_review_lineup(on: object, division_id: int, *,
                                 include_uncommitted: bool) -> LineupPicture:
        asked.append((on, division_id, include_uncommitted))
        return LineupPicture(path=png, problem=None)

    monkeypatch.setattr(bot.image_drawing, "draw_review_lineup", draw_review_lineup,
                        raising=False)
    monkeypatch.setattr(image_lineup_post, "render_for_command", AsyncMock(
        side_effect=AssertionError("the review drew its lineup past the image module's drawing")))

    drawn = await _draw_review_picture(
        bot, {"kind": "lineup", "division_id": 11, "include_uncommitted": True})

    assert drawn == png
    assert asked == [(guild, 11, True)]


@pytest.mark.parametrize(("problem", "fault"), [
    pytest.param("❌ the template's font could not be loaded",
                 "❌ the template's font could not be loaded", id="its_words"),
    pytest.param(None, "the lineup could not be drawn", id="no_words"),
])
async def test_a_review_lineup_the_image_module_s_drawing_will_not_draw_raises_with_its_words(
    tmp_path, monkeypatch, problem, fault,
):
    """The review asks for Pro's lineup picture, and the image module's drawing the builder
    attached to the bot answers `draw_review_lineup` with no picture: with its words for why, or
    with none. The builder's drawing of review pictures raises with exactly those words, or, with
    none, with "the lineup could not be drawn", so the review withholds its question and keeps
    the fault as the reviewer and the log channel read it today."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from leaguebot.__main__ import _draw_review_picture
    from leaguebot.core.services.modules.image_drawing import LineupPicture
    from leaguebot.image.services import image_lineup_post

    bot = await built_bot(tmp_path, now=_NOW)
    guild = SimpleNamespace(id=1)
    monkeypatch.setattr(bot.config_service, "get_league_server_id", AsyncMock(return_value=1))
    monkeypatch.setattr(bot, "get_guild", lambda server_id: guild if server_id == 1 else None)

    async def draw_review_lineup(on: object, division_id: int, *,
                                 include_uncommitted: bool) -> LineupPicture:
        return LineupPicture(path=None, problem=problem)

    monkeypatch.setattr(bot.image_drawing, "draw_review_lineup", draw_review_lineup,
                        raising=False)
    monkeypatch.setattr(image_lineup_post, "render_for_command", AsyncMock(
        side_effect=AssertionError("the review drew its lineup past the image module's drawing")))

    with pytest.raises(RuntimeError) as raised:
        await _draw_review_picture(bot, {"kind": "lineup", "division_id": 11})

    assert str(raised.value) == fault


#: The kinds of the scheduler's own jobs, each signed up with its handler by the builder.
_SCHEDULER_KINDS = {
    "weather_p1", "weather_p2", "weather_p3", "cleanup", "results", "rsvp_notice",
    "rsvp_last_notice", "rsvp_deadline", "rsvp_cleanup", "signup_close", "portrait_refresh",
    "amendment_sweep",
}


async def test_every_kind_is_registered_before_the_scheduler_starts(tmp_path):
    """Straight after `build`, with the scheduler not yet started, every kind is signed up in the
    one registry: the scheduler's own jobs (the three weather phases, the forecast clean-up, the
    result submission, the four check-in jobs, the signup close, the portrait refresh and the
    amendment sweep) and every one-off timer kind."""
    from leaguebot.core.services.seasons import season_review_changes
    from leaguebot.results.services.round_amendment.amend_changes import TIMER as AMEND_TIMER
    from leaguebot.results.services.review.review_lock_change import TIMER as LOCK_TIMER
    from leaguebot.signup.services.closing_steps import DELETE_TIMER, INACTIVITY_TIMER
    from leaguebot.signup.services.review_changes import CORRECTION_TIMER

    bot = await built_bot(tmp_path, now=_NOW)
    scheduler = bot.scheduler_service

    assert not scheduler._scheduler.running
    kinds = _SCHEDULER_KINDS | {AMEND_TIMER, LOCK_TIMER, DELETE_TIMER, INACTIVITY_TIMER,
                                CORRECTION_TIMER, season_review_changes.TIMER}
    assert sorted(kinds - set(scheduler._timer_callbacks)) == []


async def test_every_round_kind_has_its_advance_step_after_build(tmp_path):
    """Straight after `build`, every kind of a round's jobs (the three weather phases, the
    forecast clean-up, the result submission and the four check-in jobs) has the step
    `/test-mode advance` runs for it signed up on the bot's advance steps, which the builder
    makes, by the module whose job it is."""
    from leaguebot.core.models.round import ROUND_JOB_KINDS

    bot = await built_bot(tmp_path, now=_NOW)

    missing = []
    for kind in sorted(ROUND_JOB_KINDS):
        try:
            bot.advance_steps.step_for(kind)
        except LookupError:
            missing.append(kind)
    assert missing == []


#: Each module's part of a cancellation of rounds, as the module names it: the moment's hook on
#: the bot, the module, the subscriber, and what the test calls it.
_CANCELLATION_PARTS = (
    ("cancellation_refused", "leaguebot.results.services.submission.result_submission_service",
     "cancellation_refusal", "results' refusal"),
    ("rounds_called_off", "leaguebot.results.services.submission.result_submission_service",
     "close_called_off_on", "results' close"),
    ("rounds_called_off", "leaguebot.attendance.services.check_in_clean_up",
     "take_down_called_off_on", "attendance's take-down"),
    ("cancellation_notices", "leaguebot.attendance.services.cancellation_notice",
     "notices_on", "attendance's check-in notice"),
    ("cancellation_notices", "leaguebot.weather.services.cancellation_notice",
     "notices_on", "weather's forecast note"),
    ("cancellation_notices", "leaguebot.results.services.publishing.cancellation_notice",
     "notices_on", "results' results note"),
)


#: Each module's subscriber to a round's timing windows read, as the module names it, and what the
#: test calls it, in the order the builder signs them up.
_WINDOWS_PARTS = (
    ("leaguebot.attendance.services.attendance_hooks", "round_window", "attendance's window"),
    ("leaguebot.weather.services.weather_config_service", "round_window", "weather's window"),
)


#: Each module's part of a round amended, as the module names it: the moment's hook on the bot,
#: the module, the subscriber, and what the test calls it, in the order the builder signs them up.
_AMENDMENT_PARTS = (
    ("round_amended", "leaguebot.weather.services.phase_withdrawal", "round_amended_on",
     "weather's withdrawal"),
    ("round_amended", "leaguebot.attendance.services.check_in_amendment", "round_amended_on",
     "attendance's reopening"),
    ("round_armed", "leaguebot.attendance.services.check_in_amendment", "amendment_posts",
     "attendance's posts"),
    ("round_armed", "leaguebot.weather.services.forecast_changes", "amendment_posts",
     "weather's posts"),
    ("round_clean_ups_due", "leaguebot.weather.services.forecast_changes", "amendment_clean_ups",
     "weather's clean-ups"),
    ("round_clean_ups_due", "leaguebot.attendance.services.check_in_amendment",
     "amendment_clean_ups", "attendance's clean-ups"),
)

#: Weather's answer to which forecast phases a round has had drawn, as the module names it: the
#: module, the subscriber, and what the test calls it.
_PHASES_DRAWN_PARTS = (
    ("leaguebot.weather.services.round_phases", "phases_drawn", "weather's phases drawn"),
)

#: Each module's part of a season's end, as the module names it: the moment's hook on the bot, the
#: module, the subscriber, and what the test calls it, in the order the builder signs them up.
_SEASON_END_PARTS = (
    ("season_end_refused", "leaguebot.results.services.round_amendment.amendment_writes",
     "season_end_refusal", "results' refusal of a season's end"),
    ("drivers_returned", "leaguebot.signup.services.closing_steps", "drivers_returned_on",
     "signup's wizards ended"),
    ("test_mode_flushed", "leaguebot.weather.services.forecast_cleanup_service",
     "forecast_deletes", "weather's forecast deletions"),
    ("drivers_deleted", "leaguebot.image.services.driver_portrait_service", "discard_portraits",
     "image's portraits discarded"),
)

#: Each module's part of a season, division, round or driver deleted, or a driver merged into
#: another, as the module names it: the hook on the bot, the module, the subscriber, and what the
#: test calls it, in the order the builder signs them up. Each deletes or re-points the module's
#: own rows on the delete's connection.
_DELETE_PARTS = (
    ("rounds_deleted", "leaguebot.attendance.services.attendance_hooks", "rounds_deleted_on",
     "attendance's rounds"),
    ("rounds_deleted", "leaguebot.results.services.module.results_purge_service", "rounds_deleted_on",
     "results' rounds"),
    ("rounds_deleted", "leaguebot.weather.services.forecast_cleanup_service", "rounds_deleted_on",
     "weather's rounds"),
    ("divisions_deleted", "leaguebot.attendance.services.attendance_hooks",
     "divisions_deleted_on", "attendance's divisions"),
    ("divisions_deleted", "leaguebot.results.services.module.results_purge_service",
     "divisions_deleted_on", "results' divisions"),
    ("season_deleted", "leaguebot.results.services.module.results_purge_service", "season_deleted_on",
     "results' season"),
    ("season_deleted", "leaguebot.signup.services.signup_module_service", "season_deleted_on",
     "signup's season"),
    ("profiles_deleted", "leaguebot.attendance.services.attendance_hooks",
     "profiles_deleted_on", "attendance's drivers deleted"),
    ("profiles_deleted", "leaguebot.results.services.module.results_purge_service",
     "profiles_deleted_on", "results' drivers deleted"),
    ("profiles_merged", "leaguebot.attendance.services.attendance_hooks",
     "profiles_merged_on", "attendance's drivers merged"),
    ("profiles_merged", "leaguebot.results.services.module.results_purge_service",
     "profiles_merged_on", "results' drivers merged"),
)

#: What each delete hook hands its subscribers after the connection: a round or division delete,
#: the ids; a season's, its id; a driver deleted, the profile ids; a merge, the kept and absorbed
#: profile ids.
_DELETE_PAYLOADS = {
    "rounds_deleted": ([0],), "divisions_deleted": ([0],), "season_deleted": (0,),
    "profiles_deleted": ([0],), "profiles_merged": (0, 1),
}

#: Each module's part of the bot packed, as the module names it: the module, the subscriber, and
#: what the test calls it, in the order the builder signs them up. Each clears the module's own
#: rows tied to the server left behind, on the pack's connection.
_PACK_PARTS = (
    ("leaguebot.signup.services.signup_module_service", "bot_packed_on", "signup's pack"),
    ("leaguebot.weather.services.forecast_cleanup_service", "bot_packed_on", "weather's pack"),
    ("leaguebot.attendance.services.attendance_hooks", "bot_packed_on", "attendance's pack"),
    ("leaguebot.results.services.module.results_purge_service", "bot_packed_on", "results' pack"),
)

#: Each module's part of a round's results made final, as the module names it: the module, the
#: subscriber, and what the test calls it, in the order the builder signs them up. Each marks the
#: drivers who raced the rounds as former drivers, on the closing's connection.
_FINAL_PARTS = (
    ("leaguebot.results.services.submission.result_submission_service", "rounds_made_final_on",
     "results' former drivers"),
)

#: Each module's part of test mode turned on, as the module names it: the module, the subscriber,
#: and what the test calls it, in the order the builder signs them up. Each seeds what the module
#: seeds for a season under test, on the connection of the save that turns test mode on.
_TEST_MODE_PARTS = (
    ("leaguebot.results.services.points.points_config_service", "seed_test_configs_on",
     "results' points configurations"),
)

#: Each module's part of placements confirmed, as the module names it: the module, the subscriber,
#: and what the test calls it, in the order the builder signs them up. Each hands back the jobs it
#: plans for one division, on the connection of the approval's save.
_PLACEMENTS_PARTS = (
    ("leaguebot.results.services.standings.season_standings_posts", "placements_confirmed_on",
     "results' opening standings"),
    ("leaguebot.attendance.services.sheet_posting", "placements_confirmed_on",
     "attendance's opening sheet"),
)

#: Each module's part of a season completed, as the module names it: the module, the subscriber,
#: and what the test calls it, in the order the builder signs them up. Each hands back the jobs it
#: plans for one division's final classification, on the connection of the completion's save.
_SEASON_COMPLETED_PARTS = (
    ("leaguebot.results.services.standings.season_standings_posts", "season_completed_on",
     "results' final standings"),
    ("leaguebot.attendance.services.sheet_posting", "season_completed_on",
     "attendance's final sheet"),
)

#: Each module's part of a division's final round read, as the module names it: the module, the
#: subscriber, and what the test calls it. Each hands back the round the division's final
#: classification is drawn against, or None, on the connection of the completion's save.
_FINAL_ROUND_PARTS = (
    ("leaguebot.results.services.standings.season_standings_posts", "final_round_on",
     "results' last round with results"),
)

#: Each module's part of a season's configuration confirmed, as the module names it: the module,
#: the subscriber, and what the test calls it, in the order the builder signs them up. Each keeps
#: what the module fixes for the season, on the connection of the confirmation's save.
_CONFIGURATION_CONFIRMED_PARTS = (
    ("leaguebot.signup.services.signup_module_service", "configuration_confirmed_on",
     "signup's season settings"),
)

#: Each module's part of a driver turned down, as the module names it: the module, the
#: subscriber, and what the test calls it, in the order the builder signs them up. Each lets go of
#: what the module holds as the driver's approval, on the connection of the save that turns them
#: down.
_DRIVER_TURNED_DOWN_PARTS = (
    ("leaguebot.signup.services.signup_module_service", "driver_turned_down_on",
     "signup's withdrawal of the approval"),
)

#: The steps of an amendment attendance defines, by name, as the amendment names them today.
_ATTENDANCE_AMENDMENT_STEPS = [
    "announce", "clean_up_check_in", "close_without_call", "delete_message", "distribute",
    "lock_call", "mark_cleared", "post_call", "take_down_call",
]


async def test_the_builder_signs_every_module_up(tmp_path, monkeypatch):
    """Straight after `build`, each module's part of a moment core declares is signed up to that
    moment's hook on the bot, in the order core calls them, and nothing has been called yet. The
    bot's in-memory state cleared: the penalty reviews results holds for staging, then the reasons
    signup awaits from an admin. A cancellation of rounds: what stands in its way, results' refusal
    of a round whose open submission has accepted results; the rounds called off, results' close of
    their empty submissions, then attendance's take-down of their calls; the notices posted for a
    division, each handed the save's connection, the scope and the division told, attendance's
    check-in notice, then weather's forecast note, then results' results note; and the steps a
    module plans, results' deletion of a submission channel, attendance's take-down of a call,
    attendance's reading of a round's check-in record, then attendance's check-in notice, weather's
    forecast note and results' results note. A round's timing windows read: attendance's window,
    then weather's, each handed whether the round is being amended. A round amended: inside the
    save, weather's withdrawal of the forecasts the amendment withdraws, then attendance's reopening
    of its check-in; as the round is armed again, the posts each owes, attendance's then weather's,
    then the clean-ups due a day after the round, weather's then attendance's; the steps each
    defines, weather's then attendance's, under the names the amendment gives them today; and the
    words an outcome gives a step that was not done, weather's for a forecast. A season's end: what
    stands in its way, results' refusal while a round is being amended; the drivers it returns,
    signup's ending of their wizards in the save, and the jobs signup plans for a driver returned;
    the jobs that close what a module holds open,
    signup's close of its window; the steps each module defines for it, signup's notice and close,
    its window's close, the Sign Up button's deletion and the closed post, then weather's deletion
    of a forecast; the jobs that clear what test mode posted, weather's; and the drivers deleted,
    image's discard of their portraits. A season, division or round deleted, each module deleting
    its own rows: of the rounds, attendance's, results' then weather's; of the divisions,
    attendance's then results'; of the season, results' then signup's. A driver deleted, each module
    deleting or letting go of its own rows of them, attendance's then results'; and a driver merged
    into another, each re-pointing its own, attendance's then results'. The bot packed, each module
    clearing its own rows tied to the server left behind: signup's, weather's, attendance's then
    results'. A round's results made final, handed the closing's connection and the rounds' ids:
    results' marking of the drivers who raced them as former drivers. Test mode turned on, handed
    the save's connection and the current season's id: results' seeding of its points
    configurations. Placements confirmed, handed the approval's save's connection and a division's
    id and name: the jobs results plans for its opening standings, then attendance's for its opening
    sheet; and the steps each defines, results' opening standings, then attendance's opening sheet.
    A division's final round read, handed the completion's save's connection and the division's id:
    results' reading of its last round with results. A season completed, handed the completion's
    save's connection, a division's id and name and that round's id: the jobs results plans for its
    final standings, then attendance's for its final sheet; and the steps each defines, results'
    final standings, then attendance's final sheet. A season's configuration confirmed, handed the
    confirmation's save's connection, the season's id and the queue's clock: signup's keeping of
    its settings for the season. A driver turned down, handed the save's connection and the
    driver's profile id: signup's withdrawal of their signup's approval."""
    import importlib

    import leaguebot.__main__ as entry_point
    from leaguebot.core.models.cancellation import SCOPE_ROUND
    from leaguebot.results.services.review import review_state
    from leaguebot.signup.services import signup_views
    from leaguebot.weather.services import forecast_changes

    called: list[str] = []
    for module, name, part in (
        (review_state, "clear_held_reviews", "penalty reviews"),
        (signup_views, "clear_pending_reasons", "pending reasons"),
    ):
        spy = (lambda part: lambda: called.append(part))(part)
        monkeypatch.setattr(module, name, spy)
        if hasattr(entry_point, name):
            monkeypatch.setattr(entry_point, name, spy)
    cancellation_handed: list[tuple] = []
    for _hook, module_name, name, part in _CANCELLATION_PARTS:
        async def awaited_spy(*args, part=part, **_kwargs):
            called.append(part)
            cancellation_handed.append(args)
        monkeypatch.setattr(importlib.import_module(module_name), name, awaited_spy,
                            raising=False)
    for module_name, name, part in _WINDOWS_PARTS:
        async def window_spy(*_args, part=part, amending, **_kwargs):
            called.append(f"{part}, amending {amending}")
        monkeypatch.setattr(importlib.import_module(module_name), name, window_spy,
                            raising=False)

    for _hook, module_name, name, part in _AMENDMENT_PARTS:
        async def amendment_spy(*_args, part=part, **_kwargs):
            called.append(part)
        monkeypatch.setattr(importlib.import_module(module_name), name, amendment_spy,
                            raising=False)
    monkeypatch.setattr(forecast_changes, "forecast_failure",
                        lambda *_args, **_kwargs: called.append("weather's words"))
    for _hook, module_name, name, part in _SEASON_END_PARTS:
        async def season_end_spy(*_args, part=part, **_kwargs):
            called.append(part)
        monkeypatch.setattr(importlib.import_module(module_name), name, season_end_spy,
                            raising=False)
    handed: list[tuple] = []
    for module_name, name, part in (
        *((module_name, name, part) for _hook, module_name, name, part in _DELETE_PARTS),
        *_PACK_PARTS,
        *_FINAL_PARTS,
        *_TEST_MODE_PARTS,
        *_PLACEMENTS_PARTS,
        *_FINAL_ROUND_PARTS,
        *_SEASON_COMPLETED_PARTS,
        *_CONFIGURATION_CONFIRMED_PARTS,
        *_DRIVER_TURNED_DOWN_PARTS,
        *_PHASES_DRAWN_PARTS,
    ):
        async def delete_spy(*args, part=part, **_kwargs):
            called.append(part)
            handed.append(args)
        monkeypatch.setattr(importlib.import_module(module_name), name, delete_spy,
                            raising=False)
    monkeypatch.setattr(importlib.import_module("leaguebot.signup.services.closing_steps"),
                        "driver_returned_jobs",
                        lambda *_args, **_kwargs: called.append("signup's jobs") or (),
                        raising=False)
    weather_steps = sorted(
        {*forecast_changes.forecast_steps(now=lambda: _NOW),
         "clean_up_forecast", "notify_invalidation", "rerun_phase"}
    )

    bot = await built_bot(tmp_path, now=_NOW)
    assert called == []

    bot.in_memory_state_cleared.call()

    assert called == ["penalty reviews", "pending reasons"]
    called.clear()
    await bot.cancellation_refused.call(None, SCOPE_ROUND, ())
    assert called == ["results' refusal"]
    called.clear()
    await bot.rounds_called_off.call(None, SCOPE_ROUND, ())
    assert called == ["results' close", "attendance's take-down"]
    called.clear()
    cancellation_handed.clear()
    told = {"division_id": 0, "division_name": "Pro", "season_id": 0}
    await bot.cancellation_notices.call(None, SCOPE_ROUND, told)
    assert called == [part for each, _module, _name, part in _CANCELLATION_PARTS
                      if each == "cancellation_notices"]
    # Each part is handed the save's connection, the scope, then the division told.
    assert cancellation_handed == [(None, SCOPE_ROUND, told)] * len(called)
    assert [sorted(steps) for steps in bot.cancellation_steps.call()] == [
        ["delete_channel"], ["take_down_call"], ["read_check_in"], ["notify_checkin"],
        ["notify_forecast"], ["notify_results"],
    ]
    called.clear()
    await bot.round_windows_read.call(amending=True)
    assert called == ["attendance's window, amending True", "weather's window, amending True"]
    for hook in ("round_amended", "round_armed", "round_clean_ups_due"):
        called.clear()
        await getattr(bot, hook).call(None, 0)
        assert called == [part for each, _module, _name, part in _AMENDMENT_PARTS if each == hook]
    called.clear()
    handed.clear()
    await bot.phases_drawn_read.call([7], db=None)
    assert called == ["weather's phases drawn"]
    assert handed == [(bot.db_path, [7])]
    assert [sorted(steps) for steps in bot.amendment_steps.call()] == [
        weather_steps, _ATTENDANCE_AMENDMENT_STEPS
    ]
    called.clear()
    bot.amendment_step_failure.call(None)
    assert called == ["weather's words"]
    for hook in dict.fromkeys(each[0] for each in _SEASON_END_PARTS):
        called.clear()
        await getattr(bot, hook).call()
        assert called == [part for each, _module, _name, part in _SEASON_END_PARTS if each == hook]
    called.clear()
    bot.driver_returned_jobs.call({"user_id": "1", "state": "PENDING_ADMIN_APPROVAL"}, notice="")
    assert called == ["signup's jobs"]
    assert [
        [job.name for job in jobs] for jobs in bot.season_end_closes.call(cause="season end")
    ] == [["close_window"]]
    assert [sorted(steps) for steps in bot.season_end_steps.call(line_prefix=None)] == [
        ["close_signup", "close_window", "delete_button", "post_closed", "signup_notice"],
        ["delete_forecast"],
    ]
    assert not hasattr(bot, "season_end_untold")
    for hook in dict.fromkeys(each[0] for each in _DELETE_PARTS):
        called.clear()
        handed.clear()
        await getattr(bot, hook).call(None, *_DELETE_PAYLOADS[hook])
        assert called == [part for each, _module, _name, part in _DELETE_PARTS if each == hook]
        # Each part is handed the connection, then what the delete names.
        assert handed == [(None, *_DELETE_PAYLOADS[hook])] * len(called)
    called.clear()
    handed.clear()
    await bot.bot_packed.call(None)
    assert called == [part for _module, _name, part in _PACK_PARTS]
    # Each part is handed the pack's connection, and nothing else.
    assert handed == [(None,)] * len(called)
    called.clear()
    handed.clear()
    await bot.rounds_made_final.call(None, [0])
    assert called == [part for _module, _name, part in _FINAL_PARTS]
    # Each part is handed the closing's connection, then the ids of the rounds made final.
    assert handed == [(None, [0])] * len(called)
    called.clear()
    handed.clear()
    await bot.test_mode_turned_on.call(None, 0)
    assert called == [part for _module, _name, part in _TEST_MODE_PARTS]
    # Each part is handed the save's connection, then the current season's id.
    assert handed == [(None, 0)] * len(called)
    called.clear()
    handed.clear()
    await bot.placements_confirmed.call(None, 0, "Pro")
    assert called == [part for _module, _name, part in _PLACEMENTS_PARTS]
    # Each part is handed the save's connection, then the division's id and name.
    assert handed == [(None, 0, "Pro")] * len(called)
    assert [sorted(steps) for steps in bot.placements_confirmed_steps.call()] == [
        ["opening_standings"], ["opening_sheet"]
    ]
    called.clear()
    handed.clear()
    await bot.final_round_read.call(None, 0)
    assert called == [part for _module, _name, part in _FINAL_ROUND_PARTS]
    # Each part is handed the save's connection, then the division's id.
    assert handed == [(None, 0)] * len(called)
    called.clear()
    handed.clear()
    await bot.season_completed.call(None, 0, "Pro", 1)
    assert called == [part for _module, _name, part in _SEASON_COMPLETED_PARTS]
    # Each part is handed the save's connection, then the division's id and name and the round's.
    assert handed == [(None, 0, "Pro", 1)] * len(called)
    assert [sorted(steps) for steps in bot.season_completed_steps.call()] == [
        ["final_standings"], ["final_sheet"]
    ]
    called.clear()
    handed.clear()
    await bot.configuration_confirmed.call(None, 0, _NOW)
    assert called == [part for _module, _name, part in _CONFIGURATION_CONFIRMED_PARTS]
    # Each part is handed the save's connection, then the season's id and the queue's clock.
    assert handed == [(None, 0, _NOW)] * len(called)
    called.clear()
    handed.clear()
    await bot.driver_turned_down.call(None, 0)
    assert called == [part for _module, _name, part in _DRIVER_TURNED_DOWN_PARTS]
    # Each part is handed the save's connection, then the driver's profile id.
    assert handed == [(None, 0)] * len(called)


async def test_the_builder_signs_signup_up_to_a_driver_s_account_changed(tmp_path, monkeypatch):
    """Straight after `build`, signup's part of a driver's account changed (`/driver reassign`'s
    save) is signed up to the hook on the bot, and nothing has been called yet. Called, it is
    handed the save's connection, then the account replaced and the new one, and what it hands
    back is the jobs it plans; and the step definitions it hands back for those jobs, by name, are
    signed up to the companion hook, one set, signup's."""
    import importlib

    handed: list[tuple] = []

    async def _signup_s_part(*args, **_kwargs):
        handed.append(args)
        return ()

    monkeypatch.setattr(
        importlib.import_module("leaguebot.signup.services.signup_module_service"),
        "driver_account_changed_on", _signup_s_part, raising=False,
    )

    bot = await built_bot(tmp_path, now=_NOW)
    assert handed == []

    assert await bot.driver_account_changed.call(None, "106", "9001") == [()]
    assert handed == [(None, "106", "9001")]
    [steps] = bot.driver_account_changed_steps.call()
    assert steps and all(isinstance(name, str) for name in steps)


#: The windows the league configures in the pair test: attendance's three lead times, and
#: weather's three horizons, each unlike its default.
_ATTENDANCE_SET = (3, 12, 1)
_WEATHER_SET = (4, 1, 3)


@pytest.mark.parametrize("amending", [False, True], ids=["the approval", "the amendment"])
@pytest.mark.parametrize("weather_on", [False, True], ids=["weather off", "weather on"])
@pytest.mark.parametrize("attendance_on", [False, True], ids=["attendance off", "attendance on"])
async def test_the_windows_hook_hands_each_caller_today_s_pair(
    tmp_path, attendance_on, weather_on, amending
):
    """A league whose check-in calls go out 3 days ahead, their last notice 12 hours and their
    deadline 1 hour before the round, and whose forecasts fall 4 days, 1 day and 3 hours before
    it, with attendance and weather each on or off. Read through the hook the builder makes,
    `(attendance, weather)` is what each caller has always received: attendance's window only
    where attendance is on; weather's, for the season's approval, only where weather is on, and
    for an amendment whatever weather's switch, since a forecast posted while weather was on is
    still posted."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.models.round import AttendanceWindows, WeatherWindows
    from leaguebot.core.services.rounds.round_moments import read_round_windows
    from tests.support.change_queue import seed_server

    bot = await built_bot(tmp_path, now=_NOW)
    await seed_server(bot.db_path)
    async with get_connection(bot.db_path) as db:
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled, rsvp_notice_days, "
            "rsvp_last_notice_hours, rsvp_deadline_hours) VALUES (1, ?, ?, ?, ?)",
            (int(attendance_on), *_ATTENDANCE_SET),
        )
        await db.execute(
            "INSERT INTO weather_pipeline_config (id, phase_1_days, phase_2_days, phase_3_hours) "
            "VALUES (1, ?, ?, ?)",
            _WEATHER_SET,
        )
        await db.execute("UPDATE server_configs SET weather_module_enabled = ?",
                         (int(weather_on),))
        await db.commit()

    pair = await read_round_windows(bot.round_windows_read, amending=amending)

    assert pair == (
        AttendanceWindows(*_ATTENDANCE_SET) if attendance_on else None,
        WeatherWindows(*_WEATHER_SET) if weather_on or amending else None,
    )


async def test_login_starts_the_retry_loop_once(tmp_path, monkeypatch):
    """The bot `build` makes starts the retry loop for undelivered log lines once, from
    `setup_hook` at login, and not before: a log line that fails to post is retried until it is
    delivered (architecture.md, "Start-up runs once")."""
    bot = await built_bot(tmp_path, now=_NOW)
    loop = bot.get_cog("RetryCog").retry_loop
    assert not loop.is_running()
    starts = []
    monkeypatch.setattr(loop, "start", lambda *args, **kwargs: starts.append(args))

    await bot.setup_hook()

    assert starts == [()]


#: Core's own start-up steps by name, in the order the builder signs them up to `bot.starting`.
_CORE_STEPS = ["expired review prompts", "hub"]

#: Each module's start-up steps by name, in its own order, keyed by its switch key, the modules in
#: the order the builder signs them up: the core specification's.
_MODULE_STEPS = {
    "signup": ["signup close timer", "wizards"],
    "results": ["amendment sweep", "orphaned amend channels", "orphaned submission channels",
                "missed round starts"],
    "attendance": ["missed check-in calls", "RSVP views and deadlines",
                   "missed check-in clean-ups"],
    "weather": ["missed phases", "missed forecast clean-ups"],
    "images": ["portrait refresh job"],
}


#: The module steps whose recovery is a function handed the bot, with where that function
#: lives and its name: in its module's wiring, or, for results' and attendance's three
#: recoveries each, the module's services.
_RECOVERIES_OF_THE_STEPS = {
    "portrait refresh job": ("leaguebot.image.wiring", "recover_portrait_refresh_job"),
    "orphaned amend channels": ("leaguebot.results.services.round_amendment.amend_end_changes",
                                "recover_orphaned_amend_channels"),
    "orphaned submission channels": ("leaguebot.results.services.submission.submission_recovery",
                                     "recover_orphaned_submission_channels"),
    "missed round starts": ("leaguebot.results.services.submission.submission_recovery",
                            "recover_missed_round_starts"),
    "missed phases": ("leaguebot.weather.wiring", "recover_missed_phases"),
    "missed forecast clean-ups": ("leaguebot.weather.wiring",
                                  "recover_missed_forecast_clean_ups"),
    "missed check-in calls": ("leaguebot.attendance.services.check_in_recovery",
                              "recover_missed_check_in_calls"),
    "RSVP views and deadlines": ("leaguebot.attendance.services.check_in_recovery",
                                 "recover_rsvp_views_and_deadlines"),
    "missed check-in clean-ups": ("leaguebot.attendance.services.check_in_recovery",
                                  "recover_missed_check_in_clean_ups"),
}


#: The steps whose recovery is handed the bot's change queue as well, which a service does not look
#: up on the bot (architecture.md, "Services stay attached to the bot, and are built in one place").
_RECOVERIES_GIVEN_THE_QUEUE = frozenset(
    {"orphaned amend channels", "orphaned submission channels", "missed round starts",
     "missed check-in calls", "RSVP views and deadlines", "missed check-in clean-ups"}
)


async def test_the_builder_orders_the_start_up_steps(tmp_path, monkeypatch):
    """`build` on paths under `tmp_path` signs core's start-up steps and each module's up to
    `bot.starting`, and the sweep is then run over it with every module on, so the order is read
    from what start-up runs rather than from the source (architecture.md, "Timed work and
    restarts"). Core's two come first, the expired review prompts and then the hub; then each
    module's, the modules in the core specification's order (signup, results, attendance, weather,
    image), each module's steps in its own order. Its rules, each within one module:

    - **orphaned amend channels before orphaned submission channels** (#345): an amendment open at
      the restart has its unapproved corrections in the database, and a submission channel whose
      results were saved but never posted posts its standings from it, so swept the other way round
      those standings would publish the corrections the revert then takes back;
    - **missed round starts after both** (#450): a round's submission cleared and asked open again
      by the submission channels' recovery is then in hand on the queue, and the catch-up of the
      round starts missed while the bot was down leaves it to that opening rather than ask another;
    - **missed check-in calls, then the RSVP deadlines, then the missed check-in clean-ups**: a call
      comes before its deadline, and a check-in whose deadline and clean-up both passed while the
      bot was down is distributed before it is taken down;
    - **missed phases before the missed forecast clean-ups**.

    The names are read from the builder's sign-ups (`StartingHook.subscribe` watched as it is
    called) and from the steps each module's wiring function gives; each module is signed up under
    its switch key, the name its switch reader is signed up under with the module service. The
    sweep then runs each step's own recovery, stubbed to record what it was handed, once, for the
    bot, in the same order, so that a name cannot stand over another step's recovery: the expired
    review prompts, a review left standing, ask the bot's queue for its take-down ending `restart`;
    the hub hands its recovery the bot, its queue and its hub options; the signup close timer, a
    close still ahead on record, re-arms it on the bot's scheduler (the step reads the host's clock
    and takes no `now`, so the close is set a day after that clock); the wizards are recovered by
    the bot's wizard service; the amendment sweep arms the bot's scheduler's sweep; and each other
    step hands its recovery the bot: results' three in results' services and attendance's three in
    `attendance.services.check_in_recovery`, each of these six also handed the bot's queue, since a
    service does not look the queue up on the bot; each of the others in its module's wiring.
    The RSVP views' recovery is handed the way to build the check-in buttons too (`view_of`), made
    by attendance's wiring, so that `LeagueBot` declares no `check_in_view` for it to look up.
    Nothing is posted to the log channel.
    """
    import importlib
    from contextlib import asynccontextmanager

    import leaguebot.core.services.upkeep.start_up as start_up_module
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.services.seasons import season_review_changes
    from leaguebot.core.services.modules import hub_service
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.utils.hooks import StartingHook
    from leaguebot.signup.services.wizard_service import WizardService
    from tests.support.review_prompts import store_review_prompt

    ran: list[tuple] = []

    def recovery(name: str):
        async def recover(*args, **kwargs) -> None:
            ran.append((name, args, kwargs))
        return recover

    def scheduling(name: str):
        def schedule(*args, **kwargs) -> None:
            ran.append((name, args, kwargs))
        return schedule

    for home, function in _RECOVERIES_OF_THE_STEPS.values():
        monkeypatch.setattr(importlib.import_module(home), function, recovery(function))
    monkeypatch.setattr(hub_service, "recover_hub", recovery("recover_hub"))
    monkeypatch.setattr(season_review_changes, "take_review_down", recovery("take_review_down"))
    monkeypatch.setattr(WizardService, "recover_wizards", recovery("recover_wizards"))
    for method in ("schedule_amendment_sweep", "schedule_signup_close_timer"):
        monkeypatch.setattr(SchedulerService, method, scheduling(method))
    monkeypatch.setattr(SchedulerService, "has_job", lambda self, job_id: False)

    signed_up: list[tuple] = []
    subscribe = StartingHook.subscribe

    def watched(self, steps, **kwargs):
        signed_up.append((kwargs, steps))
        return subscribe(self, steps, **kwargs)

    monkeypatch.setattr(StartingHook, "subscribe", watched)
    bot = await built_bot(tmp_path, now=_NOW)
    close_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    async with get_connection(bot.db_path) as db:
        await db.execute("INSERT INTO signup_module_config (id, close_at) VALUES (1, ?)",
                         (close_at,))
        await store_review_prompt(db, season_id=1, channel_id=7, message_id=42, reviewer_id=9,
                                  review="/season config-review")
        await db.commit()

    assert [kwargs for kwargs, _ in signed_up] == (
        [{"name": name} for name in _CORE_STEPS] + [{"module": key} for key in _MODULE_STEPS])
    async with get_connection(bot.db_path) as db:
        for key in _MODULE_STEPS:
            await bot.module_service.is_enabled_on(db, key)  # raises where no reader is signed up
    for kwargs, steps in signed_up:
        if "module" in kwargs:
            assert [name for name, _ in steps()] == _MODULE_STEPS[kwargs["module"]], kwargs

    class _Modules:
        def __init__(self) -> None:
            self.asked: list[str] = []

        async def is_enabled(self, module: str) -> bool:
            self.asked.append(module)
            return True

    class _Queue:
        @asynccontextmanager
        async def sweeping(self):
            yield

        async def start(self) -> None:
            pass

        def release(self) -> None:
            pass

    class _Scheduler:
        def start(self, *, paused: bool) -> None:
            pass

        def resume(self) -> None:
            pass

    lines: list[str] = []

    async def post_log(content: str, **_) -> None:
        lines.append(content)

    modules = _Modules()
    start_up = importlib.reload(start_up_module).start_up
    await start_up(starting=bot.starting, modules=modules, change_queue=_Queue(),
                   scheduler=_Scheduler(), post_log=post_log)

    assert modules.asked == list(_MODULE_STEPS)
    scheduler = bot.scheduler_service
    expected = {
        "expired review prompts": ("take_review_down", (bot.change_queue, bot.db_path, 42,
                                                        season_review_changes.RESTART), {}),
        "hub": ("recover_hub", (bot,), {"queue": bot.change_queue,
                                        "hub_options": bot.hub_options}),
        "signup close timer": ("schedule_signup_close_timer", (scheduler, close_at), {}),
        "wizards": ("recover_wizards", (bot.wizard_service,), {}),
        "amendment sweep": ("schedule_amendment_sweep", (scheduler,), {}),
    } | {
        step: (function, (bot,),
               {"queue": bot.change_queue} if step in _RECOVERIES_GIVEN_THE_QUEUE else {})
        for step, (_, function) in _RECOVERIES_OF_THE_STEPS.items()
    }
    order = _CORE_STEPS + [step for steps in _MODULE_STEPS.values() for step in steps]
    assert sorted(expected) == sorted(order)
    # The catch-up of the missed round starts is handed the moment the bot came up, as a callable,
    # the one the `results` job's handler is handed.
    [caught_up] = [kwargs for function, _args, kwargs in ran if function == "recover_missed_round_starts"]
    assert caught_up.pop("came_up_at")() == bot.came_up_at
    # The recovery of the check-in calls' buttons is handed the way to build them, a callable the
    # wiring makes, rather than looking it up on the bot: core declares no `check_in_view`.
    [re_armed] = [kwargs for function, _args, kwargs in ran
                  if function == "recover_rsvp_views_and_deadlines"]
    assert callable(re_armed.pop("view_of"))
    from leaguebot.core.utils.league_bot import LeagueBot
    assert not hasattr(LeagueBot, "check_in_view")
    assert "check_in_view" not in LeagueBot.__annotations__
    assert ran == [expected[step] for step in order]
    assert lines == []


class _Tree:
    """The fake bot's command tree: its sync records itself, or raises *refusal* where given."""

    def __init__(self, calls: list[str], refusal: BaseException | None = None) -> None:
        self._calls = calls
        self._refusal = refusal

    async def sync(self) -> list:
        self._calls.append("commands synced")
        if self._refusal is not None:
            raise self._refusal
        return []


async def _run_main(tmp_path, monkeypatch, calls: list[str], tree: _Tree):
    """Drive `main` with the staged restore, the migrations, the builder, the queue's hold and the
    bot's connection each stubbed to record its call in *calls*, then report the bot ready with
    `start_up` stubbed to record itself and say it ran. Gives the fake bot, its log channel's lines
    in `bot.lines`, and the `start_up` stub."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import leaguebot.__main__ as bot_module
    from leaguebot.core.db import database
    from leaguebot.core.services.upkeep import backup_service

    settings = bot_module.Settings(token="not-a-real-token", db_path=str(tmp_path / "bot.db"),
                                   jobstore_path=str(tmp_path / "jobs.db"))

    def restore(db_path: str, jobstore_path: str) -> bool:
        calls.append("restore")
        return False

    async def migrate(db_path: str) -> None:
        calls.append("migrations")

    async def close_connections() -> None:
        pass

    class _Queue:
        def hold(self) -> None:
            calls.append("hold")

    class _OutputRouter:
        async def post_log(self, content: str, **_) -> None:
            bot.lines.append(content)

    class _Bot:
        user = SimpleNamespace(id=1)
        starting = object()
        module_service = object()
        change_queue = _Queue()
        scheduler_service = object()
        output_router = _OutputRouter()
        handlers: dict = {}

        def __init__(self) -> None:
            self.tree = tree
            self.lines: list[str] = []

        def event(self, handler):
            self.handlers[handler.__name__] = handler
            return handler

        def add_listener(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def start(self, token: str) -> None:
            calls.append("connect")

    bot = _Bot()

    async def build(settings_) -> _Bot:
        calls.append("build")
        return bot

    monkeypatch.setattr(bot_module, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot_module, "read_settings", lambda environ: settings)
    monkeypatch.setattr(bot_module, "install_late_autocomplete_filter", lambda: None)
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)
    monkeypatch.setattr(bot_module, "build", build)
    for module in (backup_service, bot_module):
        monkeypatch.setattr(module, "apply_staged_restore", restore, raising=False)
    for module in (database, bot_module):
        monkeypatch.setattr(module, "run_migrations", migrate, raising=False)
        monkeypatch.setattr(module, "close_connections", close_connections, raising=False)

    await bot_module.main()

    from leaguebot.core.services.upkeep import start_up as start_up_module

    start_up = AsyncMock(side_effect=lambda **kwargs: calls.append("start-up") or True)
    monkeypatch.setattr(start_up_module, "start_up", start_up)
    monkeypatch.setattr(bot_module, "start_up", start_up, raising=False)
    await bot.handlers["on_ready"]()
    return bot, start_up


async def test_migrations_run_before_the_bot_connects(tmp_path, monkeypatch):
    """`main` swaps in a staged restore, then applies the migrations, then builds the bot, then
    holds its change queue, so that an interaction arriving before the start-up's catch-up has
    asked its own waits, and only then connects it to Discord (architecture.md, "Start-up runs
    once, in one function", steps 2 to 4). The bot then reports itself ready: the handler `main`
    registered runs the start-up, handed the bot's starting hook, its module service, its change
    queue, its scheduler service and its log channel's `post_log`, and only after it syncs the slash
    commands. Takes the place of the planned `test_main_holds_the_queue_before_the_bot_connects`."""
    calls: list[str] = []

    bot, start_up = await _run_main(tmp_path, monkeypatch, calls, _Tree(calls))

    assert calls[:5] == ["restore", "migrations", "build", "hold", "connect"]
    start_up.assert_awaited_once_with(
        starting=bot.starting, modules=bot.module_service, change_queue=bot.change_queue,
        scheduler=bot.scheduler_service, post_log=bot.output_router.post_log)
    assert calls[5:] == ["start-up", "commands synced"]
    assert bot.lines == []


async def test_a_refused_command_sync_is_reported_to_the_log_channel(tmp_path, monkeypatch,
                                                                      caplog):
    """The bot reports itself ready, its start-up runs, and Discord refuses the slash command sync
    (`discord.HTTPException`). The host's log has the error, as before, and one line goes to the log
    channel naming the slash command sync and the kind of fault and saying the detail is in the
    host's log (core specification, "When a command fails", the bot connecting), pinned word for
    word, its ❌ mark included, in the shape of a failing start-up step's line."""
    from types import SimpleNamespace

    import discord

    calls: list[str] = []
    refusal = discord.HTTPException(SimpleNamespace(status=400, reason="Bad Request"),
                                    "commands refused")

    with caplog.at_level(logging.DEBUG, logger="leaguebot.__main__"):
        bot, _ = await _run_main(tmp_path, monkeypatch, calls, _Tree(calls, refusal))

    assert calls[-2:] == ["start-up", "commands synced"]
    assert bot.lines == [
        "❌ The bot's start-up step `slash command sync` failed — HTTPException. "
        "The details are in the host's log."]
    assert [r for r in caplog.records
            if r.name == "leaguebot.__main__" and r.levelno >= logging.ERROR
            and "commands refused" in r.getMessage()], "the host's log has no line for the refusal"


#: Each module's section of a season review, as the module names it, and what the test calls it,
#: in the order the builder signs them up: the order the review reads them in.
_REVIEW_SECTION_PARTS = (
    ("leaguebot.signup.services.signup_module_service", "review_section", "signup's section"),
    ("leaguebot.attendance.services.attendance_hooks", "review_section",
     "attendance's section"),
    ("leaguebot.results.services.points.season_points_service", "review_section", "results' section"),
    ("leaguebot.weather.services.weather_config_service", "review_section", "weather's section"),
)


async def test_the_builder_signs_every_module_up_to_the_review_sections(tmp_path, monkeypatch):
    """Straight after `build`, each module's section of a season review is signed up to the hook
    a review gathers its lines through, nothing called yet: signup's, attendance's, results' then
    weather's, each handed which review is gathered, the season and the divisions it shows."""
    import importlib

    from leaguebot.core.services.modules.review_sections import Review

    called: list[tuple] = []
    for module_name, name, part in _REVIEW_SECTION_PARTS:
        async def section_spy(*_args, part=part, review, season_id, division_ids, **_kwargs):
            called.append((part, review, season_id, tuple(division_ids)))
        monkeypatch.setattr(importlib.import_module(module_name), name, section_spy,
                            raising=False)

    bot = await built_bot(tmp_path, now=_NOW)
    assert called == []

    await bot.review_sections_read.call(review=Review.PLACEMENTS, season_id=7,
                                        division_ids=[70, 71])

    assert called == [
        (part, Review.PLACEMENTS, 7, (70, 71)) for _module, _name, part in _REVIEW_SECTION_PARTS
    ]


async def test_the_builder_signs_results_up_to_a_season_approved(tmp_path, monkeypatch):
    """Straight after `build`, results' points snapshot is signed up to the hook a season's
    approval is saved through, nothing called yet: handed the save's connection, then the
    season's id."""
    from leaguebot.results.services.points import season_points_service

    called: list[tuple] = []

    async def snapshot_spy(*args, **kwargs):
        called.append((args, kwargs))

    monkeypatch.setattr(season_points_service, "snapshot_configs_to_season_on", snapshot_spy)

    bot = await built_bot(tmp_path, now=_NOW)
    assert called == []

    await bot.season_approved.call(None, 7)

    assert called == [((None, 7), {})]


async def test_the_builder_signs_results_up_to_a_confirmation_refused(tmp_path, monkeypatch):
    """Straight after `build`, results' refusals are signed up to the hook a season's confirmation
    is refused through, nothing called yet: handed the league's modules and the database, then
    what the hook is called with (the season, and which confirmation is judged)."""
    from leaguebot.results.services.points import season_points_service

    called: list[tuple] = []

    async def refusals_spy(*args, **kwargs):
        called.append((args, kwargs))
        return []

    monkeypatch.setattr(season_points_service, "confirmation_refusals", refusals_spy,
                        raising=False)

    bot = await built_bot(tmp_path, now=_NOW)
    assert called == []

    await bot.confirmation_refused.call(7, "the placements review")

    assert called == [((bot.module_service, bot.db_path, 7, "the placements review"), {})]
