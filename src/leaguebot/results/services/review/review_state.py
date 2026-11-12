"""review_state.py — What a penalty review holds, and when it has moved on.

The in-memory state threaded through both stages of a round's review
(:class:`PenaltyReviewState`, with the :class:`StagedPardon` it stages), the reviews held for
the change queue to apply a staging to, the wording that names staged and applied penalties, and
the checks that say a review's buttons no longer act: the round's reports or appeals are being
approved, or the round has left the stage the review is for.  Nothing here builds a view.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.server_config import ServerConfig
from leaguebot.core.services.posting.output_router import OutputRouter
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.module.attendance_hook import AttendanceAfterReview
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.drivers.driver_service import current_account_map_for_division
from leaguebot.results.services.review.penalty_service import StagedPenalty
from leaguebot.core.utils.league_bot import LeagueBot

#: What reads the league's server configuration, handed to a review's views so that none looks the
#: service up on the bot.
ReadServerConfig = Callable[[], Awaitable[ServerConfig | None]]


# ---------------------------------------------------------------------------
# Attendance pardon staging dataclass
# ---------------------------------------------------------------------------

@dataclass
class StagedPardon:
    """In-memory representation of a staged attendance pardon (033-attendance-tracking)."""
    driver_user_id: int       # Discord user ID (display / audit)
    driver_profile_id: int    # FK — driver_profiles.id
    attendance_id: int        # FK — driver_round_attendance.id
    pardon_type: str          # 'NO_RSVP' | 'ABSENT' | 'NO_SHOW'
    justification: str
    grantor_id: int           # Discord user ID of staging admin
    #: When a pardon read back from the round was granted, so that an amendment writing it out
    #: again keeps the time as well as the grantor (#345). None for one staged now.
    granted_at: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """This staged pardon as plain data, for the payload of a change on the queue (#439)."""
        return {
            "driver_user_id": self.driver_user_id,
            "driver_profile_id": self.driver_profile_id,
            "attendance_id": self.attendance_id,
            "pardon_type": self.pardon_type,
            "justification": self.justification,
            "grantor_id": self.grantor_id,
            "granted_at": self.granted_at,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "StagedPardon":
        """The staged pardon `to_payload` gave."""
        return cls(
            driver_user_id=int(payload["driver_user_id"]),
            driver_profile_id=int(payload["driver_profile_id"]),
            attendance_id=int(payload["attendance_id"]),
            pardon_type=payload["pardon_type"],
            justification=payload["justification"],
            grantor_id=int(payload["grantor_id"]),
            granted_at=payload.get("granted_at"),
        )


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class PenaltyReviewState:
    """Mutable state threaded through every step of the penalty review wizard."""
    round_id: int
    division_id: int
    submission_channel_id: int
    session_types_present: list[SessionType]
    db_path: str
    bot: LeagueBot
    #: The change queue the review's buttons and forms ask their changes of, handed in by whoever
    #: builds the state, so that a view never looks it up on the bot.
    queue: ChangeQueue
    #: Reads the league's server configuration, handed in with the queue, which the tier check of
    #: every button reads the roles from, so that a view never looks the service up on the bot.
    server_config: ReadServerConfig
    #: The output router the review's presses write their log lines through, handed in with the
    #: queue, so that a view never looks it up on the interaction's client.
    router: OutputRouter
    #: Attendance's share of the review, handed in with the router, which the pardon form reads
    #: the driver's attendance row through, so that a view never looks it up on the bot.
    attendance: AttendanceAfterReview
    staged: list[StagedPenalty] = field(default_factory=list)
    staged_appeals: list[StagedPenalty] = field(default_factory=list)
    staged_pardons: list[StagedPardon] = field(default_factory=list)
    prompt_message_id: int | None = None
    appeals_prompt_message_id: int | None = None
    #: The approval message **No Penalties / Confirm** posted, while it stands (#402). Its buttons
    #: act only while they sit on this message; see :func:`_take_down_approval`.
    approval_message_id: int | None = None
    round_number: int = 0
    division_name: str = ""
    #: True where this review is an **amendment replaying a settled round**, not the round's
    #: first pass through review (#345).
    #:
    #: The stages are the same and the views are the same; what differs is what approving one
    #: means. A first pass moves the round on — report review sets `AWAITING_APPEAL_VERDICTS`,
    #: appeal review sets `FINAL`, refreshes the division's status and may wind the season
    #: down. An amended round is *already* FINAL, so replaying those transitions would move a
    #: settled round backwards and forwards through states it has long since left, and could
    #: finish a division or wind down a season a second time.
    #:
    #: Read by the finalisers, which hand an amendment's stages to functions of their own.
    is_amendment: bool = False


async def _staged_named(state: PenaltyReviewState, penalties: list[StagedPenalty]) -> str:
    """The penalties as a log line names them: "+5s for <@1>, DSQ for <@2>".

    A driver is named by the account they use now (`_shown`), as the Remove presses read it.
    """
    named = []
    for sp in penalties:
        shown = await _shown(state, sp.driver_user_id)
        named.append(f"{_pen_label(sp)} for <@{shown}>")
    return ", ".join(named)


async def _applied_named(state: PenaltyReviewState, penalties: list[StagedPenalty]) -> str:
    """What an approval applied, as the line that records it names it (#482).

    "+5s for <@1> in FEATURE_RACE, DSQ for <@2> in SPRINT_RACE": each penalty or correction,
    the driver by the account they use now, and the session. The approval's own line carries it
    because `apply_penalties_on` writes none.
    """
    named = []
    for sp in penalties:
        shown = await _shown(state, sp.driver_user_id)
        named.append(f"{_pen_label(sp)} for <@{shown}> in {sp.session_type.value}")
    return ", ".join(named)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _pen_label(sp: StagedPenalty) -> str:
    """Format a staged penalty as a human-readable string.

    A disqualification and no further action carry no seconds, and are labelled by the word the
    manager typed (#138).
    """
    if sp.penalty_type in ("DSQ", "NFA"):
        return sp.penalty_type
    assert sp.penalty_seconds is not None
    return f"+{sp.penalty_seconds}s" if sp.penalty_seconds > 0 else f"{sp.penalty_seconds}s"


async def _shown(state: PenaltyReviewState, driver_user_id: int) -> int:
    """The account a message names *driver_user_id*'s driver by: the one they use now.

    A staged penalty carries the account its result row stands under, which is what applies
    it; the driver may have moved on from that account since (issue #243).
    """
    async with get_connection(state.db_path) as db:
        current_of = await current_account_map_for_division(db, state.division_id)
    return current_of.get(driver_user_id, driver_user_id)


#: What a review answers while its reports are being approved (#402).
_BEING_APPROVED = (
    "⏳ This round's reports are being approved. Its appeals review is posted below once they are."
)


#: What a review answers while its appeals are being approved (#482).
_APPEALS_BEING_APPROVED = "⏳ This round's appeals are being approved."


async def _review_moved_on(state: PenaltyReviewState) -> str | None:
    """Why this review can no longer be acted on, or None while it can (#402).

    Every control of the review acts on *state*, which is held in memory and outlives the stage
    it was built for. The prompt and the approval message stayed on screen, and worked, through a
    resubmission, through the appeals stage and while an approval was still being applied — so a
    manager could approve results already being replaced, approve the reports a second time, or
    be told a penalty was removed when it had been applied. Every control asks this first.

    A first pass is current while all four hold:

    - its reports are not already being approved: no `results.reports.approve` of the round is in
      hand on the change queue, **a stopped one included** (#439). The approval is a change, and
      the queue, not this review's memory, says it is closing;
    - the round still awaits its report verdicts. Approved, it waits on appeals, and a round
      that has ended has nothing left to review. This is what closes its pardons (FR-011): they
      are written when the reports are approved;
    - no resubmission is collecting, which replaces the results this review is of;
    - its prompt is the one the channel records. Every review posts a prompt of its own and
      records it, so a review whose prompt has been replaced — by a cancelled or completed
      resubmission, or by restart recovery — is an old one, whatever its state still holds.

    The last three are :func:`review_stage_refusal`, which the approval's own check asks as well.

    An amendment's round is FINAL throughout, so none of that applies to it. Its review closes
    once its reports are approved, **pardons included**, as a first pass's does (decided
    2026-09-23, #402). Its pardons stayed open until its appeals were approved, because that is
    when an amendment writes them; they are still written then, with everything else it commits,
    so that a revert has nothing to take back (#345) — but they are changed in the report stage
    alone.

    **One race is left open.** A resubmission records itself only after logging what it
    discards, so an approval pressed in that moment passes the check. The window is one log post
    wide, and both buttons would have to be pressed inside it.
    """
    if state.is_amendment:
        # Read from the amendment's row (#439): the stage was approved once its save was made,
        # whichever process asks, and it is a change the queue carries out and resumes.
        async with get_connection(state.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT reports_approved_at FROM round_amend_channels WHERE round_id = ?",
                    (state.round_id,),
                )
            ).fetchone()
        if row is None or row["reports_approved_at"] is None:
            return None
        return (
            "❌ This amendment's reports are already approved, so neither they nor its pardons "
            "can be changed any more. Its appeals are reviewed below."
        )
    from leaguebot.core.services.queue.queue_records import unfinished
    from leaguebot.results.services.review.report_approval_change import KIND

    if any(p.get("round_id") == state.round_id for p in await unfinished(state.db_path, [KIND])):
        return _BEING_APPROVED
    return await review_stage_refusal(state.db_path, state.round_id, state.prompt_message_id)


async def review_stage_refusal(
    db_path: str, round_id: int, prompt_message_id: int | None
) -> str | None:
    """Why a first pass's review is no longer at its report stage, or None while it is: the round
    awaits its report verdicts, no resubmission is collecting, and *prompt_message_id* is the
    prompt the channel records. Read from the database alone, so the approval's check and a
    control's check read one rule."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.status, rsc.resubmitting, rsc.prompt_message_id
            FROM rounds r
            JOIN round_submission_channels rsc ON rsc.round_id = r.id AND rsc.closed = 0
            WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is not None and row["status"] == RoundStatus.AWAITING_APPEAL_VERDICTS.value:
        return (
            "❌ This round's post-race penalties have already been approved, and its pardons "
            "with them. Its appeals are reviewed below; a decision already applied is changed "
            "with `/results rounds amend` once the round is final."
        )
    if row is None or row["status"] != RoundStatus.AWAITING_REPORT_VERDICTS.value:
        return "❌ This round's penalty review is over, so nothing here can be changed."
    if row["resubmitting"]:
        return (
            "❌ This round's results are being resubmitted. The penalty review is posted again "
            "once every session is in, or when the resubmission is cancelled."
        )
    if row["prompt_message_id"] != prompt_message_id:
        return (
            "❌ This penalty review has been replaced by a newer one in this channel. "
            "Use that one."
        )
    return None


async def _appeals_review_moved_on(state: PenaltyReviewState) -> str | None:
    """Why the appeals review can no longer be acted on, or None while it can (#482).

    The appeals prompt and its clear confirmation outlive the stage they were built for, as the
    penalty review's do (:func:`_review_moved_on`): pressed after the appeals were approved, or
    while they are being approved, a control staged a correction that would never be applied, or
    cleared a list already being applied. Every control of the appeals review asks this first.
    It is current while its appeals are not being approved and the round still awaits its
    appeal verdicts. An amendment's appeals stage keeps the rule it had, none here.

    The appeals are being approved while a `results.appeals.approve` of the round is in hand on
    the change queue, **a stopped one included** (#439): the approval is a change, and the queue,
    not this review's memory, says it is closing. The rest is :func:`appeals_stage_refusal`,
    which the approval's own check asks as well.
    """
    if state.is_amendment:
        return None
    from leaguebot.core.services.queue.queue_records import unfinished
    from leaguebot.results.services.review.appeals_approval_change import KIND

    if any(p.get("round_id") == state.round_id for p in await unfinished(state.db_path, [KIND])):
        return _APPEALS_BEING_APPROVED
    return await appeals_stage_refusal(
        state.db_path, state.round_id, state.appeals_prompt_message_id
    )


async def appeals_stage_refusal(
    db_path: str, round_id: int, prompt_message_id: int | None
) -> str | None:
    """Why a first pass's review is no longer at its appeals stage, or None while it is: the round
    still awaits its appeal verdicts, and *prompt_message_id* is the appeals prompt the channel
    records. Read from the database alone, so the approval's check and a control's check read
    one rule.

    A prompt is compared only where both ids are known: a round whose prompt a run from before the
    appeals prompt was recorded posted has none stored, and a state built without one carries none.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT r.status, rsc.appeals_prompt_message_id FROM rounds r "
            "LEFT JOIN round_submission_channels rsc ON rsc.round_id = r.id WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None or row["status"] != RoundStatus.AWAITING_APPEAL_VERDICTS.value:
        return "❌ This round's appeals review is over, so nothing here can be changed."
    stored = row["appeals_prompt_message_id"]
    if stored is not None and prompt_message_id is not None and stored != prompt_message_id:
        return (
            "❌ This appeals review has been replaced by a newer one in this channel. "
            "Use that one."
        )
    return None


# ---------------------------------------------------------------------------
# The review a staging is applied to
# ---------------------------------------------------------------------------

#: The review each round's stage is held in, by (round, appeals): what the form or press that
#: stages hands over, for the staging change to apply its item to when its turn comes. Held for
#: the change only; the review's views and forms hold it for the manager.
_HELD_REVIEWS: dict[tuple[int, bool], "PenaltyReviewState"] = {}


def hold_review(state: "PenaltyReviewState", *, appeals: bool) -> None:
    """Hand the review a staging is asked for to the change that will apply it (#439, C54)."""
    _HELD_REVIEWS[(state.round_id, appeals)] = state


def held_review(round_id: int, *, appeals: bool) -> "PenaltyReviewState | None":
    """The review last handed to `hold_review` for the round's stage, or None, as after a
    restart."""
    return _HELD_REVIEWS.get((round_id, appeals))


def clear_held_reviews() -> None:
    """Drop every review handed to a staging change, as `/bot pack` and `/bot factory-reset` do
    with the rest of what the bot holds in memory (`clear_in_memory_state`)."""
    _HELD_REVIEWS.clear()
