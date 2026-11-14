"""What an amendment to a round may do, and what it costs the round.

A round of an active season may have its track, its moment or its format amended. Each of those
costs the round something, and some of them cost so much that the amendment must not happen at
all. This module decides which, and whether an amendment changes anything at all, and nothing
else: it computes no consequence, writes nothing and posts nothing. `round_amend_change` carries
the answer out.

**Core judges its own rules and gathers each module's.** What it refuses itself: a field that
cannot be amended, an empty change, a round whose results are in, a round moved into the past, a
round that has already started and a mystery round given a track alone. What it costs a module's
own work (the check-in's windows, the forecast phases, the forecasts already posted) is that
module's to judge, behind core's `amendment_judged` hook, each answering an `AmendmentPart` of
what it refuses, warns of, notes and keeps for the amendment's save. `judged_with_the_modules`
asks the hook and `judge_amendment` gathers the answers, the refusals in the order the rules run:
core's head rules, then each module's in the order they signed up, then core's tail rules.

**One question answers almost all of a module's part.** For every window a round carries, ask:
*would this have run already, were the round always to have stood at its new moment?* Where it
would, what was posted stands and nothing is posted in its place; a moment already past is never
honoured retroactively. Where it would not, what was posted is withdrawn and the work armed again
for its new moment. That single rule covers the delay, the bring-forward and the change that moves
no date at all.

**A round is never moved into the past**, whatever the modules enabled. A round's result submission
is armed against its own moment, so a round moved backwards would have it opened late, at once, and
not at the round's moment, its start (`results.submission.open`) being the only route to a
submission channel. The refusal is stated in its own right so that the answer does not depend on
which modules a league runs. Moving a round *forward* is untouched, and remains the remedy for a
circuit or a format that must be corrected late.

**An amendment is one change.** However many of the three fields it alters, it is judged once,
against the round as it will stand once every change is in, and where any rule refuses any part of
it none of it happens. That is why this takes a change set rather than a field: a track change is
judged against the new date when one is given in the same breath, and judging the fields
separately would refuse an amendment the league is entitled to make.

**The verdict is judged again at the moment the amendment is confirmed**, not only when it is
offered. A window can pass while the confirmation stands, and an amendment allowed on the strength
of a window that has since closed is exactly the silent loss these rules exist to prevent. That is
the caller's job; this module is given a ``now`` and answers for that moment alone.

**Pure by design**: `judge_amendment` reads no database and no Discord, and ``now`` is passed in
rather than read from the wall clock, so the tests can pin a date and a moment together. Only
`judged_with_the_modules` is awaited, for the hook's subscribers. Modelled on
`approval_window_service`, which answers the neighbouring question at the confirmation of
placements. The window offsets both modules read, and the scheduler's arming with them, come from
`round_event_moments`, so the three cannot drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from leaguebot.core.models.amendment import AmendmentPart
from leaguebot.core.models.round import (
    ROUND_CANCELLABLE,
    AttendanceWindows,
    Round,
    RoundFormat,
    RoundStatus,
    WeatherWindows,
    read_scheduled_at,
)
from leaguebot.core.services.rounds.round_moments import as_utc
from leaguebot.core.utils.hooks import AwaitedHook

#: The fields of a round a league may amend.
AMENDABLE_FIELDS = frozenset({"track_name", "format", "scheduled_at"})


@dataclass(frozen=True)
class AmendmentVerdict:
    """Whether an amendment may proceed, and what the modules say of it.

    *refusals* empty means it may. Anything in it means none of the amendment happens: a change
    set is one change, and a rule refusing one field refuses the lot. *warnings* and *notes* are
    what the confirmation tells the manager the amendment costs, the notes first, as the modules
    gave them; *kept* is what they keep for the amendment's save, merged.
    """

    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    kept: dict[str, Any] = field(default_factory=dict)
    #: The round's moment once amended.
    new_moment: datetime | None = None

    @property
    def allowed(self) -> bool:
        return not self.refusals


def _amended_moment(rnd: Round, changes: dict[str, Any]) -> datetime:
    """The round's moment once *changes* are in — the new one, or its present one."""
    moment = changes.get("scheduled_at", rnd.scheduled_at)
    if not isinstance(moment, datetime):
        moment = read_scheduled_at(str(moment))
    return moment.astimezone(timezone.utc)


def amendment_changes_nothing(standing: Mapping[str, Any], changes: Mapping[str, Any]) -> bool:
    """Whether *changes* asks for nothing but the values *standing* already holds.

    `/round amend` given what stands changes nothing, and records that nothing was changed rather
    than amending the round (the core specification's "The record of what changed"). The command
    asks this before it opens a connection, from the round it has already read.

    Args:
        standing: The round's present values, by field: its ``track_name``, its ``format`` and its
                  ``scheduled_at`` (an aware moment).
        changes:  ``{field: new value}``, as `judge_amendment` takes it. An empty set asks for
                  nothing to be judged here: it is `judge_amendment`'s to refuse.

    Returns:
        True where every field asked for already holds the value asked, a moment being the same
        moment whatever zone it was given in; False where any differs, or nothing was asked.
    """
    if not changes:
        return False
    for field, value in changes.items():
        held = standing.get(field)
        if isinstance(value, datetime) and isinstance(held, datetime):
            if value != held:
                return False
        elif value != held:
            return False
    return True


