"""The types a change is made of: a change type, its steps, and what each reads as it runs.

`ChangeType` says what a kind of change does, how it is checked and keyed and what it tells the
member; `Step` is one of the jobs it is carried out in; the contexts are what a check, a step, an
outcome and the two hooks of a refusal and a discard are handed. They hold no state and read none of
`ChangeQueue`'s: the queue (`change_queue`) is built from them, and a change type of any module
imports them from here. The engineering decisions that govern them are below, each pinned by a test
in `tests/core/test_change_queue.py`.

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

**A step is one of four kinds** (`StepKind`). A `SAVE` step is handed the open save and writes only
on it, never committing. The other kinds do Discord or legacy work with no connection open and are
marked in a save after it. Nothing is awaited inside a save but the connection.

**A job that ends on a fault does not stop it** (`Step.on_fault`, `ChangeType.on_check_fault`). A
settings change, or `/bot init`'s claim, is one atomic save, and the work after it that the setting
does not need (checking it against the templates, arming or removing a timed job) is work nothing
later depends on: a fault leaves nothing half-done, nothing behind it waits on it, and the member
can simply ask again. Where a step sets `on_fault`, `_step_failed` marks the job done with the
result `on_fault` returns, in a save of its own, its lines written and delivered, and the change
carries on to its next step or finishes, its outcome reading the failed step's result; there is no
stop notice, no Retry or Discard and no try schedule, and the traceback still goes to the host's
log. A save that faults has been rolled back whole before that, so nothing but the mark and
`on_fault`'s lines is written. *Rejected:* a step that catches its own fault, which would leave its
partial writes on the save's connection and could not catch what fails after it returns, the queue's
own audit and line writes and the commit. A check that raises as a **member's** change starts, where
its type sets `on_check_fault`, ends the change as REFUSED in one save with the failure line
(`failure_line`, a `❌ … failed` line, never a refusal's) and the acknowledgement updated with the
reply the hook returns, and the lane goes on. A bot's change whose check raises still stops its
lane, and every other step and type keeps the rule above. A step that ends on a fault cannot be
`undiscardable`, since it never stops a lane to be discarded: `register` refuses one that is both.
Pinned by the tests under "A job that ends on a fault".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.models.change import (
    ChangeOrigin,
    FollowOn,
    LEAGUE_LANE,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot


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
    last try sent before it posts again. *reply_updatable* is whether the change's acknowledgement
    can still be updated, which is where it is held and under `UPDATABLE_FOR` since it was
    acknowledged: False for a bot's change, after a restart and fourteen minutes on. A job that
    tells a channel what the member can no longer be told reads it, never a flag of its own.
    """

    step_name: str = ""
    step_payload: dict[str, Any] = field(default_factory=dict)
    tries: int = 0
    kept: dict[str, Any] | None = None
    reply_updatable: bool = False


