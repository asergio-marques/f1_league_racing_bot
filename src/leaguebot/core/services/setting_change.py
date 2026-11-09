"""One settings change, built from a `Setting`: the value, its audit entry and its line, saved together (bar a step that carries the line).

Every settings command of results, weather, attendance, images and signup declares a `Setting`, and
`setting_change` turns it into a `ChangeType` for the change queue (`change_queue`), so that the
line, the audit entry's being written, the answer for a value already held and the answer for a
fault are decided in one place (each setting's save builds the audit record itself).
`docs/design/architecture.md`, "How a change is carried out", designs the queue; the core
specification's section of that name holds the rules a league sees. This module holds the
engineering decisions, each pinned by a test in `tests/core/test_setting_change.py`.

**"Nothing changed" is judged in the save,** on the connection that writes, never at the ask: a
change waiting ahead may set the value or move it away. A request for a value already held is
acknowledged and then answered "Nothing changed", with a line saying so and no audit entry; it is
never refused.

**The check as the change starts is the last judgement of the state against queued writers.**
Settings are written only on the league lane, one change at a time, and conversation lanes may not
write these tables. No other setting ever runs beside it: an overtaking setting (below) runs ahead
of its lane's stopped head, never beside it, and the queue keeps that overtaking change running to
the end before the head is tried again, so no second writer can land between a check and its save.
The save judges only held or not.

**A setting that repairs the bot's own way in overtakes a stop on the league lane.**
`Setting.repairs_the_bot` is read once, here, to set the change type's `overtakes_a_stop` — never a
list of kinds kept in the queue itself. It marks the log channel, the interaction channel, the
interaction role and the league admin role: the core specification calls these out as a league's
way back in when one is wrong or gone, and a league locked out by a stopped job on the league lane
would have no way to clear it. It marks as well a division's results, standings and verdicts
channels (`results/services/channel_settings.py`): a stop notice for a channel gone tells the league
to set the channel and press Retry, and a setting waiting behind the stop it is meant to clear could
never do it, a stopped results post not being one a league can discard. The queue (`_heads`, `_choose`) lets such a change run ahead of its
lane's stopped head, which stays stopped, while every ordinary change still waits behind it: the
lane still runs one change at a time, never two of its jobs interleaved — a repair simply jumps the
queue behind a stop, runs to its own end, and only then is the stopped head tried again.
*Rejected:* a lane of the four settings' own, beside the league's, which would let two lanes write
the league's configuration at once, each save re-judging the other's.

**The line is formed here and nowhere else:** `{member} | {command} | {outcome}`, the values
beneath it, saved in the same save as the value and its audit entry. A save is handed only the
connection and the payload, never the actor or the clock: the queue stamps the audit entry. No
settings code reads the wall clock.

**A setting never holds up the queue.** Its save, its check as it comes up to run and the steps
after it end on a fault instead of stopping the lane (`Step.on_fault`,
`ChangeType.on_check_fault`): a settings change is one atomic save, so a fault is cured one of two
ways and nothing behind it on the lane ever waits on it. A save that faults has been rolled back
whole, so nothing is saved, the member is told so and to run it again (`NOTHING_SAVED`, or the
setting's own words), and the log channel gets the failure line — the cure is asking again. A step
after the save, which the setting does not need (checking it against the templates, arming or
removing a timed job, granting a role to every driver), leaves the setting saved: asking again with
the same value now saves nothing and repeats no step, so the cure is instead whatever the reply
names — what was not done, or what to do by hand (`After.fault_reply`).
*Rejected:* a bespoke change type per command, which forms the line, the audit and the fault
answer a score of ways; and a step catching its own fault, which could not undo its partial
writes nor catch what fails after it returns (see `change_queue`).

**Recorded twice, in one save, but for a step that carries the line.** The value, its audit entry
and its line share one save, save where a step after it `carries_line` (the arming of the daily
portrait job, and a role's grant to every driver where the one it replaces is gone from the
server): the value and the audit entry share the save, the line is kept in its result, and the
carrying step writes it once it has succeeded, so that a failed arming, or a failed grant,
records no success. A restart between the two reaches the carrying step as the change carries
on. The steps after the save are planned only where the save changed something.

**A setting is confined to the tables it declares** (`Setting.writes`, the queue's authoriser): a
save writing another table raises, is rolled back and ends as any fault in the save.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from leaguebot.core.models.change import (
    AuditRecord,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.interaction_errors import failure_line, failure_reply

#: What a fault in a setting's save, or in its check as it comes up to run, says became of it.
#: `{command}` is the command to run again.
NOTHING_SAVED = "Nothing was saved. Run `{command}` again to retry."

#: The name of the step that saves the setting.
SAVE = "save"

#: The key, in the save's result, of the line kept back for a step that carries it.
_KEPT_LINE = "setting_line"


@dataclass(frozen=True)
class Saved:
    """What a setting's save did on the connection it was handed (never committing).

    *changed* is whether the value moved; *audits* are its audit entries, empty where nothing
    changed; *details* are the values beneath the line; *outcome* is the line's outcome where it
    is neither `Success` nor `Nothing changed`; *result* is what the setting's `reply` reads;
    *then* are the steps of `Setting.after` to run, planned only where *changed*.
    """

    changed: bool
    audits: tuple[AuditRecord, ...] = ()
    details: tuple[str, ...] = ()
    outcome: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    then: tuple[PlannedStep, ...] = ()


#: The key, in a carrying step's own result, of the outcome word it reports in place of
#: "Success" — "Success, with faults" where the step has something Discord refused to report
#: without raising (the hub's permissions): the step still succeeded, so the line is
#: still written, but not as a plain success.
RENAMED_OUTCOME = "renamed_outcome"


@dataclass(frozen=True)
class After:
    """A step after the save that the setting does not need: it may fail without holding the lane.

    *step* is an `ACT` step; *fault_reply* takes the payload and the reply so far and returns the
    reply that says what was not done; *carries_line* makes the success line wait for the step
    and be written by it once it succeeds. A carrying step may rename the outcome word itself
    (`"Success, with faults"`) by putting it under `RENAMED_OUTCOME` in its own result, for a
    fault it reports rather than raises.
    """

    step: Step
    fault_reply: Callable[[dict[str, Any], str], str]
    carries_line: bool = False


@dataclass(frozen=True)
class Setting:
    """One settings command's change, as `setting_change` builds it.

    *kind* names the change type; *command* is the command as the line names it
    ("/weather config phase-1-deadline"); *doing* says what the change does, for the
    acknowledgement; *check* judges a request at the ask and as it starts; *save* writes on the
    connection it is handed and returns what it did; *reply* forms the member's answer from the
    payload and every step's result, by step name; *writes* are the tables the save may write;
    *not_saved* is what a fault's reply says became of the change, or a function of the payload
    that returns it, for a setting whose command is a paste as well as a single value (the reply
    to a check that raises as the change starts is formed from the payload, as the outcome is);
    *after* are the steps that may follow the save, by name. *repairs_the_bot*, set on a setting that points the
    bot at a channel or a role it works through, makes the change type `overtakes_a_stop`, so that it can still be asked for, and run, while the league's lane is
    stopped on something else — see the module docstring.
    """

    kind: str
    command: Callable[[dict[str, Any]], str]
    doing: Callable[[dict[str, Any]], str]
    check: Callable[[CheckContext], Awaitable[Verdict]]
    save: Callable[[aiosqlite.Connection, dict[str, Any]], Awaitable[Saved]]
    reply: Callable[[dict[str, Any], Mapping[str, dict[str, Any]]], str]
    writes: tuple[str, ...]
    not_saved: str | Callable[[dict[str, Any]], str] = NOTHING_SAVED
    after: Mapping[str, After] = field(default_factory=dict)
    repairs_the_bot: bool = False


def _line(named: str, command: str, saved: Saved) -> str:
    """The log line of a save: member, command and outcome, the values beneath."""
    outcome = saved.outcome or ("Success" if saved.changed else "Nothing changed")
    return "\n".join(
        [f"{named} | {command} | {outcome}", *(f"  {detail}" for detail in saved.details)]
    )


def _failed(ctx: StepContext, error: BaseException) -> StepResult:
    """A step ended on a fault: its mark, and the failure line."""
    return StepResult(result={"failed": True}, lines=(failure_line(ctx.what, ctx.named, error),))


def _lowered(text: str) -> str:
    return text[:1].lower() + text[1:]


def _not_saved(setting: Setting, payload: dict[str, Any]) -> str:
    """What a fault in *setting*'s change says became of it, for the change of *payload*."""
    words = setting.not_saved
    return words(payload) if callable(words) else words


