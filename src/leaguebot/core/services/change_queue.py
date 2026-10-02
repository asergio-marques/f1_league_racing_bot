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
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
    VerdictKind,
)
from leaguebot.core.services.audit_service import record_change_on
from leaguebot.core.utils.log_lines import refuse
from leaguebot.core.utils.member_names import member_named
from leaguebot.core.utils.messages import chunk_message

if TYPE_CHECKING:
    from leaguebot.core.services.output_router import OutputRouter
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

#: How long after the acknowledgement it may still be updated: a minute short of the fifteen
#: Discord's token lasts.
UPDATABLE_FOR = timedelta(minutes=14)


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
    is doing, for the report of one that keeps failing. `gone_line` is what an `EDIT` step writes
    where its message is gone. `tried_once` marks a `DELETE` or `ACT` step whose failure on
    Discord is kept in its result for the outcome, rather than retried.
    """

    name: str
    kind: StepKind
    run: Callable[..., Awaitable[StepResult]]
    still_due: Callable[[StepContext], Awaitable[bool]] | None = None
    describe: Callable[[StepContext], Awaitable[str]] | None = None
    gone_line: Callable[[StepContext], str] | None = None
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
        self._wake = asyncio.Event()
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
                await refuse(interaction, verdict.reply, what=refusal_what or what)
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
        self._wake.set()
        return change_id

    # ------------------------------------------------------------------
    # The worker
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the worker, once: a second call does nothing."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._work(), name="change-queue")
        self._task.add_done_callback(self._worker_ended)

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
        while True:
            self._wake.clear()
            await self.run_until_idle()
            await self._wake.wait()

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
            cursor = await db.execute(
                "SELECT * FROM queued_changes WHERE state IN ('RUNNING', 'QUEUED') "
                "ORDER BY state = 'RUNNING' DESC, id LIMIT 1"
            )
            change = await cursor.fetchone()
            if change is None:
                return None
            cursor = await db.execute(
                "SELECT * FROM queued_change_steps WHERE change_id = ? ORDER BY position",
                (change["id"],),
            )
            step_rows = list(await cursor.fetchall())

        change_type = self._types[change["kind"]]
        if change["state"] == ChangeState.QUEUED.value:
            verdict = await change_type.check(
                CheckContext(
                    json.loads(change["payload"]),
                    self._bot,
                    self._db_path,
                    ChangeOrigin(change["origin"]),
                )
            )
            if verdict.kind is not VerdictKind.GO:
                return None
            async with get_connection(self._db_path) as db:
                await db.execute(
                    "UPDATE queued_changes SET state = 'RUNNING' WHERE id = ?", (change["id"],)
                )
                await db.commit()
            return False

        pending = next((row for row in step_rows if row["done_at"] is None), None)
        if pending is None:
            await self._finish(change, step_rows)
            return False
        return await self._run_step(change, change_type, step_rows, pending)

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
            "named": (
                "the bot" if change["actor_id"] is None
                else member_named(change["actor_display"], change["actor_id"])
            ),
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
        """Run the step *row*, saving its mark with its writes, audits and lines."""
        step = change_type.steps[row["name"]]
        ctx = StepContext(
            **self._context_fields(change, step_rows),
            step_name=row["name"],
            step_payload=json.loads(row["payload"]),
            tries=row["tries"],
        )
        saved: list[int] | None
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
        else:
            result = await step.run(ctx)
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
        if saved is None:
            return False
        await self._router.deliver_queued(saved, interaction=self._answerable(change))
        return True

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
        return ids

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
        self._held.pop(change["id"], None)
        self._wake.set()

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
