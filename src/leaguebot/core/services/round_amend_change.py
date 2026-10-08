"""Amending a round on the change queue (#439, slice 4b, amendment A).

`/round amend` offers its confirmation as it always has. Confirm reads the round for its number and
asks the queue for this change, which is judged when it is asked and again when it comes up to run,
in the words Confirm used: a round that has gone, a window that has passed or results entered
while the amendment waited refuse it with the same reply. It imports nothing from a module and no
cog: what it needs of weather, attendance and the formatting of the round list reaches it through
the `AmendHooks` the builder fills, and it reads the season through the service it is handed.

**The refusal because a cancellation is in hand is made only as the change is asked for**
(`CheckContext.change_id` is None), through `cancellation_changes.cancellation_holding_amendment`.
Run, it cannot fire: whatever was ahead of the amendment on the queue has finished, and a
cancellation queued behind it must not make the amendment refuse itself. Two requests asked in the
same instant can both pass; the queue then runs them in order and the second reads what the first
wrote, a renumbering included.

`amendment_in_hand` reads the queue for an amendment of a division, so that a cancellation is
refused while one is waiting, running or stopped. It is read from the queue, never from memory
(architecture.md, "How a change is carried out").
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.models.change import Verdict
from leaguebot.core.models.round import Round, RoundFormat
from leaguebot.core.services.amendment_rules_service import judge_amendment
from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows
from leaguebot.core.services.cancellation_changes import cancellation_holding_amendment
from leaguebot.core.services.change_queue import ChangeType, CheckContext, in_hand

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.season_service import SeasonService

__all__ = [
    "AmendHooks",
    "ROUND_AMEND",
    "amendment_in_hand",
    "payload_changes",
    "round_amend_change",
]

ROUND_AMEND = "season.round.amend"

ROUND_GONE = "⛔ That round no longer exists. **Nothing has been changed.**"
_NOTHING_CHANGED = "**Nothing has been changed.** Run `/round amend` again to start over."


def no_longer_amendable(reasons: Iterable[str]) -> str:
    """The refusal of an amendment the rules no longer allow, as Confirm has always said it."""
    return (
        "⛔ This round can no longer be amended:\n"
        + "\n".join(f"• {reason}" for reason in reasons)
        + f"\n\n{_NOTHING_CHANGED}"
    )


@dataclass(frozen=True)
class AmendHooks:
    """What the amendment needs of weather, attendance and the formatting of the round list.

    The builder fills it, so that this module imports no module (architecture.md, "How modules and
    core fit together"). *windows* gives the lead times the amendment is judged against, as
    `LeagueBot.amendment_windows` does: attendance's only where it is on, weather's always.
    *withdraw_phases_on*, *reopen_check_in_on* write on the connection a save hands them and commit
    nothing. *delete_forecast* raises `StepFailedOnDiscord` and keeps the forecast's record where
    Discord refuses. *run_phase* draws a phase now, *repost_call* posts the check-in call again, and
    *round_list* formats the division's rounds for the reply.
    """

    windows: Callable[[], Awaitable[tuple[AttendanceWindows | None, WeatherWindows]]]
    withdraw_phases_on: Callable[[aiosqlite.Connection, int, Iterable[int]], Awaitable[None]]
    delete_forecast: Callable[[Any, int, int, int], Awaitable[None]]
    invalidation_text: Callable[[str], str]
    run_phase: Callable[[int, int, Any], Awaitable[None]]
    reopen_check_in_on: Callable[[aiosqlite.Connection, int], Awaitable[None]]
    repost_call: Callable[[int, int, Any], Awaitable[None]]
    round_list: Callable[[list[Round]], str]


def payload_changes(amendments: Iterable[tuple[str, object]]) -> list[list[str]]:
    """The amendments a member confirmed, as the change's payload holds them: ``[field, value]``,
    the moment as ISO text and the format as its value."""
    changes: list[list[str]] = []
    for field, value in amendments:
        if isinstance(value, datetime):
            text = value.isoformat()
        elif isinstance(value, RoundFormat):
            text = value.value
        else:
            text = str(value)
        changes.append([field, text])
    return changes


def amended_values(changes: Iterable[Sequence[str]]) -> dict[str, Any]:
    """The payload's changes as the rules judge them: the moment a datetime, the format a
    `RoundFormat`."""
    values: dict[str, Any] = {}
    for field, text in changes:
        if field == "scheduled_at":
            values[field] = datetime.fromisoformat(text)
        elif field == "format":
            values[field] = RoundFormat(text)
        else:
            values[field] = text
    return values


async def amendment_in_hand(db_path: str, division_id: int) -> int | None:
    """The first job of an amendment of a round of *division_id* that is queued, running or
    stopped on a failure, 0 where only its close is left, or None where none is in hand."""
    for payload, job in await in_hand(db_path, (ROUND_AMEND,)):
        if payload.get("division_id") == division_id:
            return job or 0
    return None


def round_amend_change(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    scheduler: "SchedulerService",
    hooks: AmendHooks,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that amends a round; see the module.

    The payload is ``{"round_id", "round_number", "division_id", "division_name", "changes"}``,
    *changes* being ``[[field, value]]`` (`payload_changes`). It names no season: a payload that
    does is read as holding every division of that season (`review_changes.division_job_in_hand`).
    The builder hands in the *modules* (each post's `still_due`), the *seasons* service, the
    *scheduler*, the *hooks* of the other modules and the queue's clock *now*.
    """

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        rnd = await seasons.get_round(int(payload["round_id"]))
        if rnd is None:
            return Verdict.refuse(ROUND_GONE)
        attendance, weather = await hooks.windows()
        verdict = judge_amendment(
            rnd,
            amended_values(payload["changes"]),
            now=now(),
            attendance=attendance,
            weather=weather,
        )
        if not verdict.allowed:
            return Verdict.refuse(
                no_longer_amendable(verdict.refusals),
                reason="it can no longer be amended:\n" + "\n".join(verdict.refusals),
            )
        if ctx.change_id is None:
            held = await cancellation_holding_amendment(ctx.db_path, rnd)
            if held is not None:
                return Verdict.refuse(held)
        return Verdict.go()

    return ChangeType(
        kind=ROUND_AMEND,
        opening=(),
        steps={},
        check=check,
        key=lambda payload: (
            f"{ROUND_AMEND}:{payload['round_id']}:{json.dumps(payload['changes'])}"
        ),
        doing=lambda payload: (
            f"Amending round {payload['round_number']} in **{payload['division_name']}**"
        ),
        outcome=lambda ctx: "✅ Round amended successfully.",
    )