@dataclass(frozen=True)
class Step:
    """A step a change type can run.

    A `SAVE` step's `run(db, ctx)` writes only on *db* and never commits; any other step's
    `run(ctx)` does its work with no connection open. `still_due(ctx)` is asked before the step
    runs, and a step no longer due is marked done as dropped; it is asked again, with the queue's
    write lock held, before the save of a `SAVE` step or of a step's record. `describe(ctx)` names the job in the
    lines that say it stopped the queue, was retried or discarded ("refreshing the hub panel"); by
    default the job is named by the change's own `what`. `gone_line`, a line or what forms one, is
    what an `EDIT` step writes where its message is gone. `record(db, ctx, result)`, on a step that
    is not a `SAVE`, is awaited in the save that marks the step done, before the mark, and writes on
    *db* without committing: a post's message id is saved with the post's mark. It is run, too,
    where a `DELETE` or `EDIT` completes because its message is gone, and not for a job found no
    longer due. A record that raises stops the queue at the job, and *result* is kept on it.

    *undiscardable*, where set, marks a job that cannot be discarded because dropping it could stop
    a season from being started or completed, or leave the league no way to put it right (the
    arming of a round's timed work, a cancellation's save once its timed work is removed). It gives
    what would follow, in the job's own terms ("round 3 in **Pro** would never run"), which
    Discard's refusal quotes. A reader rather than a flag, so the refusal can name the round or
    the season; every other step is discardable. The mark never changes for a step, so a press
    reads it before taking the queue's lock. Every such step must be one a Retry can always
    clear: where its work is no longer owed, its *still_due* drops it or it completes doing
    nothing.

    *on_fault*, where set, makes a fault in the step end the job instead of stopping the lane: it
    is handed the context and the error and returns the result the job is marked done with (see the
    module). It is for a step whose fault leaves nothing half-done and nothing waiting on it, so a
    step cannot carry both it and *undiscardable*.

    *not_done*, where set, is the step's own words for what a league admin's Discard of it leaves
    undone, given the discarded job, or None where that job leaves nothing to say: the outcome of
    a change that carries the step reads it instead of naming the step.
    """

    name: str
    kind: StepKind
    run: Callable[..., Awaitable[StepResult]]
    still_due: Callable[[StepContext], Awaitable[bool]] | None = None
    describe: Callable[[StepContext], Awaitable[str]] | None = None
    gone_line: Callable[[StepContext], str] | str | None = None
    record: Callable[[aiosqlite.Connection, StepContext, StepResult], Awaitable[None]] | None = None
    undiscardable: Callable[[StepContext], Awaitable[str]] | None = None
    on_fault: Callable[[StepContext, BaseException], StepResult] | None = None
    not_done: Callable[[StepView], str | None] | None = None


@dataclass(frozen=True)
class RefusedContext:
    """What a change type's `on_refused` reads: a member's change its check refused as it came up
    to run. *reply* is the refusal as the member is told it and *reason* the verdict's own;
    *reply_updatable* is whether the member can still be told it (`StepContext.reply_updatable`)."""

    payload: dict[str, Any]
    reply: str
    reason: str
    actor_id: int | None
    what: str
    reply_updatable: bool


@dataclass(frozen=True)
class DiscardedContext:
    """What a change type's `on_discarded` reads: a member's change a league admin discarded at
    the check it was stopped at, before any job of it started. *reply_updatable* is whether the
    member can still be told (`StepContext.reply_updatable`)."""

    payload: dict[str, Any]
    actor_id: int | None
    what: str
    reply_updatable: bool


def league_lane(_payload: dict[str, Any]) -> str:
    """The lane of a change type that names none: the league's."""
    return LEAGUE_LANE


