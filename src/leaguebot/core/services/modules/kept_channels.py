"""The check a module's switch-on makes of the channels it kept while it was off.

A module switched off keeps its channels, so that switching it on again finds them as they stood.
Between the two, a channel may have been deleted, given to another setting (a switched-off module
answers for none of its channels, so any setting may take one), or have lost a permission the bot
needs there. `kept_channels_check` is the step the module's switch-on runs **before** its save
(`Setting.before`), and what it finds is handed to that save (`FOUND`, read with `found_in`), which
clears the channels another setting took and names every channel to set again (`named_reply`).

**It names no module and imports none.** Each switch-on hands it a reader of its own channels, built
on its own columns and its own `channel_needs`, and the hook the registry asks each module through;
core holds the one check and the words, never a module's columns. The reader is asked per run, not
per build, because what a channel needs can change while the module is off: a picture switched on
meanwhile may attach a file.

**What each channel is judged to be, in this order.** *Taken* where the registry finds another
setting holding it (`find_channel_use`; the module is off, so its own uses answer nothing, and so a
switched-off module that took it earlier does not count: of two switched-off modules holding one
channel, the first switched back on keeps it). Otherwise `channel_presence` judges it: *gone* where
it is no longer on the server, *unusable* where the bot lacks a permission the module needs there.
Anything else it raises, and a check that cannot be made ends the setting on a fault, nothing saved
(`setting_change`), as does a league server out of the bot's reach while channels are kept.

**An unusable channel ends the setting refused** (`HALTED`), naming each one and what it lacks and
the ways out: the league can act on it, which is not so of a fault. A channel gone or taken, found
in the same pass, is named by the next, successful switch-on, since the halt names only what stops
the module. Only the live season's divisions are read (`live_divisions_on`), as the registry guards
only those; a completed season's channels are not the module's to judge.

The step forms no log line: the halt's line is formed in `setting_change`, with every other.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import aiosqlite

from leaguebot.core.models.channel_needs import Needs
from leaguebot.core.services.seasons.approval_checks import PresenceState, channel_presence
from leaguebot.core.services.settings.channel_registry_service import (
    find_channel_use,
    live_divisions_on,
)
from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import StepFailedOnDiscord, StepKind, StepResult
from leaguebot.framework.services.queue.change_types import Step, StepContext
from leaguebot.framework.services.queue.setting_change import FOUND, HALTED, Before
from leaguebot.framework.utils.hooks import AwaitedHook
from leaguebot.framework.utils.league_server import league_guild

#: The name of the step, and so the key of its result in what the save is handed (`FOUND`).
CHECK_KEPT = "check_kept_channels"

#: What the reply names the channels to set again under.
SET_AGAIN = (
    "⚠️ Set these again: each is no longer on the server, or was given to another setting while "
    "the module was off:"
)


@dataclass(frozen=True)
class KeptChannel:
    """A channel a module kept for a division of the live season: *setting* is its key in
    `SETTING_LABELS`, *label* what a league calls it and *command* the command that sets it again,
    and *needs* what the module asks of the bot there."""

    setting: str
    division_id: int
    division_name: str
    channel_id: int
    label: str
    command: str
    needs: Needs


@dataclass(frozen=True)
class Taken:
    """A kept channel another setting took while the module was off, for the module's save to
    clear."""

    setting: str
    division_id: int
    channel_id: int


#: A module's reader of the channels it kept: handed a connection and the live season's divisions
#: (id to name), it gives each kept channel of those divisions. It awaits only the connection.
KeptReader = Callable[[aiosqlite.Connection, Mapping[int, str]], Awaitable[list[KeptChannel]]]


def _whose(kept: KeptChannel) -> str:
    return f"**{kept.division_name}**'s {kept.label}"


def _halt(unusable: list[tuple[KeptChannel, tuple[str, ...]]], what: str) -> dict[str, str]:
    """The halt for the unusable channels: the reply the member reads and the reason the ⛔ line
    states."""
    bullets = "\n".join(
        f"• {_whose(kept)} <#{kept.channel_id}>: {', '.join(missing)}" for kept, missing in unusable
    )
    reply = (
        "⛔ The module is still off: kept channels are missing permissions the bot needs there:\n"
        f"{bullets}\n"
        "Give the bot them, delete the channel, or give it to another setting (the module's own "
        f"channel command is refused while it is off), then run {what} again."
    )
    reason = "kept channels are missing permissions the bot needs there: " + "; ".join(
        f"{_whose(kept)} ({', '.join(missing)})" for kept, missing in unusable
    )
    return {"reply": reply, "reason": reason}


def kept_channels_check(
    read: Callable[[], Awaitable[KeptReader]], *, channel_uses_read: AwaitedHook
) -> Before:
    """The step before a module's switch-on saves: each channel it kept for a division of the live
    season, judged as the module docstring says.

    *read* gives the module's reader of its kept channels, awaited each time the step runs;
    *channel_uses_read* is the hook the registry asks each module for the uses of a channel
    through. The step finds `{"named": bullets for the reply, "taken": the channels to clear,
    "details": lines for beneath the Success line}`, or halts where a channel is unusable.
    """

    async def run(ctx: StepContext) -> StepResult:
        reader = await read()
        async with get_connection(ctx.db_path) as db:
            kept = await reader(db, await live_divisions_on(db))
        if not kept:
            return StepResult(result={"named": [], "taken": [], "details": []})
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise StepFailedOnDiscord("the league's server is out of the bot's reach")
        named: list[str] = []
        details: list[str] = []
        taken: list[dict[str, Any]] = []
        unusable: list[tuple[KeptChannel, tuple[str, ...]]] = []
        for each in kept:
            use = await find_channel_use(
                ctx.db_path, each.channel_id, channel_uses_read=channel_uses_read
            )
            if use is not None:
                taken.append(
                    {"setting": each.setting, "division_id": each.division_id,
                     "channel_id": each.channel_id}
                )
                said = "was given to another setting and is cleared"
            else:
                presence = await channel_presence(guild, each.channel_id, each.needs)
                if presence.state is PresenceState.UNUSABLE:
                    unusable.append((each, presence.missing))
                    continue
                if presence.state is not PresenceState.GONE:
                    continue
                said = "is no longer on the server"
            named.append(f"• {_whose(each)} — `{each.command}`")
            details.append(f"{_whose(each)} {said} — `{each.command}`")
        if unusable:
            return StepResult(result={HALTED: _halt(unusable, ctx.what)})
        return StepResult(result={"named": named, "taken": taken, "details": details})

    return Before(Step(CHECK_KEPT, StepKind.ACT, run))


def found_in(payload: Mapping[str, Any]) -> tuple[list[str], list[Taken], list[str]]:
    """What the kept-channel check found, as the save of a switch-on is handed it: the bullets the
    reply names, the channels to clear and the detail lines beneath the Success line. All empty
    where the setting has no such check, or it found nothing."""
    found = (payload.get(FOUND) or {}).get(CHECK_KEPT) or {}
    return (
        list(found.get("named") or ()),
        [
            Taken(str(each["setting"]), int(each["division_id"]), int(each["channel_id"]))
            for each in found.get("taken") or ()
        ],
        list(found.get("details") or ()),
    )


def named_reply(reply: str, named: list[str]) -> str:
    """*reply* with, beneath it, the heading and each channel to set again, where *named* is not
    empty."""
    if not named:
        return reply
    return "\n".join([reply, SET_AGAIN, *named])


__all__ = [
    "CHECK_KEPT", "KeptChannel", "KeptReader", "SET_AGAIN", "Taken", "found_in",
    "kept_channels_check", "named_reply",
]
