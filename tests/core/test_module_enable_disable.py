"""`/module enable` and `/module disable` — the dependencies between the bot's modules.

Issue #208. `module_cog.py` sat at 31.9%. It is the command that decides which of the bot's
five modules a league is running, and the rules it enforces are the ones that keep the modules
from being switched on in an order that cannot work.

**The dependencies are the substance.** Attendance requires Results & Standings, because
attendance points are distributed after post-race penalties are approved — with Results off
there is no approval to hang them on. Each guard is tested with its own refusal, and the refusal
has to name what is missing rather than merely refusing: a manager reading "cannot be enabled"
has nowhere to go.

**Enabling Results or Attendance is refused once a season's placements are confirmed** (FR-003). Both change
how a round is scored, and turning one on mid-season would score the remaining rounds by
different rules than the ones already run — the standings would then be a mixture nobody could
reproduce.

**Every enable and disable writes an audit entry.** No module is enabled once a season's
placements are confirmed (issue #220); that gate stands in front of every handler here and is
tested in `test_module_stage_gates.py`, so weather no longer catches up on a running season.

Enabling something already enabled is a warning rather than an error, and does no work — a
manager running the command twice must not get two audit entries or a second set of jobs.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.module_cog import ModuleCog
from leaguebot.framework.db.database import get_connection, run_migrations
from tests.support.change_queue import MEMBER_ID
from tests.support.undecorate import undecorate
from tests.support.switches import answer_switch

SERVER_ID = 9608
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "modules.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


def _division(div_id: int, name: str):
    return SimpleNamespace(id=div_id, name=name, tier=1)


def _make_cog(
    db_path: str,
    *,
    weather_enabled: bool = False,
    results_enabled: bool = False,
    attendance_enabled: bool = False,
    season=None,
    divisions=None,
) -> ModuleCog:
    bot = MagicMock()
    bot.db_path = db_path

    bot.module_service = MagicMock()
    answer_switch(bot.module_service, "weather", weather_enabled)
    answer_switch(bot.module_service, "results", results_enabled)
    answer_switch(bot.module_service, "attendance", attendance_enabled)

    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division(11, "Division 1")]
    )
    bot.season_service.get_division_rounds = AsyncMock(return_value=[])

    bot.round_jobs = MagicMock()
    bot.round_jobs.cancel_live_rounds = AsyncMock(return_value=None)

    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Admin"
    interaction.user.__str__ = lambda self: "Admin#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, new_value, old_value FROM audit_entries "
            " ORDER BY id",
        )
        return [dict(r) for r in await cursor.fetchall()]


async def _weather_flag(db_path: str) -> int:
    """The weather module's switch, as the league's one server holds it."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT weather_module_enabled FROM server_configs")
        return (await cursor.fetchone())["weather_module_enabled"]


def _season():
    return SimpleNamespace(id=1, season_number=1)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------
#
# Weather's switch is weather's own change on the change queue (#283, S11b): `/module enable
# weather` and `/module disable weather` are acknowledged at once and carried out by the queue.
# Each test drives the command's handler on the bot the entry point builds, with the real queue
# and "now" pinned, and runs the queue.

#: The member as a saved log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"


async def _weather_league(tmp_path, *, on: bool):
    """A league whose settings run on the queue, the weather module *on* or off, and the
    module cog over its bot."""
    from tests.support.settings_league import settings_league

    league = await settings_league(tmp_path, modules=("weather",) if on else ())
    return league, ModuleCog(league.bot)


def _asked(league, verb: str):
    """`/module <verb>` as Admin, the member the league's lines name."""
    from tests.support.settings_league import interaction

    return interaction(league.bot, f"module {verb}")


async def test_weather_is_enabled(tmp_path):
    """Between seasons, with no season running: there are no divisions to check, and a league
    setting the bot up before its first season must not be blocked. The member is acknowledged
    at once, and once the change has run the module is on, and the reply and the Success line
    keep today's words."""
    from tests.support.change_queue import acknowledgement, run_queue
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _weather_league(tmp_path, on=False)
    asked = _asked(league, "enable")

    await cog._enable_weather(asked)
    assert acknowledgement(asked).startswith("⏳ Turning the weather module on. ")
    await run_queue(league.bot)

    assert await _weather_flag(league.db_path) == 1
    assert last_said(asked) == "✅ Weather module enabled."
    assert await log_lines(league) == [f"{NAMED} | /module enable weather | Success"]


async def test_enabling_weather_twice_does_no_work(tmp_path):
    """A manager running the command again must not get a second audit entry or a second
    set of scheduled jobs. The refusal reaches the log channel as one line in the standard
    form."""
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _weather_league(tmp_path, on=True)
    asked = _asked(league, "enable")

    await cog._enable_weather(asked)

    assert last_said(asked) == "⚠️ Weather module is already enabled."
    assert await _audit(league.db_path) == []
    assert await log_lines(league) == [
        f"⛔ `/module enable` refused for {NAMED} — Weather module is already enabled."
    ]


async def test_enabling_weather_is_audited(tmp_path):
    from tests.support.change_queue import run_queue

    league, cog = await _weather_league(tmp_path, on=False)

    await cog._enable_weather(_asked(league, "enable"))
    await run_queue(league.bot)

    entry = (await _audit(league.db_path))[0]
    assert entry["change_type"] == "MODULE_ENABLE"
    assert json.loads(entry["new_value"])["module"] == "weather"


async def test_disabling_weather_cancels_every_scheduled_job(tmp_path):
    """Leaving them armed would post forecasts for a switched-off module. The switch-off lowers
    the flag and writes its audit entry in one save, and only then removes the season's weather
    jobs, in a step of its own: once the save has run the module is off and audited and no job
    has been touched yet; once the change has run, the jobs are removed once, the reply says so,
    and the Success line is written."""
    from tests.support.change_queue import acknowledgement, run_queue
    from tests.support.settings_league import last_said, log_lines, weather_job_cancels

    league, cog = await _weather_league(tmp_path, on=True)
    asked = _asked(league, "disable")

    await cog._disable_weather(asked)
    assert acknowledgement(asked).startswith("⏳ Turning the weather module off. ")

    await run_queue(league.bot, steps=1)
    assert await _weather_flag(league.db_path) == 0
    assert [entry["change_type"] for entry in await _audit(league.db_path)] == ["MODULE_DISABLE"]
    assert weather_job_cancels(league.round_jobs) == 0

    await run_queue(league.bot)
    assert weather_job_cancels(league.round_jobs) == 1
    assert last_said(asked) == (
        "✅ Weather module disabled. All scheduled weather jobs have been cancelled."
    )
    assert await log_lines(league) == [f"{NAMED} | /module disable weather | Success"]


