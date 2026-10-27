"""OutputRouter — the one writer of the log channel: log lines, the stop notice and the turn-away.

It is **not** the way every post leaves the bot. A forecast, a calendar, a lineup and every
other post the bot later replaces in place go through the other handlers or are sent by the
module that makes them, so a forecast or a results table never passes through here. The one
message with buttons that does is the change queue's stop notice, sent to the log channel
(`post_notice`). The turn-away of a message typed
while an earlier turn waits is also sent from here (`turn_away`), at once and off the queue.
Which channels exist, and what each may carry, is Constitution Principle VII and
`core/services/channel_registry_service.py`.

The router is one of four output handlers (`docs/design/architecture.md`, "Posting to Discord"):
the log writer. The others are the notice handler (`notice_handler.py`), the standing-post
handler (`standing_post_handler.py`) and the bot-owned channel handler
(`owned_channel_handler.py`), each a service in core beside it. `send_log_text` is the log
writer's send for a caller that holds the channel already, which the retry worker is: it divides
a late line and sends it mentioning nobody, and raises what Discord raises.

What it holds are the rules for a log line: a mention in it names without notifying, a record
longer than one message is split, both are sent with mentions switched off, and a line that
fails to post is queued for retry where the caller asks. The log channel is written nowhere
else, which `tests/repository/test_architecture_rules.py` checks.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import aiosqlite
import discord

from leaguebot.core.db.database import inserted_id
from leaguebot.core.utils.answering import answering_now
from leaguebot.core.utils.input_validator import ROLE_MENTION, USER_MENTION
from leaguebot.core.utils.messages import chunk_message

#: Every mention a log line can carry, wrapped whole as code so it names without notifying.
#: Built from the shared forms (#362), so ``<@!123>`` is wrapped as ``<@123>`` is.
_MENTION_RE = re.compile(rf"((?:{USER_MENTION})|(?:{ROLE_MENTION}))")

if TYPE_CHECKING:
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

#: How old an interaction may be and still be answered: a minute short of the fifteen its
#: token lasts.
ANSWERABLE_FOR = timedelta(minutes=14)


async def send_log_text(channel: discord.TextChannel, content: str) -> None:
    """Send the log line *content* to the log *channel*, mentioning nobody.

    A line longer than one message is divided through `chunk_message` and sent in order. It
    raises whatever Discord raises, for the caller to answer: the retry worker
    (`retry_service.attempt_delivery`) sends a late line through it and counts a failure as
    another try. It does not run in a job of the change queue, and so does not carry
    `posts_in_a_job`.
    """
    for chunk in chunk_message(content):
        await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())


class OutputRouter:
    """Writes the log channel, each failure contained; see above."""

    def __init__(
        self,
        bot: "LeagueBot",
        config_service: "ConfigService",
        retry_db_path: "Optional[str]" = None,
    ) -> None:
        self._bot = bot
        # Handed in by the builder, which is the one place services are wired (architecture.md,
        # "Services stay attached to the bot, and are built in one place"): the router reads the
        # log channel from it and looks no service up on the bot.
        self._config = config_service
        self._retry_db_path: Optional[str] = retry_db_path
        # The warnings still to be sent, kept so that no task is dropped.
        self._tasks: set[asyncio.Task[None]] = set()
        # The interactions whose member has been warned, by id, with when each was made.
        self._warned: dict[object, datetime] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def log_destination(self) -> "Optional[int]":
        """Where the log goes now: the log channel's id, or ``None`` before the bot is set up.

        The one place outside the configuration that names the log channel. A caller about to
        erase the configuration (the factory reset) asks here first and hands the answer back to
        :meth:`post_log` as *channel*, once the configuration that would have named it is gone.
        """
        config = await self._config.get_server_config()
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

        Returns the ``discord.Message`` on success, or ``None`` on failure. A caller that
        wants to point a reader at what it just logged can take ``jump_url`` from it; every
        other caller ignores it.

        The **first** message is returned where the content had to be split across
        Discord's limit, because a link is meant to land a reader at the top of the block
        rather than at its tail.

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
        config = await self._config.get_server_config()
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

    async def post_notice(
        self,
        content: str,
        view: discord.ui.View,
        *,
        interaction: "Optional[discord.Interaction]" = None,
        mention_role: int | None = None,
    ) -> "Optional[discord.Message]":
        """Post *content* to the log channel as a notice carrying *view*'s buttons.

        Formed and sent as a log line is, mentions wrapped and the separator appended, but
        *mention_role*'s mention, where given, left as it is and allowed to notify that role alone,
        the one mention the log channel notifies (the stop notice's, for the league admins). It is
        **never put on the retry queue**: a line retried would come back without its buttons, so the
        caller keeps the notice itself and posts it again until it lands. Where the post fails
        the member whose interaction *interaction* is, where it can still be answered, is
        warned here, as `post_log` does (`_warn_member`), so the caller never does.

        Returns the message, which carries the view, or ``None`` where it could not be posted or
        the bot is not set up.
        """
        config = await self._config.get_server_config()
        if config is None:
            log.error("post_notice: the bot is not set up, so there is no log channel")
            return None
        channel_id = config.log_channel_id
        message = await self._send(
            channel_id, self._as_log_line(content, keep_role=mention_role),
            enqueue_on_failure=False, fallback_label="log", view=view, mention_role=mention_role,
        )
        if message is None:
            await self._warn_member(channel_id, interaction)
        return message

    @staticmethod
    async def turn_away(message: discord.Message, note: str) -> None:
        """Turn *message* away at once, off the queue: post *note* (with ``{member}`` filled in)
        in its channel, mentioning the author alone, and delete the message.

        Each is tried once. A refusal of either is logged on the host and lost, never retried, and
        the note stays: this is a named exception to every post being a job (#439, decision 9),
        made at once so the member is told while the earlier answer they typed still waits, where a
        job would wait behind it. It is here, in the one file that posts directly, and not in the
        turn's own module (`typed_turns.turn_away` asks it), so that the posting check needs no
        line for it. A static method: it needs the message alone.
        """
        try:
            await message.channel.send(
                note.format(member=message.author.mention),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=[message.author]),
            )
        except discord.HTTPException:
            log.warning("could not post the turn-away note in channel %s", message.channel.id,
                        exc_info=True)
        try:
            await message.delete()
        except discord.HTTPException:
            log.warning("could not delete the turned-away message %s in channel %s", message.id,
                        message.channel.id, exc_info=True)

    async def strip_view(self, channel_id: int, message_id: int) -> None:
        """Take the buttons off the message *message_id* of channel *channel_id*.

        A message already gone is no failure; any other failure goes to the host's log. Never
        raises.
        """
        try:
            channel = self._bot.get_channel(channel_id)
            if channel is None:
                channel = await self._bot.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                log.error("strip_view: channel id=%s is not a text channel", channel_id)
                return
            await channel.get_partial_message(message_id).edit(view=None)
        except discord.NotFound:
            return
        except Exception:  # noqa: BLE001 — the buttons are cosmetic once the job has cleared
            log.error("strip_view: could not take the buttons off message id=%s", message_id,
                      exc_info=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _warn_member(
        self, log_channel_id: int, interaction: "Optional[discord.Interaction]" = None
    ) -> None:
        """Tell a member that the log channel failed, seen by them alone.

        The last resort for a log line, in place of a post in the interaction channel, which
        the constitution forbids. The member is the one whose interaction the line records:
        *interaction* where the caller holds it (the change queue, which has judged it still
        answerable, and which is not the interaction's own task, so the warning is sent at
        once), otherwise the one the
        current task answers (`leaguebot.core.utils.answering`). They are told **once**
        however many of their lines fail. Where the interaction has not been answered yet,
        sending now would take the command's one response, so the warning follows when the
        command's task ends. Where there is no interaction (a scheduled job, the retry loop, a
        recovery at start-up, a Discord event), or it is over :data:`ANSWERABLE_FOR` old, the
        failure is the host's log's alone; the line itself is still retried.
        """
        task: "Optional[asyncio.Task[object]]" = None
        held = interaction is not None
        if interaction is None:
            answering = answering_now()
            if answering is None:
                log.warning(
                    "the log channel (id=%s) failed and no member's interaction can be told",
                    log_channel_id,
                )
                return
            interaction, task = answering.interaction, answering.task
        now = datetime.now(timezone.utc)
        for key, created in list(self._warned.items()):
            if now - created >= ANSWERABLE_FOR:
                del self._warned[key]
        if interaction.id in self._warned:
            return
        if not held and now - interaction.created_at >= ANSWERABLE_FOR:
            log.warning(
                "the log channel (id=%s) failed and the interaction is too old to be told",
                log_channel_id,
            )
            return
        warning = (
            f"⚠️ Failed to write to log channel (id={log_channel_id}). "
            f"Please check bot permissions."
        )
        if interaction.response.is_done():
            self._warned[interaction.id] = now
            if held:
                await self._tell(interaction, warning)
            else:
                self._keep(asyncio.create_task(self._tell(interaction, warning)))
        elif task is not None:
            self._warned[interaction.id] = now
            task.add_done_callback(
                lambda _task: self._keep(asyncio.create_task(self._tell(interaction, warning)))
            )
        else:
            log.warning(
                "the log channel (id=%s) failed and the interaction has not been answered",
                log_channel_id,
            )

    def _keep(self, task: "asyncio.Task[None]") -> None:
        """Hold *task* until it ends, logging a failure it did not catch."""
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: "asyncio.Task[None]") -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            log.error("a warning to a member failed", exc_info=error)

    @staticmethod
    async def _tell(interaction: discord.Interaction, warning: str) -> None:
        """Send *warning* to the interaction's member alone, logging a warning that cannot go."""
        try:
            await interaction.followup.send(warning, ephemeral=True)
        except Exception:
            log.error("could not tell the member the log channel failed", exc_info=True)

    async def queue_log_on(self, db: aiosqlite.Connection, content: str) -> "Optional[int]":
        """Write *content* as a log line on the retry queue, on the caller's connection.

        The change queue calls it in the save of the step the line records, so that the line
        is saved with what it records or not at all. It commits nothing: the caller does,
        then hands the id to :meth:`deliver_queued`. The row is one never tried
        (``failure_reason`` empty, ``retry_count`` 0), which the retry loop delivers should
        the bot stop before then.

        Returns the row's id, or None where the bot is not set up and there is no log channel
        to write to: it logs that to the host, as :meth:`post_log` does before set-up.
        """
        cursor = await db.execute("SELECT log_channel_id FROM server_configs LIMIT 1")
        row = await cursor.fetchone()
        if row is None:
            log.error("queue_log_on: the bot is not set up, so there is no log channel")
            return None
        cursor = await db.execute(
            "INSERT INTO pending_messages "
            "(channel_id, content, failure_reason, enqueued_at, retry_count, last_attempted_at) "
            "VALUES (?, ?, '', ?, 0, NULL)",
            (
                row["log_channel_id"],
                self._as_log_line(content),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return inserted_id(cursor)

    async def deliver_queued(
        self, ids: "Iterable[int]", *, interaction: "Optional[discord.Interaction]" = None
    ) -> None:
        """Send the lines :meth:`queue_log_on` wrote, once their save has been committed.

        Each is read again under the retry loop's lock, and one already gone (the loop
        delivered it first) is skipped. A line is divided through `chunk_message` and sent
        mentioning nobody, as :meth:`post_log` sends it. One that is delivered leaves the
        retry queue. One that cannot be is left to the retry loop with its reason, and
        *interaction*, where given and still answerable, is told as :meth:`post_log` tells
        the member, once; with none, the failure is the host's log's alone. Never raises.
        """
        from leaguebot.core.services import retry_service

        db_path = self._retry_db_path
        if not db_path:
            return
        for entry_id in ids:
            try:
                async with retry_service.delivery_lock():
                    entry = await retry_service.get_pending(db_path, entry_id)
                    if entry is None:
                        continue
                    message, reason, _retryable = await self._try_send(
                        entry.channel_id, entry.content, "log", False
                    )
                    if message is not None:
                        await retry_service.mark_delivered(db_path, entry_id)
                        continue
                    await retry_service.mark_failed(db_path, entry_id, reason=reason)
                if interaction is not None:
                    await self._warn_member(entry.channel_id, interaction)
            except Exception:
                log.error("deliver_queued: could not deliver line id=%s", entry_id, exc_info=True)

    @staticmethod
    def _as_log_line(content: str, *, keep_role: int | None = None) -> str:
        """Wrap mentions in code so they name without notifying, and append the separator;
        *keep_role*'s mention alone is left as it is."""
        kept = None if keep_role is None else f"<@&{keep_role}>"
        content = _MENTION_RE.sub(
            lambda found: found.group(1) if found.group(1) == kept else f"`{found.group(1)}`",
            content,
        )
        return content + "\n" + "\u2015" * 36

    async def _send(
        self,
        channel_id: int,
        content: str,
        *,
        enqueue_on_failure: bool = False,
        fallback_label: str = "unknown",
        return_first: bool = False,
        view: "Optional[discord.ui.View]" = None,
        mention_role: int | None = None,
    ) -> "Optional[discord.Message]":
        """Attempt to send *content* to *channel_id*.

        Returns the last ``discord.Message`` sent on success, ``None`` on failure.
        Never raises. On HTTP/Forbidden failure, enqueues for retry when
        retry_db_path is configured and *enqueue_on_failure* is set.

        *return_first* returns the **first** message instead, for a caller linking a
        reader to the start of what it wrote. It defaults to False, returning the last.

        *view* is sent with the last part, so that its buttons end the message.
        """
        message, reason, retryable = await self._try_send(
            channel_id, content, fallback_label, return_first, view, mention_role
        )
        if message is None and retryable:
            await self._enqueue_if_configured(enqueue_on_failure, channel_id, content, reason)
        return message

    async def _try_send(
        self,
        channel_id: int,
        content: str,
        fallback_label: str,
        return_first: bool,
        view: "Optional[discord.ui.View]" = None,
        mention_role: int | None = None,
    ) -> "tuple[Optional[discord.Message], str, bool]":
        """Send *content*, and say how it went: the message, or None with why and whether a
        retry could mend it (a refused or failed post can; a channel not found cannot).
        Logs every failure and never raises."""
        channel = self._bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                log.error(
                    "_send: cannot fetch %s channel id=%s: %s",
                    fallback_label, channel_id, exc,
                )
                return None, str(exc), False

        if not isinstance(channel, discord.TextChannel):
            log.error(
                "_send: channel id=%s is not a TextChannel (got %s)",
                channel_id, type(channel).__name__,
            )
            return None, f"channel {channel_id} is not a text channel", False

        try:
            # Discord messages have a 2000-char limit; chunk if needed
            first_msg: Optional[discord.Message] = None
            last_msg: Optional[discord.Message] = None
            allowed = (
                discord.AllowedMentions.none() if mention_role is None
                else discord.AllowedMentions(
                    everyone=False, users=False, roles=[discord.Object(id=mention_role)])
            )
            chunks = chunk_message(content)
            for number, chunk in enumerate(chunks, start=1):
                if view is not None and number == len(chunks):
                    last_msg = await channel.send(chunk, allowed_mentions=allowed, view=view)
                else:
                    last_msg = await channel.send(chunk, allowed_mentions=allowed)
                if first_msg is None:
                    first_msg = last_msg
            return (first_msg if return_first else last_msg), "", False
        except discord.Forbidden as exc:
            log.error(
                "_send: missing permissions for %s channel id=%s: %s",
                fallback_label, channel_id, exc,
            )
            return None, str(exc), True
        except discord.HTTPException as exc:
            log.error(
                "_send: HTTP error posting to %s channel id=%s: %s",
                fallback_label, channel_id, exc,
            )
            return None, str(exc), True

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
