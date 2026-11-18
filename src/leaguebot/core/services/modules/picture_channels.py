"""The channels a picture posts to, and the check made of them before an output is switched on.

An output switched to a picture posts a file, so every channel it posts to must take one, and
whatever else its use needs. `picture_channels_check` is the step run **before** the save of the
switch (`Setting.before`), and again by the image module's own switch-on, with the module's own
words for what it found (*refusal*). It judges nothing that is not about to be a picture: an
output left as text has no channel judged, and switching an output off asks nothing.

**It names no module and imports none.** The caller hands it which outputs are switched on
(*aspects_of*, read on the connection the step holds, from the change's payload) and the hook
`picture_channels_read`, which each module and core answer for the outputs they post: each signs
up a function handing back the
`PictureChannel`s of its own outputs asked for, given the connection, the divisions of the live
season (those not cancelled: the bot posts to no cancelled division's channel; a finished
season's channels are not judged either) and the aspects switched on. A module answers only while
it is on, as the registry's `channel_uses_read` does: a switched-off module posts nowhere. Core
answers its own channels, for the lineup and the calendar alone (`core_picture_channels`), and
each module, whose needs are its own, answers for its own. Every need is asked **as if the output
were a picture** (`attaching`), whatever its switch holds now.

**A channel is judged once**, however many outputs post to it: core's answer names the bot command
channel once, for the lineup and the calendar together. Each is judged strictly
(`channel_presence`): one unusable, lacking a permission, or gone, is collected, and anything
else it raises, ending the setting on a fault with nothing saved, as does a league server out of
the bot's reach. Where nothing is answered, no server is looked for. Anything collected is handed
to *refusal*, which words it, and ends the setting refused (`HALTED`).

The step forms no log line: the halt's line is formed in `setting_change`, with every other.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

import aiosqlite

from leaguebot.core.models.channel_needs import Needs, attaching
from leaguebot.core.services.modules.image_drawing import ImageDrawing
from leaguebot.core.services.seasons.approval_checks import (
    DIVISION_CHANNELS,
    Presence,
    PresenceState,
    channel_presence,
    DIVISION_CHANNEL_NEEDS,
)
from leaguebot.core.services.settings.bot_settings import INTERACTION_CHANNEL_NEEDS
from leaguebot.core.services.settings.channel_registry_service import (
    ChannelUse,
    live_divisions_on,
)
from leaguebot.core.services.settings.config_service import ConfigService
from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import StepFailedOnDiscord, StepKind, StepResult
from leaguebot.framework.services.queue.change_types import Step, StepContext
from leaguebot.framework.services.queue.setting_change import HALTED, Before
from leaguebot.framework.utils.hooks import AwaitedHook
from leaguebot.framework.utils.league_server import league_guild

#: The name of the step.
CHECK_PICTURE_CHANNELS = "check_picture_channels"

#: The two outputs core posts, which core alone answers channels for.
CORE_ASPECTS = frozenset({"lineup", "calendar"})


@dataclass(frozen=True)
class PictureChannel:
    """A channel an output about to be a picture posts to: the *aspects* of the outputs that post
    there, the *channel_id*, what it is used for (*use*), the *command* that sets it again and what
    the posting *needs* of the bot there, Attach Files among it."""

    aspects: frozenset[str]
    channel_id: int
    use: ChannelUse
    command: str
    needs: Needs


#: Each of the live season's divisions that is handed, and so is not cancelled, by id to name.
Divisions = Mapping[int, str]

#: Reads which outputs the change switches on, on the connection the step holds, from the payload
#: of the change.
AspectsOf = Callable[[aiosqlite.Connection, dict[str, Any]], Awaitable[frozenset[str]]]

#: Words what the check collected, each channel with what the bot proved of it, as the reply and
#: the reason the ⛔ line states (`HALTED`); handed the payload of the change.
Refusal = Callable[[list[tuple[PictureChannel, Presence]], dict[str, Any]], dict[str, str]]


async def core_picture_channels(
    config: ConfigService,
    drawing: ImageDrawing,
    *,
    db: aiosqlite.Connection,
    divisions: Divisions,
    aspects: Collection[str],
) -> list[PictureChannel]:
    """Core's own channels for the outputs it posts, `lineup` and `calendar`, among *aspects*: each
    of *divisions*' lineup and calendar channel that is set and its output asked, and the bot
    command channel, once, for the outputs asked (a season's review posts both there). Each is
    answered with what its use needs and Attach Files. Nothing for an output that is not core's.
    A channel not set is not answered: the setting is for the league to make, not this check.

    What a channel needs as a picture is fixed (`DIVISION_CHANNEL_NEEDS`,
    `INTERACTION_CHANNEL_NEEDS`, with Attach Files), so the image module is not asked what a picture
    could attach now: *drawing* is not asked, for nothing but the connection is awaited while the
    step holds it (architecture.md, "The database"), and the parameter keeps the shape every
    answer to the hook is built in.
    """
    asked = CORE_ASPECTS & set(aspects)
    if not asked or not divisions:
        return await _command_channel(config, db, asked)
    cursor = await db.execute(
        "SELECT id, lineup_channel_id, calendar_channel_id FROM divisions "
        f"WHERE id IN ({', '.join('?' * len(divisions))}) ORDER BY id",
        tuple(divisions),
    )
    rows = await cursor.fetchall()
    answered: list[PictureChannel] = []
    for row in rows:
        for column, _label, setting in DIVISION_CHANNELS:
            if setting not in asked or not row[column]:
                continue
            answered.append(
                PictureChannel(
                    frozenset({setting}), int(row[column]),
                    ChannelUse(setting, divisions[row["id"]]),
                    f"/division {setting}-channel",
                    attaching(DIVISION_CHANNEL_NEEDS[setting], True),
                )
            )
    return answered + await _command_channel(config, db, asked)


async def _command_channel(
    config: ConfigService, db: aiosqlite.Connection, asked: Collection[str]
) -> list[PictureChannel]:
    """The bot command channel, answered once for *asked* where any is and the channel is set."""
    if not asked:
        return []
    server = await config.get_server_config_on(db)
    if server is None or not server.interaction_channel_id:
        return []
    return [
        PictureChannel(
            frozenset(asked), int(server.interaction_channel_id), ChannelUse("interaction"),
            "/bot interaction-channel", attaching(INTERACTION_CHANNEL_NEEDS, True),
        )
    ]


def fault_line(channel: PictureChannel, presence: Presence) -> str:
    """The line naming a channel the check collected, for a reply to lay one to a line: the
    channel, what it is used for, and what the bot lacks there or that it is no longer on the
    server, with the command that sets it again."""
    owner = f"<#{channel.channel_id}>, the {channel.use.describe()}"
    if presence.state is PresenceState.GONE:
        return f"{owner}: no longer on the server — `{channel.command}`"
    return f"{owner}: {', '.join(presence.missing)}"


def picture_channels_check(
    aspects_of: AspectsOf, *, picture_channels_read: AwaitedHook, refusal: Refusal
) -> Before:
    """The step before an output is switched to a picture: each channel it posts to, judged as the
    module docstring says.

    *aspects_of* reads which outputs the change switches on, from its payload;
    *picture_channels_read* is the hook each module's answer is read through; *refusal* words
    what is collected, for the payload. The step finds nothing (`{}`) where no output is switched
    on or no channel is answered, or halts with what *refusal* gives. The connection is closed
    before Discord is asked.
    """

    async def run(ctx: StepContext) -> StepResult:
        async with get_connection(ctx.db_path) as db:
            aspects = await aspects_of(db, ctx.payload)
            if not aspects:
                return StepResult(result={})
            divisions = await live_divisions_on(db, cancelled=False)
            handed = await picture_channels_read.call(
                db=db, divisions=divisions, aspects=aspects
            )
        answered: list[PictureChannel] = [channel for each in handed for channel in each]
        if not answered:
            return StepResult(result={})
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise StepFailedOnDiscord("the league's server is out of the bot's reach")
        judged: dict[tuple[int, Needs], Presence] = {}
        collected: list[tuple[PictureChannel, Presence]] = []
        for channel in answered:
            key = (channel.channel_id, channel.needs)
            if key not in judged:
                judged[key] = await channel_presence(guild, channel.channel_id, channel.needs)
            if judged[key].state is not PresenceState.PRESENT:
                collected.append((channel, judged[key]))
        if collected:
            return StepResult(result={HALTED: refusal(collected, ctx.payload)})
        return StepResult(result={})

    return Before(Step(CHECK_PICTURE_CHANNELS, StepKind.ACT, run))


__all__ = [
    "CHECK_PICTURE_CHANNELS", "CORE_ASPECTS", "AspectsOf", "Divisions", "PictureChannel",
    "Refusal", "core_picture_channels", "fault_line", "picture_channels_check",
]