def _check_fault_reply(setting: Setting, what: str, payload: dict[str, Any]) -> str:
    """The reply for a check that raised as the change started, formed as the outcome path forms
    the reply to a fault in the save: the setting's own words for its payload, the command filled
    in."""
    return failure_reply(
        what, _not_saved(setting, payload).format(command=setting.command(payload))
    )


def _after_step(after: After) -> Step:
    """*after*'s step as the change runs it: ending on a fault, and writing the line it carries.

    A step's own lines are joined beneath the kept line as one post, word for word, rather than
    posted apart from it: a carrying step may add its own detail (the given/not-given lines of
    a role's grant to every driver) to what the save already has to say, never only stand in
    for it.
    """
    inner = after.step

    async def run(ctx: StepContext) -> StepResult:
        result = await inner.run(ctx)
        if not after.carries_line:
            return result
        kept = next(
            ((view.result or {}).get(_KEPT_LINE) for view in ctx.steps if view.name == SAVE), None
        )
        if kept is None:
            return result
        renamed = result.result.get(RENAMED_OUTCOME)
        if renamed is not None:
            first, _sep, rest = kept.partition("\n")
            before, _sep, _old_outcome = first.rpartition(" | ")
            kept = "\n".join((f"{before} | {renamed}", rest)) if rest else f"{before} | {renamed}"
        return dataclasses.replace(result, lines=("\n".join((kept, *result.lines)),))

    return dataclasses.replace(inner, run=run, on_fault=_failed)