async def test_disabling_weather_twice_does_no_work(tmp_path):
    from tests.support.settings_league import last_said

    league, cog = await _weather_league(tmp_path, on=False)
    asked = _asked(league, "disable")

    await cog._disable_weather(asked)

    assert "already disabled" in last_said(asked)
    league.round_jobs.cancel_live_rounds.assert_not_awaited()
    assert await _audit(league.db_path) == []


async def test_disabling_weather_is_audited(tmp_path):
    from tests.support.change_queue import run_queue

    league, cog = await _weather_league(tmp_path, on=True)

    await cog._disable_weather(_asked(league, "disable"))
    await run_queue(league.bot)

    entry = (await _audit(league.db_path))[0]
    assert entry["change_type"] == "MODULE_DISABLE"
    assert json.loads(entry["old_value"])["module"] == "weather"


async def test_a_weather_disable_whose_jobs_cannot_be_removed_leaves_weather_off_and_says_so(
    tmp_path,
):
    """Core's round jobs refuse to remove the season's weather jobs: the module stays off with its
    audit entry, the reply says it is off but its jobs could not be removed and do nothing while
    it is off, never that they were cancelled, the Success line and one failure line are written,
    and no job is left stopped on the queue."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _weather_league(tmp_path, on=True)
    league.round_jobs.cancel_live_rounds.side_effect = RuntimeError("the job store refused")
    asked = _asked(league, "disable")

    await cog._disable_weather(asked)
    await run_queue(league.bot)

    assert await _weather_flag(league.db_path) == 0
    assert [entry["change_type"] for entry in await _audit(league.db_path)] == ["MODULE_DISABLE"]
    assert last_said(asked) == (
        "✅ Weather module disabled.\n⚠️ The module is off, but its scheduled jobs could not be "
        "removed; they do nothing while the module is off."
    )
    lines = await log_lines(league)
    assert lines.count(f"{NAMED} | /module disable weather | Success") == 1
    assert [line for line in lines if line.startswith("❌ ")] == [
        f"❌ `/module disable` failed for {NAMED} — RuntimeError. The details are in the host's log."
    ]
    assert await stopped_job(league.db_path) is None


# ---------------------------------------------------------------------------
# Results & Standings
# ---------------------------------------------------------------------------
#
# Results' switch-on is results' own change on the change queue (#283, S11d): `/module enable
# results` keeps the stage refusals and asks `module.on:results`, the member is acknowledged at
# once, and the queue carries it out. Each test drives the command's handler on the bot the entry
# point builds, with the real queue and "now" pinned, and runs the queue.

async def _league(tmp_path, *modules: str):
    """A league whose settings run on the queue, with *modules* on and every other module off,
    and the module cog over its bot."""
    from tests.support.settings_league import settings_league

    league = await settings_league(tmp_path, modules=modules)
    return league, ModuleCog(league.bot)


async def _confirm_a_season(db_path: str) -> None:
    """A season whose placements are confirmed: ACTIVE, the status every stage after the first
    confirmation holds."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-09-17', 'ACTIVE', 5)"
        )
        await db.commit()


async def _flag(db_path: str, table: str) -> int | None:
    """A module's switch on its own configuration table, None where no row holds it."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT module_enabled FROM {table}")
        row = await cursor.fetchone()
    return None if row is None else row["module_enabled"]


def _refused_line(reason: str) -> str:
    """The log channel's line for `/module enable` refused for Admin, because of *reason*."""
    return f"⛔ `/module enable` refused for {NAMED} — {reason}"


#: The refusal of an attendance enable while results is off, as the member reads it.
NEEDS_RESULTS = (
    "❌ The Attendance module requires the Results & Standings module to be enabled first."
)


async def test_results_is_enabled(tmp_path):
    """The member is acknowledged at once, and once the change has run the module is on and
    audited, the sweep that undoes a lapsed amendment is armed, once, and the reply and the
    Success line keep today's words."""
    from tests.support.change_queue import acknowledgement, run_queue
    from tests.support.settings_league import amendment_sweep_armings, last_said, log_lines

    league, cog = await _league(tmp_path)
    asked = _asked(league, "enable")

    await cog._enable_results(asked)
    assert acknowledgement(asked).startswith("⏳ Turning Results & Standings on. ")
    assert amendment_sweep_armings(league.scheduler) == 0
    await run_queue(league.bot)

    assert await _flag(league.db_path, "results_module_config") == 1
    [entry] = await _audit(league.db_path)
    assert entry["change_type"] == "MODULE_ENABLE"
    assert json.loads(entry["new_value"])["module"] == "results"
    assert amendment_sweep_armings(league.scheduler) == 1
    assert last_said(asked) == "✅ Results & Standings module enabled."
    assert await log_lines(league) == [f"{NAMED} | /module enable results | Success"]


async def test_a_results_enable_whose_sweep_cannot_be_armed_leaves_results_on_and_says_so(
    tmp_path,
):
    """The scheduler refuses to arm the sweep that undoes a lapsed amendment as results is turned
    on: the module stays on with its audit entry, the reply says it is enabled and adds a warning
    line, the Success line and one failure line are written, and no job is left stopped on the
    queue."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import amendment_sweep_armings, last_said, log_lines

    league, cog = await _league(tmp_path)
    league.scheduler.arm_every.side_effect = RuntimeError("the job store refused")
    asked = _asked(league, "enable")

    await cog._enable_results(asked)
    await run_queue(league.bot)

    assert amendment_sweep_armings(league.scheduler) == 1
    assert await _flag(league.db_path, "results_module_config") == 1
    assert [entry["change_type"] for entry in await _audit(league.db_path)] == ["MODULE_ENABLE"]
    enabled, *warning = last_said(asked).split("\n")
    assert enabled == "✅ Results & Standings module enabled."
    assert len(warning) == 1 and warning[0].startswith("⚠️ "), warning
    lines = await log_lines(league)
    assert lines.count(f"{NAMED} | /module enable results | Success") == 1
    assert [line for line in lines if line.startswith("❌ ")] == [
        f"❌ `/module enable` failed for {NAMED} — RuntimeError. The details are in the host's log."
    ]
    assert await stopped_job(league.db_path) is None


async def test_enabling_results_twice_does_no_work(tmp_path):
    """Refused at once: nothing is queued and nothing audited, and the refusal reaches the log
    channel as one line in the standard form."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, "results")
    asked = _asked(league, "enable")

    await cog._enable_results(asked)

    assert last_said(asked) == "⚠️ Results & Standings module is already enabled."
    assert await change_rows(league.db_path) == []
    assert await _audit(league.db_path) == []
    assert await log_lines(league) == [
        _refused_line("Results & Standings module is already enabled.")
    ]


