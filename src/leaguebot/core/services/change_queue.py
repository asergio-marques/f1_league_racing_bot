"""The change queue: a change asked for is acknowledged at once and carried out step by step.

`docs/design/architecture.md`, "How a change is carried out", designs it; the core specification's
section of that name holds the rules a league sees. This module holds the engineering decisions,
each pinned by a test in `tests/core/test_change_queue.py`.

**A change is a row, and so is each of its steps.** `queued_changes` and `queued_change_steps`
are the queue's own record. A step's mark (`done_at`) is saved in the same transaction as the step's
own writes, its audit records and its log lines, so a stop leaves a step wholly done or not done,
and a restart carries the change on from its first step not done. A step marked done is never run
again.

**A post's id is saved with the post's mark** (`Step.record`). A step that is not a `SAVE` may carry
a `record(db, ctx, result)`, which the worker awaits inside the save that marks the step done,
before the mark, on the connection it is handed and never committing. A message id is thereby saved
with the post's done mark, or neither is: a stop between the post and its mark can only leave the
post sent, never an id unsaved beside a step marked done. It runs also where a `DELETE` or an
`EDIT` completes because its message is already gone (a channel found deleted still has its row
deleted), and never for a job found no longer due or discarded, which sent nothing to record. A
`SAVE` step writes in its own run, so `register` refuses one that carries a record. *Rejected:* a
`SAVE` job after each post to write its id, which doubles the jobs a change numbers and stops on.
A record that raises rolls the save back and stops the queue like any failure, and the result the
step returned is kept on the job as the partial result a failure on Discord keeps, so that the next
try can remove the copy already sent before it posts again (`StepContext.kept`): a second copy is
accepted only where the bot stops, not where a save fails.

**One change runs at a time, in the order asked.** One asyncio task, `ChangeQueue._task`, is kept
on the instance and started by `start()` (idempotent), from `on_ready` and never from an
interaction, so that it inherits no interaction's context. `run_until_idle` is the same loop for the
tests. A lock holds the task and a test's call to one step at a time.

**A step is one of four kinds** (`StepKind`). A `SAVE` step is handed the open save and writes only
on it, never committing. The other kinds do Discord or legacy work with no connection open and are
marked in a save after it. Nothing is awaited inside a save but the connection.

**Jobs are carried out strictly in order.** Each step of a change is a job with a number of its own,
its `id` in `queued_change_steps`, never renumbered or reused (a restored state and a factory reset
carry the numbering on: `highest_job_number`, `carry_job_numbering`) and shown wherever the job is
named. A member's
request is acknowledged once and is made of jobs that run one after another: `_choose` takes the
RUNNING change, or else the QUEUED change with the lowest id, and nothing overtakes it.

**A job that fails stops the queue.** Whatever the failure, from Discord or from the bot, and
whether in a job or in a bot change's check as it starts (`_stop`), nothing behind the job runs
until it is cleared: `_choose` runs nothing while its next job is stopped and not yet due. A stop
is the job's own record (`failing_since`, `tries`, `last_failure`, `next_try_at`), the change's
state staying as it was. The bot tries a stopped job again on `RETRY_AFTER`, counted from the first
failure, and says nothing of a try that fails but the last, which says the bot has stopped trying
on its own. A request made while the queue is stopped is queued at the back and says where the
queue is stopped.

**A stop is cleared in three ways:** a try that goes through (the bot's own, or Retry), which
writes one line saying so, or finds the job no longer due, or the change's request refused at its
check, which write a line saying that instead; a league manager's or admin's Retry, at any time; and a league admin's
Discard, which drops that one job, the request's later jobs running on and each checking whether
it is still due, or the whole change where it had not started. Retry and Discard work directly on
the queue's own records, never as changes of their own: the queue is stopped, so a change queued
behind it could not run. Each is a press on the stop notice (`QueueStopView`) and is refused, with
a line, where the presser may not use it or the notice's job no longer stops the queue.

**The stop notice is one log-channel message** carrying the stop line and the buttons. It is
posted by the router's `post_notice`, after the stop's save and never on the log-line retry queue
(that would send it again without its buttons), and posted again by the queue, at start-up and on
each pass of the worker, while `notice_message_id` is empty. `strip_view` takes the buttons off
once the job clears.

**A restart leaves a stopped job stopped.** `start()` clears its `next_try_at`, so that only Retry
or Discard moves the queue, and writes one line saying so. A change cut off by the stop, with no job
failed, carries on from its first job not done. Where recording a stop itself fails, nothing is
marked: the worker's catch-all logs it and the job runs again, its failure recorded then.

**The queue forms no standard line of its own.** A refusal is `log_lines.refusal_line`, a stop
`log_lines.stop_line` and so on for each of its lines: each standard line is formed in one place. Every line the queue
writes goes through the `OutputRouter` it was handed, never one looked up on the bot (a service does
not use the bot to look up other services).

**The check runs twice**: when the change is asked for, so that a member is refused at once, and
when it starts, since what it checked may have changed in between.

**What is still in hand is read, not remembered** (`unfinished`). A module-level read gives the
payloads of the changes of the kinds asked that are QUEUED or RUNNING, a stopped one included
(its state stays as it was) and the one named in *excluding* left out, which a check handed its own
`change_id` uses to leave itself out. A check uses it to refuse a press while the same approval is
already in hand, and a restart's recovery and a sweep use it to leave alone what the queue is still
carrying out. It gives what the repeat rule below does not: that refuses a repeat only while the
first has not started, where a specification may ask for the refusal while the first is still being
applied, whatever its position.

**A request repeating a change is refused only while it could still be the same request:** where
the last change asked for, whatever became of any other, has the same key and has not started.
Asked again once anything else has been asked for after it, or once it has started, a change is
queued again; a once-only change is refused by its own check once done.

**The acknowledgement is updated, never stored.** The member's interaction is held in memory against
the change id, and only while fewer than 14 minutes (a minute short of Discord's token) have passed
since the acknowledgement. An interaction already answered is acknowledged through a follow-up,
and that message is what the outcome updates. After a restart, or later, nothing is updated and the log channel alone
records the outcome: every change type writes its outcome line as a step.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection, inserted_id
from leaguebot.core.models.change import (
    AuditRecord,
    ChangeOrigin,
    ChangeState,
    FollowOn,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
    VerdictKind,
)
from leaguebot.core.services.audit_service import record_change_on
from leaguebot.core.services.queue_stop_view import QueueStopView
from leaguebot.core.utils.channel_guard import is_league_admin, is_league_manager
from leaguebot.core.utils.log_lines import (
    cleared_line,
    discarded_line,
    hour_line,
    refusal_line,
    refuse,
    reply_reason,
    restart_line,
    retried_line,
    retry_failed_line,
    stop_line,
    went_through_line,
)
from leaguebot.core.utils.member_names import interaction_member, member_named
from leaguebot.core.utils.messages import chunk_message

if TYPE_CHECKING:
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.output_router import OutputRouter
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

#: How long after the acknowledgement it may still be updated: a minute short of the fifteen
#: Discord's token lasts.
UPDATABLE_FOR = timedelta(minutes=14)

#: How long the worker pauses after a fault outside any change (a locked database, say) before it
#: looks again, so that it neither ends nor spins.
WORKER_PAUSE_AFTER_FAULT = 5.0

#: When the bot tries a stopped job again, in minutes after its first failure. After the last the
#: bot no longer tries on its own, and only Retry or Discard moves the queue.
RETRY_AFTER = (1, 5, 10, 15, 30, 60)

#: How often the stop notice is posted again, once the marks of `RETRY_AFTER` are spent and it has
#: still not landed.
NOTICE_REPOST_EVERY = timedelta(hours=1)


@dataclass(frozen=True)
class _Held:
    """A member's interaction held to update, with the follow-up message that acknowledged it
    where the interaction was already answered (a deferred command, a form), None where the
    acknowledgement was its response."""

    interaction: discord.Interaction
    message: discord.WebhookMessage | None


@dataclass(frozen=True)
class CheckContext:
    """What a change type's check reads: the request, the bot for Discord, and the database.

    *change_id* is the change's id where the check runs as the change starts, None where it is
    asked about a request not yet made: a check that reads `unfinished` leaves itself out with it.
    """

    payload: dict[str, Any]
    bot: "LeagueBot"
    db_path: str
    origin: ChangeOrigin
    change_id: int | None = None


@dataclass(frozen=True)
class StepView:
    """A step as an outcome reads it: its payload, its result and whether it is done."""

    name: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    done: bool


@dataclass(frozen=True)
class OutcomeContext:
    """What a change type's `outcome` reads: the change and every step of it.

    *named* is the member as the log channel names them ("Name (<@id>)"); *actor_id* and
    *actor_name* are what the audit records.
    """

    bot: "LeagueBot"
    db_path: str
    change_id: int
    kind: str
    payload: dict[str, Any]
    what: str
    actor_id: int | None
    actor_name: str | None
    named: str
    steps: tuple[StepView, ...]


@dataclass(frozen=True)
class StepContext(OutcomeContext):
    """What a step reads: the change as `OutcomeContext` gives it, and the step itself.

    *tries* is how many times the step has failed on Discord before, so that a posting step can
    post as text on a retry (Constitution XIV, rule 8). *kept* is what the stopped job's last try
    left on it (the `result` of the `StepFailedOnDiscord` it raised, or the result a record that
    raised had been handed), None on a first try: a posting job reads it to remove the messages its
    last try sent before it posts again.
    """

    step_name: str = ""
    step_payload: dict[str, Any] = field(default_factory=dict)
    tries: int = 0
    kept: dict[str, Any] | None = None


@dataclass(frozen=True)
class Step:
    """A step a change type can run.

    A `SAVE` step's `run(db, ctx)` writes only on *db* and never commits; any other step's
    `run(ctx)` does its work with no connection open. `still_due(ctx)` is asked before the step
    runs, and a step no longer due is marked done as dropped. `describe(ctx)` names the job in the
    lines that say it stopped the queue, was retried or discarded ("refreshing the hub panel"); by
    default the job is named by the change's own `what`. `gone_line`, a line or what forms one, is
    what an `EDIT` step writes where its message is gone. `record(db, ctx, result)`, on a step that
    is not a `SAVE`, is awaited in the save that marks the step done, before the mark, and writes on
    *db* without committing: a post's message id is saved with the post's mark. It is run, too,
    where a `DELETE` or `EDIT` completes because its message is gone, and not for a job found no
    longer due. A record that raises stops the queue at the job, and *result* is kept on it.
    """

    name: str
    kind: StepKind
    run: Callable[..., Awaitable[StepResult]]
    still_due: Callable[[StepContext], Awaitable[bool]] | None = None
    describe: Callable[[StepContext], Awaitable[str]] | None = None
    gone_line: Callable[[StepContext], str] | str | None = None
    record: Callable[[aiosqlite.Connection, StepContext, StepResult], Awaitable[None]] | None = None


@dataclass(frozen=True)
class ChangeType:
    """A kind of change: what it does, how it is checked and keyed, and what it tells the member.

    *opening* are the steps saved when the change is asked for; a step may plan more. *key* is what
    makes two requests the same. *doing* says what the change does ("Turning Results & Standings
    off"), from which the acknowledgement and the refusal of a repeat are formed. A *repeatable*
    change is never refused as a repeat.
    """

    kind: str
    opening: tuple[PlannedStep, ...]
    steps: Mapping[str, Step]
    check: Callable[[CheckContext], Awaitable[Verdict]]
    key: Callable[[dict[str, Any]], str]
    doing: Callable[[dict[str, Any]], str]
    outcome: Callable[[OutcomeContext], str]
    repeatable: bool = False


class ChangeRefused(Exception):
    """A bot change's check refused when the change started: the bot asked for what it may not
    do, which is a fault in the bot and is handled as one."""


def _refusal_text(verdict: Verdict) -> str:
    """What a member is told of a request whose check is not GO: the verdict's reply, or where
    it gives none (a check of the bot's kind), its reason."""
    return verdict.reply or f"⚠️ {verdict.reason}"


class ChangeQueue:
    """Acknowledges the changes asked for and carries them out one at a time; see the module."""

    def __init__(
        self,
        db_path: str,
        bot: "LeagueBot",
        output_router: "OutputRouter",
        config_service: "ConfigService",
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db_path = db_path
        self._bot = bot
        self._router = output_router
        # Handed in by the builder: Retry and Discard read the presser's tier from it, and the
        # queue looks no service up on the bot.
        self._config = config_service
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._types: dict[str, ChangeType] = {}
        self._held: dict[int, _Held] = {}
        self._signal = asyncio.Event()
        self._working = asyncio.Lock()
        self._asking = asyncio.Lock()
        # The change the worker is carrying out a unit of, which a Discard must not drop under
        # it. Set and read only under `_asking`, so a Discard and the worker's choice of a change
        # are in one order: either the Discard is saved before the choice, or it is refused.
        self._trying: int | None = None
        self._task: Optional["asyncio.Task[None]"] = None
        # When each stopped job's notice was last attempted, for the job whose notice has not
        # landed: the schedule of its re-posting. Lost at a restart, which posts it at once.
        self._notice_tried: dict[int, datetime] = {}
        # A job a Retry has made due, with who pressed it and when its next try stood before: a
        # Retry that fails writes its own line and leaves the schedule as it was.
        self._retrying: dict[int, tuple[str, str | None]] = {}

    # ------------------------------------------------------------------
    # Registering and asking
    # ------------------------------------------------------------------

    def register(self, change_type: ChangeType) -> None:
        """Make *change_type* known. A `SAVE` step carrying a `record` is refused: it writes in
        its own run."""
        for step in change_type.steps.values():
            if step.record is not None and step.kind is StepKind.SAVE:
                raise ValueError(
                    f"step {step.name!r} of {change_type.kind!r} is a SAVE and cannot carry a record"
                )
        self._types[change_type.kind] = change_type

    def _type(self, kind: str) -> ChangeType:
        try:
            return self._types[kind]
        except KeyError:
            raise KeyError(f"no change type {kind!r} is registered") from None

    async def check(
        self, kind: str, payload: dict[str, Any], *, origin: ChangeOrigin = ChangeOrigin.MEMBER
    ) -> Verdict:
        """What *kind*'s check finds of a request carrying *payload* now."""
        return await self._type(kind).check(
            CheckContext(payload, self._bot, self._db_path, origin)
        )

    async def ask(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        interaction: discord.Interaction | None = None,
        actor: Any = None,
        what: str,
        refusal_what: str | None = None,
        origin: ChangeOrigin = ChangeOrigin.MEMBER,
    ) -> int | None:
        """Ask for a change: check it, save it, and acknowledge it. Returns its id, or None
        where it was refused or is no longer due.

        A member's request that fails the check, or repeats the last change asked for before it
        has started, is refused at once through *interaction* (the line naming *refusal_what*,
        or *what*) and nothing is saved. Otherwise the change and its opening steps are saved
        in one transaction, the member is told it is under way, and the worker is woken. A fault
        in the save raises to the caller.
        """
        change_type = self._type(kind)
        member = actor if actor is not None else (interaction.user if interaction else None)
        verdict = await self.check(kind, payload, origin=origin)
        if verdict.kind is not VerdictKind.GO:
            if origin is ChangeOrigin.MEMBER and interaction is not None:
                await refuse(
                    interaction,
                    _refusal_text(verdict),
                    what=refusal_what or what,
                    detail=verdict.reason or None,
                )
                return None
            if verdict.kind is VerdictKind.NOT_DUE:
                log.info("%s is no longer due, so it was not asked for: %s", what, verdict.reason)
                return None

        # The worker passes over this lock to choose its next change, so it cannot pick the
        # change up before its acknowledgement is recorded and the interaction is held.
        async with self._asking:
            key = change_type.key(payload)
            change_id: int | None = None
            first_job: int | None = None
            async with get_connection(self._db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT dedup_key, state FROM queued_changes ORDER BY id DESC LIMIT 1"
                )
                last = await cursor.fetchone()
                repeat = (
                    not change_type.repeatable
                    and last is not None
                    and last["dedup_key"] == key
                    and last["state"] == ChangeState.QUEUED.value
                )
                if not repeat:
                    cursor = await db.execute(
                        "INSERT INTO queued_changes (kind, dedup_key, payload, origin, actor_id, "
                        "actor_name, actor_display, what) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            kind,
                            key,
                            json.dumps(payload),
                            origin.value,
                            getattr(member, "id", None),
                            None if member is None else str(member),
                            getattr(member, "display_name", None),
                            what,
                        ),
                    )
                    change_id = inserted_id(cursor)
                    for position, planned in enumerate(change_type.opening):
                        cursor = await db.execute(
                            "INSERT INTO queued_change_steps (change_id, position, name, payload) "
                            "VALUES (?, ?, ?, ?)",
                            (change_id, position, planned.name, json.dumps(planned.payload)),
                        )
                        if first_job is None:
                            first_job = inserted_id(cursor)
                    await db.commit()
                else:
                    await db.rollback()

            if change_id is None:
                reason = (
                    f"{change_type.doing(payload)} has already been asked for and has not started "
                    f"yet, so it was not asked for again."
                )
                if interaction is not None and origin is ChangeOrigin.MEMBER:
                    await refuse(interaction, f"⚠️ {reason}", what=refusal_what or what)
                else:
                    log.info("%s was not asked for again: %s", what, reason)
                return None

            if interaction is not None:
                stopped = await self._stopped_jobs()
                await self._acknowledge(
                    change_id, change_type, payload, interaction,
                    first_job=first_job, stopped_at=stopped[0][2]["id"] if stopped else None,
                )
            self._signal.set()
            return change_id

    async def _acknowledge(
        self,
        change_id: int,
        change_type: ChangeType,
        payload: dict[str, Any],
        interaction: discord.Interaction,
        *,
        first_job: int | None,
        stopped_at: int | None,
    ) -> None:
        """Tell the member their saved change is under way, and hold the interaction to update.

        The acknowledgement names the request's first job, *first_job*, and where the queue is
        stopped, at the job *stopped_at*, says so and that the request runs once that job is
        cleared.

        The change is already saved, so a failed acknowledgement is not the request's failure:
        it is logged with its details, nothing is held and `acknowledged_at` stays unset, and the
        change runs all the same, its outcome standing in the log channel alone.
        """
        text = (
            f"⏳ {change_type.doing(payload)}. This message will be updated when it is "
            f"done; if it takes longer, the log channel will say so."
        )
        if first_job is not None:
            text += f" It begins with job #{first_job}."
        if stopped_at is not None:
            text += (
                f" The queue is stopped at job #{stopped_at}, so this request runs once that "
                f"job is cleared."
            )
        message: discord.WebhookMessage | None = None
        try:
            if interaction.response.is_done():
                # A command that deferred, a form already answered: no response is left to take,
                # so the acknowledgement is a follow-up, and that message is what is updated.
                message = await interaction.followup.send(text, ephemeral=True, wait=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
            async with get_connection(self._db_path) as db:
                await db.execute(
                    "UPDATE queued_changes SET acknowledged_at = ? WHERE id = ?",
                    (self._clock().isoformat(), change_id),
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — the change is saved and goes ahead whatever this does
            log.error(
                "could not acknowledge change %s to the member; its outcome stands in the log "
                "channel alone", change_id, exc_info=True,
            )
            return
        self._held[change_id] = _Held(interaction, message)

    # ------------------------------------------------------------------
    # The worker
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the worker, once: a second call does nothing.

        A change a stop cut off, with no job failed, carries on as it was. A job that was stopped
        on a failure stays stopped: its `next_try_at` is cleared, so that after a restart only
        Retry or Discard moves the queue, and one line says so. Every log line saved and never
        tried, which the stop came between the save and the delivery of, is delivered, with no
        interaction to tell if it fails. The stop notice of a job that never landed is posted
        again, and the view of its buttons is registered, so that a notice posted before the
        restart still works.
        """
        if self._task is not None and not self._task.done():
            return
        self._bot.add_view(QueueStopView(self))
        await self._leave_stopped_jobs_stopped()
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM pending_messages WHERE failure_reason = '' ORDER BY id"
            )
            never_tried = [row["id"] for row in await cursor.fetchall()]
        await self._router.deliver_queued(never_tried)
        await self._post_missing_notices()
        self._task = asyncio.create_task(self._work(), name="change-queue")
        self._task.add_done_callback(self._worker_ended)

    async def _stopped_jobs(
        self, *, without_notice: bool = False
    ) -> list[tuple[aiosqlite.Row, list[aiosqlite.Row], aiosqlite.Row]]:
        """Every job the queue is stopped at, in job order, each with its change and the change's
        steps; with *without_notice*, only those whose stop notice has not landed."""
        missing = " AND s.notice_message_id IS NULL" if without_notice else ""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT c.*, s.id AS job_id FROM queued_change_steps s "
                "JOIN queued_changes c ON c.id = s.change_id "
                "WHERE s.done_at IS NULL AND s.failing_since IS NOT NULL "
                f"AND c.state IN ('QUEUED', 'RUNNING'){missing} ORDER BY s.id"
            )
            changes = list(await cursor.fetchall())
            found = []
            for change in changes:
                steps = await self._read_steps(db, change["id"])
                job = next(row for row in steps if row["id"] == change["job_id"])
                found.append((change, steps, job))
        return found

    async def _leave_stopped_jobs_stopped(self) -> None:
        """Clear the `next_try_at` of every stopped job, and queue the line saying the queue is
        still stopped, in one save; the line is delivered with the rest at start-up."""
        lines: list[tuple[int, str]] = []
        for change, steps, job in await self._stopped_jobs():
            name = await self._name_job(change, job, self._step_context(change, steps, job))
            lines.append((job["id"], restart_line(job["id"], name)))
        if not lines:
            return
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                for job_id, line in lines:
                    await db.execute(
                        "UPDATE queued_change_steps SET next_try_at = NULL WHERE id = ?",
                        (job_id,),
                    )
                    await self._router.queue_log_on(db, line)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def _name_job(
        self, change: aiosqlite.Row, row: aiosqlite.Row, ctx: StepContext
    ) -> str:
        """What the job *row* is doing, as a line names it: its step's `describe`, or the
        change's own words where there is none, or it cannot be had."""
        change_type = self._types.get(change["kind"])
        step = None if change_type is None else change_type.steps.get(row["name"])
        if step is None or step.describe is None:
            return str(change["what"])
        try:
            return await step.describe(ctx)
        except Exception:  # noqa: BLE001 — the line goes ahead naming the change
            log.error("could not describe job %r of %s", row["name"], change["what"],
                      exc_info=True)
            return str(change["what"])

    def _step_context(
        self, change: aiosqlite.Row, step_rows: list[aiosqlite.Row], row: aiosqlite.Row
    ) -> StepContext:
        return StepContext(
            **self._context_fields(change, step_rows),
            step_name=row["name"],
            step_payload=json.loads(row["payload"]),
            tries=row["tries"],
            kept=json.loads(row["result"]) if row["result"] else None,
        )

    def forget_held(self) -> None:
        """Drop every interaction held to update, and what is remembered of stopped jobs (when
        each notice was tried, and each Retry made), for a pack or a factory reset, which delete
        the changes they belong to."""
        self._held.clear()
        self._notice_tried.clear()
        self._retrying.clear()

    async def stop(self) -> None:
        """Stop the worker, leaving any change where it stood for the next start."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _worker_ended(task: "asyncio.Task[None]") -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            log.error("the change queue's worker stopped on a fault", exc_info=error)

    async def _work(self) -> None:
        """Carry out what can run, then sleep until woken or until the earliest retry is due."""
        while True:
            self._signal.clear()
            try:
                await self.run_until_idle()
            except Exception:  # noqa: BLE001 — a fault outside any change must not end the queue
                log.error("the change queue's worker met a fault and carries on", exc_info=True)
                await asyncio.sleep(WORKER_PAUSE_AFTER_FAULT)
                # Look again: nothing may be left to set the signal, and a change the fault
                # left QUEUED would otherwise wait for the next ask or a restart.
                continue
            try:
                await asyncio.wait_for(self._signal.wait(), await self._seconds_to_next_try())
            except asyncio.TimeoutError:
                pass

    async def _seconds_to_next_try(self) -> float | None:
        """How long until the earliest stopped job is due for a try, or the earliest stop notice
        that has not landed is due to be posted again; None where nothing waits."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT MIN(s.next_try_at) AS due FROM queued_change_steps s "
                "JOIN queued_changes c ON c.id = s.change_id "
                "WHERE s.done_at IS NULL AND s.next_try_at IS NOT NULL "
                "AND c.state IN ('QUEUED', 'RUNNING')"
            )
            row = await cursor.fetchone()
        times = [] if row is None or row["due"] is None else [datetime.fromisoformat(row["due"])]
        times += [
            self._notice_due(job) for _change, _steps, job in
            await self._stopped_jobs(without_notice=True)
        ]
        if not times:
            return None
        return max((min(times) - self._clock()).total_seconds(), 0.05)

    def _notice_due(self, job: aiosqlite.Row) -> datetime:
        """When the stop notice of *job*, which has not landed, is next to be posted: at once where
        it has not been tried since the bot started, then at the first retry mark after the last
        attempt, and hourly once the marks are spent."""
        tried = self._notice_tried.get(job["id"])
        if tried is None:
            return self._clock()
        since = datetime.fromisoformat(job["failing_since"])
        marks = (since + timedelta(minutes=minutes) for minutes in RETRY_AFTER)
        return next((mark for mark in marks if mark > tried), tried + NOTICE_REPOST_EVERY)

    async def run_until_idle(self, *, steps: int | None = None) -> None:
        """Carry out what can run now, in order: until nothing can, or *steps* steps are done."""
        done = 0
        async with self._working:
            while True:
                progress = await self._advance()
                if progress is None:
                    break
                if progress:
                    done += 1
                    if steps is not None and done >= steps:
                        return
            await self._post_missing_notices()

    async def _advance(self) -> bool | None:
        """Do the next unit of work: start a change, run a job, or finish a change.

        Returns True where a job was done, False for other progress, and None where nothing
        can run now.
        """
        async with self._asking, get_connection(self._db_path) as db:
            change = await self._choose(db)
            if change is None:
                return None
            self._trying = change["id"]
            try:
                step_rows = await self._read_steps(db, change["id"])
            except BaseException:
                self._trying = None
                raise
        try:
            return await self._carry_out(change, step_rows)
        finally:
            self._trying = None

    async def _carry_out(
        self, change: aiosqlite.Row, step_rows: list[aiosqlite.Row]
    ) -> bool:
        """Carry out the unit of work on *change*, which `_advance` has chosen."""
        pending = next((row for row in step_rows if row["done_at"] is None), None)
        change_type = self._types.get(change["kind"])
        if change_type is None:
            unknown = KeyError(f"no change type {change['kind']!r} is registered")
            log.error("%s (change %s) cannot be carried out", change["what"], change["id"],
                      exc_info=unknown)
            await self._stop(change, pending, unknown)
            return False
        if change["state"] == ChangeState.QUEUED.value:
            return await self._start(change, change_type, pending)
        if pending is None:
            await self._finish(change, step_rows)
            return False
        return await self._run_step(change, change_type, step_rows, pending)

    async def _choose(self, db: aiosqlite.Connection) -> aiosqlite.Row | None:
        """The change to work on next, in strict order.

        A RUNNING change comes first, as one resumed after a stop; otherwise the QUEUED change
        with the lowest id, which nothing overtakes. Where that change's next job is stopped and
        not yet due (its `next_try_at` in the future, or none left), nothing runs.
        """
        cursor = await db.execute("SELECT * FROM queued_changes WHERE state = 'RUNNING' LIMIT 1")
        change = await cursor.fetchone()
        if change is None:
            cursor = await db.execute(
                "SELECT * FROM queued_changes WHERE state = 'QUEUED' ORDER BY id LIMIT 1"
            )
            change = await cursor.fetchone()
        if change is None:
            return None
        cursor = await db.execute(
            "SELECT failing_since, next_try_at FROM queued_change_steps "
            "WHERE change_id = ? AND done_at IS NULL ORDER BY position LIMIT 1",
            (change["id"],),
        )
        job = await cursor.fetchone()
        if job is not None and job["failing_since"] is not None and (
            job["next_try_at"] is None
            or datetime.fromisoformat(job["next_try_at"]) > self._clock()
        ):
            return None
        return change

    @staticmethod
    async def _read_steps(db: aiosqlite.Connection, change_id: int) -> list[aiosqlite.Row]:
        cursor = await db.execute(
            "SELECT * FROM queued_change_steps WHERE change_id = ? ORDER BY position",
            (change_id,),
        )
        return list(await cursor.fetchall())

    # ------------------------------------------------------------------
    # Starting a change: the second check
    # ------------------------------------------------------------------

    async def _start(
        self, change: aiosqlite.Row, change_type: ChangeType, pending: aiosqlite.Row | None
    ) -> bool:
        """Run the second check of a change not yet started, and act on what it finds.

        A member's change refused is refused, and the queue goes on; a bot change no longer due is
        dropped. A bot change refused, or a check that raises, stops the queue on the change, at
        its first job not done, and the check runs again at each try.
        """
        try:
            verdict = await change_type.check(
                CheckContext(
                    json.loads(change["payload"]),
                    self._bot,
                    self._db_path,
                    ChangeOrigin(change["origin"]),
                    change["id"],
                )
            )
        except Exception as error:  # noqa: BLE001 — a check that raises stops the queue
            log.log(self._failure_level(pending), "the check of %s raised (change %s)",
                    change["what"], change["id"], exc_info=error)
            await self._stop(change, pending, error)
            return False
        if verdict.kind is VerdictKind.GO:
            await self._go(change, pending)
            return False

        if change["origin"] == ChangeOrigin.MEMBER.value:
            await self._refuse(change, verdict, pending)
        elif verdict.kind is VerdictKind.NOT_DUE:
            await self._drop(change, verdict.reason, pending)
        else:
            await self._stop(change, pending, ChangeRefused(verdict.reason or verdict.reply))
        return False

    async def _go(self, change: aiosqlite.Row, pending: aiosqlite.Row | None) -> None:
        """Start the change whose check has passed.

        Where the check had stopped the queue (the change's first job carries a stop), passing
        clears that stop, in the same save as the start: the job's tries, failing_since,
        last_failure and next_try_at are reset, so that a failure of the job itself is a first
        failure with a notice of its own and the whole schedule, and the line saying job #N went
        through is written. The notice's buttons come off once the save is made.
        """
        stopped = pending is not None and pending["failing_since"] is not None
        ids: list[int] = []
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "UPDATE queued_changes SET state = 'RUNNING' "
                    "WHERE id = ? AND state = 'QUEUED'",
                    (change["id"],),
                )
                if cursor.rowcount == 1 and stopped and pending is not None:
                    await db.execute(
                        "UPDATE queued_change_steps SET tries = 0, failing_since = NULL, "
                        "last_failure = NULL, next_try_at = NULL, notice_message_id = NULL, "
                        "notice_channel_id = NULL WHERE id = ? AND done_at IS NULL",
                        (pending["id"],),
                    )
                    line_id = await self._router.queue_log_on(
                        db, went_through_line(pending["id"], change["what"])
                    )
                    if line_id is not None:
                        ids.append(line_id)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        if cursor.rowcount != 1:
            log.warning(
                "change %s was ended or removed while its check ran, so it did not start",
                change["id"],
            )
            return
        if stopped and pending is not None:
            self._retrying.pop(pending["id"], None)
            await self._router.deliver_queued(ids, interaction=self._answerable(change))
            if pending["notice_message_id"] is not None:
                await self._router.strip_view(
                    pending["notice_channel_id"], pending["notice_message_id"]
                )

    async def _refuse(
        self, change: aiosqlite.Row, verdict: Verdict, pending: aiosqlite.Row | None = None
    ) -> None:
        """Refuse a member's change that fails its check as it starts: the acknowledgement is
        updated with the refusal's reply, and the line is saved with the mark.

        Where the check had stopped the queue on the change, the refusal clears the stop: a line
        says so, saved with the refusal, and the stop notice loses its buttons.
        """
        reply = _refusal_text(verdict)
        named = self._named(change)
        lines = [
            refusal_line(
                named, change["what"], reply_reason(reply), detail=verdict.reason or None
            )
        ]
        cleared = self._cleared(
            change, pending, "its request was refused at its check"
        )
        if cleared is not None:
            lines.append(cleared)
        ids = await self._end(change, ChangeState.REFUSED, *lines)
        await self._router.deliver_queued(ids, interaction=self._answerable(change))
        await self._strip_stop(pending)
        await self._update_reply(change, reply)
        self._release(change)

    async def _drop(
        self, change: aiosqlite.Row, reason: str, pending: aiosqlite.Row | None = None
    ) -> None:
        """Drop a bot change that is no longer due: the host's log says so.

        Where the check had stopped the queue on the change, the drop clears the stop: a line
        in the log channel says so, and the stop notice loses its buttons.
        """
        cleared = self._cleared(change, pending, "it is no longer due, so it was dropped")
        ids = await self._end(
            change, ChangeState.DROPPED, *([cleared] if cleared is not None else [])
        )
        log.info("%s is no longer due, so it was dropped: %s", change["what"], reason)
        await self._router.deliver_queued(ids, interaction=self._answerable(change))
        await self._strip_stop(pending)
        self._release(change)

    @staticmethod
    def _cleared(
        change: aiosqlite.Row, pending: aiosqlite.Row | None, why: str
    ) -> str | None:
        """The line saying the stop at the change's first job clears, *why*; None where that job
        was not stopped."""
        if pending is None or pending["failing_since"] is None:
            return None
        return cleared_line(pending["id"], change["what"], why)

    async def _strip_stop(self, pending: aiosqlite.Row | None) -> None:
        """Take the buttons off the stop notice of the job *pending*, once its stop has cleared
        without the job going through, and forget a Retry under way on it."""
        if pending is None or pending["failing_since"] is None:
            return
        self._retrying.pop(pending["id"], None)
        if pending["notice_message_id"] is not None:
            await self._router.strip_view(
                pending["notice_channel_id"], pending["notice_message_id"]
            )

    async def _end(
        self, change: aiosqlite.Row, state: ChangeState, *lines: str
    ) -> list[int]:
        """Mark the change *state*, with each of *lines* saved on the retry queue in the same
        save. Returns the lines' ids, for delivery once saved. A change removed under the
        worker is left, and its lines unwritten."""
        ids: list[int] = []
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "UPDATE queued_changes SET state = ? WHERE id = ?", (state.value, change["id"])
                )
                if cursor.rowcount == 1:
                    for line in lines:
                        line_id = await self._router.queue_log_on(db, line)
                        if line_id is not None:
                            ids.append(line_id)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return ids

    def _release(self, change: aiosqlite.Row) -> None:
        """Let go of the change's interaction, and wake the worker for what comes next."""
        self._held.pop(change["id"], None)
        self._signal.set()

    @staticmethod
    def _named(change: aiosqlite.Row) -> str:
        """The member who asked as the log channel names them, or "the bot" where nobody did."""
        if change["actor_id"] is None:
            return "the bot"
        return member_named(change["actor_display"], change["actor_id"])

    # ------------------------------------------------------------------
    # A step
    # ------------------------------------------------------------------

    def _outcome_context(
        self, change: aiosqlite.Row, step_rows: list[aiosqlite.Row] | tuple[aiosqlite.Row, ...]
    ) -> OutcomeContext:
        return OutcomeContext(**self._context_fields(change, step_rows))

    def _context_fields(
        self, change: aiosqlite.Row, step_rows: list[aiosqlite.Row] | tuple[aiosqlite.Row, ...]
    ) -> dict[str, Any]:
        return {
            "bot": self._bot,
            "db_path": self._db_path,
            "change_id": change["id"],
            "kind": change["kind"],
            "payload": json.loads(change["payload"]),
            "what": change["what"],
            "actor_id": change["actor_id"],
            "actor_name": change["actor_name"],
            "named": self._named(change),
            "steps": tuple(
                StepView(
                    row["name"],
                    json.loads(row["payload"]),
                    json.loads(row["result"]) if row["result"] else None,
                    row["done_at"] is not None,
                )
                for row in step_rows
            ),
        }

    async def _run_step(
        self,
        change: aiosqlite.Row,
        change_type: ChangeType,
        step_rows: list[aiosqlite.Row],
        row: aiosqlite.Row,
    ) -> bool:
        """Run the step *row*, saving its mark with its writes, audits and lines.

        A step no longer due is marked done as dropped. One that raises has its save rolled back
        whole, and stops the queue at it.
        """
        step = change_type.steps.get(row["name"])
        if step is None:
            unknown = KeyError(f"change type {change['kind']!r} has no step {row['name']!r}")
            log.error("%s (change %s) cannot be carried out", change["what"], change["id"],
                      exc_info=unknown)
            await self._stop(change, row, unknown)
            return False
        ctx = self._step_context(change, step_rows, row)
        stopped_as = await self._stopped_as(change, row, ctx)
        produced: StepResult | None = None
        try:
            if step.still_due is not None and not await step.still_due(ctx):
                return await self._complete(
                    change, row, StepResult(result={"dropped": True}), stopped_as
                )
            if step.kind is StepKind.SAVE:
                async with get_connection(self._db_path) as db:
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        result = await step.run(db, ctx)
                        saved = await self._save_result(db, change, row, result, stopped_as)
                        if saved is None:
                            await db.rollback()
                        else:
                            await db.commit()
                    except BaseException:
                        await db.rollback()
                        raise
                return await self._delivered(change, saved, row)
            produced = await step.run(ctx)
            return await self._complete(
                change, row, produced, stopped_as, record=self._recording(step, ctx, produced)
            )
        except Exception as error:  # noqa: BLE001 — the failure path for changes; see `_stop`
            log.log(self._failure_level(row), "job %s (%r) of %s raised (change %s)", row["id"],
                    row["name"], change["what"], change["id"], exc_info=error)
            return await self._step_failed(change, step, ctx, row, error, produced)

    @staticmethod
    def _recording(
        step: Step, ctx: StepContext, result: StepResult
    ) -> Callable[[aiosqlite.Connection], Awaitable[None]] | None:
        """The step's record bound to what it returned, to run in the save marking it done; None
        where it carries none."""
        record = step.record
        if record is None:
            return None

        async def _record(db: aiosqlite.Connection) -> None:
            await record(db, ctx, result)

        return _record

    async def _stopped_as(
        self, change: aiosqlite.Row, row: aiosqlite.Row, ctx: StepContext
    ) -> str | None:
        """What the job *row* is called in the line saying it went through, where it had been
        stopped; None where it had not."""
        if row["failing_since"] is None:
            return None
        return await self._name_job(change, row, ctx)

    async def _complete(
        self, change: aiosqlite.Row, row: aiosqlite.Row, result: StepResult,
        stopped_as: str | None,
        record: Callable[[aiosqlite.Connection], Awaitable[None]] | None = None,
    ) -> bool:
        """Save the mark of a step whose work needed no connection, with *result*'s audits and
        lines, then deliver the lines. The step's *record* runs first, in the same save, so that
        what it writes lands with the mark or not at all."""
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                if record is not None:
                    await record(db)
                saved = await self._save_result(db, change, row, result, stopped_as)
                if saved is None:
                    await db.rollback()
                else:
                    await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return await self._delivered(change, saved, row)

    async def _delivered(
        self, change: aiosqlite.Row, saved: list[int] | None, row: aiosqlite.Row
    ) -> bool:
        """Deliver the lines the save of the job *row* wrote, and take the buttons off its stop
        notice where it had one; False where the save was not made."""
        if saved is None:
            return False
        await self._router.deliver_queued(saved, interaction=self._answerable(change))
        if row["notice_message_id"] is not None:
            await self._router.strip_view(row["notice_channel_id"], row["notice_message_id"])
        return True

    async def _step_failed(
        self,
        change: aiosqlite.Row,
        step: Step,
        ctx: StepContext,
        row: aiosqlite.Row,
        error: Exception,
        produced: StepResult | None = None,
    ) -> bool:
        """Deal with the job *row* raising *error*, its save already rolled back.

        `NotFound` completes a `DELETE` job (the message is already gone, and no line says so)
        and an `EDIT` job (with its `gone_line`), its record run as it is marked. Every other
        failure, from Discord or from the bot, stops the queue at the job. Where the step had
        returned and what failed was the save that marks it (its record raising), *produced* is
        kept on the job as a partial result, so that the next try can remove what this one sent.
        """
        if (
            produced is None
            and isinstance(error, discord.NotFound)
            and step.kind in (StepKind.DELETE, StepKind.EDIT)
        ):
            lines: tuple[str, ...] = ()
            if step.kind is StepKind.EDIT and step.gone_line is not None:
                gone = step.gone_line if isinstance(step.gone_line, str) else step.gone_line(ctx)
                lines = (gone,)
            completed = StepResult(result={"gone": True}, lines=lines)
            return await self._complete(
                change, row, completed, await self._stopped_as(change, row, ctx),
                record=self._recording(step, ctx, completed),
            )
        job = await self._name_job(change, row, ctx)
        kept = produced.result if produced is not None and step.kind is not StepKind.SAVE else None
        await self._stop(change, row, error, job=job, partial=kept)
        return False

    @staticmethod
    def _failure_level(row: aiosqlite.Row | None) -> int:
        """How loud the host's log is about a failure: an error the first time a job fails, a
        warning at each try after."""
        return logging.ERROR if row is None or row["failing_since"] is None else logging.WARNING

    @staticmethod
    def _fault_kind(error: BaseException) -> str:
        """The kind of fault *error* is, as a stop line names it: the exception's type, or the
        reason for a bot change its check refused; for a failure a job gave Discord, the type of
        the failure that caused it where it names one, else its reason."""
        if isinstance(error, ChangeRefused):
            return str(error)
        if isinstance(error, StepFailedOnDiscord):
            cause = error.__cause__
            return error.reason if cause is None else type(cause).__name__
        return type(error).__name__

    async def _stop(
        self,
        change: aiosqlite.Row,
        row: aiosqlite.Row | None,
        error: BaseException,
        *,
        job: str | None = None,
        partial: dict[str, Any] | None = None,
    ) -> None:
        """Stop the queue at the job *row* of *change*, which failed with *error*.

        A job that fails stops the queue until it is cleared, and every kind of failure does.
        The first failure is saved in one save, then posted to the log channel as the stop notice
        (`log_lines.stop_line`, with the Retry and Discard buttons: `_post_notice`), and the
        member's acknowledgement is told, where it can still be updated. The caller has put the
        traceback in the host's log, as the catch-all it is. The job is tried again on the
        schedule, `RETRY_AFTER`, counted from that first failure; a try that fails moves it to
        the next mark and writes no line, but for the last, which writes one saying the bot has
        stopped trying on its own, and leaves no try.
        A Retry's try that fails (`retry`) writes one line naming the presser and the kind of
        fault instead, and leaves the schedule as it stood. A partial result a failure carries
        is kept on the job, as is the result of a step whose record raised (*partial*). *row* is the job, or for a check
        that fails before the change starts, the change's first job not done; *job* names it,
        the change's own words where none is given.

        Where the save itself raises, nothing is marked: the worker's catch-all logs it and the
        job runs again, so that its failure is recorded then.
        """
        what = change["what"]
        if row is None:
            log.error("%s for %s (change %s) failed with no job to stop at, so it was dropped",
                      what, self._named(change), change["id"], exc_info=error)
            await self._end(change, ChangeState.DROPPED)
            self._release(change)
            return
        first = row["failing_since"] is None
        now = self._clock()
        since = now if first else datetime.fromisoformat(row["failing_since"])
        marks = (since + timedelta(minutes=minutes) for minutes in RETRY_AFTER)
        next_try = next((mark for mark in marks if mark > now), None)
        kind = self._fault_kind(error)
        if partial is None and isinstance(error, StepFailedOnDiscord):
            partial = error.result
        named = job if job is not None else what
        line = hour_line(row["id"], named) if not first and next_try is None else None
        # Read, not popped: the entry goes once the save has committed, so a save that raises
        # leaves it for the try the worker makes again, which is still the Retry's.
        retried = self._retrying.get(row["id"])
        if retried is not None:
            # A Retry's try that fails is no mark of the schedule: it leaves it as it was.
            presser, before = retried
            next_try = None if before is None else datetime.fromisoformat(before)
            line = retry_failed_line(presser, row["id"], named, kind)
        ids: list[int] = []
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "UPDATE queued_change_steps SET tries = ?, failing_since = ?, "
                    "last_failure = ?, next_try_at = ?, result = COALESCE(?, result) "
                    "WHERE id = ? AND done_at IS NULL",
                    (
                        row["tries"] + 1,
                        since.isoformat(),
                        kind,
                        None if next_try is None else next_try.isoformat(),
                        json.dumps(partial) if partial else None,
                        row["id"],
                    ),
                )
                if cursor.rowcount == 1 and line is not None:
                    line_id = await self._router.queue_log_on(db, line)
                    if line_id is not None:
                        ids.append(line_id)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        self._retrying.pop(row["id"], None)
        await self._router.deliver_queued(ids, interaction=self._answerable(change))
        if first:
            await self._post_notice(change, row["id"], named, kind)
            await self._update_reply(
                change,
                f"❌ {what[:1].upper() + what[1:]} is stopped at job #{row['id']} and will be "
                f"tried again.",
            )

    # ------------------------------------------------------------------
    # The stop notice
    # ------------------------------------------------------------------

    async def _post_notice(
        self, change: aiosqlite.Row, job_id: int, job: str, fault: str
    ) -> None:
        """Post the stop notice of the job *job_id*, named *job* and failing with *fault*, to the
        log channel with its Retry and Discard buttons, and keep where it landed on the job.

        Never raises: where the post fails the stop stands (the router has told the member), the
        attempt is noted and `_post_missing_notices` tries again on its schedule. The notice goes
        through the router's `post_notice`, which never puts it on the log lines' retry queue, as
        that would send it again without its buttons.
        """
        self._notice_tried[job_id] = self._clock()
        try:
            message = await self._router.post_notice(
                stop_line(job_id, job, change["what"], self._named(change), fault),
                QueueStopView(self),
                interaction=self._answerable(change),
            )
            if message is None:
                return
            async with get_connection(self._db_path) as db:
                await db.execute(
                    "UPDATE queued_change_steps SET notice_message_id = ?, notice_channel_id = ? "
                    "WHERE id = ? AND done_at IS NULL",
                    (message.id, message.channel.id, job_id),
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — the stop stands; the notice is posted again
            log.error("could not post the stop notice of job %s (change %s)", job_id,
                      change["id"], exc_info=True)

    async def _post_missing_notices(self) -> None:
        """Post again the stop notice of every stopped job whose notice has not landed, where it
        is due: at start-up and on each pass of the worker, no more often than the retry marks
        (`_notice_due`) and hourly once they are spent."""
        for change, steps, job in await self._stopped_jobs(without_notice=True):
            if self._notice_due(job) > self._clock():
                continue
            name = await self._name_job(change, job, self._step_context(change, steps, job))
            await self._post_notice(change, job["id"], name, job["last_failure"] or "a fault")

    # ------------------------------------------------------------------
    # Retry and Discard
    # ------------------------------------------------------------------

    async def retry(
        self, notice_message_id: int | None, interaction: discord.Interaction
    ) -> None:
        """Retry on the stop notice *notice_message_id*, pressed through *interaction*.

        The queue is stopped, so a Retry works directly on the queue's own records and is not
        itself queued. It is a league manager's or a league admin's, at any time, and is refused,
        privately and with a line in the log channel, to anyone else and where the notice's job
        no longer stops the queue (it cleared, was discarded, or went with a pack), and where the
        worker is trying the job's change at that moment, as a Discard is. Otherwise the
        job is made due now, the worker is woken, the presser is told and the log names them.
        The try is the worker's, in the order of the queue: where it fails, `_stop` records it.
        """
        found = await self._stopped_at(notice_message_id)
        what = "Retry of a stopped job" if found is None else f"Retry of job #{found[2]['id']}"
        config = await self._config.get_server_config()
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_league_manager(config, member):
            await refuse(
                interaction,
                "⛔ Only a league manager or a league admin may retry a stopped job.",
                what=what,
            )
            return
        if found is None:
            await refuse(
                interaction,
                "⛔ That job no longer stops the queue, so there is nothing to retry.",
                what=what,
            )
            return
        presser = interaction_member(interaction)
        # Under `_asking`, as a Discard is: the worker holds it as it chooses a change and sets
        # `_trying` under it, so a Retry is saved before the worker chooses, or finds the change
        # in hand and is refused. A press saved while a try is under way would leave its entry
        # behind once the try had cleared the stop, and a later failure would be read as the
        # Retry's.
        async with self._asking:
            if self._trying == found[0]["id"]:
                await refuse(
                    interaction,
                    f"⛔ Job #{found[2]['id']} is being tried now. Press Retry again once the "
                    f"try has ended, if it still stops the queue.",
                    what="Retry of a stopped job",
                )
                return
            found = await self._stopped_at(notice_message_id)
            if found is None:
                await refuse(
                    interaction,
                    "⛔ That job no longer stops the queue, so there is nothing to retry.",
                    what=what,
                )
                return
            change, steps, job = found
            name = await self._name_job(change, job, self._step_context(change, steps, job))
            before = (
                self._retrying[job["id"]][1] if job["id"] in self._retrying else job["next_try_at"]
            )
            async with get_connection(self._db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    cursor = await db.execute(
                        "UPDATE queued_change_steps SET next_try_at = ? WHERE id = ? "
                        "AND done_at IS NULL",
                        (self._clock().isoformat(), job["id"]),
                    )
                    line_id = None
                    if cursor.rowcount == 1:
                        line_id = await self._router.queue_log_on(
                            db, retried_line(presser, job["id"], name, change["what"])
                        )
                    await db.commit()
                except BaseException:
                    await db.rollback()
                    raise
            self._retrying[job["id"]] = (presser, before)
            # Delivered under the lock the worker takes to choose its next change, so that the
            # line saying the job is tried again stands above whatever its try then writes.
            await self._router.deliver_queued([] if line_id is None else [line_id],
                                              interaction=interaction)
        await self._tell(interaction, f"🔁 Job #{job['id']} ({name}) is being tried again now.")
        self._signal.set()

    async def _stopped_at(
        self, notice_message_id: int | None
    ) -> tuple[aiosqlite.Row, list[aiosqlite.Row], aiosqlite.Row] | None:
        """The job the stop notice *notice_message_id* is of, with its change and the change's
        steps, where it still stops the queue; None where it does not."""
        if notice_message_id is None:
            return None
        for found in await self._stopped_jobs():
            if found[2]["notice_message_id"] == notice_message_id:
                return found
        return None

    @staticmethod
    async def _tell(interaction: discord.Interaction, text: str) -> None:
        """Answer the member who pressed, seen by them alone. Never raises: the press has done
        what it did."""
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception:  # noqa: BLE001 — the press has been carried out and recorded
            log.warning("could not tell the member what their press did", exc_info=True)

    async def discard(
        self, notice_message_id: int | None, interaction: discord.Interaction
    ) -> None:
        """Discard on the stop notice *notice_message_id*, pressed through *interaction*.

        Like Retry it works directly on the queue's own records, and it is refused in the same
        two cases, and also to anyone who is not a league admin, and where the worker is trying
        the job's change at that moment (the press is then to be made again once the try has
        ended: a job being tried is not dropped under the worker). Otherwise one save drops the
        job alone: it is marked done with the result `{"discarded": {"by", "at"}}` and what a
        failure had kept of its partial result, so that the change's outcome tells what was not
        done and the request's later jobs run on; a change that had not started, which stopped
        at its check, ends DISCARDED whole. The same save writes the log line naming the admin,
        job #N and what was not done, and the `CHANGE_JOB_DISCARDED` audit record. Then the
        worker is woken and the notice loses its buttons.
        """
        found = await self._stopped_at(notice_message_id)
        what = "Discard of a stopped job" if found is None else f"Discard of job #{found[2]['id']}"
        config = await self._config.get_server_config()
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_league_admin(config, member):
            await refuse(
                interaction, "⛔ Only a league admin may discard a stopped job.", what=what
            )
            return
        if found is None:
            await refuse(
                interaction,
                "⛔ That job no longer stops the queue, so there is nothing to discard.",
                what=what,
            )
            return
        change = found[0]
        ids: list[int] = []
        # Under `_asking`, which the worker holds as it chooses a change: a Discard is saved
        # before the worker chooses, or finds the change in hand and waits for the try to end.
        async with self._asking:
            if self._trying == change["id"]:
                await refuse(
                    interaction,
                    f"⛔ Job #{found[2]['id']} is being tried now. Press Discard again once the "
                    f"try has ended, if it still stops the queue.",
                    what=what,
                )
                return
            # Read again under the lock: a try that ended while this press waited for it may have
            # kept more of its partial result, and the discard is saved from what stands now.
            found = await self._stopped_at(notice_message_id)
            if found is None:
                await refuse(
                    interaction,
                    "⛔ That job no longer stops the queue, so there is nothing to discard.",
                    what=what,
                )
                return
            change, steps, job = found
            name = await self._name_job(change, job, self._step_context(change, steps, job))
            started = change["state"] == ChangeState.RUNNING.value
            now = self._clock()
            saved = await self._save_discard(
                interaction, change, job, name, started=started, now=now, ids=ids
            )
        if not saved:
            await refuse(
                interaction,
                "⛔ That job no longer stops the queue, so there is nothing to discard.",
                what=what,
            )
            return
        self._retrying.pop(job["id"], None)
        await self._tell(interaction, f"🗑️ Job #{job['id']} ({name}) is discarded.")
        await self._router.deliver_queued(ids, interaction=interaction)
        if job["notice_message_id"] is not None:
            await self._router.strip_view(job["notice_channel_id"], job["notice_message_id"])
        if not started:
            await self._update_reply(
                change,
                f"⛔ {change['what'][:1].upper() + change['what'][1:]} was discarded by a league "
                f"admin: nothing of it was done.",
            )
            self._release(change)
        else:
            self._signal.set()

    async def _save_discard(
        self,
        interaction: discord.Interaction,
        change: aiosqlite.Row,
        job: aiosqlite.Row,
        name: str,
        *,
        started: bool,
        now: datetime,
        ids: list[int],
    ) -> bool:
        """Save a Discard in one save, with its line (its id added to *ids*) and audit record.

        Returns False where the save matched no row: the job had cleared since it was found.
        """
        member = interaction.user
        presser = interaction_member(interaction)
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                if started:
                    kept = json.loads(job["result"]) if job["result"] else {}
                    discarded = {**kept, "discarded": {"by": presser, "at": now.isoformat()}}
                    cursor = await db.execute(
                        "UPDATE queued_change_steps SET done_at = ?, result = ? "
                        "WHERE id = ? AND done_at IS NULL",
                        (now.isoformat(), json.dumps(discarded), job["id"]),
                    )
                else:
                    cursor = await db.execute(
                        "UPDATE queued_changes SET state = ? WHERE id = ? AND state = 'QUEUED'",
                        (ChangeState.DISCARDED.value, change["id"]),
                    )
                if cursor.rowcount == 1:
                    line_id = await self._router.queue_log_on(
                        db, discarded_line(presser, job["id"], name, change["what"])
                    )
                    if line_id is not None:
                        ids.append(line_id)
                    await record_change_on(
                        db,
                        actor_id=member.id,
                        actor_name=str(member),
                        change_type="CHANGE_JOB_DISCARDED",
                        old_value={"job": job["id"], "step": job["name"],
                                   "change": change["id"], "request": change["what"],
                                   "failing_since": job["failing_since"],
                                   "last_failure": job["last_failure"]},
                        new_value={"discarded": True, "change_ended": not started},
                        now=now,
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return cursor.rowcount == 1

    async def _save_result(
        self, db: aiosqlite.Connection, change: aiosqlite.Row, row: aiosqlite.Row,
        result: StepResult, stopped_as: str | None,
    ) -> list[int] | None:
        """Write the step's mark, audits and lines on *db*, without committing.

        *stopped_as* names the job where it had been stopped and so goes through at last (or is
        found no longer due and dropped), which adds the line saying so, saved with the mark; it is formed beforehand, since nothing
        but the connection is awaited in a save.

        Returns the ids of the lines queued, or None where the mark updated no row: the change
        was removed under the worker (by a pack, or a factory reset), so nothing of the step is
        saved and the host's log says so.
        """
        cursor = await db.execute(
            "UPDATE queued_change_steps SET done_at = ?, result = ? "
            "WHERE change_id = ? AND position = ? AND done_at IS NULL",
            (self._clock().isoformat(), json.dumps(result.result),
             change["id"], row["position"]),
        )
        if cursor.rowcount != 1:
            log.warning(
                "change %s was removed while its step %r ran, so nothing of the step was saved",
                change["id"], row["name"],
            )
            return None
        self._retrying.pop(row["id"], None)
        for audit in result.audits:
            await self._audit(db, change, audit)
        ids: list[int] = []
        lines = list(result.lines)
        if stopped_as is not None:
            lines.append(
                cleared_line(row["id"], stopped_as, "it is no longer due, so it was dropped")
                if (result.result or {}).get("dropped")
                else went_through_line(row["id"], stopped_as)
            )
        for line in lines:
            line_id = await self._router.queue_log_on(db, line)
            if line_id is not None:
                ids.append(line_id)
        await self._plan_steps(db, change, row, result.then)
        for follow_on in result.follow_ons:
            await self._ask_on(db, change, follow_on)
        return ids

    @staticmethod
    async def _plan_steps(
        db: aiosqlite.Connection, change: aiosqlite.Row, row: aiosqlite.Row,
        planned: tuple[PlannedStep, ...],
    ) -> None:
        """Insert *planned* steps after the step *row*, renumbering the later ones to make room.

        The later steps move one at a time from the last, since a primary key is checked as each
        row is updated and not once all are.
        """
        if not planned:
            return
        cursor = await db.execute(
            "SELECT position FROM queued_change_steps WHERE change_id = ? AND position > ? "
            "ORDER BY position DESC",
            (change["id"], row["position"]),
        )
        for later in await cursor.fetchall():
            await db.execute(
                "UPDATE queued_change_steps SET position = position + ? "
                "WHERE change_id = ? AND position = ?",
                (len(planned), change["id"], later["position"]),
            )
        for offset, step in enumerate(planned, start=1):
            await db.execute(
                "INSERT INTO queued_change_steps (change_id, position, name, payload) "
                "VALUES (?, ?, ?, ?)",
                (change["id"], row["position"] + offset, step.name, json.dumps(step.payload)),
            )

    async def _ask_on(
        self, db: aiosqlite.Connection, change: aiosqlite.Row, follow_on: FollowOn
    ) -> None:
        """Queue *follow_on* as a change of the bot's for the same actor, in the step's save.

        It is never refused as a repeat: the step that asks for it has just run, so what it asks
        for is wanted whatever was asked for before.
        """
        change_type = self._type(follow_on.kind)
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, actor_id, actor_name, "
            "actor_display, what) VALUES (?, ?, ?, 'BOT', ?, ?, ?, ?)",
            (
                follow_on.kind,
                change_type.key(follow_on.payload),
                json.dumps(follow_on.payload),
                change["actor_id"],
                change["actor_name"],
                change["actor_display"],
                follow_on.what,
            ),
        )
        change_id = inserted_id(cursor)
        for position, planned in enumerate(change_type.opening):
            await db.execute(
                "INSERT INTO queued_change_steps (change_id, position, name, payload) "
                "VALUES (?, ?, ?, ?)",
                (change_id, position, planned.name, json.dumps(planned.payload)),
            )

    async def _audit(
        self, db: aiosqlite.Connection, change: aiosqlite.Row, audit: AuditRecord
    ) -> None:
        await record_change_on(
            db,
            actor_id=change["actor_id"],
            actor_name=change["actor_name"],
            change_type=audit.change_type,
            old_value=audit.old_value,
            new_value=audit.new_value,
            now=self._clock(),
            division_id=audit.division_id,
        )

    # ------------------------------------------------------------------
    # Finishing, and the member's reply
    # ------------------------------------------------------------------

    def _answerable(self, change: aiosqlite.Row) -> discord.Interaction | None:
        """The change's interaction where it is held and under 14 minutes since it was
        acknowledged, otherwise None."""
        held = self._held_and_updatable(change)
        return None if held is None else held.interaction

    def _held_and_updatable(self, change: aiosqlite.Row) -> "_Held | None":
        held = self._held.get(change["id"])
        acknowledged = change["acknowledged_at"]
        if held is None or acknowledged is None:
            return None
        if self._clock() - datetime.fromisoformat(acknowledged) >= UPDATABLE_FOR:
            return None
        return held

    async def _finish(self, change: aiosqlite.Row, step_rows: list[aiosqlite.Row]) -> None:
        """Mark the change DONE and update its acknowledgement with the outcome."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE queued_changes SET state = 'DONE' WHERE id = ?", (change["id"],)
            )
            await db.commit()
        change_type = self._types[change["kind"]]
        try:
            text = change_type.outcome(self._outcome_context(change, step_rows))
        except Exception:  # noqa: BLE001 — the change is done whatever its outcome's wording does
            log.error("could not word what became of %s (change %s)", change["what"],
                      change["id"], exc_info=True)
            self._release(change)
            return
        await self._update_reply(change, text)
        self._release(change)

    async def _update_reply(self, change: aiosqlite.Row, text: str) -> None:
        """Update the acknowledgement with *text*, in as many parts as it needs.

        Only while the interaction is held and under 14 minutes old. A failed update is logged
        and never fails the change.
        """
        held = self._held_and_updatable(change)
        if held is None:
            return
        interaction = held.interaction
        try:
            for number, part in enumerate(chunk_message(text)):
                if number == 0 and held.message is not None:
                    await held.message.edit(content=part)
                elif number == 0:
                    await interaction.edit_original_response(content=part)
                else:
                    await interaction.followup.send(part, ephemeral=True)
        except Exception:  # noqa: BLE001 — the change is done whatever the reply's fate
            log.error(
                "could not update the reply to %s (change %s)",
                change["what"], change["id"], exc_info=True,
            )


def empty_queue_in(path: str | os.PathLike[str]) -> None:
    """Delete every change, and its steps, from the league database at *path*.

    Synchronous, for the restore: it runs at start-up before the swap, on the staged file, when no
    event loop is running and nothing holds a connection. Where the tables are not there (a file
    from before the queue) it does nothing.
    """
    db = sqlite3.connect(str(path))
    try:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('queued_changes', 'queued_change_steps')"
            )
        }
        if "queued_change_steps" in tables:
            db.execute("DELETE FROM queued_change_steps")
        if "queued_changes" in tables:
            db.execute("DELETE FROM queued_changes")
        db.commit()
    finally:
        db.close()


async def unfinished(
    db_path: str, kinds: Collection[str], *, excluding: int | None = None
) -> list[dict[str, Any]]:
    """The payloads of the changes of *kinds* that are queued or running, a stopped one included,
    oldest first, leaving out the change whose id is *excluding*.

    A change done, refused, dropped or discarded is not in hand and is not given. Read on a
    connection of its own, so a check may call it while the queue is between jobs.
    """
    kinds = list(kinds)
    if not kinds:
        return []
    marks = ", ".join("?" for _ in kinds)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT payload FROM queued_changes WHERE kind IN ({marks}) "  # noqa: S608 — marks only
            "AND state IN ('QUEUED', 'RUNNING') AND (? IS NULL OR id != ?) ORDER BY id",
            (*kinds, excluding, excluding),
        )
        return [json.loads(row["payload"]) for row in await cursor.fetchall()]


def highest_job_number(path: str | os.PathLike[str]) -> int:
    """The highest job number the league database at *path* has ever issued, or 0.

    Read from the database's own count of the numbers it has issued, which a deleted job does not
    lower, and from the jobs it holds. Synchronous, for a restore and a factory reset, which run
    where no connection is open. Where the table is not there, 0.
    """
    db = sqlite3.connect(str(path))
    try:
        counted = 0
        try:
            row = db.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'queued_change_steps'"
            ).fetchone()
            counted = int(row[0]) if row else 0
            held = db.execute("SELECT COALESCE(MAX(id), 0) FROM queued_change_steps").fetchone()
            return max(counted, int(held[0]))
        except sqlite3.OperationalError:
            return counted
    finally:
        db.close()


def carry_job_numbering(path: str | os.PathLike[str], highest: int) -> None:
    """Make the next job the league database at *path* numbers come after *highest*.

    A database put in place of another (a restored state, a freshly migrated one at a factory
    reset) would otherwise begin the numbering from its own count, and a number the log channel
    already names would name a second job. Never lowers a count the database already has.
    Synchronous, as `highest_job_number`.
    """
    if highest <= 0:
        return
    db = sqlite3.connect(str(path))
    try:
        row = db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'queued_change_steps'"
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES ('queued_change_steps', ?)",
                (highest,),
            )
        elif int(row[0]) < highest:
            db.execute(
                "UPDATE sqlite_sequence SET seq = ? WHERE name = 'queued_change_steps'",
                (highest,),
            )
        db.commit()
    finally:
        db.close()
