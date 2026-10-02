"""The change queue: a change asked for is acknowledged at once and carried out step by step.

`docs/design/architecture.md`, "How a change is carried out", designs it; the core specification's
section of that name holds the rules a league sees. This module holds the engineering decisions,
each pinned by a test in `tests/core/test_change_queue.py`.

**A change is a row, and so is each of its steps.** `queued_changes` and `queued_change_steps`
are the queue's own record. A step's mark (`done_at`) is saved in the same transaction as the step's
own writes, its audit records and its log lines, so a stop leaves a step wholly done or not done,
and a restart carries the change on from its first step not done. A step marked done is never run
again.

**One change runs at a time, in the order asked.** One asyncio task, `ChangeQueue._task`, is kept
on the instance and started by `start()` (idempotent), from `on_ready` and never from an
interaction, so that it inherits no interaction's context. `run_until_idle` is the same loop for the
tests. A lock holds the task and a test's call to one step at a time.

**A step is one of four kinds** (`StepKind`). A `SAVE` step is handed the open save and writes only
on it, never committing. The other kinds do Discord or legacy work with no connection open and are
marked in a save after it. Nothing is awaited inside a save but the connection.

**A job that fails stops the queue.** Whatever the failure, from Discord or from the bot, and
whether in a job or in a bot change's check as it starts, nothing behind the job runs until it is
cleared: `_choose` takes the lowest change in order and runs nothing while its next job is stopped
and not yet due. A job's number is its `id`, never renumbered, and a stop is the job's own record
(`failing_since`, `tries`, `next_try_at`), the change's state staying as it was. The bot tries a
stopped job again on `RETRY_AFTER`, counted from the first failure, and says nothing of a try that
fails but the last.

**The queue forms no standard line of its own.** A refusal is `log_lines.refusal_line` and a stop
`log_lines.stop_line`: each standard line is formed in one place. Every line the queue
writes goes through the `OutputRouter` it was handed, never one looked up on the bot (a service does
not use the bot to look up other services).

**The check runs twice**: when the change is asked for, so that a member is refused at once, and
when it starts, since what it checked may have changed in between.

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
from collections.abc import Awaitable, Callable, Mapping
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
from leaguebot.core.utils.log_lines import (
    hour_line,
    refusal_line,
    refuse,
    reply_reason,
    restart_line,
    stop_line,
)
from leaguebot.core.utils.member_names import member_named
from leaguebot.core.utils.messages import chunk_message

if TYPE_CHECKING:
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


@dataclass(frozen=True)
class _Held:
    """A member's interaction held to update, with the follow-up message that acknowledged it
    where the interaction was already answered (a deferred command, a form), None where the
    acknowledgement was its response."""

    interaction: discord.Interaction
    message: discord.WebhookMessage | None


@dataclass(frozen=True)
class CheckContext:
    """What a change type's check reads: the request, the bot for Discord, and the database."""

    payload: dict[str, Any]
    bot: "LeagueBot"
    db_path: str
    origin: ChangeOrigin


@dataclass(frozen=True)
class StepView:
    """A step as an outcome reads it: its payload, its result and whether it is done."""

    name: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    done: bool


@dataclass(frozen=True)
class OutcomeContext:
    """What a change type's `outcome` and `fault_outcome` read: the change and every step of it.

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
    post as text on a retry (Constitution XIV, rule 8).
    """

    step_name: str = ""
    step_payload: dict[str, Any] = field(default_factory=dict)
    tries: int = 0


