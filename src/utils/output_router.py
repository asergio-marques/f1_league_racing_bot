"""OutputRouter — single chokepoint for all channel writes.

Constitution Principle VII: Two output channel categories only:
  1. Forecast channels  (per-division)
  2. Calculation log channel  (the league's one)

No other channel receives bot messages.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

import discord

from utils.input_validator import ROLE_MENTION, USER_MENTION

#: Every mention a log line can carry, wrapped whole as code so it names without notifying.
#: Built from the shared forms (#362), so ``<@!123>`` is wrapped as ``<@123>`` is.
_MENTION_RE = re.compile(rf"((?:{USER_MENTION})|(?:{ROLE_MENTION}))")

if TYPE_CHECKING:
    from discord.ext.commands import Bot
    from models.division import Division

log = logging.getLogger(__name__)


class OutputRouter:
    """Routes all bot output to the correct channels with error isolation."""

    def __init__(self, bot: "Bot", retry_db_path: "Optional[str]" = None) -> None:
        self._bot = bot
        self._retry_db_path: Optional[str] = retry_db_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def post_forecast(
        self, division: "Division", content: str, *, enqueue_on_failure: bool = False
    ) -> "Optional[discord.Message]":
        """Post *content* to the division's forecast channel.

        Returns the ``discord.Message`` on success, or ``None`` on failure.
        On failure, enqueues for retry where *enqueue_on_failure* is set and retry_db_path
        is configured.
        """
        channel_id = division.forecast_channel_id
        return await self._send(
            channel_id, content, enqueue_on_failure=enqueue_on_failure,
            fallback_label="forecast",
        )

    async def post_log(self, content: str) -> "Optional[discord.Message]":
        """Post *content* to the league's calculation log channel.

        Mention syntax (<@id>, <@&id>) is wrapped in backticks so Discord
        renders them as plain text rather than interactive mentions.
        Channel links (<#id>) are left as-is so they remain clickable.
        A separator line is appended for readability.
        On failure, attempts to surface an alert to the interaction channel.

        Returns the ``discord.Message`` on success, or ``None`` on failure — as
        :meth:`post_forecast` already does. A caller that wants to point a reader at what
        it just logged can take ``jump_url`` from it; every other caller ignores it.

        The **first** message is returned where the content had to be split across
        Discord's limit, because a link is meant to land a reader at the top of the block
        rather than at its tail. This is the one respect in which it differs from
        :meth:`post_forecast`, which returns the last because its callers store the id to
        edit the message later.
        """
        content = _MENTION_RE.sub(r"`\1`", content)
        content = content + "\n" + "\u2015" * 36
        config = await self._bot.config_service.get_server_config()
        if config is None:
            log.error("post_log: the bot is not set up, so there is no log channel")
            return None

        channel_id = config.log_channel_id
        msg = await self._send(
            channel_id, content, enqueue_on_failure=True, fallback_label="log",
            return_first=True,
        )

        if msg is None:
            # Last resort: try interaction channel (no retry enqueue to avoid loops)
            await self._send(
                config.interaction_channel_id,
                f"⚠️ Failed to write to log channel (id={channel_id}). "
                f"Please check bot permissions.",
                enqueue_on_failure=False,
                fallback_label="interaction (last resort)",
            )

        return msg

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send(
        self,
        channel_id: int,
        content: str,
        *,
        enqueue_on_failure: bool = False,
        fallback_label: str = "unknown",
        return_first: bool = False,
    ) -> "Optional[discord.Message]":
        """Attempt to send *content* to *channel_id*.

        Returns the last ``discord.Message`` sent on success, ``None`` on failure.
        Never raises. On HTTP/Forbidden failure, enqueues for retry when
        retry_db_path is configured and *enqueue_on_failure* is set.

        *return_first* returns the **first** message instead, for a caller linking a
        reader to the start of what it wrote. It defaults to False because the forecast
        callers store the returned id to edit that message later, and returning a
        different one would repoint those edits.
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
                return None

        if not isinstance(channel, discord.TextChannel):
            log.error(
                "_send: channel id=%s is not a TextChannel (got %s)",
                channel_id, type(channel).__name__,
            )
            return None

        try:
            # Discord messages have a 2000-char limit; chunk if needed
            first_msg: Optional[discord.Message] = None
            last_msg: Optional[discord.Message] = None
            for chunk in _chunk_message(content):
                last_msg = await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                if first_msg is None:
                    first_msg = last_msg
            return first_msg if return_first else last_msg
        except discord.Forbidden as exc:
            log.error(
                "_send: missing permissions for %s channel id=%s: %s",
                fallback_label, channel_id, exc,
            )
            await self._enqueue_if_configured(enqueue_on_failure, channel_id, content, str(exc))
        except discord.HTTPException as exc:
            log.error(
                "_send: HTTP error posting to %s channel id=%s: %s",
                fallback_label, channel_id, exc,
            )
            await self._enqueue_if_configured(enqueue_on_failure, channel_id, content, str(exc))
        return None

    async def _enqueue_if_configured(
        self,
        wanted: bool,
        channel_id: int,
        content: str,
        failure_reason: str,
    ) -> None:
        """Persist a failed message for retry, where *wanted* and retry_db_path is set.

        *wanted* is False for the interaction-channel last resort, which would otherwise
        queue a retry of the notice that its own failure had already failed to deliver.
        """
        if self._retry_db_path and wanted:
            try:
                from services.retry_service import enqueue
                await enqueue(self._retry_db_path, channel_id, content, failure_reason)
            except Exception as exc:
                log.error("_enqueue_if_configured: failed to enqueue: %s", exc)


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
