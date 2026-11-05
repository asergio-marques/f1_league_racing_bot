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
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import MEMBER_ID
from tests.support.undecorate import undecorate

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


def _division(div_id: int, name: str, *, forecast_channel_id: int | None = 5000):
    return SimpleNamespace(
        id=div_id, name=name, tier=1, forecast_channel_id=forecast_channel_id
    )


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
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.module_service.set_weather_enabled = AsyncMock(return_value=None)

    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division(11, "Division 1")]
    )
    bot.season_service.get_division_rounds = AsyncMock(return_value=[])

    bot.scheduler_service = MagicMock()
    bot.scheduler_service.cancel_all_weather = AsyncMock(return_value=None)

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
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _weather_league(tmp_path, on=True)
    asked = _asked(league, "disable")

    await cog._disable_weather(asked)
    assert acknowledgement(asked).startswith("⏳ Turning the weather module off. ")

    await run_queue(league.bot, steps=1)
    assert await _weather_flag(league.db_path) == 0
    assert [entry["change_type"] for entry in await _audit(league.db_path)] == ["MODULE_DISABLE"]
    league.scheduler.cancel_all_weather.assert_not_awaited()

    await run_queue(league.bot)
    league.scheduler.cancel_all_weather.assert_awaited_once_with()
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
    league.scheduler.cancel_all_weather.assert_not_awaited()
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
    """The scheduler refuses to remove the season's weather jobs: the module stays off with its
    audit entry, the reply says it is off but its jobs could not be removed and do nothing while
    it is off, never that they were cancelled, the Success line and one failure line are written,
    and no job is left stopped on the queue."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _weather_league(tmp_path, on=True)
    league.scheduler.cancel_all_weather.side_effect = RuntimeError("the job store refused")
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
    audited, and the reply and the Success line keep today's words."""
    from tests.support.change_queue import acknowledgement, run_queue
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path)
    asked = _asked(league, "enable")

    await cog._enable_results(asked)
    assert acknowledgement(asked).startswith("⏳ Turning Results & Standings on. ")
    await run_queue(league.bot)

    assert await _flag(league.db_path, "results_module_config") == 1
    [entry] = await _audit(league.db_path)
    assert entry["change_type"] == "MODULE_ENABLE"
    assert json.loads(entry["new_value"])["module"] == "results"
    assert last_said(asked) == "✅ Results & Standings module enabled."
    assert await log_lines(league) == [f"{NAMED} | /module enable results | Success"]


async def test_results_cannot_be_enabled_mid_season(tmp_path):
    """FR-003. It changes how a round is scored, so the remaining rounds would be scored
    by different rules than the ones already run. Refused at once: nothing is queued and nothing
    audited, and the refusal reaches the log channel as one line in the standard form."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path)
    await _confirm_a_season(league.db_path)
    asked = _asked(league, "enable")

    await cog._enable_results(asked)

    refusal = "Results & Standings module cannot be enabled once a season's placements are confirmed."
    assert last_said(asked) == f"❌ {refusal}"
    assert await change_rows(league.db_path) == []
    assert await _audit(league.db_path) == []
    assert await log_lines(league) == [_refused_line(refusal)]


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


async def test_attendance_cannot_be_enabled_mid_season(tmp_path):
    """Refused at once: nothing is queued and nothing audited, and the refusal reaches the log
    channel as one line in the standard form."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, "results")
    await _confirm_a_season(league.db_path)
    asked = _asked(league, "enable")

    await cog._enable_attendance(asked)

    refusal = "Attendance module cannot be enabled once a season's placements are confirmed."
    assert last_said(asked) == f"❌ {refusal}"
    assert await change_rows(league.db_path) == []
    assert await _audit(league.db_path) == []
    assert await log_lines(league) == [_refused_line(refusal)]


async def test_the_results_dependency_is_checked_before_the_season(tmp_path):
    """Within attendance's own handler, a league with neither in place is told about the
    dependency, which is the thing they have to fix first — being told about the season would
    send them the wrong way. Through `/module enable` itself, a season whose placements are
    confirmed is refused first by the stage check every module shares
    (`test_module_stage_gates.py`), before this handler is reached."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said

    league, cog = await _league(tmp_path)
    await _confirm_a_season(league.db_path)
    asked = _asked(league, "enable")

    await cog._enable_attendance(asked)

    assert last_said(asked) == NEEDS_RESULTS
    assert await change_rows(league.db_path) == []


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


#: Why a switch asked before the season's stage forbade it is not yet refused when it runs.
_STAGE_AT_RUN = (
    "#283: a module's switch does not yet read the season's stage again as its change starts"
)
#: Why a results switch-off is not yet refused where attendance came on behind it.
_CASCADE_AT_RUN = (
    "#283: results' switch-off does not yet read again, as it starts, which modules go with it"
)
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


@pytest.mark.xfail(strict=True, reason=_STAGE_AT_RUN)
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

    assert await getattr(league.bot.module_service, f"is_{module}_enabled")() is False
    assert await _audit(league.db_path) == []
    assert last_said(asked) == PLACEMENTS_CONFIRMED
    assert _refused_line(PLACEMENTS_CONFIRMED.removeprefix("❌ ")) in await log_lines(league)


@pytest.mark.xfail(strict=True, reason=_STAGE_AT_RUN)
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

    assert await getattr(league.bot.module_service, f"is_{module}_enabled")() is True
    assert await _audit(league.db_path) == []
    assert last_said(asked) == PENDING_COMPLETION
    refused = f"⛔ `/module disable` refused for {NAMED} — {PENDING_COMPLETION.removeprefix('❌ ')}"
    assert refused in await log_lines(league)


#: The refusal of a results switch-off that would now take a module the league was not warned of.
CASCADE_CHANGED = (
    "⛔ The attendance module was switched on since this was asked. Nothing was changed. Run "
    "`/module disable results` again."
)


@pytest.mark.xfail(strict=True, reason=_CASCADE_AT_RUN)
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

    assert await league.bot.module_service.is_attendance_enabled() is True
    assert await league.bot.module_service.is_results_enabled() is True
    assert last_said(disable) == CASCADE_CHANGED
    refused = f"⛔ `/module disable` refused for {NAMED} — {CASCADE_CHANGED.removeprefix('⛔ ')}"
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


async def test_re_enabling_attendance_starts_from_the_packaged_defaults(tmp_path):
    """Constitution X.6: re-enabling a module starts fresh. A league that set its own
    windows, penalties and thresholds finds the packaged ones after turning the module off
    and on again, through the commands themselves."""
    from leaguebot.attendance.services.attendance_service import AttendanceService
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
    assert (cfg.rsvp_notice_days, cfg.rsvp_last_notice_hours, cfg.rsvp_deadline_hours) == (5, 24, 2)
    assert (cfg.no_rsvp_penalty, cfg.absent_penalty, cfg.no_show_penalty) == (1, 1, 1)
    assert cfg.autoreserve_threshold is None
    assert cfg.autosack_threshold is None


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
#: way, by `test_enabling_twice_does_no_work`. Results' and attendance's refusals (the dependency,
#: the season and "already enabled") are each pinned on the league's bot, with the real queue, by
#: their own tests above.
MODULE_REFUSALS = [
    pytest.param("enable", "signup", {"signup_enabled": True}, None, "already enabled",
                 id="signup-already-enabled"),
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
    cog.bot.module_service.is_signup_enabled = AsyncMock(return_value=signup_enabled)
    cog._refresh_hub = AsyncMock()
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