def setting_change(setting: Setting) -> ChangeType:
    """The change type of *setting*: one `SAVE` step, then whatever steps after it its save plans.

    See the module for what the change does with a value already held, a fault and the line.
    """
    carriers = {name for name, after in setting.after.items() if after.carries_line}

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        saved = await setting.save(db, ctx.payload)
        planned = saved.then if saved.changed else ()
        line = _line(ctx.named, setting.command(ctx.payload), saved)
        result: dict[str, Any] = {"changed": saved.changed, **saved.result}
        lines: tuple[str, ...] = (line,)
        if any(step.name in carriers for step in planned):
            result[_KEPT_LINE] = line
            lines = ()
        return StepResult(
            result=result,
            audits=saved.audits if saved.changed else (),
            lines=lines,
            then=planned,
        )

    async def describe(ctx: StepContext) -> str:
        return _lowered(setting.doing(ctx.payload))

    def outcome(ctx: OutcomeContext) -> str:
        results = {view.name: dict(view.result) for view in ctx.steps if view.result is not None}
        if results.get(SAVE, {}).get("failed"):
            return failure_reply(
                ctx.what,
                _not_saved(setting, ctx.payload).format(command=setting.command(ctx.payload)),
            )
        reply = setting.reply(ctx.payload, results)
        for view in ctx.steps:
            after = setting.after.get(view.name)
            if after is not None and (view.result or {}).get("failed"):
                reply = after.fault_reply(ctx.payload, reply)
        return reply

    steps: dict[str, Step] = {
        SAVE: Step(SAVE, StepKind.SAVE, save, describe=describe, on_fault=_failed),
        **{name: _after_step(after) for name, after in setting.after.items()},
    }
    return ChangeType(
        kind=setting.kind,
        opening=(PlannedStep(SAVE),),
        steps=steps,
        check=setting.check,
        key=lambda payload: f"{setting.kind}|{json.dumps(payload, sort_keys=True)}",
        doing=setting.doing,
        outcome=outcome,
        writes=setting.writes,
        on_check_fault=lambda what, payload, _error: _check_fault_reply(setting, what, payload),
        overtakes_a_stop=setting.repairs_the_bot,
    )


__all__ = [
    "After", "NOTHING_SAVED", "RENAMED_OUTCOME", "SAVE", "Saved", "Setting",
    "setting_change",
]
