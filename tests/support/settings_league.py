"""A league whose settings commands run on the change queue (#439, slice 7).

`settings_league(tmp_path)` builds a database by the migrations, seeds the league's server, and
gives it `league_bot`'s bot: the one the entry point's `build` makes, with the services the
settings of results, weather and images are handed by the builder: a real `ModuleService`,
`SeasonService`, `ImageConfigService`, `ImageValidityService` and `ImageRenderService`. Its scheduler is a double whose
generic daily arming (`arm_daily`) and removal (`remove`) record their calls;
`portrait_refresh_times` and `portrait_refresh_cancels` read image's daily refresh from them.
Core's round jobs are a double too, whose cancel of the live season's rounds' jobs
(`cancel_live_rounds`) records its calls; `weather_job_cancels` reads weather's from it.
The modules named in *modules* are on (results, weather and images by default), the image
module holding its default configuration where it is on, and the real queue is attached with "now"
pinned, every change type the builder registers registered.

`interaction(bot, command)` is a member's interaction for `/<command>`, as `member_interaction`
gives one, whose follow-up returns a message that records its edits, so that `said` reads every
word the member saw, in order: the response, its edits, each follow-up and that follow-up's edits.
`log_lines` reads every log line written, `audit_rows` the audit log; `fault_in` makes a named writer raise inside the save.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from leaguebot.framework.db.database import get_connection, run_migrations
from tests.support import saves
from tests.support.change_queue import (
    league_bot,
    league_queue,
    member_interaction,
    queued_log_lines,
    seed_server,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
#: Every module a settings command of this slice belongs to.
MODULES = ("results", "weather", "images")


@dataclass
class SettingsLeague:
    """The bot, its database, the scheduler double the image settings are handed, and the round
    jobs' double weather's switch-off is handed."""

    bot: Any
    db_path: str
    scheduler: Any
    round_jobs: Any = None


def scheduler_double(*, fails: bool = False) -> MagicMock:
    """A scheduler service whose generic daily arming (`arm_daily`) and removal (`remove`) are
    recorded, as image arms and cancels its daily portrait refresh through them; with *fails*,
    each raises, as a job store refusing the job would. No job stands (`has_job` is false), so a
    removal that finds nothing is not taken for one that failed."""
    double = MagicMock()
    error = RuntimeError("scheduler unavailable") if fails else None
    double.arm_daily = MagicMock(side_effect=error)
    double.remove = MagicMock(side_effect=error)
    double.has_job = MagicMock(return_value=False)
    return double


def round_jobs_double() -> MagicMock:
    """Core's round jobs, whose cancel of the live season's rounds' jobs (`cancel_live_rounds`),
    as weather's switch-off asks it for weather's kinds, is recorded, and never raises unless a
    test sets it to."""
    double = MagicMock()
    double.cancel_live_rounds = AsyncMock(return_value=None)
    return double


def weather_job_cancels(round_jobs: Any) -> int:
    """How many times core's round jobs' double was asked to cancel the live season's rounds'
    weather jobs, and those alone."""
    from leaguebot.weather.services.forecast_leads import WEATHER_JOB_KINDS

    return sum(
        1 for call in round_jobs.cancel_live_rounds.await_args_list
        if frozenset(_argument(call, "only", 0)) == WEATHER_JOB_KINDS
    )


#: The id image's daily portrait refresh is stored under.
PORTRAIT_REFRESH_JOB_ID = "pfp_daily"


def _argument(call: Any, name: str, index: int) -> Any:
    """The argument *name* of a recorded call, handed by keyword or at *index*."""
    return call.kwargs[name] if name in call.kwargs else call.args[index]


def portrait_refresh_times(scheduler: Any) -> list[str]:
    """Each time of day, as ``HH:MM`` UTC, at which the scheduler double was asked to arm image's
    daily portrait refresh, in the order asked."""
    return [
        f"{int(_argument(call, 'hour', 2)):02d}:{int(_argument(call, 'minute', 3)):02d}"
        for call in scheduler.arm_daily.call_args_list
        if _argument(call, "job_id", 0) == PORTRAIT_REFRESH_JOB_ID
    ]


def amendment_sweep_armings(scheduler: Any) -> int:
    """How many times the scheduler double was asked to arm results' amendment sweep through its
    generic repeating call (`arm_every`)."""
    return sum(
        1 for call in scheduler.arm_every.call_args_list
        if _argument(call, "job_id", 0) == "amend_sweep"
    )


def portrait_refresh_cancels(scheduler: Any) -> int:
    """How many times the scheduler double was asked to remove image's daily portrait refresh."""
    return sum(
        1 for call in scheduler.remove.call_args_list
        if _argument(call, "job_id", 0) == PORTRAIT_REFRESH_JOB_ID
    )


