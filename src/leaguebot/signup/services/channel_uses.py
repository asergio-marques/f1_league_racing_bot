"""The signup channel, handed to the two moments core asks each module its channels at.

Core's `channel_uses_read` hook asks each module, as a channel is set, whether it holds it, so that
a channel does one job; its `setting_channels_read` hook asks each for every channel it holds, which
the factory reset cleans up: the channel its setting names and the wizard channels it created. The
setting is signup's own column (`signup_module_config`), asked whatever the module's switch: a
channel held is held whether signup is on or off. Reads only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.channel_registry_service import (
    ChannelUse,
    DiscordTargets,
    channel_ids_in,
)


async def channel_uses(
    db_path: str, *, channel_id: int, divisions: Mapping[int, str]
) -> list[ChannelUse]:
    """The signup use of *channel_id*, which names no division, or none where signup's channel is
    another. The divisions are handed, as they are every subscriber, and not read."""
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
