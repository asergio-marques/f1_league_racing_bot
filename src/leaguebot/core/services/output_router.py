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

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional, Protocol

import discord

from leaguebot.core.utils.answering import answering_now
from leaguebot.core.utils.input_validator import ROLE_MENTION, USER_MENTION
from leaguebot.core.utils.messages import chunk_message

#: Every mention a log line can carry, wrapped whole as code so it names without notifying.
#: Built from the shared forms (#362), so ``<@!123>`` is wrapped as ``<@123>`` is.
_MENTION_RE = re.compile(rf"((?:{USER_MENTION})|(?:{ROLE_MENTION}))")

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

#: How old an interaction may be and still be answered: a minute short of the fifteen its
#: token lasts.
ANSWERABLE_FOR = timedelta(minutes=14)


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
        # The warnings still to be sent, kept so that no task is dropped.
        self._warnings: set[asyncio.Task[None]] = set()

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

    async def log_destination(self) -> "Optional[int]":
        """Where the log goes now: the log channel's id, or ``None`` before the bot is set up.

        The one place outside the configuration that names the log channel. A caller about to
        erase the configuration (the factory reset) asks here first and hands the answer back to
        :meth:`post_log` as *channel*, once the configuration that would have named it is gone.
        """
        config = await self._bot.config_service.get_server_config()
        return None if config is None else config.log_channel_id

    async def post_log(
        self, content: str, *, channel: "Optional[int]" = None
    ) -> "Optional[discord.Message]":
        """Post *content* to the league's calculation log channel.

        Mention syntax (<@id>, <@&id>) is wrapped in backticks so Discord
        renders them as plain text rather than interactive mentions.
        Channel links (<#id>) are left as-is so they remain clickable.
        A separator line is appended for readability.
        On failure the line waits on the retry queue, and the member whose command, button or
        form it records is warned, seen by them alone, while their interaction can still be
        answered (:meth:`_warn_member`).

        Returns the ``discord.Message`` on success, or ``None`` on failure — as
        :meth:`post_forecast` already does. A caller that wants to point a reader at what
        it just logged can take ``jump_url`` from it; every other caller ignores it.

        The **first** message is returned where the content had to be split across
        Discord's limit, because a link is meant to land a reader at the top of the block
        rather than at its tail. This is the one respect in which it differs from
        :meth:`post_forecast`, which returns the last because its callers store the id to
        edit the message later.

        *channel* sends to that channel id instead, wrapped, separated and split exactly as
        any other line, but **neither queued for retry nor told to any member** when it cannot
        be posted: it returns ``None`` and the caller puts the line in the host's log. It
        exists for the factory reset's closing line, written after the wipe, when a queued row
        would sit in the fresh database of a bot serving no server. A line that cannot be
        posted is written to the host's log (core specification, "Factory reset"). Get the id
        from :meth:`log_destination` before the wipe.
        """
        content = self._as_log_line(content)
        if channel is not None:
            return await self._send(
                channel, content, enqueue_on_failure=False, fallback_label="log",
                return_first=True,
            )
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
            await self._warn_member(channel_id)

        return msg

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _warn_member(self, log_channel_id: int) -> None:
        """Tell the member whose interaction this task answers that the log channel failed.

        The last resort for a log line, in place of a post in the interaction channel, which
        the constitution forbids. The warning goes through the member's own interaction,
        seen by them alone, and **once** however many of their lines fail. Where the
        interaction has not been answered yet, sending now would take the command's one
        response, so the warning follows when the command's task ends. Where no interaction
        is being answered (a scheduled job, the retry loop, a recovery at start-up, a Discord
        event), or it is over :data:`ANSWERABLE_FOR` old, the failure is the host's log's
        alone, which `_send` has written already; the line itself is still retried.
        """
        answering = answering_now()
        if answering is None:
            log.warning(
                "the log channel (id=%s) failed and no member's interaction can be told",
                log_channel_id,
            )
            return
        if answering.warned:
            return
        interaction = answering.interaction
        if datetime.now(timezone.utc) - interaction.created_at >= ANSWERABLE_FOR:
            log.warning(
                "the log channel (id=%s) failed and the interaction is too old to be told",
                log_channel_id,
            )
            return
        answering.warned = True
        warning = (
            f"⚠️ Failed to write to log channel (id={log_channel_id}). "
            f"Please check bot permissions."
        )
        if interaction.response.is_done():
            self._keep(asyncio.create_task(self._tell(interaction, warning)))
        elif answering.task is not None:
            answering.task.add_done_callback(
                lambda _task: self._keep(asyncio.create_task(self._tell(interaction, warning)))
            )

    def _keep(self, task: "asyncio.Task[None]") -> None:
        """Hold *task* until it ends, logging a failure it did not catch."""
        self._warnings.add(task)
        task.add_done_callback(self._warning_done)

    def _warning_done(self, task: "asyncio.Task[None]") -> None:
        self._warnings.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            log.error("a warning to a member failed", exc_info=error)

    @staticmethod
    async def _tell(interaction: discord.Interaction, warning: str) -> None:
        """Send *warning* to the interaction's member alone, logging a warning that cannot go."""
        try:
            await interaction.followup.send(warning, ephemeral=True)
        except Exception:
            log.error("could not tell the member the log channel failed", exc_info=True)

    @staticmethod
    def _as_log_line(content: str) -> str:
        """Wrap mentions in code so they name without notifying, and append the separator."""
        content = _MENTION_RE.sub(r"`\1`", content)
        return content + "\n" + "\u2015" * 36

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
            for chunk in chunk_message(content):
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

        *wanted* is False for a line sent to a given channel, the factory reset's, which would
        otherwise leave a row in the fresh database of a bot serving no server.
        """
        if self._retry_db_path and wanted:
            try:
                from leaguebot.core.services.retry_service import enqueue
                await enqueue(self._retry_db_path, channel_id, content, failure_reason)
            except Exception as exc:
                log.error("_enqueue_if_configured: failed to enqueue: %s", exc, exc_info=True)
