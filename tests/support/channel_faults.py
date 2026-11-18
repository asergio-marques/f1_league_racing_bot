"""A server to judge a division's channels upon, and a drawing saying which pictures attach a file,
for the tests of each module's channel faults.

A channel is looked for in the server's cache and then fetched, a fetch answering NotFound being
the one proof it is gone and one Discord refuses the bot proving it unusable, lacking View
Channel (`approval_checks.channel_presence`). A channel found is judged by the permissions the
bot holds in it, against what that channel's use needs.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock


def _cached(denied: tuple[str, ...]) -> Any:
    """A channel in the cache in which the bot holds every permission but those *denied*, each
    named as `discord.Permissions` names it (`embed_links`)."""
    permissions = MagicMock()
    for attribute in denied:
        setattr(permissions, attribute, False)
    channel = MagicMock()
    channel.permissions_for = MagicMock(return_value=permissions)
    return channel


def server_holding(
    *,
    gone: tuple[int, ...] = (),
    unreadable: tuple[int, ...] = (),
    unusable: Mapping[int, tuple[str, ...]] = {},
    forbidden: tuple[int, ...] = (),
) -> Any:
    """A server holding every channel but those *gone*, which a fetch reports not found, those
    *unreadable*, out of the cache and whose fetch fails with a server error, and those
    *forbidden*, out of the cache and whose fetch Discord refuses the bot. A channel in *unusable*
    is in the cache, the bot denied the permissions it maps to there; every other channel is in
    the cache with every permission held."""
    import discord

    async def _fetch(channel_id: int) -> Any:
        if channel_id in gone:
            raise discord.NotFound(MagicMock(status=404), "Unknown Channel")
        if channel_id in forbidden:
            raise discord.Forbidden(MagicMock(status=403), "Missing Access")
        raise discord.HTTPException(MagicMock(status=500), "Internal Server Error")

    def _get(channel_id: int) -> Any:
        if channel_id in gone or channel_id in unreadable or channel_id in forbidden:
            return None
        return _cached(unusable.get(channel_id, ()))

    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=_get)
    guild.fetch_channel = AsyncMock(side_effect=_fetch)
    return guild


def answer_attachments(drawing: Any, *aspects: str) -> Any:
    """Make *drawing*, a stand-in for the image module's, answer that the pictures of *aspects*
    could attach a file to a posting and no others: `aspect_attaches_files` by aspect, and
    `calendar_attaches_files` and `lineup_attaches_files` by whether `calendar` and `lineup` are
    among them. Returns *drawing*."""
    drawing.aspect_attaches_files = AsyncMock(side_effect=lambda aspect: aspect in aspects)
    drawing.calendar_attaches_files = AsyncMock(return_value="calendar" in aspects)
    drawing.lineup_attaches_files = AsyncMock(return_value="lineup" in aspects)
    return drawing
