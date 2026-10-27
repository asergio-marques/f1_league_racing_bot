"""What every standing post shares: the job that deletes the message a post replaced.

A standing post (a calendar, a lineup, an attendance sheet, a results table, a forecast) is two
jobs of a change (architecture.md, "How a change is carried out", "Posts are remembered", and
"Posting to Discord"). The *post* job reads, as it runs, the message it replaces from the place
the owning module keeps it, posts (the picture on the first try alone, text on every later one)
and returns `{"new": <id>, "old": <id or None>, "channel_id": <id>, ...}`. Its `record` saves the
new id in the save that marks the job done, and it plans, through `StepResult.then`, the
`delete_replaced` job that `replaced` builds for the old message. A post discarded plans nothing,
so nothing is deleted; a deletion discarded leaves both messages standing, and `not_deleted` names
the earlier one for deletion by hand. No function under these flows commits its own id.

A try after a `record` that failed removes the copy it sent first: each post job begins with
`remove_kept`, which deletes the ids in the job's kept result and raises `StepFailedOnDiscord`,
keeping what is left, where Discord refuses.

The delete itself is the standing-post handler's (`standing_post_handler.delete_standing`),
which `_delete_replaced` and `remove_kept` both go through; this module keeps the jobs.

There are three delete sites, and this is the generic one. `review_posting`'s `delete_message` is
what a review uses for its tables, its sheet and its lineup, and weather's `delete_forecast` has a
`record` of its own, since a forecast's row is keyed by phase and is forgotten with its message.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import discord

from leaguebot.core.models.change import (
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
)
from leaguebot.core.services.change_queue import Step, StepContext, StepView
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.services.standing_post_handler import delete_standing
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild

DELETE_REPLACED = "delete_replaced"


def replaced(
    channel_id: int | None, old: int | str | None, new: int | None, *, what: str,
    division_name: str,
) -> tuple[PlannedStep, ...]:
    """The job that deletes *old*, the message a post replaced, or none where there is nothing to
    delete: no old message, the old one being the new one, or no channel to look in."""
    if old is None or channel_id is None or (new is not None and int(old) == int(new)):
        return ()
    return (PlannedStep(DELETE_REPLACED, {
        "channel_id": channel_id, "message_id": int(old), "what": what,
        "division_name": division_name,
    }),)


async def _guild(bot: LeagueBot) -> discord.Guild:
    guild = await league_guild(bot)
    if guild is None:
        raise GuildUnavailable("the league's server is not in the cache")
    return guild


async def _delete_replaced(ctx: StepContext) -> StepResult:
    """Delete the replaced message. A channel no longer on the server completes the job (the
    message went with it); a `NotFound` propagates, which the queue completes; any other Discord
    refusal stops the queue, keeping the message's link for a Discard to name."""
    payload = ctx.step_payload
    channel = as_text_channel(ctx.bot.get_channel(int(payload["channel_id"])))
    if channel is None:
        # Not in the cache at all: the league's server is asked, so that a server not yet cached
        # stops the queue rather than counting the message as gone with its channel.
        guild = await _guild(ctx.bot)
        channel = as_text_channel(guild.get_channel(int(payload["channel_id"])))
    if channel is None:
        return StepResult(result={"gone": True})
    message = channel.get_partial_message(int(payload["message_id"]))
    try:
        await delete_standing(message)
    except discord.NotFound:
        raise
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(
            f"the earlier {payload.get('what', 'message')} could not be deleted",
            result={"link": message.jump_url},
        ) from exc
    return StepResult(result={"deleted": True})


async def _describe_delete(ctx: StepContext) -> str:
    payload = ctx.step_payload
    return (
        f"deleting the earlier {payload.get('what', 'message')} of "
        f"**{payload.get('division_name', 'the division')}** in <#{payload['channel_id']}>"
    )


def delete_replaced_step(name: str = DELETE_REPLACED) -> Step:
    """The `delete_replaced` job, for a change type's `steps`. A change type whose delete wears
    another name in its job list (the check-in call's `delete_earlier`) builds the same step
    under *name*, so that every standing message comes down through the one delete."""
    return Step(
        name, StepKind.DELETE, _delete_replaced, describe=_describe_delete,
    )


async def remove_kept(bot: LeagueBot, kept: Mapping[str, Any] | None) -> None:
    """Delete the messages a post job's last try sent and could not record, before it posts again.

    *kept* is the job's kept result: `new`, one id or a list, in `channel_id`. A message already
    gone, or its channel, is as good as removed. One Discord refuses to delete fails the job
    again, keeping what is left in `new`.
    """
    if not kept:
        return
    raw = kept.get("new")
    channel_id = kept.get("channel_id")
    if raw is None or channel_id is None:
        return
    ids = [int(i) for i in (raw if isinstance(raw, (list, tuple)) else [raw])]
    guild = await _guild(bot)
    channel = as_text_channel(guild.get_channel(int(channel_id)))
    if channel is None:
        return
    left: list[int] = []
    failure: discord.HTTPException | None = None
    for message_id in ids:
        try:
            await delete_standing(channel.get_partial_message(message_id))
        except discord.NotFound:
            continue
        except discord.HTTPException as exc:
            left.append(message_id)
            failure = failure or exc
    if left:
        raise StepFailedOnDiscord(
            f"{len(left)} message(s) of the earlier try could not be removed",
            result={"new": left, "channel_id": int(channel_id)},
        ) from failure


def not_deleted(view: StepView) -> str | None:
    """The line naming a discarded `delete_replaced` for deletion by hand, or None for any other
    step or one not discarded."""
    if view.name != DELETE_REPLACED or not (view.result or {}).get("discarded"):
        return None
    payload = view.payload
    link = (view.result or {}).get("link") or f"in <#{payload['channel_id']}>"
    return (
        f"**{payload.get('division_name', 'the division')}** — its earlier "
        f"{payload.get('what', 'message')} could not be deleted ({link}): delete it by hand."
    )
