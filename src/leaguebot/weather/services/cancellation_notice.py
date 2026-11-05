"""Weather's own note of a round, a division or a season called off (#175, #283).

Weather posts a note to the forecast channel that no forecast is coming, sent as a Discord
**silent** message: it sits in the channel for anyone reading it and pings nobody, attendance's
check-in notice being the one a driver is told by. It is a job of its own on the change queue
(`cancellation_changes`): a channel set and gone, or a send Discord refuses, raises
`StepFailedOnDiscord`, so that the queue stops until the channel stands again and Retry is
pressed; a channel never set is answered "no channel is set", which the cancellation names; and a
fault of the bot's own is raised as it is.

`notices_on` is weather's subscriber to core's `cancellation_notices` hook, handing back the job
(`notify_forecast_steps`'s `NOTIFY_FORECAST`) that posts this note for the division told, inside
the save that calls it. The job itself reads its division from its own payload (`division_id`,
`division_name`, `season_id`, and a round's `round_number`, `track_name`), through *seasons*, and
drops itself, as it runs, where weather is off.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.cancellation import SCOPE_DIVISION, SCOPE_ROUND, CancellationNotices
from leaguebot.core.models.change import PlannedStep, StepFailedOnDiscord, StepKind, StepResult
from leaguebot.core.services.change_queue import Step, StepContext
from leaguebot.core.services.notice_handler import send_notice
from leaguebot.core.services.season_service import SeasonService
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService

NOTIFY_FORECAST = "notify_forecast"


def _round_label(round_number: int, track_name: str | None) -> str:
    return f"Round {round_number} ({track_name or 'Mystery'})"


def weather_note(scope: str, division_name: str, round_number=None, track_name=None) -> str:
    """The forecast channel's note of *scope*'s cancellation, word for word."""
    if scope == SCOPE_ROUND:
        return (
            f"\U0001f4e2 **Round {round_number} Cancelled: {division_name}**\n"
            f"{_round_label(round_number, track_name)} has been cancelled. "
            "No weather forecast will be posted for it."
        )
    if scope == SCOPE_DIVISION:
        return (
            f"\U0001f4e2 **Division Cancelled: {division_name}**\n"
            "No further weather forecasts will be posted for this division."
        )
    return (
        "\U0001f4e2 **Season Cancelled**\n"
        "No further weather forecasts will be posted this season."
    )


async def _forecast_channel(db_path: str, division_id: int) -> int | None:
    """`divisions.forecast_channel_id`, weather's own column on core's `divisions` table
    (architecture.md, "The database"; `forecast_channel.py`'s own read of it)."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT forecast_channel_id FROM divisions WHERE id = ?", (division_id,),
        )
        row = await cursor.fetchone()
    return None if row is None else row["forecast_channel_id"]


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
    """Post weather's note of *division*'s cancellation to its forecast channel, silently, as a
    job on the change queue. Returns "no channel is set" where the division has none; raises
    `StepFailedOnDiscord` (no cause) where the channel is set and gone, and `from` Discord's
    refusal where it refuses the send; any other exception is a fault of the bot's own and
    propagates unchanged.
    """
    channel_id = await _forecast_channel(bot.db_path, division.id)
    if not channel_id:
        return "no channel is set"
    channel = guild.get_channel(int(channel_id)) if guild is not None else None
    if channel is None:
        raise StepFailedOnDiscord(f"the channel <#{channel_id}> is no longer on the server")
    content = weather_note(scope, division.name, round_number=round_number, track_name=track_name)
    try:
        await send_notice(
            channel, content, mentions=discord.AllowedMentions.none(), silent=True,
        )
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(
            f"the message could not be posted to <#{channel_id}>: {exc}"
        ) from exc
    return None


async def notices_on(
    _db: aiosqlite.Connection, scope: str, target: dict[str, Any]
) -> CancellationNotices:
    """Weather's part of `cancellation_notices`: the forecast note planned for the division
    *target* names (`division_id`, `division_name`, `season_id`, and a round's `round_number`,
    `track_name`), so that core registers the job without naming weather. The job's own payload
    carries *scope* beside *target*'s fields, and `notice_target` ("forecast channel"), the
    words core's `not_notified` names this job by without naming the module, read back by
    `notify_forecast_steps`. Planned after the rounds-called-off modules' `with_division` steps,
    as the forecast note is today."""
    return CancellationNotices(
        after_with_division=(
            PlannedStep(
                NOTIFY_FORECAST, {**target, "scope": scope, "notice_target": "forecast channel"}
            ),
        )
    )


def notify_forecast_steps(*, modules: "ModuleService", seasons: SeasonService) -> dict[str, Step]:
    """The step `cancellation_notices` plans for weather (`NOTIFY_FORECAST`): posts this note for
    the division named in the job's own payload, due only while weather is on. The builder hands
    in *modules* (the switch) and *seasons* (the division read) and signs this up to core's
    `cancellation_steps`.
    """

    async def notify_forecast(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        guild = await league_guild(ctx.bot)
        division = next(
            (
                each
                for each in await seasons.get_divisions(int(payload["season_id"]))
                if each.id == int(payload["division_id"])
            ),
            None,
        )
        if division is None:
            raise LookupError(f"division {payload['division_id']} is no longer there")
        problem = await post_notice(
            ctx.bot, guild, division,
            scope=payload.get("scope", ctx.payload.get("scope", SCOPE_ROUND)),
            round_number=payload.get("round_number", ctx.payload.get("round_number")),
            track_name=payload.get("track_name", ctx.payload.get("track_name")),
        )
        return StepResult(result={"unset": True} if problem else {"sent": True})

    async def weather_on(_ctx: StepContext) -> bool:
        return await modules.is_weather_enabled()

    async def describe(ctx: StepContext) -> str:
        return f"posting the forecast note for **{ctx.step_payload['division_name']}**"

    return {
        NOTIFY_FORECAST: Step(
            NOTIFY_FORECAST, StepKind.ACT, notify_forecast, still_due=weather_on,
            describe=describe,
        ),
    }
