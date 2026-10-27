"""The jobs the results dialogues share (#439, slice 6b): a channel created, a prompt posted.

A round's submission channel, an amendment's channel and the prompts posted in them are made by
jobs of a change, so a refusal stops the queue with Retry and Discard rather than leaving a
manager waiting on a prompt that never came. A dialogue's change plans these jobs and the
queue saves and resumes them.

- **`create_channel_step(record)`** is an `ACT`: it creates a text channel beside the channel its
  payload names (in that channel's category), opened to the roles the payload lists. The
  dialogue hands the *record* that saves the channel's id in the save that marks the job done. A
  channel created whose save then failed is kept on the job, and the next try deletes it before
  it creates another, so a retry leaves no second channel. A creation Discord refuses stops the
  queue, naming the permission the bot lacks. Deleting a dialogue's channel is
  `review_posting.DELETE_CHANNEL`, reused.
- **`post_step(name)`** is an `ACT`: it posts one line in the dialogue's channel, mentioning the
  roles its payload names and nobody else; a refusal stops the queue. A dialogue's opening, its
  acknowledgements and its refusals are posted through it.
- **`post_prompt_step()`** is an `ACT`: it posts the prompt asking for one session's results, with
  its format hint, in the dialogue's channel; a refusal stops the queue.

**Why a record is handed in.** Each dialogue keeps its channel in a row of its own
(`round_submission_channels`, `round_amend_channels`), so the step cannot know which to write:
the dialogue that plans the step says, and `core` keeps no table of a module's rows.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import GuildUnavailable, StepFailedOnDiscord, StepKind, StepResult
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.services.change_queue import Step, StepContext
from leaguebot.core.services.owned_channel_handler import post_in_owned_channel
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.league_server import league_guild
from leaguebot.image.utils.tyre_compound import tyre_compound_list
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.results_post_service import delete_round_channel
from leaguebot.results.utils import results_formatter

CREATE_CHANNEL = "create_channel"
POST = "post"
POST_PROMPT = "post_prompt"

Record = Callable[[aiosqlite.Connection, StepContext, StepResult], Awaitable[None]]


async def _guild(ctx: StepContext) -> discord.Guild:
    guild = await league_guild(ctx.bot)
    if guild is None:
        raise GuildUnavailable("the league's server is not in the cache")
    return guild


def session_prompt(session_type: SessionType, *, is_sprint: bool, override_fl: bool = False) -> str:
    """The prompt asking for *session_type*'s results, with its format hint.

    A first submission's hint says the optional first line designates the fastest-lap holder
    where two drivers share the lap time; a resubmission's and an amendment's (*override_fl*)
    says it overrides the holder.
    """
    label = results_formatter.format_session_label(session_type, is_sprint=is_sprint)
    if session_type.is_qualifying:
        hint = (
            "Format: `Position, @Driver, Team, Tyre, BestLap, Gap`\n"
            "Team: its shorthand.\n"
            f"Tyre: {tyre_compound_list()} (or blank for none recorded)."
        )
    elif override_fl:
        hint = (
            "Format: `Position, @Driver, Team, TotalTime, FastestLap, TimePenalties`\n"
            "Team: its shorthand.\n"
            "Optional first line: `FL: @Driver` to override fastest-lap holder."
        )
    else:
        hint = (
            "Format: `Position, @Driver, Team, TotalTime, FastestLap, TimePenalties`\n"
            "Team: its shorthand.\n"
            "Optional first line: `FL: @Driver` to designate the fastest-lap holder "
            "(use when two drivers share the same lap time)."
        )
    return f"📋 Submit **{label}** results (one driver per line), or type `CANCELLED`.\n{hint}"


def create_channel_step(record: Record) -> Step:
    """The job that creates a dialogue's channel; *record* saves the channel it made.

    The payload is ``name``, ``beside_channel_id`` (the channel whose category the new one joins,
    none if that channel is gone) and, optionally, ``role_ids``: the roles that may read and write
    in it, beside the bot itself. It returns ``{"channel_id": <id>}``.
    """

    @posts_in_a_job
    async def run(ctx: StepContext) -> StepResult:
        guild = await _guild(ctx)
        payload = ctx.step_payload
        kept = (ctx.kept or {}).get("channel_id")
        if kept is not None:
            # A try that created the channel and failed to save its id left it standing.
            earlier = guild.get_channel(int(kept))
            if earlier is not None:
                try:
                    await delete_round_channel(earlier, reason="Replaced by a later try")
                except discord.NotFound:
                    pass
                except discord.HTTPException as error:
                    raise StepFailedOnDiscord(
                        "the channel an earlier try made could not be deleted",
                        result=dict(ctx.kept or {}),
                    ) from error
        beside_id = payload.get("beside_channel_id")
        beside = guild.get_channel(int(beside_id)) if beside_id is not None else None
        overwrites: dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
        }
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True, manage_messages=True
            )
        for role_id in payload.get("role_ids", ()):
            role = guild.get_role(int(role_id))
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        try:
            made = await guild.create_text_channel(
                name=str(payload["name"]),
                category=getattr(beside, "category", None),
                overwrites=overwrites,
                reason="Results channel created by bot",
            )
        except discord.HTTPException as error:
            raise StepFailedOnDiscord(
                "the channel could not be created",
                notice="the bot needs the Manage Channels permission and room in its category",
            ) from error
        return StepResult(result={"channel_id": made.id})

    async def describe(_ctx: StepContext) -> str:
        return "creating the results channel"

    return Step(CREATE_CHANNEL, StepKind.ACT, run, describe=describe, record=record)


@posts_in_a_job
async def _send(
    channel: discord.TextChannel, text: str, *, mentions: discord.AllowedMentions, refused: str,
    view: discord.ui.View | None = None,
) -> int:
    """The one place a dialogue's line is sent: *text* to *channel*, notifying only what
    *mentions* allows, and the id of the message posted.

    Both posting steps below send through it, so the module has one posting step to list and
    every post states who it may notify. *view*, where given, is the buttons the line carries.
    Raises `StepFailedOnDiscord` with *refused* where Discord refuses it."""
    try:
        return await post_in_owned_channel(channel, text, mentions=mentions, view=view)
    except discord.HTTPException as error:
        raise StepFailedOnDiscord(refused) from error


def post_step(
    name: str = POST,
    *,
    describing: str = "posting a line in the dialogue's channel",
    record: Record | None = None,
) -> Step:
    """The job that posts one line in a dialogue's channel, named *name* on the queue.

    The payload is ``channel_id``, ``text`` and, optionally, ``mention_role_ids`` and
    ``mention_user_ids``: the only roles and members the line may notify (none where absent). It
    returns ``{"message_id": <id>}``. *describing* is what a stop or a Discard says the job was
    doing; *record*, where given, is written in the save that marks the job done.
    """

    async def post(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        guild = await _guild(ctx)
        channel = guild.get_channel(int(payload["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            raise StepFailedOnDiscord("the dialogue's channel is not in the server")
        roles = [discord.Object(id=int(each)) for each in payload.get("mention_role_ids", ())]
        members = [discord.Object(id=int(each)) for each in payload.get("mention_user_ids", ())]
        mentions = discord.AllowedMentions(
            everyone=False, users=members or False, roles=roles or False
        )
        message_id = await _send(
            channel, str(payload["text"]), mentions=mentions, refused="the line could not be posted"
        )
        return StepResult(result={"message_id": message_id})

    async def describe(_ctx: StepContext) -> str:
        return describing

    return Step(name, StepKind.ACT, post, describe=describe, record=record)


def post_choice_step(
    name: str, view_for: Callable[[StepContext], Awaitable[discord.ui.View]], *, describing: str
) -> Step:
    """The job that posts a line carrying buttons in a dialogue's channel, named *name*.

    The payload is ``channel_id`` and ``text``; *view_for* builds the buttons from the job's
    context as it runs, and they notify nobody. It returns ``{"message_id": <id>}``.
    """

    async def post(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        guild = await _guild(ctx)
        channel = guild.get_channel(int(payload["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            raise StepFailedOnDiscord("the dialogue's channel is not in the server")
        message_id = await _send(
            channel, str(payload["text"]), mentions=discord.AllowedMentions.none(),
            refused="the choice could not be posted", view=await view_for(ctx),
        )
        return StepResult(result={"message_id": message_id})

    async def describe(_ctx: StepContext) -> str:
        return describing

    return Step(name, StepKind.ACT, post, describe=describe)


def post_prompt_step() -> Step:
    """The job that posts the prompt for one session in a dialogue's channel.

    The payload is ``channel_id``, ``round_id``, ``session_type`` and, optionally,
    ``override_fl`` (the wording of a resubmission's and an amendment's hint). It returns
    ``{"message_id": <id>}``.
    """

    async def post(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        guild = await _guild(ctx)
        channel = guild.get_channel(int(payload["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            raise StepFailedOnDiscord("the dialogue's channel is not in the server")
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT format FROM rounds WHERE id = ?", (int(payload["round_id"]),)
            )
            row = await cursor.fetchone()
        if row is None:
            raise LookupError(f"round {payload['round_id']} is not there to prompt for")
        text = session_prompt(
            SessionType(payload["session_type"]),
            is_sprint=RoundFormat(row["format"]) is RoundFormat.SPRINT,
            override_fl=bool(payload.get("override_fl", False)),
        )
        message_id = await _send(
            channel, text, mentions=discord.AllowedMentions.none(),
            refused="the prompt could not be posted",
        )
        return StepResult(result={"message_id": message_id})

    async def describe(_ctx: StepContext) -> str:
        return "posting the prompt for the next session"

    return Step(POST_PROMPT, StepKind.ACT, post, describe=describe)


__all__ = [
    "CREATE_CHANNEL",
    "POST",
    "POST_PROMPT",
    "create_channel_step",
    "post_choice_step",
    "post_prompt_step",
    "post_step",
    "session_prompt",
]