async def test_a_results_enable_that_cannot_be_written_says_the_module_is_still_off(tmp_path):
    """The flag and its audit entry are one save, so a fault at the audit leaves the module off.
    The member gets the standard failure reply, saying the module is still off and what to do
    next, in place of today's 'It may have been partly done', with no error text; the log channel
    gets the one failure line, and never a success."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path)
    await _refuse_the_audit(league.db_path)
    asked = _asked(league, "enable")

    await cog._enable_results(asked)
    await run_queue(league.bot)

    assert await _flag(league.db_path, "results_module_config") in (None, 0)
    assert last_said(asked) == (
        "❌ `/module enable` stopped on a fault in the bot, not on anything you entered, and did "
        "not finish. The module is still off. Run `/module enable results` again once the fault "
        "is cleared. The fault is recorded in the log channel."
    )
    assert await log_lines(league) == [
        f"❌ `/module enable` failed for {NAMED} — IntegrityError. The details are in the host's log."
    ]


# ---------------------------------------------------------------------------
# Attendance — the dependency
# ---------------------------------------------------------------------------
#
# Attendance's switch-on is attendance's own change on the change queue (#283, S11d): `/module
# enable attendance` keeps the dependency and stage refusals and asks `module.on:attendance`, the
# member is acknowledged at once, and the queue carries it out.


async def test_attendance_requires_results_first(tmp_path):
    """Attendance points are distributed after post-race penalties are approved; with
    Results off there is no approval to hang them on. Refused at once: nothing is queued and
    nothing audited, and the refusal reaches the log channel as one line in the standard form."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path)
    asked = _asked(league, "enable")

    await cog._enable_attendance(asked)

    assert last_said(asked) == NEEDS_RESULTS
    assert await change_rows(league.db_path) == []
    assert await _audit(league.db_path) == []
    assert await log_lines(league) == [_refused_line(NEEDS_RESULTS.removeprefix("❌ "))]


async def test_attendance_is_enabled_once_results_is(tmp_path):
    """The member is acknowledged at once, and once the change has run the module is on, and
    the reply and the Success line keep today's words."""
    from tests.support.change_queue import acknowledgement, run_queue
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, "results")
    asked = _asked(league, "enable")

    await cog._enable_attendance(asked)
    assert acknowledgement(asked).startswith("⏳ Turning the attendance module on. ")
    await run_queue(league.bot)

    assert await _flag(league.db_path, "attendance_config") == 1
    assert last_said(asked) == "✅ Attendance module enabled."
    assert await log_lines(league) == [f"{NAMED} | /module enable attendance | Success"]


async def test_enabling_attendance_writes_the_packaged_defaults(tmp_path):
    """The league can drive the module the moment it is on, without a setup command that
    does not exist. Both sanction thresholds start unset — a league opts into them."""
    from tests.support.change_queue import run_queue

    league, cog = await _league(tmp_path, "results")

    await cog._enable_attendance(_asked(league, "enable"))
    await run_queue(league.bot)

    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours, "
            "no_rsvp_penalty, absent_penalty, no_show_penalty, "
            "autoreserve_threshold, autosack_threshold FROM attendance_config",
        )
        row = await cursor.fetchone()
    assert (row["rsvp_notice_days"], row["rsvp_last_notice_hours"], row["rsvp_deadline_hours"]) == (5, 24, 2)
    assert (row["no_rsvp_penalty"], row["absent_penalty"], row["no_show_penalty"]) == (1, 1, 1)
    assert row["autoreserve_threshold"] is None
    assert row["autosack_threshold"] is None


async def test_the_dependency_table_decides_whether_attendance_may_be_enabled(tmp_path, monkeypatch):
    """The refusal reads the table in `core/models/` rather than a rule of its own, for every
    module: with attendance's entry taken out and a test-only entry making weather need results,
    attendance is switched on while results is off, and `/module enable weather` is refused
    while results is off, naming the module it needs, and weather stays off."""
    from leaguebot.core.models import module_dependencies
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said

    monkeypatch.setattr(
        module_dependencies, "NEEDS", {"weather": frozenset({"results"})}
    )
    league, cog = await _league(tmp_path)
    asked = _asked(league, "enable")

    await cog._enable_attendance(asked)
    await run_queue(league.bot)

    assert last_said(asked) == "✅ Attendance module enabled."
    assert await _flag(league.db_path, "attendance_config") == 1

    weather = _asked(league, "enable")
    choice = SimpleNamespace(name="weather", value="weather")
    await undecorate(ModuleCog.enable)(cog, weather, choice)
    await run_queue(league.bot)

    assert "requires the Results & Standings module to be enabled first." in last_said(weather)
    assert await _weather_flag(league.db_path) == 0


async def test_an_attendance_enable_run_after_results_went_off_is_refused(tmp_path):
    """Asked while results is on, and run once results has gone off: the change's own check
    reads the dependency again as it starts, so attendance stays off, nothing is audited, and the
    member's reply becomes the dependency refusal, which reaches the log channel."""
    from tests.support.change_queue import acknowledgement, run_queue
    from tests.support.settings_league import last_said, log_lines, switch

    league, cog = await _league(tmp_path, "results")
    asked = _asked(league, "enable")

    await cog._enable_attendance(asked)
    assert acknowledgement(asked).startswith("⏳ Turning the attendance module on. ")
    await switch(league.bot, ("results",), on=False)
    await run_queue(league.bot)

    assert await _flag(league.db_path, "attendance_config") in (None, 0)
    assert await _audit(league.db_path) == []
    assert last_said(asked) == NEEDS_RESULTS
    assert _refused_line(NEEDS_RESULTS.removeprefix("❌ ")) in await log_lines(league)


#: The stage refusal of `/module enable` once a season's placements are confirmed, word for word.
PLACEMENTS_CONFIRMED = (
    "❌ A module cannot be enabled once the season's placements have been confirmed. Enable it "
    "before then, or once the season has ended."
)
#: The refusal of `/module disable` in Pending completion, word for word.
PENDING_COMPLETION = (
    "❌ No module can be disabled while the season is pending completion. Complete it with "
    "`/season complete` first."
)


