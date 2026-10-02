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

**The queue forms no standard line of its own.** A refusal is `log_lines.refusal_line`, and a fault
`interaction_errors.failure_line`: each standard line is formed in one place. Every line the queue
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
since the acknowledgement. After a restart, or later, nothing is updated and the log channel alone
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

import aiohttp
import aiosqlite
import discord

from leaguebot.core.db.database import get_connection, inserted_id
from leaguebot.core.models.change import (
    AuditRecord,
    ChangeOrigin,
    ChangeState,
    FollowOn,
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
    VerdictKind,
)
from leaguebot.core.services.audit_service import record_change_on
from leaguebot.core.utils.interaction_errors import failure_line, failure_reply
from leaguebot.core.utils.log_lines import refusal_line, refuse, reply_reason
from leaguebot.core.utils.member_names import member_named
from leaguebot.core.utils.messages import chunk_message

if TYPE_CHECKING:
    from leaguebot.core.services.output_router import OutputRouter
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

#: How long after the acknowledgement it may still be updated: a minute short of the fifteen
#: Discord's token lasts.
UPDATABLE_FOR = timedelta(minutes=14)

#: How long a step waits after its first failure, doubling at each try after, up to the ceiling.
RETRY_FIRST_WAIT = timedelta(seconds=30)
RETRY_CEILING = timedelta(minutes=15)
#: How long a step fails before one line says so, and how long before it says so again.
REPORT_AFTER = timedelta(hours=1)
REPORT_AGAIN_AFTER = timedelta(hours=24)


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


def _no_places(_payload: dict[str, Any]) -> tuple[str, ...]:
    return ()


