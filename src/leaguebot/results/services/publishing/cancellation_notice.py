"""Results' own note of a round, a division or a season called off (#175, #283).

Results posts a note to the results channel that no results are coming, sent as a Discord
**silent** message: it sits in the channel for anyone reading it and pings nobody, attendance's
check-in notice being the one a driver is told by. It is a job of its own on the change queue
(`cancellation_changes`): a channel set and gone, or a send Discord refuses, raises
`StepFailedOnDiscord`, so that the queue stops until the channel stands again and Retry is
pressed; a channel never set is answered "no channel is set", which the cancellation names; and a
fault of the bot's own is raised as it is.

`notices_on` is results' subscriber to core's `cancellation_notices` hook, handing back the job
(`notify_results_steps`'s `NOTIFY_RESULTS`) that posts this note for the division told, inside the
save that calls it. The job itself reads its division from its own payload (`division_id`,
`division_name`, `season_id`, and a round's `round_number`, `track_name`), through *seasons*, and
drops itself, as it runs, where results is off.

**The note is kept so that it can be taken down.** A round's or a division's note records its
message with the job's mark (`record_note_on`, in `called_off_notes`, keyed by what was called off),
and switching results off reads and deletes it (`results_purge_service`). A season's note is not
recorded: the purge reads the live season alone, and a season called off is an archive.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.cancellation import (
    SCOPE_DIVISION,
    SCOPE_ROUND,
    CancellationNotices,
)
from leaguebot.core.models.change import PlannedStep, StepKind, StepResult
from leaguebot.core.services.cancellations.cancellation_notice_service import (
    post_cancellation_message,
    require_division,
    round_label,
)
from leaguebot.core.services.queue.change_types import Step, StepContext
from leaguebot.core.services.seasons.season_lifecycle_service import require_guild
from leaguebot.core.services.seasons.season_service import SeasonService
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.league_bot import LeagueBot

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService

NOTIFY_RESULTS = "notify_results"


def results_note(scope: str, division_name: str, round_number=None, track_name=None) -> str:
    """The results channel's note of *scope*'s cancellation, word for word."""
    if scope == SCOPE_ROUND:
        return (
            f"\U0001f4e2 **Round {round_number} Cancelled: {division_name}**\n"
            f"{round_label(round_number, track_name)} has been cancelled. "
            "No results will be posted for it."
        )
    if scope == SCOPE_DIVISION:
        return (
            f"\U0001f4e2 **Division Cancelled: {division_name}**\n"
            "No further results will be posted for this division."
        )
    return (
        "\U0001f4e2 **Season Cancelled**\n"
        "No further results will be posted this season."
    )


async def _results_channel(db_path: str, division_id: int) -> int | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT results_channel_id FROM division_results_config WHERE division_id = ?",
            (division_id,),
        )
        row = await cursor.fetchone()
    return None if row is None else row["results_channel_id"]


@posts_in_a_job
async def post_note(
    bot: LeagueBot,
    guild,
    division,
    *,
    scope: str,
    round_number: int | None = None,
    track_name: str | None = None,
) -> discord.Message | str:
    """Post results' note of *division*'s cancellation to its results channel, silently, as a job
    on the change queue. Returns the message, or "no channel is set" where the division has none;
    raises `StepFailedOnDiscord` (no cause) where the channel is set and gone, and `from`
    Discord's refusal where it refuses the send; any other exception is a fault of the bot's own
    and propagates unchanged.
    """
    channel_id = await _results_channel(bot.db_path, division.id)
    content = results_note(scope, division.name, round_number=round_number, track_name=track_name)
    return await post_cancellation_message(
        guild, channel_id, content, mentions=discord.AllowedMentions.none(), silent=True,
    )


@posts_in_a_job
async def post_notice(
    bot: LeagueBot,
    guild,
    division,
    *,
    scope: str,
    round_number: int | None = None,
    track_name: str | None = None,
) -> str | None:
    """:func:`post_note`, answering "no channel is set" where the division has none and nothing
    else."""
    posted = await post_note(
        bot, guild, division, scope=scope, round_number=round_number, track_name=track_name
    )
    return posted if isinstance(posted, str) else None


async def record_note_on(
    db: aiosqlite.Connection, ctx: StepContext, result: StepResult
) -> None:
    """Keep the note *result* says was posted, on *db* and in the job's own save, keyed by what
    was called off: the round (`round_id`) for a round, the division for a division. Whatever the
    key held before is replaced, so a note posted again is the one standing. A season's note, and
    a job that posted none, record nothing."""
    posted = result.result or {}
    payload = ctx.step_payload
    scope = payload.get("scope", ctx.payload.get("scope", SCOPE_ROUND))
    if "message_id" not in posted or scope not in (SCOPE_ROUND, SCOPE_DIVISION):
        return
    round_id = (
        int(payload["round_id"] if "round_id" in payload else ctx.payload["round_id"])
        if scope == SCOPE_ROUND else None
    )
    division_id = int(payload["division_id"])
    if round_id is None:
        await db.execute(
            "DELETE FROM called_off_notes WHERE division_id = ? AND round_id IS NULL",
            (division_id,),
        )
    else:
        await db.execute("DELETE FROM called_off_notes WHERE round_id = ?", (round_id,))
    await db.execute(
        "INSERT INTO called_off_notes (division_id, round_id, channel_id, message_id) "
        "VALUES (?, ?, ?, ?)",
        (division_id, round_id, str(posted["channel_id"]), str(posted["message_id"])),
    )


async def notices_on(
    _db: aiosqlite.Connection, scope: str, target: dict[str, Any]
) -> CancellationNotices:
    """Results' part of `cancellation_notices`: the results note planned for the division
    *target* names (`division_id`, `division_name`, `season_id`, and a round's `round_number`,
    `track_name`), so that core registers the job without naming results. The job's own payload
    carries *scope* beside *target*'s fields, and `notice_target` ("results channel"), the words
    core's `not_notified` names this job by without naming the module, read back by
    `notify_results_steps`. Planned after the rounds-called-off modules' `with_division` steps,
    as the results note is today."""
    return CancellationNotices(
        after_with_division=(
            PlannedStep(
                NOTIFY_RESULTS, {**target, "scope": scope, "notice_target": "results channel"}
            ),
        )
    )


def notify_results_steps(*, modules: "ModuleService", seasons: SeasonService) -> dict[str, Step]:
    """The step `cancellation_notices` plans for results (`NOTIFY_RESULTS`): posts this note for
    the division named in the job's own payload, due only while results is on. The builder hands
    in *modules* (the switch) and *seasons* (the division read) and signs this up to core's
    `cancellation_steps`.
    """

    async def notify_results(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        guild = await require_guild(ctx.bot)
        division = await require_division(seasons, payload["season_id"], payload["division_id"])
        posted = await post_note(
            ctx.bot, guild, division,
            scope=payload.get("scope", ctx.payload.get("scope", SCOPE_ROUND)),
            round_number=payload.get("round_number", ctx.payload.get("round_number")),
            track_name=payload.get("track_name", ctx.payload.get("track_name")),
        )
        if isinstance(posted, str):
            return StepResult(result={"unset": True})
        return StepResult(result={
            "sent": True, "message_id": posted.id, "channel_id": posted.channel.id,
        })

    async def results_on(_ctx: StepContext) -> bool:
        return await modules.is_results_enabled()

    async def describe(ctx: StepContext) -> str:
        return f"posting the results note for **{ctx.step_payload['division_name']}**"

    return {
        NOTIFY_RESULTS: Step(
            NOTIFY_RESULTS, StepKind.ACT, notify_results, still_due=results_on,
            describe=describe, record=record_note_on,
        ),
    }
