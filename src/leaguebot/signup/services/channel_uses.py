"""The signup channel, handed to the two moments core asks each module its channels at.

Core's `channel_uses_read` hook asks each module, as a channel is set, whether it holds it, so that
a channel does one job; its `setting_channels_read` hook asks each for every channel its settings
hold, which the factory reset cleans up. The channel is signup's own column (`signup_module_config`),
asked whatever the module's switch: a channel held is held whether signup is on or off. Reads only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.channel_registry_service import ChannelUse, channel_ids_in


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


async def setting_channels(db_path: str, *, division_ids: Sequence[int]) -> set[int]:
    """The channel signup's setting names, where it is set. The divisions are handed, as they are
    every subscriber, and not read."""
    async with get_connection(db_path) as db:
        return await channel_ids_in(db, "signup_module_config", "signup_channel_id")