@pytest.mark.parametrize(
    ("module", "already_on"),
    [("weather", ()), ("results", ()), ("attendance", ("results",)), ("images", ())],
)
async def test_a_switch_on_asked_before_placements_are_confirmed_and_run_after_is_refused(
    tmp_path, module, already_on
):
    """No season's placements are confirmed when `/module enable <module>` is asked, so the ask is
    acknowledged; a season's placements are confirmed before the change runs, as an approval
    waiting ahead of it on the lane would. The change's own check reads the stage again as it
    starts: the module stays off, nothing is audited, and the member's reply becomes the stage
    refusal the command gives at the ask, word for word, which reaches the log channel."""
    from tests.support.change_queue import acknowledgement, run_queue
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, *already_on)
    asked = _asked(league, "enable")

    await getattr(cog, f"_enable_{module}")(asked)
    assert acknowledgement(asked).startswith("⏳ ")
    await _confirm_a_season(league.db_path)
    await run_queue(league.bot)

    assert await league.bot.module_service.is_enabled(module) is False
    assert await _audit(league.db_path) == []
    assert last_said(asked) == PLACEMENTS_CONFIRMED
    assert _refused_line(PLACEMENTS_CONFIRMED.removeprefix("❌ ")) in await log_lines(league)


@pytest.mark.parametrize(
    ("module", "on"),
    [("weather", ("weather",)), ("attendance", ("results", "attendance")), ("images", ("images",))],
)
async def test_a_switch_off_asked_before_pending_completion_and_run_in_it_is_refused(
    tmp_path, module, on
):
    """No season stands in Pending completion when `/module disable <module>` is asked, so the ask
    is acknowledged; the season reaches Pending completion before the change runs. The change's
    own check reads the stage again as it starts, as results' switch-off already does: the module
    stays on, nothing is audited, and the member's reply becomes the Pending completion refusal,
    word for word, which reaches the log channel."""
    from tests.support.change_queue import acknowledgement, run_queue
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, *on)
    asked = _asked(league, "disable")

    await getattr(cog, f"_disable_{module}")(asked)
    assert acknowledgement(asked).startswith("⏳ ")
    await _stage(league.db_path, "PENDING_COMPLETION", "ACTIVE")
    await run_queue(league.bot)

    assert await league.bot.module_service.is_enabled(module) is True
    assert await _audit(league.db_path) == []
    assert last_said(asked) == PENDING_COMPLETION
    refused = f"⛔ `/module disable` refused for {NAMED} — {PENDING_COMPLETION.removeprefix('❌ ')}"
    assert refused in await log_lines(league)


#: The reason in the stage refusal of a signup switch asked of a league whose Season 5 has had its
#: configuration confirmed since, for *verb* ("enable" or "disable"), word for word.
SIGNUP_FIXED = (
    "The signup module is fixed for Season 5 now that its configuration has been confirmed. It "
    "can be {verb}d again once the season has ended, or while a new season is in configuration."
)


@pytest.mark.parametrize(
    ("verb", "on"),
    [
        pytest.param("enable", (), id="enable"),
        pytest.param("disable", ("signup",), id="disable"),
    ],
)
async def test_a_signup_switch_asked_before_the_configuration_is_confirmed_and_run_after_is_refused(
    tmp_path, verb, on
):
    """No season stands when `/module <verb> signup` is asked, so the ask is acknowledged; Season
    5's configuration is confirmed (it is in Setup, waiting) before the change runs, as an approval
    waiting ahead of it on the lane would. The change's own check reads the stage again as it
    starts: the module is left as it was, nothing is audited, and the member's reply becomes the
    stage refusal the command gives at the ask, word for word, which reaches the log channel."""
    from tests.support.change_queue import acknowledgement, run_queue
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, *on)
    asked = _asked(league, verb)

    await getattr(cog, f"_{verb}_signup")(asked)
    assert acknowledgement(asked).startswith("⏳ ")
    await _stage(league.db_path, "WAITING", "SETUP")
    await run_queue(league.bot)

    assert await league.bot.module_service.is_enabled("signup") is bool(on)
    assert await _audit(league.db_path) == []
    reason = SIGNUP_FIXED.format(verb=verb)
    assert last_said(asked) == f"❌ {reason}"
    assert f"⛔ `/module {verb}` refused for {NAMED} — {reason}" in await log_lines(league)


#: The refusal of a results switch-off that would now take a module the league was not warned of.
CASCADE_CHANGED = (
    "⛔ The attendance module was switched on since this was asked. Nothing was changed. Run "
    "`/module disable results` again."
)


async def test_a_results_switch_off_is_refused_where_attendance_came_on_behind_it(tmp_path):
    """Results on, attendance off, no season. `/module enable attendance` is asked and waits on
    the lane; `/module disable results` is then asked, and with attendance still off it asks the
    switch-off at once, with no cascade and no warning. When the lane runs, attendance goes on
    first. Results' switch-off then reads again, as it starts, which modules go with it: attendance
    now would, and the league was never warned of it, so it is refused rather than carried out
    either way. Results and attendance both stay on, and the member who asked is told to run the
    command again, which then shows the warning."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, "results")
    enable = _asked(league, "enable")
    disable = _asked(league, "disable")

    await cog._enable_attendance(enable)
    await cog._disable_results(disable)
    await run_queue(league.bot)

    assert await league.bot.module_service.is_enabled("attendance") is True
    assert await league.bot.module_service.is_enabled("results") is True
    assert last_said(disable) == CASCADE_CHANGED
    refused = (
        f"⛔ `/module disable results` refused for {NAMED} — "
        f"{CASCADE_CHANGED.removeprefix('⛔ ')}"
    )
    assert refused in await log_lines(league)


async def test_enabling_attendance_twice_does_no_work(tmp_path):
    """Refused at once: nothing is queued and nothing audited, and the refusal reaches the log
    channel as one line in the standard form."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, "results", "attendance")
    asked = _asked(league, "enable")

    await cog._enable_attendance(asked)

    assert last_said(asked) == "⚠️ Attendance module is already enabled."
    assert await change_rows(league.db_path) == []
    assert await _audit(league.db_path) == []
    assert await log_lines(league) == [_refused_line("Attendance module is already enabled.")]


async def test_enabling_attendance_is_audited(tmp_path):
    from tests.support.change_queue import run_queue

    league, cog = await _league(tmp_path, "results")

    await cog._enable_attendance(_asked(league, "enable"))
    await run_queue(league.bot)

    assert [entry["change_type"] for entry in await _audit(league.db_path)] == [
        "ATTENDANCE_MODULE_ENABLED"
    ]


