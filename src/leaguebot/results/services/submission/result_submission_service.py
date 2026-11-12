"""result_submission_service.py — A round's results submission: its state and its saves.

Owns the submission's state (the session orders, which submissions are open, the session each
expects next, closing one, the cancellation refusal and a round called off), the former-driver
recompute and the round's going final, saving a session's result, the points-configuration picker,
and the resubmission swap with its cancel view. Reading a paste is in ``paste_reading``, scoring
in ``scoring``, amending in ``amendment_writes`` and the review's prompts in ``review_prompts``."""
from __future__ import annotations

import asyncio
import json as _json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, NamedTuple

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.cancellation import (
    SCOPE_DIVISION,
    SCOPE_ROUND,
    SCOPE_SEASON,
    CalledOffSteps,
    CancellationRound,
)
from leaguebot.core.models.change import PlannedStep, StepFailedOnDiscord, VerdictKind
from leaguebot.core.services.posting.output_router import OutputRouter
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.services.posting.owned_channel_handler import (
    edit_in_owned_channel,
    post_in_owned_channel,
)
from leaguebot.results.models.points_config import SessionType
from leaguebot.core.models.round import ROUND_CANCELLABLE, ROUND_TERMINAL, RoundFormat
from leaguebot.results.models.session_result import OutcomeModifier
from leaguebot.core.services.drivers.driver_service import set_former_drivers_on
from leaguebot.core.utils.channel_guard import is_league_manager
from leaguebot.core.utils.input_validator import parse_gap, parse_lap_gap, parse_time
from leaguebot.core.utils.league_server import CallbackButton, LeagueView
from leaguebot.core.utils.log_lines import refuse
from leaguebot.core.utils.member_names import interaction_member
from leaguebot.results.services.publishing.results_post_service import _parse_ids
from leaguebot.results.services.review.review_posting import DELETE_CHANNEL
from leaguebot.results.services.submission.paste_reading import ParsedQualifyingRow, ParsedRaceRow, _parse_time_to_ms
from leaguebot.results.services.points.scoring import _apply_points_in_tx

log = logging.getLogger(__name__)

#: Rendered from the model's own sets; see season_service for the same fragment.
_CANCELLABLE_SQL = ", ".join(f"'{v}'" for v in sorted(ROUND_CANCELLABLE))
_TERMINAL_SQL = ", ".join(f"'{v}'" for v in sorted(ROUND_TERMINAL))

# ---------------------------------------------------------------------------
# Session ordering
# ---------------------------------------------------------------------------

SESSION_ORDER_NORMAL: list[SessionType] = [
    SessionType.FEATURE_QUALIFYING,
    SessionType.FEATURE_RACE,
]

SESSION_ORDER_SPRINT: list[SessionType] = [
    SessionType.SPRINT_QUALIFYING,
    SessionType.SPRINT_RACE,
    SessionType.FEATURE_QUALIFYING,
    SessionType.FEATURE_RACE,
]

_SESSION_LABELS: dict[SessionType, str] = {
    SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
    SessionType.SPRINT_RACE: "Sprint Race",
    SessionType.FEATURE_QUALIFYING: "Feature Qualifying",
    SessionType.FEATURE_RACE: "Feature Race",
}