def status_refusal(status: str) -> str:
    """Why a round in *status* can no longer be amended: it is cancelled, or its results are in.

    Said by the rules, and again by the amendment's save when it finds the round no longer one
    that may be amended, so the two cannot differ.
    """
    if status == RoundStatus.CANCELLED.value:
        return "This round has been cancelled and can no longer be amended."
    return (
        "This round's results have been entered, so it can no longer be amended. "
        "Drivers have reports and appeals to lodge against them."
    )


def judge_amendment(
    rnd: Round,
    changes: dict[str, Any],
    *,
    now: datetime,
    parts: Sequence[AmendmentPart] = (),
) -> AmendmentVerdict:
    """Judge *changes* against *rnd* as it will stand once they are in, with the modules' *parts*.

    Args:
        rnd:     The round as it stands, carrying its status.
        changes: ``{field: new value}`` over `AMENDABLE_FIELDS`. An empty set is refused.
        now:     The moment to judge against. Required, and never the wall clock.
        parts:   What each module answered of the amendment (`judged_with_the_modules`), in the
                 order they signed up; none where no module is signed up.

    Returns:
        An `AmendmentVerdict`. The refusals run core's head rules, then each module's, then core's
        tail rules. Where `refusals` is non-empty the rest describes the amendment that was
        refused and must not be acted upon.

    Raises:
        ValueError: two parts keep the same name, so that neither could be handed to the save
                    without replacing the other.
    """
    now = as_utc(now)
    refusals: list[str] = []

    unknown = sorted(set(changes) - AMENDABLE_FIELDS)
    if unknown:
        refusals.append(f"A round's {', '.join(unknown)} cannot be amended.")
    if not changes:
        refusals.append("An amendment must change at least one of the track, the moment or the format.")

    # ── Is the round still one that may be amended at all? ──────────────────────────────────
    # The same set that governs cancellation, and for the same reason: once results are entered
    # the drivers have reports and appeals lodged against them, and an amendment would take that
    # from them. It blocks a cancelled round for free, that being outside the set too.
    if rnd.status not in ROUND_CANCELLABLE:
        refusals.append(status_refusal(rnd.status))

    new_moment = _amended_moment(rnd, changes) if not unknown else rnd.scheduled_at

    # ── Where the round may be moved to ────────────────────────────────────────────────────
    # A round is never moved into the past, whatever the modules enabled. The round's result
    # submission is armed against its moment, and a moment already passed is opened late, at
    # once, and not at the round's moment: the rule stands as written, and the reason given is
    # that the submission would open late.
    #
    # Judged regardless of enablement, so that the answer does not turn on which modules a league
    # happens to run.
    if "scheduled_at" in changes and new_moment <= now:
        refusals.append(
            "A round cannot be moved into the past. Its result submission would open late, at "
            "once, and not at the round's moment. Give it a moment still to come."
        )

    # ── What each module refuses, warns of and keeps, in the order they signed up ──────────
    warnings: list[str] = []
    notes: list[str] = []
    kept: dict[str, Any] = {}
    for part in parts:
        refusals.extend(part.refusals)
        warnings.extend(part.warnings)
        notes.extend(part.notes)
        for name in part.kept:
            if name in kept:
                raise ValueError(f"two modules keep {name!r} for the amendment's save")
        kept.update(part.kept)

    # ── What the track and the format may no longer be ─────────────────────────────────────
    if ("track_name" in changes or "format" in changes) and new_moment <= now:
        refusals.append(
            "This round has already started, so its track and format can no longer be amended."
        )

    # A mystery round names no track, and giving it one is a change of its format, not a track
    # change slipped in beside it.
    if (
        "track_name" in changes
        and "format" not in changes
        and rnd.format == RoundFormat.MYSTERY
    ):
        refusals.append(
            "A mystery round names no circuit. Change its format as well to give it one."
        )

    return AmendmentVerdict(
        refusals=refusals,
        warnings=warnings,
        notes=notes,
        kept=kept,
        new_moment=new_moment,
    )


async def judged_with_the_modules(
    amendment_judged: AwaitedHook,
    rnd: Round,
    changes: dict[str, Any],
    *,
    now: datetime,
    attendance: AttendanceWindows | None,
    weather: WeatherWindows | None,
    db: aiosqlite.Connection | None,
) -> AmendmentVerdict:
    """`judge_amendment`, with each module's part asked through the *amendment_judged* hook.

    Each subscriber is handed the save's connection *db* (None where the amendment is asked
    outside a save), the round, the changes, the round's moment once amended and the one clock
    core judges by, both in UTC, and the lead times by name, *attendance* and *weather*, as read
    fresh by the caller. It answers an `AmendmentPart`, or None for a part of none.
    """
    now = as_utc(now).astimezone(timezone.utc)
    moment = (
        _amended_moment(rnd, changes)
        if set(changes) <= AMENDABLE_FIELDS
        else rnd.scheduled_at.astimezone(timezone.utc)
    )
    handed = await amendment_judged.call(
        db, rnd, changes, moment=moment, now=now, attendance=attendance, weather=weather
    )
    return judge_amendment(
        rnd, changes, now=now, parts=tuple(part for part in handed if part is not None)
    )
