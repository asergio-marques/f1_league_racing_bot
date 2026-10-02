"""The plain data of the change queue (#439): no Discord and no database.

`docs/design/architecture.md`, "How a change is carried out", designs the queue. This module holds
the types a change type's steps are written in, which is why they live in a model: another module's
step code imports them from core, and core's queue (`core/services/change_queue.py`) reads them.

A change is a row of `queued_changes` and its steps are the rows of `queued_change_steps`;
`ChangeState` and `ChangeOrigin` are the closed lists of the `CHECK` constraints on the first, tied
to them by `tests/core/test_schema_rules.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChangeState(str, Enum):
    """Where a change stands. It runs QUEUED, RUNNING, DONE; WAITING while one of its steps waits
    on a retry; and ends REFUSED, DROPPED, DISCARDED or FAULTED."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    DONE = "DONE"
    REFUSED = "REFUSED"
    DROPPED = "DROPPED"
    DISCARDED = "DISCARDED"
    FAULTED = "FAULTED"


class ChangeOrigin(str, Enum):
    """Who asked: a member, through a command, button or form, or the bot itself."""

    MEMBER = "MEMBER"
    BOT = "BOT"


class StepKind(str, Enum):
    """What a step does, which fixes how the worker runs it and how it treats a failure.

    SAVE: the worker opens the save and hands the step its connection. The step writes on it and
    never commits; its mark, audits and lines go in the same save.
    ACT: Discord or legacy work, run with no connection open and marked in a save after it.
    DELETE, EDIT: Discord work for which `NotFound` completes the step, a message already gone.
    """

    SAVE = "SAVE"
    ACT = "ACT"
    DELETE = "DELETE"
    EDIT = "EDIT"


class VerdictKind(str, Enum):
    GO = "GO"
    REFUSE = "REFUSE"
    NOT_DUE = "NOT_DUE"
    REPAIRABLE = "REPAIRABLE"


@dataclass(frozen=True)
class Verdict:
    """What a change type's check finds, as the queue reads it.

    `go()`: carry on. `refuse(reply)`: a member's change is not allowed, `reply` being what the
    member is told and `reason` an optional line written beneath the refusal's line in the log
    channel. `not_due(reason)`: the bot's change no longer has anything to do. `repairable(reason)`:
    the bot's change lacks something a league can repair, so it waits and says what.
    """

    kind: VerdictKind
    reply: str = ""
    reason: str = ""

    @classmethod
    def go(cls) -> Verdict:
        return cls(VerdictKind.GO)

    @classmethod
    def refuse(cls, reply: str, reason: str = "") -> Verdict:
        return cls(VerdictKind.REFUSE, reply=reply, reason=reason)

    @classmethod
    def not_due(cls, reason: str) -> Verdict:
        return cls(VerdictKind.NOT_DUE, reason=reason)

    @classmethod
    def repairable(cls, reason: str) -> Verdict:
        return cls(VerdictKind.REPAIRABLE, reason=reason)


@dataclass(frozen=True)
class AuditRecord:
    """One audit entry a step writes in its own save; the values are plain data, saved as JSON."""

    change_type: str
    old_value: Any
    new_value: Any
    division_id: int | None = None


@dataclass(frozen=True)
class PlannedStep:
    """A step a change plans, to be saved in a table with the others in its order."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    places: tuple[str, ...] = ()


@dataclass(frozen=True)
class FollowOn:
    """A change of its own that a step asks for in its own save, as the bot's, for the member's
    actor: work that must go ahead whatever becomes of the steps after it."""

    kind: str
    payload: dict[str, Any]
    what: str


@dataclass(frozen=True)
class StepResult:
    """What a step returns for the worker to save with the step's mark.

    `result` is kept with the step; `audits` and `lines` are written in the same save; `then` are
    steps inserted after this one; `follow_ons` are changes asked for; `places` are added to the
    places the change holds.
    """

    result: dict[str, Any] = field(default_factory=dict)
    audits: tuple[AuditRecord, ...] = ()
    lines: tuple[str, ...] = ()
    then: tuple[PlannedStep, ...] = ()
    follow_ons: tuple[FollowOn, ...] = ()
    places: tuple[str, ...] = ()


class StepFailedOnDiscord(Exception):
    """Raised by step code for a failure Discord caused.

    `result` is plain data the step wants kept where the step is tried once, such as the ids of
    what it could not remove.
    """

    def __init__(self, reason: str, *, result: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.result = result


class GuildUnavailable(Exception):
    """The league's server is not in the bot's cache: a failure on Discord's side, as any other."""


def module_off(module: str) -> str:
    """The kind of the change that turns *module* off, so core's cog names it without importing
    the module."""
    return f"module.off:{module}"