async def test_re_enabling_attendance_keeps_the_league_s_settings(tmp_path):
    """Principle X rule 6: a disabled module keeps its configuration, and re-enabling it restores
    it as it stood. A league that set its own windows, penalties and thresholds finds them all
    after turning the module off and on again, through the commands themselves."""
    from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService
    from tests.support.change_queue import run_queue

    league, cog = await _league(tmp_path, "results")

    await cog._enable_attendance(_asked(league, "enable"))
    await run_queue(league.bot)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE attendance_config SET rsvp_notice_days = 7, rsvp_last_notice_hours = 48, "
            "rsvp_deadline_hours = 6, no_rsvp_penalty = 2, absent_penalty = 3, "
            "no_show_penalty = 4, autoreserve_threshold = 5, autosack_threshold = 9"
        )
        await db.commit()
    await cog._disable_attendance(_asked(league, "disable"))
    await run_queue(league.bot)
    await cog._enable_attendance(_asked(league, "enable"))
    await run_queue(league.bot)

    cfg = await AttendanceService(league.db_path).get_config()
    assert cfg.module_enabled is True
    assert (cfg.rsvp_notice_days, cfg.rsvp_last_notice_hours, cfg.rsvp_deadline_hours) == (7, 48, 6)
    assert (cfg.no_rsvp_penalty, cfg.absent_penalty, cfg.no_show_penalty) == (2, 3, 4)
    assert cfg.autoreserve_threshold == 5
    assert cfg.autosack_threshold == 9


async def test_enabling_attendance_leaves_the_schema_s_own_defaults(tmp_path):
    """The packaged values are held once, as the column defaults of `attendance_config`, and the
    enable's save names none of them: a configuration it writes holds exactly the values the
    schema declares, so the two cannot drift apart."""
    from tests.support.change_queue import run_queue

    league, cog = await _league(tmp_path, "results")

    await cog._enable_attendance(_asked(league, "enable"))
    await run_queue(league.bot)

    async with get_connection(league.db_path) as db:
        cursor = await db.execute("PRAGMA table_info(attendance_config)")
        declared = {row["name"]: row["dflt_value"] for row in await cursor.fetchall()}
        cursor = await db.execute("SELECT * FROM attendance_config")
        stored = dict(await cursor.fetchone())
    for column in (
        "rsvp_notice_days", "rsvp_last_notice_hours", "rsvp_deadline_hours", "no_rsvp_penalty",
        "absent_penalty", "no_show_penalty", "autoreserve_threshold", "autosack_threshold",
    ):
        default = declared[column]
        assert stored[column] == (None if default is None else int(default)), column


async def test_an_attendance_enable_that_cannot_be_audited_leaves_the_module_off(tmp_path):
    """The configuration and its audit entry are one write: where the second fails, the
    first is not kept either, and the league is told, in the standard failure reply, that the
    module is still off and what to do next. The error's own text is not shown, and the log
    channel gets the one failure line, naming the kind of fault and never its words."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, "results")
    await _refuse_the_audit(league.db_path)
    asked = _asked(league, "enable")

    await cog._enable_attendance(asked)
    await run_queue(league.bot)

    replied = last_said(asked)
    assert replied == (
        "❌ `/module enable` stopped on a fault in the bot, not on anything you entered, and did "
        "not finish. The module is still off. Run `/module enable attendance` again once the "
        "fault is cleared. The fault is recorded in the log channel."
    )
    assert "disk I/O error" not in replied
    async with get_connection(league.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM attendance_config")
        assert (await cursor.fetchone())[0] == 0
    assert await log_lines(league) == [
        f"❌ `/module enable` failed for {NAMED} — IntegrityError. The details are in the host's log."
    ]


# ---------------------------------------------------------------------------
# A failed enable, and every refusal, reach the log channel (#442)
# ---------------------------------------------------------------------------


def _lines(cog) -> list[str]:
    return [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]


async def _refuse_the_audit(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "CREATE TRIGGER refuse_the_audit BEFORE INSERT ON audit_entries "
            "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
        )
        await db.commit()


async def test_a_weather_enable_that_cannot_be_written_says_the_module_is_still_off(tmp_path):
    """The flag and its audit entry are one write, so the module stays off. The member gets
    the standard failure reply, saying the module is still off and what to do next, with no
    error text; the log channel gets the one failure line, and no success."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _weather_league(tmp_path, on=False)
    await _refuse_the_audit(league.db_path)
    asked = _asked(league, "enable")

    await cog._enable_weather(asked)
    await run_queue(league.bot)

    assert await _weather_flag(league.db_path) == 0
    replied = last_said(asked)
    assert replied == (
        "❌ `/module enable` stopped on a fault in the bot, not on anything you entered, and did "
        "not finish. The module is still off. Run `/module enable weather` again once the fault "
        "is cleared. The fault is recorded in the log channel."
    )
    assert "disk I/O error" not in replied
    assert "Module remains disabled" not in replied
    [line] = await log_lines(league)
    assert line == (
        f"❌ `/module enable` failed for {NAMED} — IntegrityError. The details are in the host's log."
    )
    assert "disk I/O error" not in line


async def _stage(db_path: str, stage: str, status: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-09-17', ?, 5, ?)",
            (status, stage),
        )
        await db.commit()


#: Every refusal `/module enable` makes, and the one it shares with `/module disable`: the
#: verb, the module, how the cog is set up, the stage the season stands in, and a fragment of
#: the reply. A case whose verb is None calls the enable handler directly, the command's own
#: stage check standing in front of it otherwise. Weather's "already enabled", refused by its
#: change on the queue, is pinned by `test_enabling_weather_twice_does_no_work`; images' the same
#: way, by `test_enabling_twice_does_no_work`; signup's by
#: `test_module_disable_paths.py::test_enabling_signup_twice_does_no_work`. Results' and attendance's own refusals (the dependency
#: and "already enabled") are each pinned on the league's bot, with the real queue, by their own
#: tests above; the season refuses them, as every module, only through the command's stage check
#: (`test_module_stage_gates.py::test_no_module_is_enabled_once_placements_are_confirmed`).
MODULE_REFUSALS = [
    pytest.param("enable", "weather", {"season": "confirmed"}, None, "cannot be enabled once",
                 id="enable-once-placements-are-confirmed"),
    pytest.param("enable", "signup", {}, ("WAITING", "SETUP"), "fixed for Season 5",
                 id="enable-signup-once-fixed"),
    pytest.param("disable", "signup", {}, ("WAITING", "SETUP"), "fixed for Season 5",
                 id="disable-signup-once-fixed"),
    pytest.param("disable", "weather", {}, ("PENDING_COMPLETION", "ACTIVE"),
                 "pending completion", id="disable-in-pending-completion"),
]