@dataclass(frozen=True)
class Step:
    """A step a change type can run.

    A `SAVE` step's `run(db, ctx)` writes only on *db* and never commits; any other step's
    `run(ctx)` does its work with no connection open. `still_due(ctx)` is asked before the step
    runs, and a step no longer due is marked done as dropped. `describe(ctx)` names what the step
    is doing, for the report of one that keeps failing. `gone_line`, a line or what forms one, is
    what an `EDIT` step writes where its message is gone. `tried_once` marks a `DELETE` or `ACT` step whose failure on
    Discord is kept in its result for the outcome, rather than retried.
    """

    name: str
    kind: StepKind
    run: Callable[..., Awaitable[StepResult]]
    still_due: Callable[[StepContext], Awaitable[bool]] | None = None
    describe: Callable[[StepContext], Awaitable[str]] | None = None
    gone_line: Callable[[StepContext], str] | str | None = None
    tried_once: bool = False


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
    fault_outcome: Callable[[OutcomeContext], str]
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
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db_path = db_path
        self._bot = bot
        self._router = output_router
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._types: dict[str, ChangeType] = {}
        self._held: dict[int, _Held] = {}
        self._signal = asyncio.Event()
        self._working = asyncio.Lock()
        self._asking = asyncio.Lock()
        self._task: Optional["asyncio.Task[None]"] = None

    # ------------------------------------------------------------------
    # Registering and asking
    # ------------------------------------------------------------------

    def register(self, change_type: ChangeType) -> None:
        """Make *change_type* known. Refuses a step tried once of a kind that cannot be."""
        for step in change_type.steps.values():
            if step.tried_once and step.kind not in (StepKind.DELETE, StepKind.ACT):
                raise ValueError(
                    f"step {step.name!r} of {change_type.kind!r} is tried once, which only a "
                    f"DELETE or ACT step may be"
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
                        await db.execute(
                            "INSERT INTO queued_change_steps (change_id, position, name, payload) "
                            "VALUES (?, ?, ?, ?)",
                            (change_id, position, planned.name, json.dumps(planned.payload)),
                        )
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
                await self._acknowledge(change_id, change_type, payload, interaction)
            self._signal.set()
            return change_id

    async def _acknowledge(
        self,
        change_id: int,
        change_type: ChangeType,
        payload: dict[str, Any],
        interaction: discord.Interaction,
    ) -> None:
        """Tell the member their saved change is under way, and hold the interaction to update.

        The change is already saved, so a failed acknowledgement is not the request's failure:
        it is logged with its details, nothing is held and `acknowledged_at` stays unset, and the
        change runs all the same, its outcome standing in the log channel alone.
        """
        text = (
            f"⏳ {change_type.doing(payload)}. This message will be updated when it is "
            f"done; if it takes longer, the log channel will say so."
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
        interaction to tell if it fails.
        """
        if self._task is not None and not self._task.done():
            return
        await self._leave_stopped_jobs_stopped()
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM pending_messages WHERE failure_reason = '' ORDER BY id"
            )
            never_tried = [row["id"] for row in await cursor.fetchall()]
        await self._router.deliver_queued(never_tried)
        self._task = asyncio.create_task(self._work(), name="change-queue")
        self._task.add_done_callback(self._worker_ended)

    async def _leave_stopped_jobs_stopped(self) -> None:
        """Clear the `next_try_at` of every stopped job, and queue the line saying the queue is
        still stopped, in one save; the line is delivered with the rest at start-up."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT c.*, s.id AS job_id FROM queued_change_steps s "
                "JOIN queued_changes c ON c.id = s.change_id "
                "WHERE s.done_at IS NULL AND s.failing_since IS NOT NULL "
                "AND c.state IN ('QUEUED', 'RUNNING') ORDER BY s.id"
            )
            stopped = list(await cursor.fetchall())
            jobs = {
                change["job_id"]: await self._read_steps(db, change["id"]) for change in stopped
            }
        lines: list[tuple[int, str]] = []
        for change in stopped:
            row = next(row for row in jobs[change["job_id"]] if row["id"] == change["job_id"])
            name = await self._name_job(
                change, row, self._step_context(change, jobs[change["job_id"]], row)
            )
            lines.append((change["job_id"], restart_line(change["job_id"], name)))
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
        )

    def forget_held(self) -> None:
        """Drop every interaction held to update, for a pack or a factory reset, which delete the
        changes they were held for."""
        self._held.clear()

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
        """How long until the earliest waiting step is due, or None where none waits."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT MIN(next_try_at) AS due FROM queued_change_steps "
                "WHERE done_at IS NULL AND next_try_at IS NOT NULL"
            )
            row = await cursor.fetchone()
        if row is None or row["due"] is None:
            return None
        return max((datetime.fromisoformat(row["due"]) - self._clock()).total_seconds(), 0.05)

    async def run_until_idle(self, *, steps: int | None = None) -> None:
        """Carry out what can run now, in order: until nothing can, or *steps* steps are done."""
        done = 0
        async with self._working:
            while True:
                progress = await self._advance()
                if progress is None:
                    return
                if progress:
                    done += 1
                    if steps is not None and done >= steps:
                        return

    async def _advance(self) -> bool | None:
        """Do the next unit of work: start a change, run a job, or finish a change.

        Returns True where a job was done, False for other progress, and None where nothing
        can run now.
        """
        async with self._asking, get_connection(self._db_path) as db:
            change = await self._choose(db)
            if change is None:
                return None
            step_rows = await self._read_steps(db, change["id"])

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
                )
            )
        except Exception as error:  # noqa: BLE001 — a check that raises stops the queue
            log.log(self._failure_level(pending), "the check of %s raised (change %s)",
                    change["what"], change["id"], exc_info=error)
            await self._stop(change, pending, error)
            return False
        if verdict.kind is VerdictKind.GO:
            async with get_connection(self._db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    "UPDATE queued_changes SET state = 'RUNNING' WHERE id = ?", (change["id"],)
                )
                await db.commit()
            return False

        if change["origin"] == ChangeOrigin.MEMBER.value:
            await self._refuse(change, verdict)
        elif verdict.kind is VerdictKind.NOT_DUE:
            await self._drop(change, verdict.reason)
        else:
            await self._stop(change, pending, ChangeRefused(verdict.reason or verdict.reply))
        return False

    async def _refuse(self, change: aiosqlite.Row, verdict: Verdict) -> None:
        """Refuse a member's change that fails its check as it starts: the acknowledgement is
        updated with the refusal's reply, and the line is saved with the mark."""
        reply = _refusal_text(verdict)
        named = self._named(change)
        ids = await self._end(
            change,
            ChangeState.REFUSED,
            refusal_line(
                named, change["what"], reply_reason(reply), detail=verdict.reason or None
            ),
        )
        await self._router.deliver_queued(ids, interaction=self._answerable(change))
        await self._update_reply(change, reply)
        self._release(change)

    async def _drop(self, change: aiosqlite.Row, reason: str) -> None:
        """Drop a bot change that is no longer due: the host's log alone says so."""
        await self._end(change, ChangeState.DROPPED)
        log.info("%s is no longer due, so it was dropped: %s", change["what"], reason)
        self._release(change)

    async def _end(
        self, change: aiosqlite.Row, state: ChangeState, line: str | None = None
    ) -> list[int]:
        """Mark the change *state*, with *line*, where given, saved on the retry queue in the
        same save. Returns the line's id, for delivery once saved. A change removed under the
        worker is left, and its line unwritten."""
        ids: list[int] = []
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "UPDATE queued_changes SET state = ? WHERE id = ?", (state.value, change["id"])
                )
                if cursor.rowcount == 1 and line is not None:
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
        try:
            if step.still_due is not None and not await step.still_due(ctx):
                return await self._complete(change, row, StepResult(result={"dropped": True}))
            if step.kind is StepKind.SAVE:
                async with get_connection(self._db_path) as db:
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        result = await step.run(db, ctx)
                        saved = await self._save_result(db, change, row, result)
                        if saved is None:
                            await db.rollback()
                        else:
                            await db.commit()
                    except BaseException:
                        await db.rollback()
                        raise
                return await self._delivered(change, saved)
            return await self._complete(change, row, await step.run(ctx))
        except Exception as error:  # noqa: BLE001 — the failure path for changes; see `_stop`
            log.log(self._failure_level(row), "job %s (%r) of %s raised (change %s)", row["id"],
                    row["name"], change["what"], change["id"], exc_info=error)
            return await self._step_failed(change, step, ctx, row, error)

    async def _complete(
        self, change: aiosqlite.Row, row: aiosqlite.Row, result: StepResult
    ) -> bool:
        """Save the mark of a step whose work needed no connection, with *result*'s audits and
        lines, then deliver the lines."""
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                saved = await self._save_result(db, change, row, result)
                if saved is None:
                    await db.rollback()
                else:
                    await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return await self._delivered(change, saved)

    async def _delivered(self, change: aiosqlite.Row, saved: list[int] | None) -> bool:
        """Deliver the lines a step's save wrote; False where the save was not made."""
        if saved is None:
            return False
        await self._router.deliver_queued(saved, interaction=self._answerable(change))
        return True

    async def _step_failed(
        self,
        change: aiosqlite.Row,
        step: Step,
        ctx: StepContext,
        row: aiosqlite.Row,
        error: Exception,
    ) -> bool:
        """Deal with the job *row* raising *error*, its save already rolled back.

        `NotFound` completes a `DELETE` job (the message is already gone, and no line says so)
        and an `EDIT` job (with its `gone_line`). Every other failure, from Discord or from the
        bot, stops the queue at the job.
        """
        if isinstance(error, discord.NotFound) and step.kind in (StepKind.DELETE, StepKind.EDIT):
            lines: tuple[str, ...] = ()
            if step.kind is StepKind.EDIT and step.gone_line is not None:
                gone = step.gone_line if isinstance(step.gone_line, str) else step.gone_line(ctx)
                lines = (gone,)
            return await self._complete(
                change, row, StepResult(result={"gone": True}, lines=lines)
            )
        job = await self._name_job(change, row, ctx)
        await self._stop(change, row, error, job=job)
        return False

    @staticmethod
    def _failure_level(row: aiosqlite.Row | None) -> int:
        """How loud the host's log is about a failure: an error the first time a job fails, a
        warning at each try after."""
        return logging.ERROR if row is None or row["failing_since"] is None else logging.WARNING

    @staticmethod
    def _fault_kind(error: BaseException) -> str:
        """The kind of fault *error* is, as a stop line names it: the exception's type, or for a
        bot change its check refused, the check's reason."""
        return str(error) if isinstance(error, ChangeRefused) else type(error).__name__

    async def _stop(
        self,
        change: aiosqlite.Row,
        row: aiosqlite.Row | None,
        error: BaseException,
        *,
        job: str | None = None,
    ) -> None:
        """Stop the queue at the job *row* of *change*, which failed with *error*.

        A job that fails stops the queue until it is cleared, and every kind of failure does.
        The first failure is saved with its line (`log_lines.stop_line`) in one save, and the
        member's acknowledgement is told, where it can still be updated. The caller has put the
        traceback in the host's log, as the catch-all it is. The job is tried again on the schedule, `RETRY_AFTER`, counted from that first
        failure; a try that fails moves it to the next mark and writes no line, but for the
        last, which writes one saying the bot has stopped trying on its own, and leaves no try.
        A partial result a failure carries is kept on the job. *row* is the job, or for a check
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
        partial = error.result if isinstance(error, StepFailedOnDiscord) else None
        named = job if job is not None else what
        line = None
        if first:
            line = stop_line(row["id"], named, what, self._named(change), kind)
        elif next_try is None:
            line = hour_line(row["id"], named)
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
        await self._router.deliver_queued(ids, interaction=self._answerable(change))
        if first:
            await self._update_reply(
                change,
                f"❌ {what[:1].upper() + what[1:]} is stopped at job #{row['id']} and will be "
                f"tried again.",
            )

    async def _save_result(
        self, db: aiosqlite.Connection, change: aiosqlite.Row, row: aiosqlite.Row,
        result: StepResult,
    ) -> list[int] | None:
        """Write the step's mark, audits and lines on *db*, without committing.

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
        for audit in result.audits:
            await self._audit(db, change, audit)
        ids: list[int] = []
        for line in result.lines:
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