@dataclass(frozen=True)
class ChangeType:
    """A kind of change: what it does, how it is checked and keyed, and what it tells the member.

    *opening* are the steps saved when the change is asked for; a step may plan more. *key* is what
    makes two requests the same. *doing* says what the change does ("Turning Results & Standings
    off"), from which the acknowledgement and the refusal of a repeat are formed. *places* are
    where the change posts, as "channel:<id>". A *repeatable* change is never refused as a repeat,
    and one that *overrides_waiting* (a switch-off, a cancel) is never held behind a change
    waiting on a retry.
    """

    kind: str
    opening: tuple[PlannedStep, ...]
    steps: Mapping[str, Step]
    check: Callable[[CheckContext], Awaitable[Verdict]]
    key: Callable[[dict[str, Any]], str]
    doing: Callable[[dict[str, Any]], str]
    outcome: Callable[[OutcomeContext], str]
    fault_outcome: Callable[[OutcomeContext], str]
    places: Callable[[dict[str, Any]], tuple[str, ...]] = _no_places
    repeatable: bool = False
    overrides_waiting: bool = False


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
        self._held: dict[int, discord.Interaction] = {}
        self._signal = asyncio.Event()
        self._working = asyncio.Lock()
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
                    reason=verdict.reason or None,
                )
                return None
            if verdict.kind is VerdictKind.NOT_DUE:
                log.info("%s is no longer due, so it was not asked for: %s", what, verdict.reason)
                return None

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
                    "actor_name, actor_display, what, places) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        kind,
                        key,
                        json.dumps(payload),
                        origin.value,
                        getattr(member, "id", None),
                        None if member is None else str(member),
                        getattr(member, "display_name", None),
                        what,
                        json.dumps(list(change_type.places(payload))),
                    ),
                )
                change_id = inserted_id(cursor)
                for position, planned in enumerate(change_type.opening):
                    await db.execute(
                        "INSERT INTO queued_change_steps (change_id, position, name, payload, "
                        "places) VALUES (?, ?, ?, ?, ?)",
                        (
                            change_id,
                            position,
                            planned.name,
                            json.dumps(planned.payload),
                            json.dumps(list(planned.places)),
                        ),
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
            await interaction.response.send_message(
                f"⏳ {change_type.doing(payload)}. This message will be updated when it is "
                f"done; if it takes longer, the log channel will say so.",
                ephemeral=True,
            )
            async with get_connection(self._db_path) as db:
                await db.execute(
                    "UPDATE queued_changes SET acknowledged_at = ? WHERE id = ?",
                    (self._clock().isoformat(), change_id),
                )
                await db.commit()
            self._held[change_id] = interaction
        self._signal.set()
        return change_id

    # ------------------------------------------------------------------
    # The worker
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the worker, once: a second call does nothing.

        Every step waiting on a retry is made due now, so that what the bot's stop interrupted is
        tried again at once. Every log line saved and never tried, which the stop came between
        the save and the delivery of, is delivered, with no interaction to tell if it fails.
        """
        if self._task is not None and not self._task.done():
            return
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE queued_change_steps SET next_try_at = ? "
                "WHERE done_at IS NULL AND next_try_at IS NOT NULL",
                (self._clock().isoformat(),),
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT id FROM pending_messages WHERE failure_reason = '' ORDER BY id"
            )
            never_tried = [row["id"] for row in await cursor.fetchall()]
        await self._router.deliver_queued(never_tried)
        self._task = asyncio.create_task(self._work(), name="change-queue")
        self._task.add_done_callback(self._worker_ended)

    async def wake(self, place: str | None = None) -> None:
        """Try a waiting step at once: those holding *place*, or every waiting step where none
        is given. Called by a command that repairs what a waiting step lacks."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT s.change_id, s.position, s.places, c.places AS change_places "
                "FROM queued_change_steps s JOIN queued_changes c ON c.id = s.change_id "
                "WHERE s.done_at IS NULL AND s.next_try_at IS NOT NULL"
            )
            for step in await cursor.fetchall():
                held = set(json.loads(step["places"])) | set(json.loads(step["change_places"]))
                if place is None or place in held:
                    await db.execute(
                        "UPDATE queued_change_steps SET next_try_at = ? "
                        "WHERE change_id = ? AND position = ?",
                        (self._clock().isoformat(), step["change_id"], step["position"]),
                    )
            await db.commit()
        self._signal.set()

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
            await self.run_until_idle()
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
        """Do the next unit of work: start a change, run a step, or finish a change.

        Returns True where a step was done, False for other progress, and None where nothing
        can run now.
        """
        async with get_connection(self._db_path) as db:
            change = await self._choose(db)
            if change is None:
                return None
            step_rows = await self._read_steps(db, change["id"])

        change_type = self._types.get(change["kind"])
        if change_type is None:
            await self._fault(
                change, None,
                KeyError(f"no change type {change['kind']!r} is registered"),
            )
            return False
        if change["state"] == ChangeState.QUEUED.value or self._awaiting_repair(change, step_rows):
            return await self._start(change, change_type, step_rows)

        pending = next((row for row in step_rows if row["done_at"] is None), None)
        if pending is None:
            await self._finish(change, step_rows)
            return False
        return await self._run_step(change, change_type, step_rows, pending)

    async def _choose(self, db: aiosqlite.Connection) -> aiosqlite.Row | None:
        """The change to work on next.

        A RUNNING change comes first, as one resumed after a stop. Next comes the lowest id among
        the WAITING changes whose step is due and the QUEUED changes not blocked. A QUEUED change
        is blocked while its places meet those of a not-done step of a WAITING change with a lower
        id, so that a post to the same place never overtakes one waiting on a retry; a change
        whose type `overrides_waiting` (a switch-off, a cancel) is never blocked.
        """
        cursor = await db.execute("SELECT * FROM queued_changes WHERE state = 'RUNNING' LIMIT 1")
        running = await cursor.fetchone()
        if running is not None:
            return running
        cursor = await db.execute(
            "SELECT c.*, (SELECT MIN(s.next_try_at) FROM queued_change_steps s "
            "WHERE s.change_id = c.id AND s.done_at IS NULL AND s.next_try_at IS NOT NULL) "
            "AS due_at FROM queued_changes c WHERE c.state IN ('QUEUED', 'WAITING') ORDER BY c.id"
        )
        candidates = list(await cursor.fetchall())
        cursor = await db.execute(
            "SELECT s.change_id, s.places FROM queued_change_steps s JOIN queued_changes c "
            "ON c.id = s.change_id WHERE c.state = 'WAITING' AND s.done_at IS NULL"
        )
        held: dict[int, set[str]] = {}
        for step in await cursor.fetchall():
            held.setdefault(step["change_id"], set()).update(json.loads(step["places"]))
        now = self._clock()
        for row in candidates:
            if row["state"] == ChangeState.WAITING.value:
                if row["due_at"] is not None and datetime.fromisoformat(row["due_at"]) <= now:
                    return row
                continue
            change_type = self._types.get(row["kind"])
            if change_type is not None and change_type.overrides_waiting:
                return row
            behind = set().union(*(places for waiting, places in held.items() if waiting < row["id"]))
            if not behind.intersection(json.loads(row["places"])):
                return row
        return None

    @staticmethod
    async def _read_steps(db: aiosqlite.Connection, change_id: int) -> list[aiosqlite.Row]:
        cursor = await db.execute(
            "SELECT * FROM queued_change_steps WHERE change_id = ? ORDER BY position",
            (change_id,),
        )
        return list(await cursor.fetchall())

    @staticmethod
    def _awaiting_repair(change: aiosqlite.Row, step_rows: list[aiosqlite.Row]) -> bool:
        """Whether the bot's change, WAITING with no step done, is to have its check run again:
        it was found lacking something a league can repair before it started."""
        return (
            change["state"] == ChangeState.WAITING.value
            and change["origin"] == ChangeOrigin.BOT.value
            and all(row["done_at"] is None for row in step_rows)
        )

    # ------------------------------------------------------------------
    # Starting a change: the second check
    # ------------------------------------------------------------------

    async def _start(
        self, change: aiosqlite.Row, change_type: ChangeType, step_rows: list[aiosqlite.Row]
    ) -> bool:
        """Run the second check of a change not yet started, and act on what it finds."""
        verdict = await change_type.check(
            CheckContext(
                json.loads(change["payload"]),
                self._bot,
                self._db_path,
                ChangeOrigin(change["origin"]),
            )
        )
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
        elif verdict.kind is VerdictKind.REPAIRABLE:
            await self._wait_on_repair(change, step_rows, verdict.reason)
        else:
            await self._fault(
                change, change_type, ChangeRefused(verdict.reason or verdict.reply)
            )
        return False

    async def _refuse(self, change: aiosqlite.Row, verdict: Verdict) -> None:
        """Refuse a member's change that fails its check as it starts: the acknowledgement is
        updated with the refusal's reply, and the line is saved with the mark."""
        reply = _refusal_text(verdict)
        named = self._named(change)
        ids = await self._end(
            change,
            ChangeState.REFUSED,
            refusal_line(named, change["what"], verdict.reason or reply_reason(reply)),
        )
        await self._router.deliver_queued(ids, interaction=self._answerable(change))
        await self._update_reply(change, reply)
        self._release(change)

    async def _drop(self, change: aiosqlite.Row, reason: str) -> None:
        """Drop a bot change that is no longer due: the host's log alone says so."""
        await self._end(change, ChangeState.DROPPED)
        log.info("%s is no longer due, so it was dropped: %s", change["what"], reason)
        self._release(change)

    async def _wait_on_repair(
        self, change: aiosqlite.Row, step_rows: list[aiosqlite.Row], reason: str
    ) -> None:
        """Make the bot's change wait on the retry waits for a league to repair what it lacks.

        One line says what is missing, when it first waits; a later check finding it still
        missing makes it wait longer and says nothing more.
        """
        pending = next((row for row in step_rows if row["done_at"] is None), None)
        first = change["state"] == ChangeState.QUEUED.value
        ids: list[int] = []
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    "UPDATE queued_changes SET state = 'WAITING' WHERE id = ?", (change["id"],)
                )
                if pending is not None:
                    await self._wait_step(db, pending, reason)
                if first:
                    what = change["what"]
                    line_id = await self._router.queue_log_on(
                        db,
                        f"⚠️ {what[:1].upper() + what[1:]} for {self._named(change)} is "
                        f"waiting: {reason}. It goes ahead once that is repaired.",
                    )
                    if line_id is not None:
                        ids.append(line_id)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        await self._router.deliver_queued(ids)

    async def _wait_step(
        self, db: aiosqlite.Connection, row: aiosqlite.Row, reason: str, *, reported: bool = False
    ) -> bool:
        """Make the step *row* wait for its next try, on the retry waits: 30 seconds, doubling,
        to a ceiling of 15 minutes, noting that its failure is *reported* where it has been.

        Writes on *db*, committing nothing. Returns False where the step is gone: its change
        was removed under the worker.
        """
        tries = row["tries"] + 1
        now = self._clock()
        wait = min(RETRY_FIRST_WAIT * 2 ** min(tries - 1, 16), RETRY_CEILING)
        cursor = await db.execute(
            "UPDATE queued_change_steps SET tries = ?, next_try_at = ?, "
            "failing_since = COALESCE(failing_since, ?), last_failure = ?, reported_at = ? "
            "WHERE change_id = ? AND position = ?",
            (
                tries,
                (now + wait).isoformat(),
                now.isoformat(),
                reason,
                now.isoformat() if reported else row["reported_at"],
                row["change_id"],
                row["position"],
            ),
        )
        return cursor.rowcount == 1

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
        whole: a failure Discord caused is retried, or kept where the step is tried once, and any
        other makes the change fault.
        """
        step = change_type.steps.get(row["name"])
        if step is None:
            await self._fault(
                change, change_type,
                KeyError(f"change type {change['kind']!r} has no step {row['name']!r}"),
            )
            return False
        ctx = StepContext(
            **self._context_fields(change, step_rows),
            step_name=row["name"],
            step_payload=json.loads(row["payload"]),
            tries=row["tries"],
        )
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
        except Exception as error:  # noqa: BLE001 — the failure path for changes; see `_fault`
            log.warning("step %r of %s (change %s) raised", row["name"], change["what"],
                        change["id"], exc_info=error)
            try:
                return await self._step_failed(change, change_type, step, ctx, row, error)
            except Exception as again:  # noqa: BLE001
                log.warning("could not deal with the failure of step %r of %s (change %s)",
                            row["name"], change["what"], change["id"], exc_info=again)
                await self._fault(change, change_type, again)
                return False

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

    @staticmethod
    def _failed_on_discord(error: BaseException) -> str | None:
        """Why *error* failed the step, where Discord caused it, else None."""
        if isinstance(
            error,
            (
                StepFailedOnDiscord,
                GuildUnavailable,
                discord.HTTPException,
                aiohttp.ClientError,
                asyncio.TimeoutError,
            ),
        ):
            return str(error) or type(error).__name__
        return None

    async def _step_failed(
        self,
        change: aiosqlite.Row,
        change_type: ChangeType,
        step: Step,
        ctx: StepContext,
        row: aiosqlite.Row,
        error: Exception,
    ) -> bool:
        """Deal with the step *row* raising *error*, its save already rolled back.

        `NotFound` completes a `DELETE` step (the message is already gone, and no line says so)
        and an `EDIT` step (with its `gone_line`). Any other failure Discord caused completes a
        step tried once, with the failure kept in its result (merged with what the step raised it
        with) for the change's outcome to read: it is not retried, never waits, and so holds no
        place. A step not tried once waits on a retry. Anything else is a fault in the bot.
        """
        if isinstance(error, discord.NotFound) and step.kind in (StepKind.DELETE, StepKind.EDIT):
            lines: tuple[str, ...] = ()
            if step.kind is StepKind.EDIT and step.gone_line is not None:
                gone = step.gone_line if isinstance(step.gone_line, str) else step.gone_line(ctx)
                lines = (gone,)
            return await self._complete(
                change, row, StepResult(result={"gone": True}, lines=lines)
            )
        reason = self._failed_on_discord(error)
        if reason is None:
            await self._fault(change, change_type, error)
            return False
        if step.tried_once:
            kept = error.result if isinstance(error, StepFailedOnDiscord) and error.result else {}
            return await self._complete(
                change, row, StepResult(result={"failed": reason, **kept})
            )
        await self._wait(change, step, ctx, row, reason)
        return False

    async def _wait(
        self, change: aiosqlite.Row, step: Step, ctx: StepContext, row: aiosqlite.Row, reason: str
    ) -> None:
        """Make the step *row* wait on a retry, and the change with it.

        A step failing for about an hour is reported by one line, in the same save, and again
        after a further day; it is still retried.
        """
        now = self._clock()
        since = datetime.fromisoformat(row["failing_since"]) if row["failing_since"] else now
        reported = datetime.fromisoformat(row["reported_at"]) if row["reported_at"] else None
        report = now - since >= REPORT_AFTER and (
            reported is None or now - reported >= REPORT_AGAIN_AFTER
        )
        line: str | None = None
        if report:
            doing = "it"
            if step.describe is not None:
                try:
                    doing = await step.describe(ctx)
                except Exception:  # noqa: BLE001 — the report goes ahead without the name
                    log.error("could not describe step %r of %s", step.name, change["what"],
                              exc_info=True)
            what = change["what"]
            line = (
                f"⚠️ {what[:1].upper() + what[1:]} for {self._named(change)} is still at work: "
                f"{doing} has failed for over an hour ({reason}). The bot keeps trying."
            )
        ids: list[int] = []
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                if await self._wait_step(db, row, reason, reported=report):
                    await db.execute(
                        "UPDATE queued_changes SET state = 'WAITING' WHERE id = ?", (change["id"],)
                    )
                    if line is not None:
                        line_id = await self._router.queue_log_on(db, line)
                        if line_id is not None:
                            ids.append(line_id)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        await self._router.deliver_queued(ids, interaction=self._answerable(change))

    async def _fault(
        self, change: aiosqlite.Row, change_type: ChangeType | None, error: BaseException
    ) -> None:
        """End the change FAULTED on *error*, telling the host, the log channel and the member.

        This is the failure path for changes, the first place "Errors and failures" allows a
        catch-all, and it keeps the details: the traceback goes to the host's log, the line
        in the log channel names the fault's type, and the acknowledgement is updated with the
        change type's account of what became of the change. What earlier steps saved stays saved.
        """
        what = change["what"]
        log.error("%s failed for %s (change %s)", what, self._named(change), change["id"],
                  exc_info=error)
        async with get_connection(self._db_path) as db:
            step_rows = await self._read_steps(db, change["id"])
        outcome: str | None = None
        if change_type is not None:
            try:
                outcome = change_type.fault_outcome(self._outcome_context(change, step_rows))
            except Exception:  # noqa: BLE001 — the reply falls back to the general outcome
                log.error("could not word what became of %s (change %s)", what, change["id"],
                          exc_info=True)
        ids = await self._end(
            change, ChangeState.FAULTED, failure_line(what, self._named(change), error)
        )
        await self._router.deliver_queued(ids, interaction=self._answerable(change))
        await self._update_reply(change, failure_reply(what, outcome))
        self._release(change)

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
        await db.execute(
            "UPDATE queued_changes SET state = 'RUNNING' WHERE id = ? AND state = 'WAITING'",
            (change["id"],),
        )
        for audit in result.audits:
            await self._audit(db, change, audit)
        ids: list[int] = []
        for line in result.lines:
            line_id = await self._router.queue_log_on(db, line)
            if line_id is not None:
                ids.append(line_id)
        await self._plan_steps(db, change, row, result.then)
        await self._add_places(db, change, result.places)
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
                "INSERT INTO queued_change_steps (change_id, position, name, payload, places) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    change["id"],
                    row["position"] + offset,
                    step.name,
                    json.dumps(step.payload),
                    json.dumps(list(step.places)),
                ),
            )

    @staticmethod
    async def _add_places(
        db: aiosqlite.Connection, change: aiosqlite.Row, places: tuple[str, ...]
    ) -> None:
        """Merge *places* into the places the change holds."""
        if not places:
            return
        cursor = await db.execute("SELECT places FROM queued_changes WHERE id = ?", (change["id"],))
        current = await cursor.fetchone()
        held = json.loads(current["places"]) if current is not None else []
        merged = held + [place for place in places if place not in held]
        await db.execute(
            "UPDATE queued_changes SET places = ? WHERE id = ?", (json.dumps(merged), change["id"])
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
            "actor_display, what, places) VALUES (?, ?, ?, 'BOT', ?, ?, ?, ?, ?)",
            (
                follow_on.kind,
                change_type.key(follow_on.payload),
                json.dumps(follow_on.payload),
                change["actor_id"],
                change["actor_name"],
                change["actor_display"],
                follow_on.what,
                json.dumps(list(change_type.places(follow_on.payload))),
            ),
        )
        change_id = inserted_id(cursor)
        for position, planned in enumerate(change_type.opening):
            await db.execute(
                "INSERT INTO queued_change_steps (change_id, position, name, payload, places) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    change_id,
                    position,
                    planned.name,
                    json.dumps(planned.payload),
                    json.dumps(list(planned.places)),
                ),
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
        interaction = self._held.get(change["id"])
        acknowledged = change["acknowledged_at"]
        if interaction is None or acknowledged is None:
            return None
        if self._clock() - datetime.fromisoformat(acknowledged) >= UPDATABLE_FOR:
            return None
        return interaction

    async def _finish(self, change: aiosqlite.Row, step_rows: list[aiosqlite.Row]) -> None:
        """Mark the change DONE and update its acknowledgement with the outcome."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE queued_changes SET state = 'DONE' WHERE id = ?", (change["id"],)
            )
            await db.commit()
        change_type = self._types[change["kind"]]
        text = change_type.outcome(self._outcome_context(change, step_rows))
        await self._update_reply(change, text)
        self._release(change)

    async def _update_reply(self, change: aiosqlite.Row, text: str) -> None:
        """Update the acknowledgement with *text*, in as many parts as it needs.

        Only while the interaction is held and under 14 minutes old. A failed update is logged
        and never fails the change.
        """
        interaction = self._answerable(change)
        if interaction is None:
            return
        try:
            for number, part in enumerate(chunk_message(text)):
                if number == 0:
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
