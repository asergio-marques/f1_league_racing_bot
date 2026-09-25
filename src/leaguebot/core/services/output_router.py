"""OutputRouter — the one writer of the log channel, and the poster of a forecast's text.

It is **not** the way every post leaves the bot. A graphic, an embed, a message carrying
buttons, and every post the bot later replaces in place are sent by the module that makes
them, so a forecast drawn as a graphic, a calendar or a results table never passes through
here. Which channels exist, and what each may carry, is Constitution Principle VII and
`core/services/channel_registry_service.py`.

What it holds, for its two kinds of post, are the rules for them: a mention in a log line
names without notifying, a record longer than one message is split, both are sent with
mentions switched off, and a failed post is queued for retry where the caller asks. The log
channel is written nowhere else, which `tests/repository/test_architecture_rules.py` checks. The
target is one handler for each kind of post, of which this is the log writer
(`docs/design/architecture.md`, "Posting to Discord").
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Protocol

import discord

from leaguebot.core.utils.input_validator import ROLE_MENTION, USER_MENTION

#: Every mention a log line can carry, wrapped whole as code so it names without notifying.
#: Built from the shared forms (#362), so ``<@!123>`` is wrapped as ``<@123>`` is.
_MENTION_RE = re.compile(rf"((?:{USER_MENTION})|(?:{ROLE_MENTION}))")

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)


class ForecastTarget(Protocol):
    """What `OutputRouter.post_forecast` reads of a division: where its forecasts go.

    A `leaguebot.core.models.division.Division` is one. So is the stand-in a caller holding only the channel
    id builds, which is why this names the one attribute rather than asking for a whole
    division (#228).
    """

    @property
    def forecast_channel_id(self) -> int | None: ...


@dataclass(frozen=True)
class ForecastChannel:
    """A `ForecastTarget` for a caller holding only the channel id, not a whole division."""

    forecast_channel_id: int | None


class OutputRouter:
    """Writes the log channel and a forecast's text, each failure contained; see above."""

    def __init__(self, bot: "LeagueBot", retry_db_path: "Optional[str]" = None) -> None:
        self._bot = bot
        self._retry_db_path: Optional[str] = retry_db_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def post_forecast(
        self, division: ForecastTarget, content: str, *, enqueue_on_failure: bool = False
    ) -> "Optional[discord.Message]":
        """Post *content* to the division's forecast channel.

        Returns the ``discord.Message`` on success, or ``None`` on failure.
        On failure, enqueues for retry where *enqueue_on_failure* is set and retry_db_path
        is configured.
        """
        channel_id = division.forecast_channel_id
        if channel_id is None:
            # Nothing to post to. It came to the same before — `_send` asked Discord for a
            # channel with no id and logged the failure — less the request.
            log.error("post_forecast: the division has no forecast channel")
            return None
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
                from leaguebot.core.services.retry_service import enqueue
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