def get_sessions_for_format(round_format: RoundFormat) -> list[SessionType]:
    """Return the ordered list of session types for a given round format.

    SPRINT rounds include all four sessions.
    NORMAL and ENDURANCE rounds use Feature sessions only (spec §8).
    """
    if round_format is RoundFormat.SPRINT:
        return SESSION_ORDER_SPRINT
    return SESSION_ORDER_NORMAL


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def is_submission_open(db_path: str, round_id: int) -> bool:
    """Return True if a submission channel for this round exists and is not yet closed."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT closed FROM round_submission_channels WHERE round_id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    return row is not None and row["closed"] == 0


class OpenSubmission(NamedTuple):
    """A round's results submission standing open: its channel, and whether it has accepted
    anything (a `session_results` row of the round, of either status)."""

    round_id: int
    channel_id: int
    accepted: bool


async def open_submissions_on(
    db: aiosqlite.Connection, round_ids: Iterable[int]
) -> list[OpenSubmission]:
    """Each open submission among *round_ids*, in round order, read on the connection handed.

    **Accepted means any session saved** (#439): the wizard saves each session as it accepts it
    (`_save_session_result_in_tx`), with ``status`` ``ACTIVE`` for results and ``CANCELLED`` for a
    session entered as not held, so any `session_results` row of the round, of either status,
    while the submission stands open, is data a cancellation would lose. A round in its review
    keeps its submission open with its sessions accepted, so it counts. Fixed SQL, one round at
    a time, so nothing is spliced.
    """
    found: list[OpenSubmission] = []
    for round_id in sorted(set(round_ids)):
        cursor = await db.execute(
            "SELECT channel_id FROM round_submission_channels WHERE round_id = ? AND closed = 0",
            (round_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            continue
        cursor = await db.execute(
            "SELECT 1 FROM session_results WHERE round_id = ? LIMIT 1", (round_id,)
        )
        found.append(
            OpenSubmission(round_id, int(row["channel_id"]), await cursor.fetchone() is not None)
        )
    return found


# ---------------------------------------------------------------------------
# Which session each dialogue expects next (#439)
#
# A paste is a turn on the change queue, and nothing waits in a coroutine for the next one: a
# turn reads the session its dialogue expects from what the dialogue has saved, and these are the
# three readers. They read on the caller's connection and write nothing.
# ---------------------------------------------------------------------------

async def expected_session_on(db: aiosqlite.Connection, round_id: int) -> SessionType | None:
    """The session a round's first submission expects next, or None once all are in.

    The first of the round's sessions, in running order, with no ``session_results`` row. A
    session typed CANCELLED has its row too, so it is not asked for again.
    """
    row = await (await db.execute("SELECT format FROM rounds WHERE id = ?", (round_id,))).fetchone()
    if row is None:
        return None
    saved = {
        found["session_type"]
        for found in await (
            await db.execute(
                "SELECT session_type FROM session_results WHERE round_id = ?", (round_id,)
            )
        ).fetchall()
    }
    for session in get_sessions_for_format(RoundFormat(row["format"])):
        if session.value not in saved:
            return session
    return None


async def expected_resubmitted_session_on(
    db: aiosqlite.Connection, round_id: int
) -> SessionType | None:
    """The session a round's resubmission expects next, or None once every one is drafted.

    The first session, in running order, with no complete draft: a resubmission holds its pastes
    until the last swaps the results, so what the round's results already hold counts for
    nothing. A draft whose configuration is still to be chosen (``config_name`` unset) is not
    complete, and its session is expected again; one typed CANCELLED needs no configuration.
    """
    row = await (await db.execute("SELECT format FROM rounds WHERE id = ?", (round_id,))).fetchone()
    if row is None:
        return None
    drafted = {
        found["session_type"]
        for found in await (
            await db.execute(
                "SELECT session_type FROM resubmission_drafts "
                "WHERE round_id = ? AND (config_name IS NOT NULL OR status = 'CANCELLED')",
                (round_id,),
            )
        ).fetchall()
    }
    for session in get_sessions_for_format(RoundFormat(row["format"])):
        if session.value not in drafted:
            return session
    return None


async def expected_amended_session_on(
    db: aiosqlite.Connection, amend_id: int
) -> SessionType | None:
    """The session an amendment expects next, or None once all it covers are drafted.

    The first of the amendment's own ``session_types``, in the order its record lists them, with
    no complete draft; never a session it does not cover. A draft whose configuration is still
    to be chosen is not complete.
    """
    row = await (
        await db.execute("SELECT session_types FROM round_amend_channels WHERE id = ?", (amend_id,))
    ).fetchone()
    if row is None or not row["session_types"]:
        return None
    try:
        covered = list(_json.loads(row["session_types"]))
    except (TypeError, ValueError):
        return None
    drafted = {
        found["session_type"]
        for found in await (
            await db.execute(
                "SELECT session_type FROM amend_drafts "
                "WHERE amend_id = ? AND config_name IS NOT NULL",
                (amend_id,),
            )
        ).fetchall()
    }
    for value in covered:
        if value not in drafted:
            return SessionType(value)
    return None


async def close_submissions_on(
    db: aiosqlite.Connection, round_ids: Iterable[int]
) -> list[tuple[int, int]]:
    """Mark each open submission among *round_ids* closed on the connection handed, committing
    nothing, and give ``(round_id, channel_id)`` for each so that its channel can be deleted.

    The channel's deletion is a job of its own after the save (`delete_channel`); this is the
    save's half of a submission's closing.
    """
    closed = [(each.round_id, each.channel_id) for each in await open_submissions_on(db, round_ids)]
    for round_id, _channel_id in closed:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1 WHERE round_id = ?", (round_id,)
        )
    return closed


def _accepted_in_the_way(scope: str, held: list[tuple[CancellationRound, int]]) -> str:
    """The refusal of a cancellation of *scope* for the first of *held*, the rounds whose open
    submission has accepted results, each with its channel: the lowest-numbered round for a
    division's, the first in the order handed for a season's. The words are results': what the
    cancellation would lose is the results."""
    if scope == SCOPE_ROUND:
        each, channel_id = held[0]
        return (
            f"❌ Cannot cancel Round {each.round_number} — results have already been accepted "
            f"in its submission channel <#{channel_id}>, and cancelling would lose them."
        )
    if scope == SCOPE_DIVISION:
        each, channel_id = min(held, key=lambda found: found[0].round_number)
        return (
            f"❌ Cannot cancel **{each.division_name}** — results have already been accepted in "
            f"the submission channel of round {each.round_number} (<#{channel_id}>), and "
            "cancelling would lose them."
        )
    each, channel_id = held[0]
    return (
        f"❌ Cannot cancel the season — results have already been accepted in the submission "
        f"channel of round {each.round_number} of **{each.division_name}** (<#{channel_id}>), "
        "and cancelling would lose them."
    )


async def cancellation_refusal(
    db: aiosqlite.Connection, scope: str, rounds: Iterable[CancellationRound]
) -> str | None:
    """What stands in a cancellation's way, as results has it: the refusal where any of *rounds*
    has an open submission that has accepted results (**owner, 2026-10-08, amending Constitution
    XII**: a cancellation would lose them, and a round in its review counts), else None.

    Subscribed to core's `cancellation_refused`, which asks it on a connection of its own as the
    cancellation is asked and as it runs, and again inside the save as a backstop. Reads only.
    """
    standing = list(rounds)
    accepted = {
        each.round_id: each.channel_id
        for each in await open_submissions_on(db, [each.round_id for each in standing])
        if each.accepted
    }
    held = [(each, accepted[each.round_id]) for each in standing if each.round_id in accepted]
    return _accepted_in_the_way(scope, held) if held else None


#: Why each scope's cancellation deletes a submission channel, as the job says it.
_CHANNEL_REASON = {
    SCOPE_ROUND: "Round cancelled",
    SCOPE_DIVISION: "Division cancelled",
    SCOPE_SEASON: "Season cancelled",
}


async def close_called_off_on(
    db: aiosqlite.Connection, scope: str, rounds: Iterable[CancellationRound]
) -> CalledOffSteps:
    """Close the open submission of each of *rounds* on the connection handed, committing nothing,
    and plan the deletion of each one's channel, in the order handed, first after the save: the
    channel is where a manager pastes, so it goes before any notice.

    Subscribed to core's `rounds_called_off`. A season's cancellation hands every round of the
    divisions it ends, whether or not it calls the round off, and every empty submission is
    closed. A submission that has accepted nothing is all there is to close: the cancellation was
    refused while one had accepted results (`cancellation_refusal`).

    The job's own payload carries `channel_target` ("results submission channel of round N"),
    results' own word for what a discard of it leaves undeleted, so that core's `not_notified`
    can name it without naming the job (`notice_target` is the same shape for a module's notice).
    """
    standing = {each.round_id: each for each in rounds}
    closed = dict(await close_submissions_on(db, list(standing)))
    return CalledOffSteps(first=tuple(
        PlannedStep(DELETE_CHANNEL, {
            "channel_id": closed[round_id],
            "round_id": round_id,
            "what": "results submission channel",
            "channel_target": f"results submission channel of round {each.round_number}",
            "reason": _CHANNEL_REASON[scope],
            "division_name": each.division_name,
            "round_number": each.round_number,
        })
        for round_id, each in standing.items()
        if round_id in closed
    ))


_RACED_A_ROUND_SQL = """
    SELECT rsr.driver_profile_id AS profile_id, sr.round_id AS round_id
    FROM race_session_results rsr
    JOIN session_results sr ON sr.id = rsr.session_result_id
    JOIN rounds r ON r.id = sr.round_id
    WHERE rsr.driver_profile_id IS NOT NULL
      AND rsr.outcome != 'DNS'
      AND sr.status = 'ACTIVE'
      AND r.status = 'FINAL'
    UNION
    SELECT qsr.driver_profile_id AS profile_id, sr.round_id AS round_id
    FROM qualifying_session_results qsr
    JOIN session_results sr ON sr.id = qsr.session_result_id
    JOIN rounds r ON r.id = sr.round_id
    WHERE qsr.driver_profile_id IS NOT NULL
      AND qsr.outcome != 'DNS'
      AND sr.status = 'ACTIVE'
      AND r.status = 'FINAL'
"""

# Every profile named anywhere in one round's live results, whatever the outcome and whatever
# the round's status. The recompute's candidate set, and the amendment path's record of who the
# round held before it was rewritten. A driver struck out of the round entirely is in neither
# table afterwards, which is what `also_consider` is for.
_PROFILES_IN_ROUND_SQL = """
    SELECT rsr.driver_profile_id AS profile_id
    FROM race_session_results rsr
    JOIN session_results sr ON sr.id = rsr.session_result_id
    WHERE sr.round_id = ? AND sr.status = 'ACTIVE' AND rsr.driver_profile_id IS NOT NULL
    UNION
    SELECT qsr.driver_profile_id AS profile_id
    FROM qualifying_session_results qsr
    JOIN session_results sr ON sr.id = qsr.session_result_id
    WHERE sr.round_id = ? AND sr.status = 'ACTIVE' AND qsr.driver_profile_id IS NOT NULL
"""


async def recompute_former_drivers_for_round(
    db, round_id: int, *, also_consider: Iterable[int] | None = None
) -> None:
    """Set the former-driver flag from one round's final results, both ways (#216).

    Runs on the caller's connection and commits nothing, so the flag moves in the same
    transaction as whatever made the round final. The flag is core's: this works out who has
    raced and writes it through core's `set_former_drivers_on`, never into the table itself.
    Five paths reach it, each in the save that made the round final: the appeals finaliser, an
    amendment's stage three, the revert, and, through :func:`rounds_made_final_on`, the two
    closers of core's that make a round FINAL (results switched off, a season cancelled).

    The core specification's **Leaving the league** section is the rule: a driver has raced a
    round only once that round is FINAL and only by its final results, an entry recording that
    they did not start does not count, and an amendment leaving them no longer having raced
    clears the flag unless another final round marks them. Before this the flag was raised the
    moment a classification was pasted in, for every driver in it whatever the outcome, and
    lowered by nothing.

    Three decisions sit behind the SQL, none of them derivable from the spec's wording alone:

    **Only ``DNS`` excludes** (decided 2026-09-21). A driver who retired or was disqualified
    started the race, so they raced it; the spec excludes the did-not-start entry alone. Pinned
    by ``test_a_driver_who_retired_or_was_disqualified_is_marked``.

    **The recompute is scoped to this round's drivers**, never the whole roster. A driver is
    considered only where the named round's live results mention them, so a flag raised by a
    round this one knows nothing about is never disturbed. That scoping is
    ``test_a_driver_of_another_round_is_left_alone``.

    The scoping is by *this round's drivers*, though, not by how the flag came to be set: a
    value put there by hand through ``/test-mode set-former-driver`` is overwritten like any
    other where the driver does appear in this round. A maintainer who sets the flag and then
    finalises a round the driver is in — DNS, say — will find it back at 0. The flag is
    derived from results; the command sets it directly so both branches of the driver pass can
    be reached without racing, not to pin a value against them.

    **A driver struck out entirely must be named by the caller**, through ``also_consider``.
    The scoping above reads the round as it stands *now*, and a driver an amendment removed is
    by then in no result at all — so nothing would ever reconsider the flag the old
    classification gave them, which is exactly the "never cleared" half of #216. The amendment
    path collects the profiles the round held beforehand and passes them here;
    ``test_a_driver_struck_from_the_round_is_cleared`` fails without it.

    Clearing asks whether any *other* final round still marks the driver, so a driver struck
    from this round but racing elsewhere keeps the flag. That is
    ``test_a_driver_marked_by_another_final_round_keeps_the_flag``.

    Idempotent: calling it twice for the same round changes nothing the first call did not.
    """
    cursor = await db.execute(_PROFILES_IN_ROUND_SQL, (round_id, round_id))
    candidates = {r["profile_id"] for r in await cursor.fetchall()}
    candidates.update(p for p in (also_consider or ()) if p is not None)
    if not candidates:
        return

    cursor = await db.execute(
        f"SELECT DISTINCT profile_id FROM ({_RACED_A_ROUND_SQL}) WHERE round_id = ?",
        (round_id,),
    )
    raced_here = {r["profile_id"] for r in await cursor.fetchall()}

    # Only those this round does not already mark need asking about, and only where there are
    # any: a finalisation where every candidate raced — the common case by far — asks nothing.
    # The question is bounded by the candidates too, because the rest of the query cannot be:
    # `round_id != ?` is not a predicate an index can serve, so this scans `session_results`
    # (one row per session, four to a round) across every season the league has ever run.
    # `profile_id IN (...)` keeps that scan proportional to the round rather than the history.
    to_ask = sorted(candidates - raced_here)
    raced_elsewhere: set[int] = set()
    if to_ask:
        marks = ", ".join("?" for _ in to_ask)
        cursor = await db.execute(
            f"SELECT DISTINCT profile_id FROM ({_RACED_A_ROUND_SQL}) "  # noqa: S608
            f"WHERE round_id != ? AND profile_id IN ({marks})",
            (round_id, *to_ask),
        )
        raced_elsewhere = {r["profile_id"] for r in await cursor.fetchall()}

    await set_former_drivers_on(
        db, {p: p in raced_here or p in raced_elsewhere for p in candidates}
    )


async def rounds_made_final_on(db: aiosqlite.Connection, round_ids: Iterable[int]) -> None:
    """Mark the drivers who raced each of *round_ids* as former drivers, on the save's connection.

    Results' part of the rounds a closer of core's made FINAL (`rounds_made_final`): core hands
    the rounds it closed, when results is switched off and when a season is cancelled, and each
    round is recomputed as :func:`recompute_former_drivers_for_round` does it. Commits nothing.
    """
    for round_id in round_ids:
        await recompute_former_drivers_for_round(db, round_id)


async def _save_session_result_in_tx(
    db,
    round_id: int,
    division_id: int,
    session_type: SessionType,
    status: str,
    config_name: str | None,
    submitted_by: int | None,
    driver_rows: list[dict],
    fl_driver_override: int | None = None,
    *,
    now: datetime,
) -> int:
    """Save one session's results with its driver rows on the caller's connection, committing nothing.
    The session is recorded as submitted at *now*, the queue's clock handed in by the caller.

    A resubmission's swap writes every session of a round in the same transaction as the delete
    of the results they replace.
    """
    from leaguebot.core.services.seasons.season_service import SeasonImmutableError
    from leaguebot.core.services.drivers.driver_service import resolve_driver_profile_id

    submitted_at = now.isoformat()
    cursor = await db.execute(
        """
        SELECT s.status AS season_status
        FROM rounds r
        JOIN divisions d ON d.id = r.division_id
        JOIN seasons s ON s.id = d.season_id
        WHERE r.id = ?
        """,
        (round_id,),
    )
    season_row = await cursor.fetchone()
    if season_row and season_row["season_status"] == "COMPLETED":
        raise SeasonImmutableError(
            f"Round {round_id} belongs to an archived season — results cannot be submitted."
        )

    cursor = await db.execute(
        """
        INSERT INTO session_results
            (round_id, division_id, session_type, status, config_name,
             submitted_by, submitted_at, fl_driver_override)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            round_id,
            division_id,
            session_type.value,
            status,
            config_name,
            submitted_by,
            submitted_at,
            fl_driver_override,
        ),
    )
    session_result_id = cursor.lastrowid
    _profile_id_map: dict[int, int | None] = {}
    for row in driver_rows:
        driver_profile_id: int | None = None
        if season_row is not None:
            driver_profile_id = await resolve_driver_profile_id(
                row["driver_user_id"], db
            )
        _profile_id_map[row["driver_user_id"]] = driver_profile_id
    # **Submitting a result marks nobody a former driver** (#216). The round is still awaiting
    # its verdicts, so this classification is provisional: a driver in it may yet be taken out
    # by a resubmission or an appeal. The flag is set from the round's *final* results, by
    # `recompute_former_drivers_for_round`, at the moment the round becomes FINAL.
    await _insert_new_tables_in_tx(db, session_result_id, session_type, driver_rows, _profile_id_map)
    return session_result_id