@pytest.mark.parametrize("verb, module, setup, stage, said", MODULE_REFUSALS)
async def test_every_module_enable_and_disable_refusal_reaches_the_log_channel(
    tmp_path, verb, module, setup, stage, said
):
    """Every outcome of a command that changes something is recorded, a refusal included: the
    member is answered as before, and the log channel gets one line in the standard form."""
    from leaguebot.core.cogs.module_cog import ModuleCog

    db_path = await _make_db(tmp_path)
    if stage is not None:
        await _stage(db_path, *stage)
    setup = dict(setup)
    season = _season() if setup.pop("season", None) == "confirmed" else None
    signup_enabled = setup.pop("signup_enabled", False)
    cog = _make_cog(db_path, season=season, **setup)
    answer_switch(cog.bot.module_service, "signup", signup_enabled)
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = f"module {verb or 'enable'}"

    if verb is None:
        await getattr(cog, f"_enable_{module}")(interaction)
    else:
        choice = SimpleNamespace(name=module.title(), value=module)
        await undecorate(getattr(ModuleCog, verb))(cog, interaction, choice)

    assert said in _replied(interaction)
    [line] = _lines(cog)
    assert line.startswith("⛔ ")
    assert f"/module {verb or 'enable'}" in line
    assert f"refused for Admin (<@{ACTOR_ID}>)" in line
    assert said in line


# ---------------------------------------------------------------------------
# Kept channels, checked as a module is switched back on (#449)
# ---------------------------------------------------------------------------
#
# A module switched off keeps its channels. Switched back on, each channel it kept for a division
# of the live season is checked before the switch is saved: one no longer on the server is named
# for the league to set again; one another setting took while the module was off is cleared from
# the module and named; one the bot cannot use keeps the module off (⛔), and a check that cannot
# be made keeps it off as a fault (❌).

#: Each module's kept channel under test: its setting's column and table, its label and command,
#: the reply and audit type of its switch-on and of the setting a clear undoes.
_KEPT = {
    "weather": ("weather", "divisions", "forecast_channel_id", "weather channel",
                "/weather channel", "✅ Weather module enabled.", "DIVISION_CHANNEL_SET"),
    "results": ("results", "division_results_config", "results_channel_id", "results channel",
                "/results channel results", "✅ Results & Standings module enabled.",
                "DIVISION_CHANNEL_SET"),
    "verdicts": ("results", "division_results_config", "penalty_channel_id", "verdicts channel",
                 "/results channel verdicts", "✅ Results & Standings module enabled.",
                 "VERDICTS_CHANNEL_SET"),
    "rsvp": ("attendance", "attendance_division_config", "rsvp_channel_id", "RSVP channel",
             "/attendance channel rsvp", "✅ Attendance module enabled.", "RSVP_CHANNEL_SET"),
    "attendance": ("attendance", "attendance_division_config", "attendance_channel_id",
                   "attendance channel", "/attendance channel attendance",
                   "✅ Attendance module enabled.", "ATTENDANCE_CHANNEL_SET"),
    "signup": ("signup", "signup_module_config", "signup_channel_id", "signup channel",
               "/signup channel", "✅ Signup module enabled.", "SIGNUP_CHANNEL_SET"),
}
#: Why signup's case fails until its switch-on checks the channel it keeps.
_SIGNUP_UNCHECKED = "#449: signup forgets its channel on switch-off, and its switch-on checks none"
KEPT_SETTINGS = [
    *(setting for setting in _KEPT if setting != "signup"),
    pytest.param("signup", marks=pytest.mark.xfail(strict=True, reason=_SIGNUP_UNCHECKED)),
]
#: The server's own roles, set where the kept channel is the server's (signup's), so that its
#: switch-on asks for neither.
SERVER_BASE_ROLE, SERVER_DRIVER_ROLE = 819, 820


def _server_wide(setting: str) -> bool:
    """Whether *setting* is the server's, kept once (signup's channel), not a division's."""
    return _KEPT[setting][1] == "signup_module_config"


def _whose(setting: str) -> str:
    """Whose kept *setting* the reply and the line name: Pro's, or the server's own."""
    label = _KEPT[setting][3]
    return f"the {label}" if _server_wide(setting) else f"**Pro**'s {label}"

#: The heading the reply names each channel to set again under.
SET_AGAIN = (
    "⚠️ Set these again: each is no longer on the server, or was given to another setting while "
    "the module was off:"
)

PRO_CHANNEL, AM_CHANNEL, OLD_CHANNEL = 501, 502, 503


async def _division_row(db, season_id: int, name: str) -> int:
    cursor = await db.execute(
        "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
        "VALUES (?, ?, 1, 'ACTIVE', 1)",
        (season_id, name),
    )
    return cursor.lastrowid


async def _keep(db, setting: str, division_id: int, channel_id: int | None) -> None:
    """Hold *channel_id* as *division_id*'s *setting*, where the module's own command writes it."""
    _module, table, column = _KEPT[setting][:3]
    if table == "divisions":
        await db.execute(f"UPDATE divisions SET {column} = ? WHERE id = ?", (channel_id, division_id))
    elif _server_wide(setting):
        await db.execute(
            f"INSERT INTO {table} (id, {column}, signups_open) VALUES (1, ?, 0) "
            f"ON CONFLICT(id) DO UPDATE SET {column} = ?",
            (channel_id, channel_id),
        )
    else:
        await db.execute(
            f"INSERT INTO {table} (division_id, {column}) VALUES (?, ?) "
            f"ON CONFLICT(division_id) DO UPDATE SET {column} = ?",
            (division_id, channel_id, channel_id),
        )


async def _kept(db_path: str, setting: str, division_id: int) -> int | None:
    """The channel *division_id* holds as *setting*, None where none; the server's own where
    *setting* is the server's."""
    _module, table, column = _KEPT[setting][:3]
    key = "id" if table == "divisions" or _server_wide(setting) else "division_id"
    held_by = 1 if _server_wide(setting) else division_id
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT {column} FROM {table} WHERE {key} = ?", (held_by,))
        row = await cursor.fetchone()
    return None if row is None or row[column] is None else int(row[column])