async def settings_league(tmp_path: Any, *, modules: tuple[str, ...] = MODULES,
                          now: datetime = NOW) -> SettingsLeague:
    """The league of the module docstring, with *modules* on."""
    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.core.services.seasons.season_service import SeasonService
    from leaguebot.image.services.render.image_render_service import ImageRenderService
    from leaguebot.image.services.validity.image_validity_service import ImageValidityService

    db_path = os.path.join(str(tmp_path), "settings.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = await league_bot(db_path, scheduler=scheduler_double(), round_jobs=round_jobs_double())
    await switch(bot, modules, on=True)
    if "images" in modules:
        await saves.create_image_defaults(db_path)
    league_queue(bot, db_path, now=now)
    return SettingsLeague(bot, db_path, bot.scheduler_service, bot.round_jobs)


async def switch(bot: Any, modules: tuple[str, ...], *, on: bool) -> None:
    """Turn each of *modules* on or off, as `/module enable|disable` leaves its flag
    (`set_module_flag`)."""
    from tests.support.season_league import set_module_flag

    for module in modules:
        await set_module_flag(bot.db_path, module, on)


def interaction(bot: Any, command: str | None = None, *, user: Any = None) -> Any:
    """A member's interaction for `/<command>` (None: a form's or a button's), whose follow-up
    message records its edits for `said`."""
    double = member_interaction(bot, user=user)
    double.command = SimpleNamespace(qualified_name=command) if command else None
    return record_followups(double)


def record_followups(double: Any) -> Any:
    """Make *double*'s follow-up return a message recording its edits, as `said` reads them; the
    queue updates an acknowledgement sent as a follow-up by editing that message."""
    double.followup_messages = []

    async def _follow(content: Any = None, *args: Any, **kwargs: Any) -> Any:
        message = MagicMock()
        message.content = content if content is not None else kwargs.get("content")
        message.edits = []

        async def _edit(*_args: Any, **changes: Any) -> Any:
            if changes.get("content") is not None:
                message.edits.append(changes["content"])
            return message

        message.edit = AsyncMock(side_effect=_edit)
        double.followup_messages.append(message)
        return message

    double.followup.send = AsyncMock(side_effect=_follow)
    return double


def _text(call: Any) -> str:
    if call.args:
        return str(call.args[0]) if call.args[0] is not None else ""
    content = call.kwargs.get("content")
    return "" if content is None else str(content)


def said(double: Any) -> list[str]:
    """Every word the member saw, in order: the response and its edits, then each follow-up and
    that message's edits."""
    words = [_text(call) for call in double.response.send_message.await_args_list]
    words += [_text(call) for call in double.edit_original_response.await_args_list]
    for message in getattr(double, "followup_messages", []):
        words.append(str(message.content or ""))
        words += [str(edit) for edit in message.edits]
    return [word for word in words if word]


def last_said(double: Any) -> str:
    """What the member's reply reads now: the last word `said` holds."""
    words = said(double)
    return words[-1] if words else ""


async def log_lines(league: SettingsLeague) -> list[str]:
    """Every log line written: those the log channel took, then those waiting on a retry, each
    without the rule the log channel draws beneath a post."""
    taken = [unruled(str(line)) for line in league.bot.log_channel.sent]
    return taken + [unruled(line) for line in await queued_log_lines(league.db_path)]


def unruled(line: str) -> str:
    """*line* without the rule of "―" the log channel draws beneath it, where it carries one."""
    head, _, last = line.rpartition("\n")
    return head if head and last and set(last) == {"―"} else line


async def audit_rows(db_path: str) -> list[dict[str, Any]]:
    """Every audit entry, oldest first, its values decoded."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM audit_entries ORDER BY id")
        rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        for key in ("old_value", "new_value"):
            if isinstance(row.get(key), str):
                try:
                    row[key] = json.loads(row[key])
                except ValueError:
                    pass
    return rows


def fault_in(target: str, error: BaseException | None = None) -> AbstractContextManager[Any]:
    """Make the writer at *target* (a dotted path) raise inside the save: a database fault, by
    default, as a full disk gives one."""
    raised = error if error is not None else sqlite3.OperationalError("database or disk is full")
    return patch(target, AsyncMock(side_effect=raised))
