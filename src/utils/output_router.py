"""OutputRouter — single chokepoint for all channel writes.

Constitution Principle VII: Two output channel categories only:
  1. Forecast channels  (per-division)
  2. Calculation log channel  (per-server)

No other channel receives bot messages.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from discord.ext.commands import Bot
    from models.division import Division

log = logging.getLogger(__name__)


class OutputRouter:
    """Routes all bot output to the correct channels with error isolation."""

    def __init__(self, bot: "Bot") -> None:
        self._bot = bot

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def post_forecast(self, division: "Division", content: str) -> None:
        """Post *content* to the division's forecast channel.

        On failure, attempts to surface an alert to the log channel.
        """
        channel_id = division.forecast_channel_id
        await self._send(channel_id, content, fallback_label="forecast")

    async def post_log(self, server_id: int, content: str) -> None:
        """Post *content* to the server's calculation log channel.

        On failure, attempts to surface an alert to the interaction channel.
        """
        config = await self._bot.config_service.get_server_config(server_id)
        if config is None:
            log.error("post_log: no server config found for server_id=%s", server_id)
            return

        channel_id = config.log_channel_id
        success = await self._send(channel_id, content, fallback_label="log")

        if not success:
            # Last resort: try interaction channel
            await self._send(
                config.interaction_channel_id,
                f"⚠️ Failed to write to log channel (id={channel_id}). "
                f"Please check bot permissions.",
                fallback_label="interaction (last resort)",
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send(
        self, channel_id: int, content: str, *, fallback_label: str = "unknown"
    ) -> bool:
        """Attempt to send *content* to *channel_id*.

        Returns True on success, False on failure. Never raises.
        """
        channel = self._bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                log.error(
                    "_send: cannot fetch %s channel id=%s: %s",
                    fallback_label, channel_id, exc,
                )
                return False

        if not isinstance(channel, discord.TextChannel):
            log.error(
                "_send: channel id=%s is not a TextChannel (got %s)",
                channel_id, type(channel).__name__,
            )
            return False

        try:
            # Discord messages have a 2000-char limit; chunk if needed
            for chunk in _chunk_message(content):
                await channel.send(chunk)
            return True
        except discord.Forbidden as exc:
            log.error(
                "_send: missing permissions for %s channel id=%s: %s",
                fallback_label, channel_id, exc,
            )
        except discord.HTTPException as exc:
            log.error(
                "_send: HTTP error posting to %s channel id=%s: %s",
                fallback_label, channel_id, exc,
            )
        return False


def _chunk_message(content: str, limit: int = 1990) -> list[str]:
    """Split *content* into chunks that fit within Discord's message limit."""
    if len(content) <= limit:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= limit:
            chunks.append(content)
            break
        split_at = content.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(content[:split_at])
        content = content[split_at:].lstrip("\n")
    return chunks