async def _switched_off_league(tmp_path, setting: str, *, guild=None, pictures: tuple[str, ...] = ()):
    """A league in a season in setup, divisions Pro and Am, whose module of *setting* is off with
    Pro's *setting* kept as channel 501 and Am's as 502, and a completed season's division Old
    keeping 503; results on where the module is attendance. A setting of the server's own
    (signup's channel) is kept once, as 501, with the league's base and driver roles set and the
    season in setup standing in Configuration, where signup may be switched. The
    bot's server is *guild*
    (`server_holding()`, every channel there and usable, by default), and the image module's
    drawing answers that the pictures of *pictures* attach a file. Gives the league, the cog and
    Pro's division id."""
    from tests.support.channel_faults import answer_attachments, server_holding

    module = _KEPT[setting][0]
    league, cog = await _league(tmp_path, *(("results",) if module == "attendance" else ()))
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) VALUES ('2026-01-10', "
            "'COMPLETED', 1)"
        )
        old = await _division_row(db, cursor.lastrowid, "Old")
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) VALUES ('2026-11-01', "
            "'SETUP', 2)"
        )
        pro = await _division_row(db, cursor.lastrowid, "Pro")
        am = await _division_row(db, cursor.lastrowid, "Am")
        await _keep(db, setting, pro, PRO_CHANNEL)
        if _server_wide(setting):
            await db.execute(
                "UPDATE server_configs SET base_role_id = ?, driver_role_id = ?",
                (SERVER_BASE_ROLE, SERVER_DRIVER_ROLE),
            )
            # Signup is switched only while the season in setup stands in Configuration.
            await db.execute("UPDATE seasons SET stage = 'CONFIGURATION' WHERE status = 'SETUP'")
        else:
            await _keep(db, setting, am, AM_CHANNEL)
            await _keep(db, setting, old, OLD_CHANNEL)
        await db.commit()
    league.bot.get_guild = MagicMock(return_value=guild if guild is not None else server_holding())
    answer_attachments(league.bot.image_drawing, *pictures)
    return league, cog, pro


async def _switch_on(league, cog, module: str):
    """`/module enable <module>` as Admin, the queue run. Gives the interaction."""
    from tests.support.change_queue import run_queue

    asked = _asked(league, "enable")
    await getattr(cog, f"_enable_{module}")(asked)
    await run_queue(league.bot)
    return asked


async def _module_on(league, module: str) -> bool:
    return bool(await league.bot.module_service.is_enabled(module))


@pytest.mark.parametrize("setting", KEPT_SETTINGS)
async def test_a_switch_on_names_each_kept_channel_no_longer_on_the_server(tmp_path, setting):
    """Pro's kept channel was deleted while the module was off, Am's is still there, and a
    completed season's division keeps a deleted channel too. Switched back on, the module is on;
    the reply names Pro's channel alone, under the heading, with the command that sets it again;
    Pro's channel is still held, for the league to set again; and the Success line says it is no
    longer on the server. The completed season is not checked. Signup's channel is the server's:
    it was deleted while signup was off, and is named as the signup channel."""
    from tests.support.channel_faults import server_holding
    from tests.support.settings_league import last_said, log_lines

    module, *_rest = _KEPT[setting]
    command, enabled = _KEPT[setting][4:6]
    league, cog, pro = await _switched_off_league(
        tmp_path, setting, guild=server_holding(gone=(PRO_CHANNEL, OLD_CHANNEL))
    )

    asked = await _switch_on(league, cog, module)

    assert await _module_on(league, module)
    assert last_said(asked) == "\n".join(
        [enabled, SET_AGAIN, f"• {_whose(setting)} — `{command}`"]
    )
    assert await _kept(league.db_path, setting, pro) == PRO_CHANNEL
    lines = "\n".join(await log_lines(league))
    assert f"{NAMED} | /module enable {module} | Success" in lines
    assert f"  {_whose(setting)} is no longer on the server — `{command}`" in lines
    assert "**Am**" not in lines and "**Old**" not in lines