@dataclass(frozen=True)
class ChangeType:
    """A kind of change: what it does, how it is checked and keyed, and what it tells the member.

    *opening* are the steps saved when the change is asked for; a step may plan more. *key* is what
    makes two requests the same. *doing* says what the change does ("Turning Results & Standings
    off"), from which the acknowledgement and the refusal of a repeat are formed. A *repeatable*
    change is never refused as a repeat.

    *lane* says, from the payload, the lane the change runs on: `LEAGUE_LANE` (the default, the
    queue as a league knows it), or a conversation's, `"channel:<id>"`, keyed by the channel it is
    held in. The lane is fixed by what asked for the change, so the same payload always gives the
    same lane; a follow-on a step asks takes its own kind's lane, not its asker's.

    *overtakes_a_stop*, set on a kind marked `Setting.repairs_the_bot` (through `setting_change`,
    which says which settings are), lets a queued change of this kind run ahead of its lane's own
    head while that head is stopped on a failure: the stopped job stays stopped and everything
    else on the lane still waits behind it, but a repair asked meanwhile need not. See `_heads`
    and "A job that fails stops its lane".

    *conversation_open*, on a change type of a conversation's lane, says from the payload whether
    that conversation still stands: a job of the change whose conversation has ended is found no
    longer due and dropped. A turn needs the conversation still running; a closing change the
    league asks back needs only its channel to stand. It reads on the connection it is handed.
    None where the change has no conversation to end.

    *writes*, where declared, on any lane, are the tables its kind's saves may write, the queue's
    own always besides (`QUEUE_TABLES`): a save writing another table raises and stops the lane at
    the job, and an empty set allows the queue's own tables alone. None restricts nothing; a change
    type of a conversation's lane declares them.

    *turn_channel*, on a change that answers a message typed into a channel, says from the payload
    which channel: the channel then waits (`typed_turns.turn_waiting`) while the change is in hand,
    and a message typed there meanwhile is turned away. A change that is not a turn declares none.

    *plan*, on a kind whose first steps depend on what it was asked (a check-in answer is one
    driver's press or a maintainer's paste of many), says from the payload which steps to open
    with, in place of *opening*; `opening_steps` is the one place either is read.

    *finished_lines* says from what became of a change the lines the log channel carries once it
    is done: what a league admin discarded and so left undone, named for doing by hand, on a change
    no member waits on (a bot's); or the line of a job whose own was discarded with it. They are
    written in the save that marks the change done.

    *tell_origin* says from what became of a change asked for by another's step (a follow-on) a
    note for the reply of the member who made that other change, where it is still held to update:
    a prompt the league discarded, which the member's reply has no other way of hearing of. None,
    or a note of None, tells nothing; the note is added beneath what the reply already says, or
    stands in the log alone once the reply can no longer be updated.

    *on_check_fault*, where set, makes a check that raises as a **member's** change starts end the
    change instead of stopping the lane: it is handed the change's `what`, its payload and the error, and returns
    the reply the acknowledgement is updated with, and the queue records the failure line. It
    applies to a member's change only: a bot's change whose check raises still stops its lane.

    *fill*, where set, lets a change type fill its own payload with a value read from the state
    standing at the ask (`signup_off_change`'s window), before its check is run: `ChangeQueue.check`
    runs it on every ask and every probe made before the change is saved, never as the change
    starts, so the value saved with the request is the one that stood then, and the check made
    again as the change starts can judge whether the two still agree. None fills nothing.
    """

    kind: str
    opening: tuple[PlannedStep, ...]
    steps: Mapping[str, Step]
    check: Callable[[CheckContext], Awaitable[Verdict]]
    key: Callable[[dict[str, Any]], str]
    doing: Callable[[dict[str, Any]], str]
    outcome: Callable[[OutcomeContext], str]
    repeatable: bool = False
    fill: Callable[[CheckContext], Awaitable[None]] | None = None
    on_refused: Callable[[RefusedContext], tuple[FollowOn, ...]] | None = None
    on_discarded: Callable[[DiscardedContext], tuple[FollowOn, ...]] | None = None
    lane: Callable[[dict[str, Any]], str] = league_lane
    overtakes_a_stop: bool = False
    conversation_open: Callable[[aiosqlite.Connection, dict[str, Any]], Awaitable[bool]] | None = None
    writes: tuple[str, ...] | None = None
    turn_channel: Callable[[dict[str, Any]], int | None] | None = None
    plan: Callable[[dict[str, Any]], tuple[PlannedStep, ...]] | None = None
    finished_lines: Callable[[OutcomeContext], tuple[str, ...]] | None = None
    tell_origin: Callable[[OutcomeContext], str | None] | None = None
    on_check_fault: Callable[[str, dict[str, Any], BaseException], str] | None = None

    def opening_steps(self, payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        """The steps saved when the change is asked for with *payload*: *opening*, or where the
        kind opens by what it was asked (*plan*), what *plan* says of the payload."""
        return self.opening if self.plan is None else self.plan(payload)


class ChangeRefused(Exception):
    """A bot change's check refused when the change started: the bot asked for what it may not
    do, which is a fault in the bot and is handled as one."""


@dataclass(frozen=True)
class Ahead:
    """A change to be saved with another and carried out before it (`ChangeQueue.ask_after`):
    its kind, its payload, what it is called in the log and its origin (the bot's, as no member
    asked for it)."""

    kind: str
    payload: dict[str, Any]
    what: str
    origin: ChangeOrigin = ChangeOrigin.BOT
