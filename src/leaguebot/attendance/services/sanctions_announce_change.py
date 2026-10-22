"""The announcement of the attendance sanctions `/attendance sync` applied, on the change queue
(#439, slice 6b): `attendance.sanctions.announce`.

The sync applies its sanctions itself and then asks this change, as the bot's and repeatably, for
their cards. Its jobs are results' own (`review_verdicts`): `announce_heading`, which posts the
round's banner where none stands and records it with its mark, and one `announce_sanction` for
each sanction, whose card goes beneath that banner and whose record notes the banner as heading a
sanction card. A card Discord refuses stops the queue at its job, to be tried again as text
(Constitution XIV, rule 8) or discarded; a discarded card is named in the log channel as applied but
not announced, for posting by hand. The sanctions are already applied, so each card is due
whatever the thresholds now say (`applied` on its payload); the change is dropped only while
attendance is off.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import ChangeOrigin, PlannedStep, Verdict
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
)
from leaguebot.results.services.attendance_hook import AttendanceAfterReview
from leaguebot.results.services.review_verdicts import (
    ANNOUNCE_HEADING,
    ANNOUNCE_SANCTION,
    not_done,
    verdict_steps,
)

SANCTIONS_ANNOUNCE = "attendance.sanctions.announce"

#: The heading's payload says it heads sanction cards, not verdicts (`review_verdicts`).
_OVER_SANCTIONS = "sanctions"


def _plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
    """The banner where none stands (the job finds out when it runs), then each card."""
    round_id = int(payload["round_id"])
    heading: dict[str, Any] = {"round_id": round_id, "over": _OVER_SANCTIONS}
    if payload.get("round_number") is not None:
        heading["round_number"] = payload["round_number"]
    return (
        PlannedStep(ANNOUNCE_HEADING, heading),
        *(
            PlannedStep(ANNOUNCE_SANCTION, {
                "round_id": round_id, "division_id": int(payload["division_id"]),
                "division": payload.get("division", "the division"), "candidate": candidate,
                "heading_round_id": round_id, "applied": True,
            })
            for candidate in payload["sanctions"]
        ),
    )


def sanctions_announce_change(
    *, attendance: AttendanceAfterReview, now: Callable[[], datetime]
) -> ChangeType:
    """`attendance.sanctions.announce`, *attendance* being the hook the builder hands the review's
    change types and *now* the queue's clock, which stamps the heading's record."""
    steps = verdict_steps(now, attendance)

    async def check(ctx: CheckContext) -> Verdict:
        if not ctx.payload.get("sanctions"):
            return Verdict.not_due("no sanction was applied")
        if not await attendance.enabled():
            return Verdict.not_due("attendance is off")
        return Verdict.go()

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    def finished_lines(ctx: OutcomeContext) -> tuple[str, ...]:
        """What a Discard left undone, in the log channel, for the manager to do by hand."""
        left = not_done(ctx)
        if not left:
            return ()
        return (
            f"ATTENDANCE_SANCTIONS | Incomplete\n  division: {ctx.payload.get('division', '?')}\n"
            + "\n".join(f"  {line}" for line in left),
        )

    return ChangeType(
        kind=SANCTIONS_ANNOUNCE,
        opening=(PlannedStep(ANNOUNCE_HEADING),),
        plan=_plan,
        steps={name: steps[name] for name in (ANNOUNCE_HEADING, ANNOUNCE_SANCTION)},
        check=check,
        key=lambda payload: f"{SANCTIONS_ANNOUNCE}:{payload['round_id']}",
        doing=lambda payload: (
            f"announcing round {payload.get('round_number', payload['round_id'])}'s attendance "
            "sanctions"
        ),
        outcome=outcome,
        finished_lines=finished_lines,
        repeatable=True,
    )


async def ask_sanctions_announce(
    queue: ChangeQueue,
    db_path: str,
    round_id: int,
    division_id: int,
    sanctions: Iterable[dict[str, Any]],
) -> None:
    """Ask the queue to announce *sanctions*, the candidates each was owed as, applied to
    *division_id* at *round_id*. The division's name and the round's number are read here, on a
    connection of their own; nothing is asked where no sanction was applied."""
    owed = list(sanctions)
    if not owed:
        return
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT d.name AS division, r.round_number FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
            (round_id,),
        )).fetchone()
    payload: dict[str, Any] = {
        "round_id": round_id, "division_id": division_id,
        "division": row["division"] if row is not None else str(division_id),
        "round_number": row["round_number"] if row is not None else None,
        "sanctions": owed,
    }
    await queue.ask(
        SANCTIONS_ANNOUNCE, payload,
        what=f"announcing round {payload['round_number'] or round_id}'s attendance sanctions",
        origin=ChangeOrigin.BOT,
    )