@pytest.mark.parametrize("setting", KEPT_SETTINGS)
async def test_a_switch_on_clears_and_names_a_kept_channel_another_setting_took(tmp_path, setting):
    """While the module is off, `/division lineup-channel` gives Pro's kept channel 501 to Pro's
    lineup, and is not refused. Switched back on, the module is on, names 501 under the heading,
    and clears it from Pro's *setting*, audited from 501 to none under the setting's own change
    type; the Success line says it was given to another setting and is cleared; and the lineup
    keeps 501. Signup's channel is the server's, named as the signup channel, and the switch-on
    puts none of signup's permissions on 501, now the lineup's."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said, log_lines

    module, *_rest = _KEPT[setting]
    command, enabled, audited = _KEPT[setting][4:7]
    league, cog, pro = await _switched_off_league(tmp_path, setting)
    guild = league.bot.get_guild.return_value
    finding = guild.get_channel.side_effect
    taken = finding(PRO_CHANNEL)
    taken.set_permissions, taken.edit = AsyncMock(), AsyncMock()
    guild.get_channel = MagicMock(
        side_effect=lambda cid: taken if int(cid) == PRO_CHANNEL else finding(cid)
    )
    lineup = _asked(league, "division lineup-channel")
    await league.bot.change_queue.ask(
        "core.setting.lineup_channel_id",
        {"division_id": pro, "division_name": "Pro", "value": PRO_CHANNEL,
         "channel_name": "pro-lineup"},
        interaction=lineup, what="`/division lineup-channel`",
    )
    await run_queue(league.bot)
    assert last_said(lineup).startswith("✅ ")
    edits_before = (taken.set_permissions.await_count, taken.edit.await_count)

    asked = await _switch_on(league, cog, module)

    assert await _module_on(league, module)
    assert last_said(asked) == "\n".join(
        [enabled, SET_AGAIN, f"• {_whose(setting)} — `{command}`"]
    )
    assert await _kept(league.db_path, setting, pro) is None
    assert any(
        row["change_type"] == audited and str(PRO_CHANNEL) in (row["old_value"] or "")
        and str(PRO_CHANNEL) not in (row["new_value"] or "")
        for row in await _audit(league.db_path)
    )
    lines = "\n".join(await log_lines(league))
    assert f"  {_whose(setting)} was given to another setting and is cleared — `{command}`" in lines
    assert (taken.set_permissions.await_count, taken.edit.await_count) == edits_before
    async with get_connection(league.db_path) as db:
        cursor = await db.execute("SELECT lineup_channel_id FROM divisions WHERE id = ?", (pro,))
        assert int((await cursor.fetchone())["lineup_channel_id"]) == PRO_CHANNEL


@pytest.mark.parametrize("cause", ["a channel's fetch fails", "the server is out of reach"])
@pytest.mark.parametrize("setting", KEPT_SETTINGS)
async def test_a_switch_on_whose_kept_channels_cannot_be_checked_leaves_the_module_off(
    tmp_path, setting, cause
):
    """Pro's kept channel is out of the cache and Discord answers its fetch with a server error,
    or the league's server is out of the bot's reach: the check cannot be made, so the module
    stays off, the member is told to run the command again, the one line is a failure, nothing is
    audited, and nothing stops the queue."""
    from tests.support.change_queue import stopped_job
    from tests.support.channel_faults import server_holding
    from tests.support.settings_league import last_said, log_lines

    module, *_rest = _KEPT[setting]
    guild = server_holding(unreadable=(PRO_CHANNEL,)) if cause == "a channel's fetch fails" else None
    league, cog, pro = await _switched_off_league(tmp_path, setting, guild=guild)
    if guild is None:
        league.bot.get_guild = MagicMock(return_value=None)
    audits_before = await _audit(league.db_path)

    asked = await _switch_on(league, cog, module)

    assert not await _module_on(league, module)
    assert last_said(asked).endswith(
        f"The module is still off. Run `/module enable {module}` again once the fault is cleared. "
        "The fault is recorded in the log channel."
    )
    [line] = await log_lines(league)
    assert line.startswith(f"❌ `/module enable` failed for {NAMED} — ")
    assert await _audit(league.db_path) == audits_before
    assert await stopped_job(league.db_path) is None
    assert await _kept(league.db_path, setting, pro) == PRO_CHANNEL


@pytest.mark.parametrize("setting, denied, missing, pictures", [
    ("weather", "forbidden", "View Channel", ()),
    ("results", "read_message_history", "Read Message History", ()),
    ("verdicts", "attach_files", "Attach Files", ("verdicts",)),
    ("rsvp", "embed_links", "Embed Links", ()),
    ("attendance", "read_message_history", "Read Message History", ()),
    ("attendance", "send_messages", "Send Messages", ()),
    pytest.param("signup", "manage_roles", "Manage Permissions", (),
                 marks=pytest.mark.xfail(strict=True, reason=_SIGNUP_UNCHECKED)),
])
async def test_a_switch_on_finding_a_kept_channel_the_bot_cannot_use_leaves_the_module_off_and_names_the_way_out(
    tmp_path, setting, denied, missing, pictures
):
    """Pro's kept channel is missing one permission its module needs of it (for the verdicts
    channel, Attach Files, with the verdict pictures on; for the attendance channel, also Send
    Messages alone, a channel the bot can see but not post in), and Am's kept channel was deleted
    while the module was off. The module stays off; the reply names Pro's channel and what it
    lacks, and the three ways out, and does not name Am's; the one line is a refusal naming the
    same, and no failure; nothing is audited, nothing stops the queue, and Pro's and Am's channels
    are still held, Am's for the next switch-on to name. Signup's channel, the server's, lacks
    Manage Permissions, which putting the bot's permissions back on it needs."""
    from tests.support.change_queue import stopped_job
    from tests.support.channel_faults import server_holding
    from tests.support.settings_league import last_said, log_lines

    module, *_rest = _KEPT[setting]
    guild = (server_holding(gone=(AM_CHANNEL,), forbidden=(PRO_CHANNEL,)) if denied == "forbidden"
             else server_holding(gone=(AM_CHANNEL,), unusable={PRO_CHANNEL: (denied,)}))
    league, cog, pro = await _switched_off_league(tmp_path, setting, guild=guild, pictures=pictures)
    audits_before = await _audit(league.db_path)

    asked = await _switch_on(league, cog, module)

    assert not await _module_on(league, module)
    replied = last_said(asked)
    assert replied.startswith(
        "⛔ The module is still off: kept channels are missing permissions the bot needs there:"
    )
    assert f"• {_whose(setting)} <#{PRO_CHANNEL}>: {missing}" in replied
    assert (
        "Give the bot them, delete the channel, or give it to another setting (the module's own "
        f"channel command is refused while it is off), then run `/module enable {module}` again."
    ) in replied
    assert "**Am**" not in replied
    [line] = await log_lines(league)
    assert line == _refused_line(
        f"kept channels are missing permissions the bot needs there: {_whose(setting)} ({missing})"
    )
    assert await _audit(league.db_path) == audits_before
    assert await stopped_job(league.db_path) is None
    assert await _kept(league.db_path, setting, pro) == PRO_CHANNEL
    if not _server_wide(setting):
        assert await _kept(league.db_path, setting, pro + 1) == AM_CHANNEL  # Am, added after Pro


async def test_the_first_of_two_switched_off_modules_back_on_keeps_their_channel(tmp_path):
    """Attendance keeps 501 as Pro's check-in channel and is off. Weather is switched on, and
    `/weather channel` gives 501 to Pro's forecasts, which is not refused; then weather is
    switched off too. Attendance switched back on keeps 501 and names nothing; weather switched
    back on names 501 and clears it from Pro's forecasts."""
    from leaguebot.weather.services.weather_settings import FORECAST_CHANNEL
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said

    league, cog, pro = await _switched_off_league(tmp_path, "rsvp")
    await _switch_on(league, cog, "weather")
    forecasts = _asked(league, "weather channel")
    await league.bot.change_queue.ask(
        FORECAST_CHANNEL,
        {"division_id": pro, "division_name": "Pro", "value": PRO_CHANNEL,
         "channel_name": "pro-weather"},
        interaction=forecasts, what="`/weather channel`",
    )
    await run_queue(league.bot)
    assert last_said(forecasts).startswith("✅ ")
    await cog._disable_weather(_asked(league, "disable"))
    await run_queue(league.bot)

    attendance = await _switch_on(league, cog, "attendance")
    assert last_said(attendance) == "✅ Attendance module enabled."
    assert await _kept(league.db_path, "rsvp", pro) == PRO_CHANNEL

    weather = await _switch_on(league, cog, "weather")
    assert await _module_on(league, "weather")
    assert last_said(weather) == "\n".join(
        ["✅ Weather module enabled.", SET_AGAIN, "• **Pro**'s weather channel — `/weather channel`"]
    )
    assert await _kept(league.db_path, "weather", pro) is None
    assert await _kept(league.db_path, "rsvp", pro) == PRO_CHANNEL
