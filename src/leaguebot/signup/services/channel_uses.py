"""The signup channel, handed to the two moments core asks each module its channels at.

Core's `channel_uses_read` hook asks each module, as a channel is set, whether it holds it, so that
a channel does one job; its `setting_channels_read` hook asks each for every channel it holds, which
the factory reset cleans up: the channel its setting names and the wizard channels it created. The
setting is signup's own column (`signup_module_config`). Signup answers for its channel only while
it is on: the channel is kept while it is off, and another setting may take it then, which the
switch-on's check finds (`kept_channels`). The factory reset's ask is made whatever the switch, as
it cleans up what the module holds in any state. Reads only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

import aiosqlite

from leaguebot.core.models.channel_needs import (
    EMBED,
    MANAGE_CHANNEL,
    MANAGE_PERMISSIONS,
    SEND,
    VIEW,
    Needs,
)
from leaguebot.framework.db.database import get_connection
from leaguebot.core.services.modules.kept_channels import KeptChannel, KeptReader
from leaguebot.core.services.settings.channel_registry_service import (
    ChannelUse,
    DiscordTargets,
    channel_ids_in,
)
from leaguebot.signup.models.switch import SWITCH_KEY

if TYPE_CHECKING:
    from leaguebot.framework.services.switches.module_service import ModuleService

#: The signup channel as a league calls it, and the command that sets it: what its faults name.
LABEL = "signup channel"
COMMAND = "/signup channel"

#: What the bot needs on the signup channel to post there: the information post with its Sign Up
#: button is an embed (`window_changes`), so View Channel, Send Messages and Embed Links.
POSTING_NEEDS: Needs = (VIEW, SEND, EMBED)
#: What the bot needs on the signup channel to set who may see it (`channel_setting`): Manage
#: Channel and Manage Permissions.
OVERWRITE_NEEDS: Needs = (MANAGE_CHANNEL, MANAGE_PERMISSIONS)


def channel_needs() -> Needs:
    """What the signup channel needs of the bot, stated once: its posting needs
    (`POSTING_NEEDS`), which `/signup open` checks before it posts, and its overwrite needs
    (`OVERWRITE_NEEDS`)."""
    return POSTING_NEEDS + OVERWRITE_NEEDS


async def kept_channels() -> KeptReader:
    """The reader of the signup channel signup keeps, for the check its switch-on makes
    (`core/services/modules/kept_channels.py`): given a connection, it gives the channel
    `signup_module_config` names, whatever the season, where one is set, with what it needs of the
    bot (`channel_needs`), and no division. The divisions it is handed are not read. Asked per run,
    as weather's is, though what signup's channel needs does not change while it is off. Reads
    only."""
    needs = channel_needs()

    async def read(db: aiosqlite.Connection, divisions: Mapping[int, str]) -> list[KeptChannel]:
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_module_config WHERE signup_channel_id IS NOT NULL"
        )
        row = await cursor.fetchone()
        if row is None:
            return []
        return [KeptChannel("signup", None, None, int(row[0]), LABEL, COMMAND, needs)]

    return read


async def channel_uses(
    modules: "ModuleService", db_path: str, *, channel_id: int, divisions: Mapping[int, str]
) -> list[ChannelUse]:
    """The signup use of *channel_id*, which names no division, or none where signup's channel is
    another, and always none where signup is off: a switched-off module answers for none of its
    channels, which it keeps, and the switch-on clears one another setting took
    (`kept_channels`). The divisions are handed, as they are every subscriber, and not read."""
    if not await modules.is_enabled(SWITCH_KEY):
        return []
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM signup_module_config WHERE signup_channel_id = ? LIMIT 1", (channel_id,)
        )
        return [ChannelUse("signup")] if await cursor.fetchone() is not None else []


async def setting_channels(db_path: str, *, division_ids: Sequence[int]) -> DiscordTargets:
    """The channel signup's setting names, where it is set, and, apart from it, the channel each
    open wizard was created in. The divisions are handed, as they are every subscriber, and not
    read."""
    async with get_connection(db_path) as db:
        named = await channel_ids_in(
            db,
            "SELECT signup_channel_id FROM signup_module_config WHERE signup_channel_id IS NOT NULL",
            "signup_module_config.signup_channel_id",
        )
        created = await channel_ids_in(
            db,
            "SELECT signup_channel_id FROM signup_wizard_records "
            "WHERE signup_channel_id IS NOT NULL",
            "signup_wizard_records.signup_channel_id",
        )
    return DiscordTargets(created=tuple(sorted(created)), channels=tuple(sorted(named)))
