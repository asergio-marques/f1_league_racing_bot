"""What a round's check-in changes share: the names of their jobs, and the round as they read it.

The check-in is five changes, each in a module of its own: the call (`check_in_call`), its last
notice (`check_in_last_notice`), its deadline (`check_in_deadline`), its clean-up and the
take-down of its call (`check_in_clean_up`), and the steps an amendment and `/test-mode advance`
plan over them (`check_in_amendment`). This module holds what they read alike, so that none
imports another for it: the names of their jobs; the round as a check-in reads it (`call_row`,
with `round_not_owed` judging what a round is owed, `scheduled_at_of` and `deadline_of` its
moments); the judgement of a timer's ask under test mode (`left_to_advance`); and the factory of
a call's buttons (`CheckInViewFactory`), which wiring hands to the jobs that post or redraw one.

The posts of a check-in go through core's notice handler, each stating the mentions the post may
notify where it is sent (architecture.md, "Posting to Discord").
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

import aiosqlite
import discord

from leaguebot.attendance.services.module_switch_settings import attendance_enabled_on
from leaguebot.attendance.services.test_mode_steps import check_ins_cleared
from leaguebot.core.models.change import ChangeOrigin
from leaguebot.core.models.round import (
    ROUND_CANCELLABLE,
    AttendanceWindows,
    read_scheduled_at,
)
from leaguebot.core.services.queue.change_types import CheckContext, StepContext
from leaguebot.core.services.rounds import track_service
from leaguebot.core.services.rounds.round_moments import round_event_moments
from leaguebot.core.services.seasons import season_service
from leaguebot.core.services.seasons.season_end_changes import test_mode_on

#: How the buttons of a call are built for a round's id. A service may not import the cog that
#: owns them, so wiring hands this to the jobs that post or redraw a call.
CheckInViewFactory = Callable[[int], discord.ui.View]

GIVE_UP = "give_up"
POST_CALL = "post_call"
DELETE_EARLIER = "delete_earlier"
POST_LAST_NOTICE = "post_last_notice"
DISTRIBUTE = "distribute"
CLOSE_WITHOUT_CALL = "close_without_call"
LOCK_CALL = "lock_call"
ANNOUNCE = "announce"
PLAN_CLEAN_UP = "plan"
DELETE_MESSAGE = "delete_message"
MARK_CLEARED = "mark_cleared"
#: The job the amendment plans to take a standing check-in down a day after the round.
CLEAN_UP_CHECK_IN = "clean_up_check_in"
#: Taking a round's call down when a cancellation calls the round off (`take_down_steps`).
TAKE_DOWN_CALL = "take_down_call"


def _value(ctx: StepContext, key: str, default: Any = None) -> Any:
    """The job's own value, else the change's."""
    return ctx.step_payload.get(key, ctx.payload.get(key, default))


#: A round as its check-in reads it: its row, with `attendance_on` from attendance's own switch
#: reader (`attendance_enabled_on`).
CallRow = Mapping[str, Any]


async def call_row(db: aiosqlite.Connection, round_id: int) -> CallRow | None:
    """The round, its division and season, its track and the check-in settings, as one row, read on
    *db*: core's tables through core's readers, attendance's own settings and its column on `rounds`
    by its own, or None where the round is gone."""
    facts = await season_service.round_facts_on(db, round_id)
    if facts is None:
        return None
    division = await season_service.division_on(db, facts.division_id)
    track = await track_service.get_track_by_name(db, facts.track_name) if facts.track_name else None
    config = await (await db.execute(
        "SELECT rsvp_deadline_hours, rsvp_notice_days, rsvp_last_notice_hours "
        "FROM attendance_config LIMIT 1"
    )).fetchone()
    return {
        "id": facts.round_id, "division_id": facts.division_id,
        "round_number": facts.round_number, "format": facts.format.value,
        "track_name": facts.track_name, "scheduled_at": facts.scheduled_at.isoformat(),
        "round_status": facts.status.value, "not_held": int(facts.not_held),
        "checkin_cleared": int(bool(await check_ins_cleared(db, [round_id]))),
        "division_status": facts.division_status.value, "division_name": facts.division_name,
        "division_tier": facts.division_tier,
        "mention_role_id": division.mention_role_id if division else None,
        "season_id": facts.season_id, "season_number": facts.season_number,
        "track_gp_name": track["gp_name"] if track else None,
        "track_country": track["country"] if track else None,
        "deadline_hours": config["rsvp_deadline_hours"] if config else None,
        "notice_days": config["rsvp_notice_days"] if config else None,
        "last_notice_hours": config["rsvp_last_notice_hours"] if config else None,
        "attendance_on": await attendance_enabled_on(db),
    }


async def standing_call_row(db: aiosqlite.Connection, round_id: int) -> aiosqlite.Row | None:
    cursor = await db.execute(
        "SELECT message_id FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
    )
    return await cursor.fetchone()


def round_not_owed(row: CallRow | None) -> str | None:
    """Why the round is owed nothing of its check-in (no call, no notice), else None."""
    if row is None:
        return "the round is gone"
    if row["round_status"] not in ROUND_CANCELLABLE:
        return "the round is cancelled or its results are in"
    if row["division_status"] == "CANCELLED":
        return "the division is cancelled"
    if not row["attendance_on"]:
        return "the attendance module is off"
    if row["checkin_cleared"]:
        return "the round's check-in is over"
    return None


#: How long after its moment a timer's ask still counts as on time under test mode: the five
#: minutes the scheduler's own jobs are given, so that a timer a restart fires late is told from
#: one that fires as it falls due.
_ON_TIME = timedelta(minutes=5)


async def left_to_advance(
    db: aiosqlite.Connection, row: CallRow | None, kind: str, ctx: CheckContext, *, now: datetime
) -> str | None:
    """Why a timer's call or last notice is left to `/test-mode advance`, else None.

    **Under test mode a restart posts neither late** (core specification, "Test mode"): the timers
    are armed with no lateness limit, so one that fell due while the bot was down fires when it is
    back, and the change judges its own moment against *now*, the queue's clock. A bot's request
    that is not advance's and is asked more than `_ON_TIME` after the moment of *kind*
    (`rsvp_notice` or `rsvp_last_notice`) is not due, and the round's check-in is left open for
    the advance to take in its turn. A call replacing another, an amendment's, is not judged so.

    Lateness is judged **at the ask only** (`ctx.change_id` is None), where a timer fires: a
    request accepted on time is never dropped as the change starts for the queue's delay, a
    stopped or busy lane having kept it waiting while the bot was up.
    """
    if (
        row is None
        or ctx.change_id is not None
        or ctx.origin is not ChangeOrigin.BOT
        or ctx.payload.get("advanced")
        or ctx.payload.get("replaces") is not None
        or not await test_mode_on(db)
    ):
        return None
    moments = round_event_moments(
        scheduled_at_of(row), weather=None,
        attendance=AttendanceWindows(
            row["notice_days"] or 0, row["last_notice_hours"] or 0, row["deadline_hours"] or 0
        ),
    )
    moment = moments.get(kind)
    if moment is None or now - moment <= _ON_TIME:
        return None
    return "test mode leaves a moment that passed before the bot came up to /test-mode advance"


def scheduled_at_of(row: CallRow) -> datetime:
    return read_scheduled_at(str(row["scheduled_at"]))


def deadline_of(row: CallRow) -> datetime:
    return scheduled_at_of(row) - timedelta(hours=row["deadline_hours"] or 0)


