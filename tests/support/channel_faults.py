"""A server to judge a division's channels upon, for the tests of each module's channel faults.

A channel is looked for in the server's cache and then fetched, a fetch answering NotFound being
the one proof it is gone (`approval_checks.channel_on_server`).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock


def server_holding(*, gone: tuple[int, ...] = (), unreadable: tuple[int, ...] = ()) -> Any:
    """A server holding every channel but those *gone*, which a fetch reports not found, and
    those *unreadable*, out of the cache and whose fetch fails with a server error."""
    import discord

    async def _fetch(channel_id: int) -> Any:
        if channel_id in gone:
            raise discord.NotFound(MagicMock(status=404), "Unknown Channel")
        raise discord.HTTPException(MagicMock(status=500), "Internal Server Error")

    guild = MagicMock()
    guild.get_channel = MagicMock(
        side_effect=lambda cid: None if cid in gone or cid in unreadable else MagicMock()
    )
    guild.fetch_channel = AsyncMock(side_effect=_fetch)
    return guild