# ---------------------------------------------------------------------------
# Config selection view
# ---------------------------------------------------------------------------

class _ConfigSelectView(LeagueView):
    """Button view for selecting an attached points config.

    **A league manager's, and it asked nothing at all until 2026-09-10.** This view is
    posted publicly into the submission and amendment channels — never ephemerally — from
    two call sites, so every member who could read one of those channels could decide
    which points configuration scored the session, and the first press won the race against
    whoever was actually running the round.

    Choosing how a session is scored is running the league, so it asks the league manager
    tier, the same as the commands that attach a configuration in the first place.

    **Every press is recorded** (#482): a refusal as one refusal line, a choice as one line
    naming the member, the configuration and the session. A press, not a paste — the pastes
    around it are out of the rule's reach. *session* says which session of which round the
    choice is for ("the Feature Race of round 3 (Pro)"), and every caller passes it, so a league
    reads it in each line. It is optional so that a view built without one still names its
    button; the lines then say nothing of the session.

    **The first press chooses** (results spec, "Submitting round results"). It claims the
    choice (`selected`) before anything is awaited, so a later press — made while the first
    press's check or edit is still on its way, or on a client still showing the buttons — is
    refused by the view itself, seen by its presser alone, naming the configuration claimed, and
    never edits the message. It is told the configuration is being chosen while the choice is
    still being made, and that it has been chosen once it is (`_chosen`): the edit landed and the
    queue took the choice. The message the press edits names the press's choice, and is put back
    to the picker — its text, kept when the view is built, and its buttons — where the queue then
    refuses the choice or fails on it.

    The claim is released wherever the choice is not made, a fault included: where the queue's
    check fails or refuses the choice, where the edit fails, and where the queue's ask, after the
    edit, refuses the choice or fails. The check is made before the edit, so that a press the
    queue would refuse (an amendment's, pressed after its five minutes) leaves the message alone;
    the refusal is the queue's own, its ask checking again. The claim is released only once the
    message is put back, so that no press chooses while the picker is being restored over it.
    A put-back that fails is logged on the host and changes nothing else (`_put_back`): a refusal
    stays the presser's one reply, and a fault in the ask is still the one the press raises.

    *ask* is the queue, the change's kind and its payload, the queue being the one its builder was
    handed, which the view never looks up on the bot.

    *router* is the output router the press's log line is written through, handed by the builder
    as the queue is, and not looked up on the bot.

    *text* is required with *ask*, as only a submission's picker is put back and the text it was
    posted with is what it is put back to.
    """

    def __init__(
        self,
        config_names: list[str],
        config: Any | None = None,
        *,
        session: str | None = None,
        ask: tuple[ChangeQueue, str, dict[str, Any]] | None = None,
        text: str | None = None,
        router: OutputRouter,
    ) -> None:
        if ask is not None and text is None:
            raise TypeError("a picker asking the queue needs the text it is put back to")
        super().__init__(timeout=None)
        #: The configuration claimed by the press in hand, or chosen.
        self.selected: str | None = None
        #: Whether the claim is a choice made, not one still being made.
        self._chosen = False
        #: The text the picker is posted with, which a press puts back.
        self._text = text
        self._config = config
        self._session = session
        self._ask = ask
        self._router = router
        for name in config_names:
            async def _cb(
                interaction: discord.Interaction,
                _name: str = name,
            ) -> None:
                what = self._what(_name)
                if not self._may_choose(interaction):
                    await refuse(
                        interaction,
                        "⛔ Only league managers can choose the points configuration.",
                        what=what,
                        reason="Only league managers can choose the points configuration.",
                    )
                    return
                if self.selected is not None:
                    state = "has already been chosen" if self._chosen else "is being chosen"
                    await refuse(
                        interaction,
                        "⚠️ The points configuration for "
                        f"{self._session or 'this session'} {state}: **{self.selected}**.",
                        what=what,
                    )
                    return
                # Claimed before anything is awaited, so that no second press gets past the
                # check above while this one is in hand.
                self.selected = _name
                if self._ask is not None:
                    # A submission's picker: the choice is a change of its own, asked of the
                    # queue with the pasted session's payload, and the picker comes down by the
                    # press's own edit of the message pressed, which is a reply
                    # (docs/design/architecture.md, "Posting to Discord").
                    queue, kind, asked = self._ask
                    pressed = interaction.created_at
                    payload = {
                        **asked, "config_name": _name,
                        # When it was pressed, which an amendment's five minutes are judged by
                        # however long the choice's turn waits.
                        "typed_at": pressed.isoformat() if isinstance(pressed, datetime) else None,
                        "choice_line": (
                            f"{interaction_member(interaction)} | {what} | Success\n"
                            f"  configuration: {_name}"
                        ),
                    }
                    # Whether the choice was made. Until it is, the claim is released on every
                    # way out, a fault included: the buttons are still up, or put back, and
                    # nothing was asked, so another press may choose.
                    chosen = False
                    try:
                        if (await queue.check(kind, payload)).kind is not VerdictKind.GO:
                            # The message is left offering the configurations, and the queue's
                            # ask, checking again, refuses the press privately.
                            chosen = await queue.ask(
                                kind, payload, interaction=interaction, what=what
                            ) is not None
                            return
                        await interaction.response.edit_message(
                            content=f"🔧 Config selected: **{_name}**", view=None
                        )
                        try:
                            chosen = await queue.ask(
                                kind, payload, interaction=interaction, what=what
                            ) is not None
                        except Exception:
                            await self._put_back(interaction)
                            raise
                        if not chosen:
                            await self._put_back(interaction)
                        return
                    finally:
                        # Released only once the message is put back, so that no press chooses
                        # while the picker is being restored over its edit.
                        if chosen:
                            self._chosen = True
                        else:
                            self.selected = None
                self._chosen = True
                self.stop()
                await interaction.response.defer()
                await self._record_choice(interaction, what, _name)

            button = CallbackButton(
                label=name[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"config_sel_{name[:60]}",
                on_press=_cb,
            )
            self.add_item(button)

    async def _put_back(self, interaction: discord.Interaction) -> None:
        """Edit the message pressed back to the picker, its text and its buttons, where the
        press's edit took them down but the queue did not take the choice.

        Never raises: the press has already been answered, by a refusal, or is failing on the
        fault of its ask, and a failed put-back must neither give the presser a second reply nor
        stand in that fault's place. It is logged on the host with its traceback, and the
        message stays as the press's edit left it. Pinned by
        `test_a_put_back_that_fails_keeps_the_original_fault_in_the_report` and
        `test_a_put_back_that_fails_after_a_refusal_gives_no_second_reply`.
        """
        try:
            await interaction.edit_original_response(content=self._text, view=self)
        except Exception:  # noqa: BLE001 — the press is answered already; this must not answer it
            message = getattr(interaction, "message", None)
            log.error(
                "could not put the points-configuration picker back on message %s; it is left "
                "without its buttons", getattr(message, "id", None), exc_info=True,
            )

    def _what(self, name: str) -> str:
        """The button as the log channel names it, with the choice it belongs to."""
        named = f"the “{name}” button of the points-configuration choice"
        return f"{named} for {self._session}" if self._session else named

    async def _record_choice(
        self, interaction: discord.Interaction, what: str, name: str
    ) -> None:
        """Write the one line a choice leaves in the log channel, in the success form."""
        await self._router.post_log(
            f"{interaction_member(interaction)} | {what} | Success\n"
            f"  configuration: {name}"
        )

    def _may_choose(self, interaction: discord.Interaction) -> bool:
        """Whether the presser holds the league manager tier.

        Refuses when the server configuration could not be read: a view that cannot tell who
        is pressing must not guess in the permissive direction.
        """
        if self._config is None or not isinstance(interaction.user, discord.Member):
            return False
        return is_league_manager(self._config, interaction.user)


# ---------------------------------------------------------------------------
# Resubmission flow — triggered by the 🔄 Resubmit Initial Results button
# ---------------------------------------------------------------------------


def _row_dict_from_qualifying(row: ParsedQualifyingRow) -> dict:
    return {
        "driver_user_id": row.driver_user_id,
        "team_instance_id": row.team_instance_id,
        "finishing_position": row.position,
        "outcome": row.outcome.value,
        "tyre": row.tyre,
        "best_lap": row.best_lap,
        "gap": row.gap,
    }


def _row_dict_from_race(row: ParsedRaceRow) -> dict:
    fl = row.fastest_lap if row.fastest_lap.upper() != "N/A" else None
    ip = row.ingame_penalties if row.ingame_penalties.upper() != "N/A" else None
    return {
        "driver_user_id": row.driver_user_id,
        "team_instance_id": row.team_instance_id,
        "finishing_position": row.position,
        "outcome": row.outcome.value,
        "total_time": row.total_time,
        "fastest_lap": fl,
        "ingame_penalties": ip,
    }


async def _insert_new_tables_in_tx(
    db,
    session_result_id: int,
    session_type: SessionType,
    rows: list[dict],
    profile_id_map: dict[int, int | None],
) -> None:
    """Insert rows into qualifying_session_results or race_session_results.

    Must be called inside an open transaction; caller is responsible for commit.
    *rows* must be plain dicts with keys: driver_user_id, team_instance_id,
    finishing_position, outcome, and for qualifying: tyre, best_lap;
    for race: total_time, ingame_penalties, fastest_lap.

    A row is inserted with no post-race or appeal sanction. Those are applied by the review
    stages, as records carrying their justification and author (#345); a classification
    carries only what the game itself imposed.
    """
    if session_type.is_qualifying:
        for row in rows:
            uid: int = row["driver_user_id"]
            await db.execute(
                """
                INSERT INTO qualifying_session_results
                    (session_result_id, driver_user_id, team_instance_id, finishing_position,
                     outcome, tyre, best_lap, points_awarded, driver_profile_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_result_id,
                    uid,
                    row["team_instance_id"],
                    row["finishing_position"],
                    row.get("outcome", OutcomeModifier.CLASSIFIED.value),
                    row.get("tyre"),
                    row.get("best_lap"),
                    row.get("points_awarded", 0),
                    profile_id_map.get(uid),
                ),
            )
    else:
        # Compute base_time_ms: P1 absolute time determines reference
        p1_abs_ms: int | None = None
        for row in sorted(rows, key=lambda r: r["finishing_position"]):
            tt = row.get("total_time") or ""
            if parse_time(tt) is not None:
                p1_abs_ms = _parse_time_to_ms(tt)
                break

        for row in rows:
            uid = row["driver_user_id"]
            outcome_str = row.get("outcome", OutcomeModifier.CLASSIFIED.value)
            outcome = OutcomeModifier(outcome_str)
            total_time = row.get("total_time") or ""

            ip_str = row.get("ingame_penalties")
            ingame_ms = _parse_time_to_ms(ip_str) if ip_str else 0
            total_penalty_ms = ingame_ms

            base_time_ms: int | None = None
            laps_behind: int | None = None

            if outcome in (OutcomeModifier.DNF, OutcomeModifier.DNS):
                pass  # base_time_ms stays None
            elif parse_lap_gap(total_time) is not None:
                m = re.search(r"(\d+)", total_time)
                laps_behind = int(m.group(1)) if m else 1
            elif parse_time(total_time) is not None:
                # Store base time even for DSQ (penalties may be overturned on appeal)
                base_time_ms = _parse_time_to_ms(total_time) - total_penalty_ms
            elif parse_gap(total_time) is not None and p1_abs_ms is not None:
                delta_ms = _parse_time_to_ms(total_time)
                base_time_ms = p1_abs_ms + delta_ms - total_penalty_ms

            fl = row.get("fastest_lap")
            await db.execute(
                """
                INSERT INTO race_session_results
                    (session_result_id, driver_user_id, team_instance_id, finishing_position,
                     outcome, base_time_ms, laps_behind,
                     ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms,
                     fastest_lap, fastest_lap_bonus, points_awarded, driver_profile_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_result_id,
                    uid,
                    row["team_instance_id"],
                    row["finishing_position"],
                    outcome_str,
                    base_time_ms,
                    laps_behind,
                    ingame_ms,
                    0,
                    0,
                    fl,
                    row.get("fastest_lap_bonus", 0),
                    row.get("points_awarded", 0),
                    profile_id_map.get(uid),
                ),
            )


@dataclass
class CollectedSession:
    """One session of a resubmission, validated and held until the last session is in."""

    session_type: SessionType
    status: str
    config_name: str | None
    submitted_by: int | None
    driver_rows: list[dict] = field(default_factory=list)
    fl_driver_override: int | None = None


async def replace_round_results_on(
    db: aiosqlite.Connection,
    round_id: int,
    division_id: int,
    season_id: int,
    collected: list[CollectedSession],
    *,
    now: datetime,
) -> list[int]:
    """Replace every session of a round with a resubmission's on the connection handed, committing
    nothing: the resubmission's swap does it in a save of its own (#439).

    A resubmission supersedes the round's results: the ones already submitted stand, published and
    counted, until every session has been entered again (issue #210), so the delete of the old
    results, the insert of the new, their points and the clearing of the channel's `resubmitting`
    flag land together or not at all. `results_posted` is cleared with them: the round is still in
    penalty review, so the new results go out then. Raises `SeasonImmutableError` for an archived
    season, with the old results untouched.

    The table posted for an old session is carried onto the new session of the same type, as the
    message it replaces: the posting then takes the old table down once the new one stands, so an
    amended table replaces the original rather than standing beside it (results spec :412). The
    message ids of an old session whose type the resubmission does not enter are returned, for the
    caller to take down; they are otherwise lost with the old row. *now* is the time the new
    sessions are recorded as submitted at, the queue's clock handed in by the swap."""
    earlier = {
        each["session_type"]: each
        for each in await (await db.execute(
            "SELECT session_type, results_message_id, results_message_ids FROM session_results "
            "WHERE round_id = ? AND results_message_id IS NOT NULL", (round_id,),
        )).fetchall()
    }
    await db.execute("DELETE FROM session_results WHERE round_id = ?", (round_id,))
    for session in collected:
        session_result_id = await _save_session_result_in_tx(
            db, round_id, division_id, session.session_type, session.status,
            session.config_name, session.submitted_by, session.driver_rows,
            fl_driver_override=session.fl_driver_override, now=now,
        )
        if session.config_name is not None:
            await _apply_points_in_tx(
                db, session_result_id, season_id, session.config_name,
                session.session_type,
            )
        carried = earlier.pop(session.session_type.value, None)
        if carried is not None:
            await db.execute(
                "UPDATE session_results SET results_message_id = ?, results_message_ids = ? "
                "WHERE id = ?",
                (carried["results_message_id"], carried["results_message_ids"], session_result_id),
            )
    await db.execute(
        "UPDATE round_submission_channels SET resubmitting = 0, results_posted = 0, "
        "resubmit_prompt_message_id = NULL WHERE round_id = ?",
        (round_id,),
    )
    unplaced: list[int] = []
    for each in earlier.values():
        ids = _parse_ids(each["results_message_ids"]) or [int(each["results_message_id"])]
        unplaced.extend(ids)
    return unplaced


async def resubmission_drafts_on(
    db: aiosqlite.Connection, round_id: int
) -> list[CollectedSession]:
    """The sessions a round's resubmission holds as drafts, in running order, read on the
    connection handed. A draft kept for a season with no configuration to choose
    (`NO_CONFIG`) gives none."""
    row = await (await db.execute("SELECT format FROM rounds WHERE id = ?", (round_id,))).fetchone()
    if row is None:
        return []
    drafts = {
        found["session_type"]: found
        for found in await (
            await db.execute(
                "SELECT session_type, status, rows_json, config_name, submitted_by "
                "FROM resubmission_drafts WHERE round_id = ?",
                (round_id,),
            )
        ).fetchall()
    }
    collected: list[CollectedSession] = []
    for session in get_sessions_for_format(RoundFormat(row["format"])):
        found = drafts.get(session.value)
        if found is None:
            continue
        held = _json.loads(found["rows_json"])
        collected.append(CollectedSession(
            session, found["status"], found["config_name"] or None, found["submitted_by"],
            list(held["rows"]), held["fl_override"],
        ))
    return collected


class ResubmissionCancelView(LeagueView):
    """The **Cancel** button on a resubmission's announcement.

    Pressing it ends the resubmission and keeps the round's earlier results, which were never
    touched: nothing is written until the last session is in. The press asks the queue for the
    round's review back (`results.review.open`, returning as cancelled), answered privately; the
    review's own jobs end the resubmission and take the button down. Not persistent — a restart
    ends the resubmission itself, and the restart sweep takes the button down.

    The *state* is the review the button belongs to (`resubmission_changes.ReviewRef`, which also
    hands the queue; a bare review state stands in for it where a press is refused).
    """

    def __init__(self, state) -> None:
        import asyncio

        super().__init__(timeout=None)
        self.state = state
        self.cancelled_by: int | None = None
        self.message: discord.Message | None = None
        # Waited on instead of `View.wait()`. Every paste cancels the wait it lost the race
        # to, and cancelling a task parked in `View.wait()` cancels the view's own stopped
        # future with it, after which every later wait raises at once. Cancelling a wait on
        # an Event leaves the Event as it was.
        self.pressed = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self.cancelled_by is not None

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from leaguebot.results.services.review.penalty_wizard import _button, _require_lm

        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        from leaguebot.results.services.submission.resubmission_changes import ask_resubmission_cancel

        if await ask_resubmission_cancel(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            self.cancelled_by = interaction.user.id
            self.pressed.set()
            self.stop()


CANCELLED_LINE = "↩️ **Resubmission cancelled.** The earlier results stand."


@posts_in_a_job
async def take_down_cancel_message(message: discord.Message | discord.PartialMessage) -> None:
    """Take the Cancel button off the resubmission's announcement; one already gone is no fault.

    Called by the review's job (#439). An edit
    Discord refuses otherwise raises `StepFailedOnDiscord`, for the job to stop the queue on.
    """
    try:
        await edit_in_owned_channel(message, mentions=discord.AllowedMentions.none(), view=None)
    except discord.NotFound:
        pass
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(
            "the Cancel button could not be taken off the resubmission's announcement"
        ) from exc


@posts_in_a_job
async def send_cancelled_line(sub_channel: discord.TextChannel) -> None:
    """Tell the submission channel that a resubmission was cancelled and the earlier results
    stand. The one place it is sent, for the review's job."""
    await post_in_owned_channel(
        sub_channel, CANCELLED_LINE, mentions=discord.AllowedMentions.none()
    )


