"""result_submission_service.py — Round result submission wizard and channel management."""
from __future__ import annotations

import json as _json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Mapping, NamedTuple, TypeVar

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import ChangeOrigin
from leaguebot.results.models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from leaguebot.core.models.round import ROUND_CANCELLABLE, ROUND_TERMINAL, RoundFormat, RoundStatus
from leaguebot.results.models.session_result import DriverSessionResult, OutcomeModifier  # DriverSessionResult kept as DTO for compute_points_for_session
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.services.team_service import resolve_team_reference
from leaguebot.results.utils import results_formatter
from leaguebot.core.utils.channel_guard import is_league_manager
from leaguebot.core.utils.input_validator import (
    USER_MENTION,
    parse_gap,
    parse_lap_gap,
    parse_role_mention,
    parse_time,
    parse_user_mention,
)
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.image.utils.tyre_compound import (
    canonicalise_tyre,
    records_no_tyre,
    tyre_compound_list,
)
from leaguebot.core.utils.interaction_errors import describe_fault, report_failure
from leaguebot.core.utils.league_server import CallbackButton, LeagueView, league_guild
from leaguebot.core.utils.log_lines import name_of_member, record_abandoned, refuse
from leaguebot.core.utils.member_names import interaction_member

if TYPE_CHECKING:
    from leaguebot.results.services.penalty_wizard import PenaltyReviewState

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

async def create_submission_channel(
    guild: discord.Guild,
    division_name: str,
    season_number: int,
    round_number: int,
    round_id: int,
    db_path: str,
    *,
    bot_cmd_channel_id: int | None = None,
    interaction_role: discord.Role | None = None,
    league_admin_role: discord.Role | None = None,
) -> discord.TextChannel:
    """Create a transient text channel for result submission.

    The channel is named S{season_number}-{slug}-R{round_number}-results, placed in the bot
    command channel's category, and opened to both tiers of the league — the interaction
    role and the league admin role. Every button of the penalty and appeals reviews lives in
    here, so a league admin who does not also hold the interaction role would otherwise be
    shut out of a round they are entitled to judge (issue #116).
    """
    slug = _make_slug(division_name)
    name = f"S{season_number}-{slug}-R{round_number}-results"

    # Determine category from the bot command channel (if available)
    category: discord.CategoryChannel | None = None
    if bot_cmd_channel_id is not None:
        cmd_channel = guild.get_channel(bot_cmd_channel_id)
        if cmd_channel is not None:
            category = getattr(cmd_channel, "category", None)

    # Deny @everyone; grant the bot itself and both of the league's tiers
    overwrites: dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
    }
    bot_member = guild.me
    if bot_member is not None:
        overwrites[bot_member] = discord.PermissionOverwrite(
            read_messages=True, send_messages=True, manage_messages=True
        )
    for role in (interaction_role, league_admin_role):
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            )

    channel = await guild.create_text_channel(
        name=name,
        category=category,
        overwrites=overwrites,
        reason="Results submission channel created by bot",
    )
    created_at = datetime.now(timezone.utc).isoformat()
    async with get_connection(db_path) as db:
        # Remove any previous row for this round (e.g. a prior closed submission or
        # an orphaned row from a bot restart) so the INSERT never hits the UNIQUE constraint.
        await db.execute(
            "DELETE FROM round_submission_channels WHERE round_id = ?",
            (round_id,),
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, closed) "
            "VALUES (?, ?, ?, 0)",
            (round_id, channel.id, created_at),
        )
        await db.commit()
    return channel


async def close_submission_channel(
    channel_id: int,
    round_id: int,
    guild: discord.Guild,
    db_path: str,
) -> bool:
    """Mark the submission channel closed in the DB then delete it from Discord.

    Returns whether the channel is gone, as `_close_amend_channel_record` does.

    **Both channel tables are cleared** (#345). A first pass through review runs in a
    submission channel, recorded in ``round_submission_channels``; an amendment replays the
    same stages in an amend channel, recorded in ``round_amend_channels``. This closes either,
    because the appeals stage reaches it by the same path whichever it was — and a row left
    behind for a channel that no longer exists would have restart recovery announce an
    abandoned amendment that in fact completed.
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1 WHERE round_id = ?",
            (round_id,),
        )
        await db.commit()

    # **The amendment's record goes only once its channel has** (#345); see
    # `_close_amend_channel_record`. A first pass has no such record, and its channel is simply
    # deleted.
    channel = guild.get_channel(channel_id) if guild is not None else None
    return await _close_amend_channel_record(
        db_path, round_id, channel_id, channel, reason="Results submission complete"
    )


async def _close_amend_channel_record(
    db_path: str, round_id: int, channel_id: int, channel, *, reason: str
) -> bool:
    """Close an amendment's record, delete its channel, and forget the record once it is gone.

    **Marked closed first, forgotten only once the channel is gone** (#345). Closed, the record
    holds nothing — no division, no deadline — and restart recovery still finds the channel by
    it. Forgotten first, a channel out of cache, one the bot may no longer delete, or a crash
    between the two left a private channel with its stage prompts standing and nothing anywhere
    naming it.

    **Scoped by the channel as well as the round.** A closed record no longer holds the round,
    so a fresh amendment of it may take its place while this channel's delete is still awaited;
    matching on the round alone would then forget the fresh one.

    *channel* is None where it could not be reached; the record is then kept, closed. A channel
    with no amendment record — a first pass's — is deleted just the same.

    Returns whether the channel is gone: True where it was deleted or was already gone
    (`NotFound`), False where it could not be reached or could not be deleted, so a caller can
    tell a failure from a success.
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_amend_channels SET closed_at = ? WHERE round_id = ? AND channel_id = ?",
            (datetime.now(timezone.utc).isoformat(), round_id, channel_id),
        )
        await db.commit()
    if channel is None:
        log.warning(
            "channel %s of round %s is unreachable; any amendment record naming it is kept, "
            "closed, for restart recovery", channel_id, round_id,
        )
        return False
    from leaguebot.results.services.results_post_service import delete_round_channel

    try:
        await delete_round_channel(channel, reason=reason)
    except discord.NotFound:
        pass
    except discord.HTTPException:
        log.exception("could not delete channel %s of round %s", channel_id, round_id)
        return False
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM round_amend_channels WHERE round_id = ? AND channel_id = ?",
            (round_id, channel_id),
        )
        await db.commit()
    return True


async def is_submission_open(db_path: str, round_id: int) -> bool:
    """Return True if a submission channel for this round exists and is not yet closed."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT closed FROM round_submission_channels WHERE round_id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    return row is not None and row["closed"] == 0


async def is_channel_in_penalty_review(db_path: str, channel_id: int) -> bool:
    """Return True if *channel_id* belongs to an open submission channel in
    penalty-review state (all sessions submitted/cancelled, round not yet finalized).

    A channel resubmitting is not: the round is still in review, but the manager is pasting
    its results again and the guard would delete every paste.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT rsc.round_id
            FROM round_submission_channels rsc
            JOIN rounds r ON r.id = rsc.round_id
            WHERE rsc.channel_id = ?
              AND rsc.closed = 0
              AND rsc.in_penalty_review = 1
              AND rsc.resubmitting = 0
              AND r.status != 'FINAL'
            """,
            (channel_id,),
        )
        row = await cursor.fetchone()
    return row is not None


async def _build_penalty_review_state(
    bot: LeagueBot,
    round_id: int,
    division_id: int,
    submission_channel_id: int,
) -> "PenaltyReviewState":
    """Reconstruct a :class:`~leaguebot.results.services.penalty_wizard.PenaltyReviewState` from the DB.

    Used by the bot restart-recovery path to re-post the appeals review prompt
    after a crash during the appeals phase (``status = 'AWAITING_APPEAL_VERDICTS'``).
    """
    from leaguebot.results.services.penalty_wizard import PenaltyReviewState

    db_path: str = bot.db_path

    async with get_connection(db_path) as db:
        ctx_cursor = await db.execute(
            """
            SELECT r.round_number, d.name AS division_name
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        ctx = await ctx_cursor.fetchone()

        sr_cursor = await db.execute(
            "SELECT session_type FROM session_results WHERE round_id = ? AND status = 'ACTIVE'",
            (round_id,),
        )
        sr_rows = await sr_cursor.fetchall()

    round_number: int = ctx["round_number"] if ctx else 0
    division_name: str = ctx["division_name"] if ctx else ""

    _stype_order = list(SessionType)
    session_types_present = sorted(
        [SessionType(r["session_type"]) for r in sr_rows],
        key=lambda s: _stype_order.index(s),
    )

    return PenaltyReviewState(
        round_id=round_id,
        division_id=division_id,
        submission_channel_id=submission_channel_id,
        session_types_present=session_types_present,
        db_path=db_path,
        bot=bot,
        round_number=round_number,
        division_name=division_name,
    )


async def send_review_prompt(
    bot: LeagueBot, sub_channel: discord.TextChannel, state: "PenaltyReviewState"
) -> discord.Message:
    """Post the penalty review prompt of *state* in *sub_channel* and register its view, so that
    its buttons keep working across a restart. Gives the message; *state* learns its id.

    The one place the prompt is sent, which `enter_penalty_state` and the review's
    `post_review_prompt` job both call (#439).
    """
    from leaguebot.results.services.penalty_wizard import PenaltyReviewView, _render_prompt_content

    view = PenaltyReviewView(state=state)
    content = await _render_prompt_content(state)
    msg = await sub_channel.send(content, view=view)
    state.prompt_message_id = msg.id
    bot.add_view(view, message_id=msg.id)
    return msg


async def enter_penalty_state(
    bot: LeagueBot,
    guild: discord.Guild,
    round_id: int,
    division_id: int,
    sub_channel: discord.TextChannel,
    *,
    season_id: int | None = None,
    skip_results_post: bool = False,
    is_resubmission: bool = False,
) -> None:
    """Transition the submission channel to post-round penalty-review state.

    Steps performed:
    1. Mark the submission channel row as ``in_penalty_review = 1`` in the DB
       *before* any results posting so that a crash during posting is seen as a
       penalty-review orphan (not a mid-submission orphan) on the next restart.
    2. (Unless *skip_results_post*) Compute standings, post interim results and
       standings to the configured division channels; then set ``results_posted = 1``.
    3. Post a :class:`PenaltyReviewView` prompt to *sub_channel*.
    4. Register the view with the bot for persistent interaction routing.

    When called from restart-recovery, pass ``skip_results_post=True`` only when
    ``results_posted = 1`` in the DB (i.e. posting already completed before the crash).
    """
    from leaguebot.results.services import standings_service, results_post_service  # lazy imports
    from leaguebot.results.services.penalty_wizard import PenaltyReviewState

    db_path: str = bot.db_path

    # ------------------------------------------------------------------
    # Fetch round context
    # ------------------------------------------------------------------
    async with get_connection(db_path) as db:
        rnd_cursor = await db.execute(
            """
            SELECT r.round_number, r.track_name, d.name AS division_name,
                   d.season_id, drc.results_channel_id, drc.standings_channel_id,
                   drc.reserves_in_standings
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        ctx = await rnd_cursor.fetchone()

    if ctx is None:
        log.error("enter_penalty_state: round %s not found", round_id)
        return

    round_number: int = ctx["round_number"]
    track_name: str = ctx["track_name"] or "Unknown"
    division_name: str = ctx["division_name"]
    if season_id is None:
        season_id = ctx["season_id"]

    results_ch_id: int | None = ctx["results_channel_id"]
    standings_ch_id: int | None = ctx["standings_channel_id"]
    show_reserves: bool = bool(ctx["reserves_in_standings"]) if ctx["reserves_in_standings"] is not None else True

    # ------------------------------------------------------------------
    # Step 1a: Mark in_penalty_review BEFORE any results posting so that
    # a crash during posting is detected as a penalty-review orphan (not a
    # mid-submission orphan) on the next restart.  This prevents the
    # recovery path from deleting the session_results and skipping the round.
    # ------------------------------------------------------------------
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET in_penalty_review = 1 WHERE round_id = ?",
            (round_id,),
        )
        # The results are in, so the round now waits on report verdicts — and from this point it
        # can no longer be cancelled, because the drivers have reports and appeals to lodge and
        # calling the round off would take that from them. Written in the same transaction as the
        # flag above, and for the same reason: a crash between the two would leave the round and
        # its channel contradicting each other.
        #
        # Guarded to the states before it, so a resubmission cannot drag a round that has already
        # reached appeals, or ended, backwards.
        await db.execute(
            f"UPDATE rounds SET status = ? WHERE id = ? AND status IN ({_CANCELLABLE_SQL})",
            (RoundStatus.AWAITING_REPORT_VERDICTS.value, round_id),
        )
        await db.commit()

    # ------------------------------------------------------------------
    # Step 1b: Compute and post interim results + standings
    # ------------------------------------------------------------------
    if not skip_results_post:
        try:
            # The names the standings below are posted under also order the snapshot, so
            # the stored classification and the one the league is shown cannot disagree.
            _names = await results_post_service.standings_display_names(
                db_path, division_id, guild, bot
            )
            await standings_service.compute_and_persist_round(
                db_path, round_id, division_id, _names
            )

            if results_ch_id:
                rc = as_text_channel(guild.get_channel(results_ch_id))
                if rc:
                    _results_label = "Provisional Results (amended)" if is_resubmission else "Provisional Results"
                    await results_post_service.post_round_results(
                        db_path, round_id, division_id, rc, guild,
                        label=_results_label, bot=bot,
                    )

            if standings_ch_id:
                sc = as_text_channel(guild.get_channel(standings_ch_id))
                if sc:
                    from leaguebot.results.services.standings_service import (
                        compute_driver_standings,
                        compute_team_standings,
                    )

                    driver_snaps = await compute_driver_standings(
                        db_path, division_id, round_id, _names
                    )
                    team_snaps = await compute_team_standings(db_path, division_id, round_id)
                    _standings_label = "Provisional Results (amended)" if is_resubmission else "Provisional Results"
                    await results_post_service.post_standings(
                        db_path, division_id, round_id, round_number, track_name,
                        sc, driver_snaps, team_snaps, guild, show_reserves,
                        label=_standings_label, bot=bot,
                    )
            async with get_connection(db_path) as db:
                await db.execute(
                    "UPDATE round_submission_channels SET results_posted = 1 WHERE round_id = ?",
                    (round_id,),
                )
                await db.commit()
        except Exception:
            log.exception(
                "enter_penalty_state: error posting interim results for round %s", round_id
            )

    # ------------------------------------------------------------------
    # Step 2: Query non-cancelled session types for PenaltyReviewState
    # ------------------------------------------------------------------
    async with get_connection(db_path) as db:
        sr_cursor = await db.execute(
            "SELECT session_type FROM session_results WHERE round_id = ? AND status = 'ACTIVE'",
            (round_id,),
        )
        sr_rows = await sr_cursor.fetchall()

    _stype_order = list(SessionType)
    session_types_present = sorted(
        [SessionType(r["session_type"]) for r in sr_rows],
        key=lambda s: _stype_order.index(s),
    )

    # ------------------------------------------------------------------
    # Step 3: Build state and post the penalty review prompt
    # ------------------------------------------------------------------
    state = PenaltyReviewState(
        round_id=round_id,
        division_id=division_id,
        submission_channel_id=sub_channel.id,
        session_types_present=session_types_present,
        db_path=db_path,
        bot=bot,
        round_number=round_number,
        division_name=division_name,
    )

    msg = await send_review_prompt(bot, sub_channel, state)

    # Persist the prompt message ID so recovery can delete it before reposting.
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET prompt_message_id = ? WHERE round_id = ?",
            (msg.id, round_id),
        )
        await db.commit()

    log.info(
        "enter_penalty_state: penalty review prompt posted for round %s (msg=%s)",
        round_id,
        msg.id,
    )


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
    transaction as whatever made the round final.

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

    for profile_id in sorted(candidates):
        should_be_former = profile_id in raced_here or profile_id in raced_elsewhere
        await db.execute(
            "UPDATE driver_profiles SET former_driver = ? WHERE id = ? AND former_driver != ?",
            (int(should_be_former), profile_id, int(should_be_former)),
        )


async def send_appeals_prompt(
    bot: LeagueBot, sub_channel: discord.TextChannel, state: "PenaltyReviewState"
) -> discord.Message:
    """Post the appeals review prompt of *state* in *sub_channel* and register its view, so that
    its buttons keep working across a restart. Gives the message; *state* learns its id.

    The one place the appeals prompt is sent, which `_post_appeals_prompt` and the queue's
    `post_appeals_prompt` job both call (#439).
    """
    from leaguebot.results.services.penalty_wizard import AppealsReviewView, _render_appeals_prompt_content

    appeals_view = AppealsReviewView(state=state)
    content = await _render_appeals_prompt_content(state)
    msg = await sub_channel.send(content, view=appeals_view)
    state.appeals_prompt_message_id = msg.id
    bot.add_view(appeals_view, message_id=msg.id)
    return msg


async def _insert_appeal_records_on(
    db: aiosqlite.Connection,
    staged_appeals: list,
    applied_pr: list[dict],
    actor_id: int,
    *,
    now: datetime,
) -> list[dict]:
    """Write one ``appeal_records`` row per upheld correction on *db*, committing nothing.

    *applied_pr* are the penalty records the corrections were applied as, in the order the
    corrections were staged. Each appeal keeps its own author and time where it was read back
    from the round (#345), and is given *now* otherwise. Returns the records written, shaped as
    the verdict announcement reads them.
    """
    records: list[dict] = []
    now_str = now.isoformat()
    for sp, pr in zip(staged_appeals, applied_pr):
        race_result_id = pr.get("race_result_id")
        qual_result_id = pr.get("qual_result_id")
        cursor = await db.execute(
            """
            INSERT INTO appeal_records (
                race_result_id, qual_result_id,
                status, penalty_type, time_seconds,
                description, justification, submitted_by, submitted_at,
                announcement_channel_id
            ) VALUES (?, ?, 'UPHELD', ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                race_result_id,
                qual_result_id,
                sp.penalty_type,
                sp.penalty_seconds,
                sp.description,
                sp.justification,
                # An appeal read back from the round keeps its own author and time (#345).
                sp.decided_by or str(actor_id),
                sp.decided_at or now_str,
            ),
        )
        records.append(
            {
                "id": cursor.lastrowid,
                "race_result_id": race_result_id,
                "qual_result_id": qual_result_id,
                "driver_user_id": sp.driver_user_id,
                "penalty_type": sp.penalty_type,
                "time_seconds": sp.penalty_seconds,
                "description": sp.description,
                "justification": sp.justification,
                "submitted_by": sp.decided_by or str(actor_id),
                "announcement_channel_id": None,
            }
        )
    return records


async def _apply_staged_appeals_on(
    db: aiosqlite.Connection,
    round_id: int,
    division_id: int,
    staged_appeals: list,
    actor_id: int,
    *,
    now: datetime,
) -> list[dict]:
    """Apply a round's upheld appeal corrections on *db* and record each as an appeal record.

    What `_apply_staged_appeals` does, written on the connection it is handed and committing
    nothing, so the corrections, the points they move and the appeal records are one save with
    the rest of the stage. The points are recalculated by `_recompute_session_points_on`, which
    **raises** on a session it cannot score: the save is rolled back and the change stops,
    where the old form logged the failure and published the old points (defect 3).

    Shared by the first pass and the amendment's appeal stage. Returns the appeal records
    written, shaped as the verdict announcement reads them, in the order the corrections were
    staged.
    """
    from leaguebot.results.services import penalty_service as _ps

    if not staged_appeals:
        return []

    applied_pr = await _ps.apply_penalties_on(
        db, round_id, division_id, staged_appeals, actor_id, now=now, _phase="APPEAL",
    )
    await _recompute_session_points_on(db, round_id)
    return await _insert_appeal_records_on(db, staged_appeals, applied_pr, actor_id, now=now)


_ACTIVE_SESSIONS_OF_ROUND_SQL = """
    SELECT sr.id AS session_result_id, sr.config_name, sr.session_type,
           s.id AS season_id
    FROM session_results sr
    JOIN rounds r ON r.id = sr.round_id
    JOIN divisions d ON d.id = r.division_id
    JOIN seasons s ON s.id = d.season_id
    WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
"""


async def _recompute_session_points(db_path: str, round_id: int) -> None:
    """Re-run ``_apply_points_from_config`` for every ACTIVE session in *round_id*
    so that ``points_awarded`` and ``fastest_lap_bonus`` reflect any position changes
    caused by penalties applied to the new result tables.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(_ACTIVE_SESSIONS_OF_ROUND_SQL, (round_id,))
        sessions = await cursor.fetchall()

    for row in sessions:
        if row["config_name"] is None:
            continue  # no config attached — skip points recompute
        try:
            await _apply_points_from_config(
                db_path,
                row["session_result_id"],
                row["season_id"],
                row["config_name"],
                SessionType(row["session_type"]),
            )
        except Exception:
            log.exception(
                "_recompute_session_points: error for session_result %s",
                row["session_result_id"],
            )


async def _recompute_session_points_on(db: aiosqlite.Connection, round_id: int) -> None:
    """Re-score every ACTIVE session of *round_id* on the save *db* belongs to, committing nothing.

    What `_recompute_session_points` does, written on the connection it is handed through
    `_apply_points_in_tx` so that the points are part of the approval's one save, and **raising
    on the first session that cannot be scored** (issue #439, defect 3). The old form logged a
    failure to the host and went on, so a round was published as approved with its old points;
    here the failure rolls the whole save back and stops the change.

    A session with no points configuration is not a failure: it is skipped, its rows keeping
    the points they have, as the old form did.
    """
    cursor = await db.execute(_ACTIVE_SESSIONS_OF_ROUND_SQL, (round_id,))
    for row in await cursor.fetchall():
        if row["config_name"] is None:
            continue
        await _apply_points_in_tx(
            db,
            row["session_result_id"],
            row["season_id"],
            row["config_name"],
            SessionType(row["session_type"]),
        )


async def _snapshot_staged_drivers(
    db_path: str,
    round_id: int,
    division_id: int,
    staged,
) -> list[dict]:
    """:func:`_snapshot_staged_drivers_on`, on a connection of its own."""
    if not staged:
        return []
    async with get_connection(db_path) as db:
        return await _snapshot_staged_drivers_on(db, round_id, division_id, staged)


async def _snapshot_staged_drivers_on(
    db: aiosqlite.Connection,
    round_id: int,
    division_id: int,
    staged,
) -> list[dict]:
    """Return current ``finishing_position``, ``post_race_time_penalties``, and
    ``total_points`` for every driver referenced in *staged*, read on *db*. Used for audit log.

    Read on the connection it is handed, so a change's one save can take the picture before and
    after what it writes (#439).
    """
    from leaguebot.results.services.standings_service import compute_driver_standings_on

    if not staged:
        return []

    driver_ids = list({sp.driver_user_id for sp in staged})

    placeholders = ",".join("?" * len(driver_ids))
    cursor = await db.execute(
        f"""
        SELECT driver_user_id, finishing_position, postrace_time_penalties_ms
        FROM race_session_results rsr
        JOIN session_results sr ON sr.id = rsr.session_result_id
        WHERE sr.round_id = ? AND rsr.driver_user_id IN ({placeholders})
        UNION ALL
        SELECT driver_user_id, finishing_position, 0 AS postrace_time_penalties_ms
        FROM qualifying_session_results qsr
        JOIN session_results sr ON sr.id = qsr.session_result_id
        WHERE sr.round_id = ? AND qsr.driver_user_id IN ({placeholders})
        """,
        (round_id, *driver_ids, round_id, *driver_ids),
    )
    dsr_rows = await cursor.fetchall()
    # The standings are keyed by the account a driver uses now; a staged penalty by the
    # one its result stands under (issue #243).
    from leaguebot.core.services.driver_service import current_account_map_for_division

    current_of = await current_account_map_for_division(db, division_id)

    # Get total_points from latest standings snapshot
    driver_snaps = await compute_driver_standings_on(db, division_id, round_id)
    pts_map = {snap.driver_user_id: snap.total_points for snap in driver_snaps}

    result = []
    for r in dsr_rows:
        uid = r["driver_user_id"]
        result.append(
            {
                "driver_user_id": uid,
                "finishing_position": r["finishing_position"],
                "total_points": pts_map.get(current_of.get(uid, uid), 0),
                "post_race_time_penalties": r["postrace_time_penalties_ms"],
            }
        )
    return result


async def save_session_result(
    db_path: str,
    round_id: int,
    division_id: int,
    session_type: SessionType,
    status: str,
    config_name: str | None,
    submitted_by: int | None,
    driver_rows: list[dict],
    fl_driver_override: int | None = None,
) -> int:
    """INSERT session_results + new result tables; return session_result_id."""
    async with get_connection(db_path) as db:
        session_result_id = await _save_session_result_in_tx(
            db, round_id, division_id, session_type, status, config_name,
            submitted_by, driver_rows, fl_driver_override,
        )
        await db.commit()
    return session_result_id


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
) -> int:
    """Do what `save_session_result` does on the caller's connection, without committing.

    Split out for `replace_round_results`, which has to write every session of a round in the
    same transaction as the delete of the results they replace.
    """
    from leaguebot.core.services.season_service import SeasonImmutableError
    from leaguebot.core.services.driver_service import resolve_driver_profile_id

    submitted_at = datetime.now(timezone.utc).isoformat()
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


class AmendmentWouldOrphanVerdictError(Exception):
    """An amendment would leave a penalty or appeal verdict with no result row to point at.

    Raised where a driver carrying a verdict is absent from the corrected classification. The
    amendment is refused rather than applied, because every disposal open to the bot is worse
    than not proceeding: deleting the verdict discards the audit of why the driver lost places,
    and orphaning it keeps a row nothing can ever find — every read of these tables is by the
    foreign key, so a detached verdict is invisible to the republish and to the DSQ map alike.

    Whether an amendment may drop a driver who carries a verdict, and what becomes of that
    verdict, is a rule the results specification does not yet state. Refusing is the conservative
    reading until it does (issue #345), and it is what the three-stage replay supersedes: the
    wizard shows the manager the verdict and lets them decide, which is the answer this exception
    stands in for.
    """


#: The two tables whose rows point at a driver's result row, with the column each uses.
#: ``results_purge_service._delete_rows`` names the same pair for the same reason.
_VERDICT_TABLES: tuple[str, ...] = ("penalty_records", "appeal_records")


def _placeholders(items) -> str:
    """``?, ?, ?`` for binding *items* into an ``IN (...)`` clause."""
    return ", ".join("?" for _ in items)


def _verdict_fk_column(session_type: SessionType) -> str:
    """The column of a verdict record that points at *session_type*'s result rows."""
    return "qual_result_id" if session_type.is_qualifying else "race_result_id"


async def _verdicts_by_driver(
    db, session_result_id: int, session_type: SessionType
) -> dict[str, dict[int, list[int]]]:
    """Map every verdict of this session to the driver it was applied to, before the rows go.

    Returned as ``{table: {driver_user_id: [verdict row id, ...]}}``. A driver may carry several
    verdicts in one session — the penalty wizard stages one record per incident, not per driver —
    so the value is a list rather than a single id.

    Read **before** the driver rows are deleted, because the driver a verdict belongs to is only
    recoverable through the row it points at: ``penalty_records`` and ``appeal_records`` store
    neither ``driver_user_id`` nor ``session_type``.
    """
    from leaguebot.results.services.verdict_records import select_verdicts

    by_table: dict[str, dict[int, list[int]]] = {}
    for verdict_table in _VERDICT_TABLES:
        per_driver: dict[int, list[int]] = {}
        for row in await select_verdicts(
            db, verdict_table, "v.id AS verdict_id, r.driver_user_id AS driver_user_id",
            session_result_id=session_result_id,
        ):
            per_driver.setdefault(row["driver_user_id"], []).append(row["verdict_id"])
        if per_driver:
            by_table[verdict_table] = per_driver
    return by_table


async def _detach_verdicts(
    db, session_type: SessionType, verdicts: dict[str, dict[int, list[int]]]
) -> None:
    """Null every verdict's reference so the driver rows can be deleted.

    The foreign key is checked on the DELETE itself, so re-pointing afterwards is too late — the
    reference has to be released first. The rows are detached only within the transaction that
    re-points them a few statements later, and :func:`_repoint_verdicts` refuses before writing
    anything where a driver is missing, so no verdict is ever left detached once the transaction
    settles: either all of them are re-pointed and committed, or the whole of it is abandoned.
    """
    if not verdicts:
        return
    fk_col = _verdict_fk_column(session_type)
    for verdict_table, per_driver in verdicts.items():
        ids = [vid for vids in per_driver.values() for vid in vids]
        placeholders = ", ".join("?" for _ in ids)
        await db.execute(
            f"UPDATE {verdict_table} SET {fk_col} = NULL WHERE id IN ({placeholders})",  # noqa: S608
            ids,
        )


async def _repoint_verdicts(
    db,
    session_result_id: int,
    session_type: SessionType,
    verdicts: dict[str, dict[int, list[int]]],
    division_id: int,
) -> None:
    """Point every verdict of this session at its driver's **new** result row.

    The amendment deletes a session's driver rows and re-inserts them, so a verdict recorded
    against the old row would fail the foreign key on the delete and, were the constraint absent,
    point at nothing afterwards. The verdict follows its driver: the audit it carries — the
    justification, who applied it and when — is the only record of why that driver lost places,
    and an amendment correcting a lap time has no business discarding it (issue #345).

    **Both sides are read under the driver's current account** (#243). A pasted classification is
    normalised onto it by `validate_submission_block` before anything is stored, while the row a
    verdict points at holds whichever account the driver raced under — so comparing them raw made
    a session un-amendable the moment somebody changed account, and told the manager to include a
    driver the classification already named.

    Raises :class:`AmendmentWouldOrphanVerdictError` where a driver carrying a verdict is not in
    the corrected classification. Nothing is written in that case; the caller's transaction is
    abandoned whole.
    """
    if not verdicts:
        return
    from leaguebot.core.services.driver_service import current_account_map_for_division

    current_of = await current_account_map_for_division(db, division_id)

    def _now(driver_user_id: int) -> int:
        return current_of.get(driver_user_id, driver_user_id)

    fk_col = _verdict_fk_column(session_type)
    table = "qualifying_session_results" if session_type.is_qualifying else "race_session_results"
    cursor = await db.execute(
        f"SELECT id, driver_user_id FROM {table} WHERE session_result_id = ?",  # noqa: S608
        (session_result_id,),
    )
    new_row_of: dict[int, int] = {
        _now(r["driver_user_id"]): r["id"] for r in await cursor.fetchall()
    }

    orphaned: set[int] = set()
    for per_driver in verdicts.values():
        orphaned.update({_now(driver) for driver in per_driver} - set(new_row_of))
    if orphaned:
        named = ", ".join(f"<@{driver}>" for driver in sorted(orphaned))
        raise AmendmentWouldOrphanVerdictError(
            f"The corrected classification leaves out {named}, who carries a penalty or appeal "
            f"verdict for this session. Amending would discard the record of why that driver's "
            f"result changed, so the amendment was refused and nothing was altered. Include the "
            f"driver in the classification, or ask for the verdict to be withdrawn first."
        )

    for verdict_table, per_driver in verdicts.items():
        for driver_user_id, verdict_ids in per_driver.items():
            for verdict_id in verdict_ids:
                await db.execute(
                    f"UPDATE {verdict_table} SET {fk_col} = ? WHERE id = ?",  # noqa: S608
                    (new_row_of[_now(driver_user_id)], verdict_id),
                )


async def _remember_superseded_announcements_on(
    db: aiosqlite.Connection, round_id: int, session_types: list[SessionType]
) -> None:
    """Note the amended sessions' verdict announcements, before their records are cleared.

    The report stage deletes the session's verdict records and writes the approved set back, so
    the ids of the announcements to be taken down do not survive it. The final stage re-announces
    and needs them; without this it found every id already NULL and left both the old
    announcement and its replacement standing (#345).

    **The amended sessions only.** The round's other sessions keep their records, ids included,
    and the final stage's republish takes their announcements down itself — noting them here as
    well sent a second delete after each.

    **The first capture stands.** It is taken before any record is cleared, so a later one —
    a report stage retried after a failure — would read what the first had already cleared and
    overwrite the list with less.

    Commits nothing: the amendment's report stage saves it with the clearing of the records
    and the writing of the approved set, so a stop between them cannot lose the list.
    """
    from leaguebot.results.services.verdict_records import VERDICT_TABLES, select_verdicts

    rows: list[dict] = []
    for table in VERDICT_TABLES:
        rows.extend(
            await select_verdicts(
                db, table,
                "v.announcement_message_id AS anchor, v.announcement_message_ids AS chunks, "
                "v.announcement_channel_id AS channel_id, r.driver_user_id AS driver_user_id",
                round_id=round_id, session_types=session_types,
                where=" AND v.announcement_message_id IS NOT NULL",
            )
        )
    await db.execute(
        "UPDATE round_amend_channels SET superseded_announcements = ? "
        "WHERE round_id = ? AND superseded_announcements IS NULL",
        (_json.dumps(rows) if rows else None, round_id),
    )


async def _clear_round_verdict_records_on(
    db: aiosqlite.Connection, round_id: int, session_types: "list[SessionType] | None" = None
) -> None:
    """Remove a round's penalty and appeal records so the approved set can be written whole.

    **The amendment rewrites rather than adds** (#345). ``apply_penalties`` only ever inserts,
    and adds to the stored penalty columns; replaying a round's reports over records that are
    still there duplicated every one of them, and doubled the sanction again on a second
    amendment. The times were right — stage one re-inserts the driver rows with those columns
    at zero — but the audit was not.

    **Nothing a league can see is lost.** The reports and appeals were read back into the
    review stages before this runs, carrying their justification, their author and the time
    they were given; approving writes them out again with those intact. What changes is the
    row id, which no league reads. That is the price of a stage where removing a report
    actually removes it.

    Deleting them is also what releases the foreign key, so the rows may be rewritten against
    whichever driver rows the corrected classification produced.

    Commits nothing, so the amendment's report stage clears and rewrites in one save.
    """
    from leaguebot.results.services.verdict_records import delete_verdicts

    # Scoped to the sessions given, because those are all an amendment replays: clearing the
    # whole round would drop the decisions of sessions whose reports are never re-approved,
    # losing them outright.
    await delete_verdicts(db, round_id=round_id, session_types=session_types)


#: How long an amendment may sit unapproved before it is reverted (#345).
#:
#: The amendment's first stage commits: the corrected classification is written and the round
#: scored from it, before its reports and appeals have been reviewed. A manager who pastes and
#: then walks away therefore leaves the round scored one way and posted another, with the views
#: carrying no timeout of their own — so nothing resolves it.
#:
#: Half an hour is long enough to work through two review stages without being hurried, and
#: short enough that an abandoned amendment does not outlive the evening it was started in.
#:
#: **Counted from stage one, across both review stages, and never extended** (decided
#: 2026-09-21). Approving the reports hands back the deadline it was given rather than a fresh
#: one: a league amending a round is expected to have its decisions prepared before it starts,
#: however many sessions it chose. Do not turn this into an inactivity timer.
AMENDMENT_STAGE_TIMEOUT_SECONDS: int = 1800


def amendment_wait_text() -> str:
    """How an open amendment ends, as the refusals it causes tell a league.

    Worked out from the deadline rather than written out, so that tuning it cannot leave a
    refusal quoting the old figure. "Once", never "at most": the sweep that undoes a lapsed
    amendment runs every few minutes, and nothing bounds the pastes before the deadline starts.
    """
    minutes = AMENDMENT_STAGE_TIMEOUT_SECONDS // 60
    return (
        f"it ends when approved, or is undone once {minutes} minutes have passed since its "
        "corrections were entered"
    )


async def snapshot_before_amendment(
    db_path: str, round_id: int, session_types: list[SessionType]
) -> None:
    """Record the round as it stands, so an abandoned amendment can be undone.

    Taken before stage one writes. Captures, for every session being amended, its header, its
    driver rows, and its verdict records, and the round's attendance pardons — which is the
    whole of what the amendment changes that a revert cannot recompute. The standings and the
    points follow from the driver rows, so restoring those and cascading again reproduces them.
    """
    from leaguebot.results.services.verdict_records import VERDICT_TABLES, select_verdicts

    sessions: list[dict] = []
    async with get_connection(db_path) as db:
        for session_type in session_types:
            is_qualifying = session_type.is_qualifying
            rows_table = (
                "qualifying_session_results" if is_qualifying else "race_session_results"
            )
            cursor = await db.execute(
                "SELECT * FROM session_results WHERE round_id = ? AND session_type = ?",
                (round_id, session_type.value),
            )
            header = await cursor.fetchone()
            if header is None:
                continue
            cursor = await db.execute(
                f"SELECT * FROM {rows_table} WHERE session_result_id = ? ORDER BY id",  # noqa: S608
                (header["id"],),
            )
            driver_rows = [dict(row) for row in await cursor.fetchall()]

            # **Whole rows, not references** (#345). Approving the report stage *deletes* the
            # session's verdict records and writes the approved set back, so an amendment
            # reverted after that point has nothing left to re-point: a snapshot holding only
            # ``(id, points_at)`` would silently no-op and lose every appeal record it carried.
            verdicts: list[dict] = []
            for table in VERDICT_TABLES:
                for row in await select_verdicts(
                    db, table, "v.*", session_result_id=header["id"]
                ):
                    verdicts.append({"table": table, "row": row})

            sessions.append(
                {
                    "session_result_id": header["id"],
                    "session_type": session_type.value,
                    "rows_table": rows_table,
                    "header": dict(header),
                    "driver_rows": driver_rows,
                    "verdicts": verdicts,
                }
            )
        if not sessions:
            return

        # **The pardons, whole** (#345). The appeal stage rewrites them before it releases the
        # snapshot, so a failure between the two — or a crash — would otherwise revert the
        # classification and the verdicts and leave the amendment's pardons standing on them.
        cursor = await db.execute(
            "SELECT * FROM attendance_pardons WHERE attendance_id IN "
            "(SELECT id FROM driver_round_attendance WHERE round_id = ?) ORDER BY id",
            (round_id,),
        )
        pardons = [dict(row) for row in await cursor.fetchall()]

        expires = (
            datetime.now(timezone.utc)
            + timedelta(seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS)
        ).isoformat()
        await db.execute(
            "UPDATE round_amend_channels SET pre_amendment_state = ?, expires_at = ? "
            "WHERE round_id = ?",
            (_json.dumps({"sessions": sessions, "pardons": pardons}), expires, round_id),
        )
        await db.commit()


async def revert_abandoned_amendment(db_path: str, round_id: int, bot: LeagueBot | None = None) -> bool:
    """Put a round back as it was before an amendment nobody approved.

    The round keeps the classification it raced rather than a half-amended one: for every
    session the amendment re-entered, the driver rows are restored from the snapshot, its
    verdict records written back whole, and its header put back; the standings are then
    cascaded again so the championship follows.

    **Nothing is reposted, because nothing was posted** (#345). An amendment publishes nothing
    until its last stage is approved, and approving that stage releases the snapshot before the
    rebuild begins — so wherever a snapshot is still here to revert from, the channels are still
    showing the round as it was. Reposting would only move the round to the bottom of each
    channel, out of order.

    The round's pardons come back as they were too: the last stage rewrites them before it
    releases the snapshot. The attendance itself is left alone, being recalculated only after
    the release.

    Reached by the sweep, by **Cancel**, by an internal failure part-way through, and by restart
    recovery, and safe to call where no snapshot was taken — an amendment abandoned before stage
    one wrote has nothing to undo. The caller holds the claim on the amendment, where there is
    anybody else who could act on it; see :func:`_claim_amendment`.

    *bot*, where given, is how the league's names are reached, so that the standings put back
    settle a full tie as the posting does; see :func:`_standings_names`.

    Returns True where the round was put back, and False where there was nothing to revert.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT pre_amendment_state FROM round_amend_channels WHERE round_id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None or not row["pre_amendment_state"]:
        return False

    snapshot = _json.loads(row["pre_amendment_state"])

    from leaguebot.results.services.verdict_records import delete_verdicts

    async with get_connection(db_path) as db:
        for state in snapshot["sessions"]:
            rows_table: str = state["rows_table"]
            session_result_id: int = state["session_result_id"]
            # Whatever the amendment left behind goes, in both tables, so the snapshot is
            # written over a clean slate rather than merged into one.
            await delete_verdicts(db, session_result_id=session_result_id)
            await db.execute(
                f"DELETE FROM {rows_table} WHERE session_result_id = ?",  # noqa: S608
                (session_result_id,),
            )
            for driver_row in state["driver_rows"]:
                columns = ", ".join(driver_row)
                placeholders = ", ".join("?" for _ in driver_row)
                await db.execute(
                    f"INSERT INTO {rows_table} ({columns}) VALUES ({placeholders})",  # noqa: S608
                    list(driver_row.values()),
                )
            # The verdicts come back whole, ids included, so anything that referenced one
            # still finds it — and the DSQ marks they alone produce reappear with them.
            for verdict in state["verdicts"]:
                verdict_row = verdict["row"]
                columns = ", ".join(verdict_row)
                placeholders = ", ".join("?" for _ in verdict_row)
                await db.execute(
                    f"INSERT INTO {verdict['table']} ({columns}) VALUES ({placeholders})",  # noqa: S608
                    list(verdict_row.values()),
                )
            header = state["header"]
            await db.execute(
                "UPDATE session_results SET submitted_by = ?, submitted_at = ?, "
                "config_name = ?, fl_driver_override = ? WHERE id = ?",
                (
                    header.get("submitted_by"),
                    header.get("submitted_at"),
                    header.get("config_name"),
                    header.get("fl_driver_override"),
                    session_result_id,
                ),
            )
        # The flag follows the results back (#216). Stage one marks nobody now, so in the
        # ordinary case this finds nothing to do; it runs all the same, because the round's
        # rows have just been restored and the flag is defined by them. `profiles_before`
        # names anyone the abandoned amendment had struck out, who is in no result to be
        # found by until the restore above puts them back.
        await recompute_former_drivers_for_round(
            db, round_id, also_consider=snapshot.get("profiles_before", [])
        )
        if "pardons" in snapshot:
            await db.execute(
                "DELETE FROM attendance_pardons WHERE attendance_id IN "
                "(SELECT id FROM driver_round_attendance WHERE round_id = ?)",
                (round_id,),
            )
            for pardon in snapshot["pardons"]:
                columns = ", ".join(pardon)
                placeholders = ", ".join("?" for _ in pardon)
                await db.execute(
                    f"INSERT INTO attendance_pardons ({columns}) VALUES ({placeholders})",  # noqa: S608
                    list(pardon.values()),
                )
        await db.execute(
            "UPDATE round_amend_channels SET pre_amendment_state = NULL, expires_at = NULL "
            "WHERE round_id = ?",
            (round_id,),
        )
        await db.commit()

    # The championship follows from the rows, so it is recomputed rather than snapshotted.
    #
    # **Reported, never raised** (#345). The restore and the clearing of the snapshot are
    # committed by the time this runs, so raising here would tell the caller the revert failed
    # when the round is in fact back — and the caller would then hand an amendment with no
    # snapshot left to the sweep, which cannot claim it, leaving the row and its channel for a
    # restart to clear. A stale standing is a `/results standings sync` away.
    from leaguebot.results.services import standings_service

    division_id = await _division_of_round(db_path, round_id)
    if division_id is not None:
        try:
            await standings_service.cascade_recompute_from_round(
                db_path, division_id, round_id,
                await _standings_names(db_path, division_id, bot),
            )
        except Exception:  # noqa: BLE001 — the round is back; its standings are downstream
            log.exception(
                "revert: round %s was put back but its standings were not recomputed", round_id
            )
    return True


async def _standings_names(db_path: str, division_id: int, bot: LeagueBot | None, guild=None):
    """The names the division's standings are drawn under, or None — never raising.

    A standings snapshot stores the order the league is shown, and a full tie is settled by name
    there (decided 2026-09-15). Recomputed without the names, the tie is settled by user id, and
    the next round's movement arrows — worked out from the stored order — then show a driver
    moving who did not. Where the names cannot be had, the cascade runs on ids rather than not
    at all.
    """
    from leaguebot.results.services.results_post_service import standings_display_names

    try:
        if guild is None and bot is not None:
            guild = await league_guild(bot)
        return await standings_display_names(db_path, division_id, guild, bot)
    except Exception:  # noqa: BLE001 — ordering a tie by id is the lesser fault
        log.exception("could not resolve the standings names of division %s", division_id)
        return None


async def _division_of_round(db_path: str, round_id: int) -> int | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT division_id FROM rounds WHERE id = ?", (round_id,)
        )
        row = await cursor.fetchone()
    return row["division_id"] if row else None


# ---------------------------------------------------------------------------
# Acting on an open amendment (#345)
# ---------------------------------------------------------------------------


#: What a manager is told on pressing a stage of an amendment that is no longer open.
_AMENDMENT_NOT_OPEN = (
    "❌ This amendment is no longer open — it lapsed, was cancelled, or this step is "
    "already being approved. Nothing was changed. Run `/results rounds amend` again if the "
    "round still needs correcting."
)


#: The next step an amendment that stopped tells its manager.
AMENDMENT_RE_RUN = "Re-run `/results rounds amend` to try again."
#: What an amendment put back as it was says became of the round.
ROUND_PUT_BACK = "The round was put back as it was. " + AMENDMENT_RE_RUN
#: The next step an amendment whose round could not be put back yet tells its manager: a re-run
#: before then is refused, the amendment still holding its division.
AMENDMENT_RE_RUN_ONCE_PUT_BACK = "Run `/results rounds amend` again once it has been put back."
#: What an amendment handed to the sweep says became of the round: the sweep retries within
#: minutes, and restart recovery at the next start.
ROUND_NOT_PUT_BACK_YET = (
    "The round could not be put back yet; the bot tries again within a few minutes. "
    "Restarting the bot retries that. " + AMENDMENT_RE_RUN_ONCE_PUT_BACK
)


def amendment_fault_reply(kind: str, became: str) -> str:
    """The reply to an amendment that stopped on a fault in the bot.

    It says the fault is the bot's and not anything the manager entered, names the *kind* of
    fault in plain words — `describe_fault`'s, never the exception — and says what *became* of
    the round and what to do next. An amendment undoes itself, so it says that rather than the
    standard "may have been partly done" (the core specification's "When a command fails").
    """
    return (
        "❌ The amendment stopped on a fault in the bot, not on anything you entered: "
        f"{kind}. {became}"
    )


def _sessions_text(session_types) -> str:
    """The sessions of an amendment as its log lines name them."""
    return ", ".join(st.value if isinstance(st, SessionType) else str(st) for st in session_types)


async def _claim_amendment(db_path: str, round_id: int) -> str | None:
    """Take an open amendment's deadline, so that nothing else acts on it until it is returned.

    Four things act on an amendment once its first stage has written: approving its report
    stage, approving its appeal stage, **Cancel**, and the sweep. Each rewrites or reverts the
    same rows, and two at once — the sweep landing while a stage is being approved, or a
    double click — would revert a round from under a stage still writing to it, or apply a
    stage twice. So each first takes the deadline, by a compare-and-set of ``expires_at`` to
    NULL that only one of them can win. The sweep reads only rows with a deadline, so a claimed
    amendment is invisible to it as well.

    Returns the deadline taken, for :func:`_rearm_amendment` to hand back, or None where the
    amendment is not open: never begun, already committed or reverted, or claimed by another.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT expires_at FROM round_amend_channels "
            "WHERE round_id = ? AND pre_amendment_state IS NOT NULL",
            (round_id,),
        )
        row = await cursor.fetchone()
        if row is None or row["expires_at"] is None:
            return None
        cursor = await db.execute(
            "UPDATE round_amend_channels SET expires_at = NULL "
            "WHERE round_id = ? AND expires_at = ?",
            (round_id, row["expires_at"]),
        )
        await db.commit()
    return row["expires_at"] if cursor.rowcount == 1 else None


async def _recompute_former_drivers_after_amendment_on(db: aiosqlite.Connection, round_id: int) -> None:
    """Settle the former-driver flag once an amendment commits (#216), on the save *db* belongs to.

    Reads ``profiles_before`` from the snapshot — the profiles the round held before stage one
    rewrote it — and hands them to :func:`recompute_former_drivers_for_round` as candidates to
    reconsider alongside the drivers the round now holds. Without them a driver the amendment
    struck out is in no result to be found by, and their flag would stand for ever on a round
    they are no longer in. Commits nothing.

    Must run before :func:`_release_amendment_on`, which drops the snapshot this reads.
    """
    cursor = await db.execute(
        "SELECT pre_amendment_state FROM round_amend_channels WHERE round_id = ?", (round_id,)
    )
    row = await cursor.fetchone()
    profiles_before: list[int] = []
    if row is not None and row["pre_amendment_state"]:
        profiles_before = _json.loads(row["pre_amendment_state"]).get("profiles_before", [])
    await recompute_former_drivers_for_round(db, round_id, also_consider=profiles_before)


async def _rearm_amendment(db_path: str, round_id: int, deadline: str) -> None:
    """Hand back a deadline taken by :func:`_claim_amendment`, the amendment still open."""
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_amend_channels SET expires_at = ? "
            "WHERE round_id = ? AND pre_amendment_state IS NOT NULL",
            (deadline, round_id),
        )
        await db.commit()


async def _release_amendment_on(db: aiosqlite.Connection, round_id: int) -> None:
    """Drop the snapshot and the deadline: the amendment is committed and can no longer revert.

    Done by the appeal stage's save, together with everything the rebuild shows, so that a stop
    after it leaves no standings or attendance owed. With the snapshot gone, a restart part-way
    through the rebuild would otherwise have reverted the round from under channels already
    showing the amendment. Commits nothing.
    """
    await db.execute(
        "UPDATE round_amend_channels SET pre_amendment_state = NULL, expires_at = NULL "
        "WHERE round_id = ?",
        (round_id,),
    )


async def _close_amendment_channel(db_path: str, guild, round_id: int, *, reason: str) -> None:
    """End an amendment's record and delete its channel, as `_close_amend_channel_record` does."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT channel_id FROM round_amend_channels WHERE round_id = ?", (round_id,)
        )
        row = await cursor.fetchone()
    if row is None:
        return
    channel = guild.get_channel(row["channel_id"]) if guild is not None else None
    await _close_amend_channel_record(
        db_path, round_id, row["channel_id"], channel, reason=reason
    )


async def _amendment_sessions_of(db_path: str, round_id: int) -> list[str]:
    """The session-type values a round's amendment re-enters, as its record lists them."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT session_types FROM round_amend_channels WHERE round_id = ?", (round_id,)
        )
        row = await cursor.fetchone()
    if row is None or not row["session_types"]:
        return []
    try:
        return list(_json.loads(row["session_types"]))
    except (TypeError, ValueError):
        return []


async def open_amendment_in_division(db_path: str, division_id: int):
    """The amendment open in a division — its round, round number, channel and sessions — or None.

    One is open from the moment its channel is recorded until it is approved, cancelled or
    reverted. A row carrying ``closed_at`` is one that has ended and could not delete its
    channel; it is kept for restart recovery to find, and holds nothing open.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT rac.round_id, r.round_number, rac.channel_id, rac.session_types "
            "FROM round_amend_channels rac JOIN rounds r ON r.id = rac.round_id "
            "WHERE r.division_id = ? AND rac.closed_at IS NULL",
            (division_id,),
        )
        return await cursor.fetchone()


async def open_amendment_in_season(db_path: str, season_id: int):
    """An amendment open in any division of a season — its division, round and channel — or None.

    For what reposts or closes every division at once: completing the season, and approving a
    change to its points (#345, decided 2026-09-21). Where several are open, the one in the
    highest division is named; each is refused until none is left.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT d.name AS division_name, r.round_number, rac.channel_id "
            "FROM round_amend_channels rac JOIN rounds r ON r.id = rac.round_id "
            "JOIN divisions d ON d.id = r.division_id "
            "WHERE d.season_id = ? AND rac.closed_at IS NULL "
            "ORDER BY d.tier, r.round_number LIMIT 1",
            (season_id,),
        )
        return await cursor.fetchone()


async def held_by_amendment(
    db_path: str, round_id: int, division_id: int, *, then: str
) -> str | None:
    """Why nothing of *round_id* may be committed now, or None when it may.

    **A division with an amendment open commits nothing else** (#345, decided 2026-09-21).
    Stage one writes the amended round's corrected classification and recalculates the
    championship from it, publishing nothing. A first pass of another round in the division
    posts standings as its results go in and at each approval, and would publish those
    unapproved corrections — and leave them published if the amendment were then cancelled or
    lapsed, its revert posting nothing. So while one is open, a first pass's results paste and
    its report and appeal approvals are refused, and the channel stays open for the manager to
    try again. The wait is short: an amendment lapses a fixed time after its first stage.

    Called at the point of the commit itself — after the points configuration is chosen, not
    when the paste arrives — so an amendment opened while the manager is choosing still holds.
    *then* ends the refusal, saying what to do once the amendment has ended.
    """
    row = await open_amendment_in_division(db_path, division_id)
    if row is None or row["round_id"] == round_id:
        return None
    return (
        f"⏸️ Round {row['round_number']} of this division is being amended in "
        f"<#{row['channel_id']}>, so nothing can be committed for another of its rounds until "
        f"that ends — {amendment_wait_text()}. " + then
    )


async def stage_in_hand(db_path: str, round_id: int) -> bool:
    """Whether an approval of one of the amendment's stages is in hand on the change queue for
    *round_id*, queued, running or stopped on a failure. Nothing overtakes it, so the sweep,
    Cancel and restart recovery leave the amendment alone while it is."""
    from leaguebot.core.services.change_queue import unfinished
    from leaguebot.results.services.amendment_stage_changes import APPEALS_KIND, REPORTS_KIND

    return any(
        change.get("round_id") == round_id
        for change in await unfinished(db_path, [REPORTS_KIND, APPEALS_KIND])
    )


async def cancel_amendment(bot: LeagueBot, round_id: int, *, cancelled_by) -> bool:
    """Undo an amendment its manager cancelled after the first stage, and close its channel.

    Returns False where it was too late — the appeal stage is already committing it — or the
    amendment is otherwise no longer open; nothing is done then. Raises where the revert itself
    failed, having handed the amendment to the sweep with the snapshot intact.

    **It writes no line to the log channel.** Its callers do: the Cancel Amendment button
    records the cancel in the standard form, naming whoever pressed it, and a report stage that
    could not open records the one failure it was. Written here too, either would make two
    lines of one outcome. *cancelled_by* is kept for the host's log.
    """
    db_path: str = bot.db_path
    # A stage in hand on the queue, a stopped one included, is left alone: it is the amendment's
    # own approval, and Cancel is refused while it is carried out ("Leave it while stuck").
    if await stage_in_hand(db_path, round_id):
        return False
    deadline = await _claim_amendment(db_path, round_id)
    if deadline is None:
        return False
    try:
        await revert_abandoned_amendment(db_path, round_id, bot)
    except Exception:
        await _rearm_amendment(db_path, round_id, datetime.now(timezone.utc).isoformat())
        raise

    guild = await league_guild(bot)
    await _close_amendment_channel(db_path, guild, round_id, reason="Amendment cancelled")
    log.info("cancel_amendment: round %s put back, cancelled by %s", round_id, cancelled_by)
    return True


async def run_amendment_review_stages(
    db_path: str,
    round_id: int,
    division_id: int,
    channel,
    bot: LeagueBot,
    *,
    round_number: int,
    division_name: str,
    session_types: list[SessionType],
) -> "PenaltyReviewState":
    """Replay a settled round's report and appeal stages in the amendment channel (#345).

    Opens stage two of three. The corrected classification of every amended session has been
    written, and nothing posted; this shows the league manager what was decided about those
    sessions, together, and lets them change it — keep a report, remove it, add one, and the
    same for the appeals and for the round's attendance pardons.

    **The round's decisions are read back out of the database first.** They were written when
    the round was originally reviewed and nothing has held them in memory since, so
    :func:`~leaguebot.results.services.penalty_service.load_staged_from_records` turns the stored rows back into
    the staged entries the review screens already know how to draw. A manager who changes
    nothing approves exactly what was there before.

    **The state is marked an amendment**, which routes approving either screen to
    :func:`_approve_amendment_reports` and :func:`_approve_amendment_appeals` rather than the
    first pass's finalisers. The round is already FINAL: a first pass through these screens sets
    ``AWAITING_APPEAL_VERDICTS`` then ``FINAL``, finishes the division, may wind the season down
    and publishes as it goes, none of which an amendment may do.

    Returns the state the screens share. The approvals happen through the screens themselves.
    """
    from leaguebot.results.services.penalty_service import load_staged_from_records
    from leaguebot.results.services.penalty_wizard import PenaltyReviewState, PenaltyReviewView
    from leaguebot.results.services.penalty_wizard import _render_prompt_content

    # **Scoped to the sessions being amended** (#345). `apply_penalties` walks whatever
    # session types the staged set names, and stage one re-inserted only the amended sessions'
    # driver rows — with their penalty columns at zero. A report hydrated from an *unamended*
    # session would therefore be added on top of the milliseconds already standing there,
    # doubling a sanction in a session the manager never touched. To review a session's
    # decisions, the manager includes that session in the amendment.
    staged, staged_appeals, staged_pardons = await load_staged_from_records(
        db_path, round_id, session_types=session_types
    )
    # **The pardons only while attendance is on.** The appeal stage writes them back only then,
    # so offering them with the module off let a manager remove one, be told it was removed,
    # and approve a round still carrying it.
    if not await bot.module_service.is_attendance_enabled():
        staged_pardons = []

    state = PenaltyReviewState(
        round_id=round_id,
        division_id=division_id,
        submission_channel_id=channel.id,
        session_types_present=list(session_types),
        db_path=db_path,
        bot=bot,
        staged=staged,
        staged_appeals=staged_appeals,
        staged_pardons=staged_pardons,
        round_number=round_number,
        division_name=division_name,
        is_amendment=True,
    )

    view = PenaltyReviewView(state=state)
    content = await _render_prompt_content(state)
    message = await channel.send(content, view=view)
    state.prompt_message_id = message.id
    bot.add_view(view, message_id=message.id)

    return state


@dataclass
class AmendedSession:
    """One session's corrected classification, as stage one of an amendment writes it (#345)."""

    session_type: SessionType
    #: ``ParsedQualifyingRow`` / ``ParsedRaceRow`` dataclasses, or plain dicts of the same keys.
    driver_rows: list
    config_name: str | None
    fl_driver_override: int | None = None


async def amend_round_results(
    db_path: str,
    round_id: int,
    division_id: int,
    sessions: list[AmendedSession],
    amended_by: int,
    bot: LeagueBot,
) -> None:
    """Supersede the chosen sessions' results with their corrected classifications.

    The first of an amendment's three stages (#345). It snapshots every chosen session, writes
    each corrected classification and re-points its verdicts at it — all in one transaction, so
    the round is never left holding some sessions corrected and others not — then applies each
    session's points and recomputes the standings once. It posts nothing:
    :func:`run_amendment_review_stages` opens the next stage.

    Raises :class:`AmendmentWouldOrphanVerdictError` where a driver carrying a verdict is missing
    from their session's corrected classification, and ``SeasonImmutableError`` for an archived
    season, with nothing written in either case.
    """
    from leaguebot.core.services.season_service import SeasonImmutableError

    submitted_at = datetime.now(timezone.utc).isoformat()
    # The round as it stands, before any of it is overwritten (#345). Stage one commits, so an
    # amendment abandoned before its last stage is approved would otherwise leave the round
    # scored from the corrected classification and posted from the old one, for ever.
    await snapshot_before_amendment(db_path, round_id, [s.session_type for s in sessions])
    written: list[tuple[int, AmendedSession]] = []
    async with get_connection(db_path) as db:
        # Immutability guard: reject writes to archived seasons
        cursor = await db.execute(
            """
            SELECT s.status AS season_status, s.id AS season_id
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
                f"Round {round_id} belongs to an archived season — results cannot be amended."
            )
        season_id: int | None = season_row["season_id"] if season_row else None

        # **Who the round held before the rewrite goes into the snapshot** (#216), read here
        # because the next lines delete those rows. A driver an amendment strikes out entirely
        # is afterwards in no result at all, so nothing would find them to reconsider their
        # former-driver flag — which was the "never cleared" half of #216. Stage three passes
        # this list to `recompute_former_drivers_for_round` as `also_consider`.
        cursor = await db.execute(_PROFILES_IN_ROUND_SQL, (round_id, round_id))
        profiles_before = sorted({r["profile_id"] for r in await cursor.fetchall()})

        for session in sessions:
            written.append(
                (
                    await _write_amended_session_in_tx(
                        db, round_id, division_id, session, amended_by, submitted_at,
                        has_season=season_row is not None,
                    ),
                    session,
                )
            )
        if profiles_before:
            cursor = await db.execute(
                "SELECT pre_amendment_state FROM round_amend_channels WHERE round_id = ?",
                (round_id,),
            )
            snapshot_row = await cursor.fetchone()
            if snapshot_row is not None and snapshot_row["pre_amendment_state"]:
                snapshot = _json.loads(snapshot_row["pre_amendment_state"])
                snapshot["profiles_before"] = profiles_before
                await db.execute(
                    "UPDATE round_amend_channels SET pre_amendment_state = ? WHERE round_id = ?",
                    (_json.dumps(snapshot), round_id),
                )
        await db.commit()

    # Apply points from the config so points_awarded / fastest_lap_bonus are populated
    # before standings computation. (ParsedRaceRow / ParsedQualifyingRow carry no pts.)
    for session_result_id, session in written:
        if season_id is not None and session.config_name is not None:
            await _apply_points_from_config(
                db_path, session_result_id, season_id, session.config_name,
                session.session_type,
            )

    # **Stage one publishes nothing** (#345). It records the corrected classification and the
    # championship follows in the database; everything a league sees is rebuilt once, when the
    # appeal stage is approved, so that an amendment abandoned before then has nothing posted to
    # take back and the channels stay in round order.
    #
    # It used to post the round as "Provisional Results", which was wrong three ways. The
    # reports and appeals had not been reviewed, so the table carried the sanctions of the round
    # being replaced — and its post-race DSQ marks too, drawn from verdict rows stage one had
    # just re-pointed onto the new classification. The posting also *added* a message rather than
    # replacing one, so the original Final Results posting was orphaned above it. And an
    # amendment reverted before it completed left that provisional posting standing.
    from leaguebot.results.services import standings_service  # lazy import

    await standings_service.cascade_recompute_from_round(
        db_path, division_id, round_id, await _standings_names(db_path, division_id, bot)
    )

    # **A stage, not an outcome.** The `RESULT_AMENDED` entry belongs to the final stage.
    rctx = await _get_round_context(db_path, round_id)
    await bot.output_router.post_log(
        f"{await name_of_member(bot, amended_by)} | AMEND_STAGE_1 | Recorded\n"
        f"  season: {rctx['season_number']}, division: {rctx['division_name']!r}\n"
        f"  round: {rctx['round_number']}, sessions: "
        + ", ".join(f"{s.session_type.value}={s.config_name}" for s in sessions)
        + "\n"
        "  Corrected classification recorded. Nothing is published until the report and "
        "appeal stages are approved."
    )


async def _write_amended_session_in_tx(
    db,
    round_id: int,
    division_id: int,
    session: AmendedSession,
    amended_by: int,
    submitted_at: str,
    *,
    has_season: bool,
) -> int:
    """Replace one session's driver rows with its corrected classification, inside the caller's
    transaction, and return the session's ``session_results`` id.

    **Nothing is superseded here, and nothing keeps the classification being replaced.** The
    header is updated in place and the driver rows deleted outright, then re-inserted from the
    amendment. That is why `/results rounds amend` is a league admin's command rather than a
    league manager's (issue #116): amending a FINAL round overwrites what the league raced, and
    only the amendment's own snapshot, held until it completes, puts it back.
    """
    from leaguebot.core.services.driver_service import resolve_driver_profile_id

    def _get(obj, key, default=None):
        if hasattr(obj, key):
            return getattr(obj, key)
        if hasattr(obj, "get"):
            return obj.get(key, default)
        return default

    session_type = session.session_type
    await db.execute(
        """
        UPDATE session_results
        SET submitted_by = ?, submitted_at = ?, config_name = ?, fl_driver_override = ?
        WHERE round_id = ? AND session_type = ?
        """,
        (amended_by, submitted_at, session.config_name, session.fl_driver_override,
         round_id, session_type.value),
    )
    cursor = await db.execute(
        "SELECT id FROM session_results WHERE round_id = ? AND session_type = ?",
        (round_id, session_type.value),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(
            f"No session_results row found for round={round_id} session={session_type.value}"
        )
    session_result_id = row["id"]
    # **A verdict is re-pointed, not deleted** (#345). The driver rows below are deleted and
    # re-inserted, and `penalty_records` / `appeal_records` reference them without
    # `ON DELETE CASCADE` while `PRAGMA foreign_keys` is ON — so the delete was refused outright
    # for any round that had reached a penalty or appeal verdict, which is every round eligible
    # for amendment. Which driver a verdict belongs to is only knowable while the old rows
    # stand, so it is read first and applied after the re-insertion.
    verdicts = await _verdicts_by_driver(db, session_result_id, session_type)
    await _detach_verdicts(db, session_type, verdicts)
    if session_type.is_qualifying:
        await db.execute(
            "DELETE FROM qualifying_session_results WHERE session_result_id = ?",
            (session_result_id,),
        )
    else:
        await db.execute(
            "DELETE FROM race_session_results WHERE session_result_id = ?",
            (session_result_id,),
        )
    profile_map: dict[int, int | None] = {}
    rows_normalised: list[dict] = []
    for i, dr in enumerate(session.driver_rows, start=1):
        driver_user_id = _get(dr, "driver_user_id")
        drv_profile_id: int | None = None
        if has_season and driver_user_id is not None:
            drv_profile_id = await resolve_driver_profile_id(driver_user_id, db)
        if driver_user_id is not None:
            profile_map[driver_user_id] = drv_profile_id
        fp = _get(dr, "position") or _get(dr, "finishing_position") or i
        rows_normalised.append({
            "driver_user_id": driver_user_id,
            "team_instance_id": _get(dr, "team_instance_id"),
            "finishing_position": fp,
            "outcome": _get(dr, "outcome", OutcomeModifier.CLASSIFIED.value),
            "tyre": _get(dr, "tyre"),
            "best_lap": _get(dr, "best_lap"),
            "gap": _get(dr, "gap"),
            "total_time": _get(dr, "total_time"),
            "fastest_lap": _get(dr, "fastest_lap"),
            "ingame_penalties": _get(dr, "ingame_penalties"),
        })
    # **The former-driver flag is not touched here** (#216). Stage one writes the corrected
    # classification but publishes nothing and may yet be reverted, and an amendment can take a
    # driver *out* of a round as readily as put one in — so the flag is recomputed from the
    # round's results as a whole, by `recompute_former_drivers_for_round`, once stage three
    # commits the amendment. Marking row by row could only ever raise it.
    await _insert_new_tables_in_tx(
        db, session_result_id, session_type, rows_normalised, profile_map
    )
    # The verdicts follow their drivers onto the rows just inserted. Refuses, without writing,
    # where a driver carrying one is no longer in the classification.
    await _repoint_verdicts(db, session_result_id, session_type, verdicts, division_id)
    return session_result_id


# ---------------------------------------------------------------------------
# Validation — regex constants
# ---------------------------------------------------------------------------

# Times, gaps and lap gaps are read by the shared parsers (#362): one strict form across the
# bot, the one this paste always asked for.

# Optional fastest-lap override header:  FL: <@123>  (case-insensitive). The mention is the
# shared one (#362), so the header names a driver exactly as a row of the paste does.
_FL_OVERRIDE_RE = re.compile(rf"^FL:\s*{USER_MENTION}\s*$", re.IGNORECASE)

_OUTCOME_LITERALS: frozenset[str] = frozenset({"DNS", "DNF", "DSQ"})


def _parse_outcome(time_field: str) -> OutcomeModifier:
    """Infer OutcomeModifier from a race/qualifying time field value."""
    val = time_field.strip().upper()
    if val == "DNS":
        return OutcomeModifier.DNS
    if val == "DNF":
        return OutcomeModifier.DNF
    if val == "DSQ":
        return OutcomeModifier.DSQ
    return OutcomeModifier.CLASSIFIED


# ---------------------------------------------------------------------------
# Validation — parsed row types
# ---------------------------------------------------------------------------

@dataclass
class ParsedQualifyingRow:
    position: int
    driver_user_id: int
    #: The role the team column mentions, where it mentions one — which is refused, a team
    #: being typed by its shorthand (#381). None where the column holds anything else.
    team_role_id: int | None
    #: The canonical compound, or None where the submission recorded none (v7.8.0).
    tyre: str | None
    best_lap: str           # time string or DNS/DNF/DSQ (in-game result)
    gap: str                # delta string or "N/A"
    outcome: OutcomeModifier  # derived from best_lap
    #: The division's team *team_typed* names, set by ``validate_submission_block``. The
    #: shorthand is only what was typed; the team is what is stored (#375, #381).
    team_instance_id: int | None = None
    #: The team column as typed: the team's shorthand (#381). Echoed in a refusal.
    team_typed: str = ""


@dataclass
class ParsedRaceRow:
    position: int
    driver_user_id: int
    #: As on ParsedQualifyingRow.
    team_role_id: int | None
    total_time: str         # absolute time, delta, lap-gap, or outcome literal
    fastest_lap: str        # time string or "N/A"
    ingame_penalties: str   # time string (e.g. "5.000") or "N/A"
    outcome: OutcomeModifier  # derived from total_time
    # No post-race or appeal sanction: those are decided in the review stages, never pasted —
    # an amendment included, since #345 withdrew the two columns it used to take.
    #: As on ParsedQualifyingRow: the team the typed shorthand resolved to (#375, #381).
    team_instance_id: int | None = None
    #: As on ParsedQualifyingRow.
    team_typed: str = ""


# ---------------------------------------------------------------------------
# Validation — per-row functions
# ---------------------------------------------------------------------------


def _tyre_error(tyre: str) -> str | None:
    """The error a tyre field earns, or None where it is acceptable.

    The five compounds are a closed set the module defines (Constitution XIV.13, v7.8.0),
    so a submission naming something else names nothing the bot can draw, and is refused
    here rather than resolved to a placeholder six steps later. What is stored is the
    canonical spelling from :func:`canonicalise_tyre`, which is what lets the qualifying
    graphic find its file on every row without the league supplying any tyre artwork.

    An **empty** field is not an error and is stored as null: the submission of a session
    does not oblige a compound, and its absence is a state the graphic depicts rather than
    a gap it reports (XIV.13's per-field absent-datum declaration). `N/A` says the same
    thing and is accepted for it.
    """
    if records_no_tyre(tyre) or canonicalise_tyre(tyre) is not None:
        return None
    return f"Tyre must be one of {tyre_compound_list()}, got `{tyre}`"


#: What to tell somebody who pasted the amendment's old eight-column format (#345).
#:
#: Amending a round used to take two extra columns carrying the post-race and appeal penalties,
#: so that a re-inserted classification kept the sanctions already applied. The replay decides
#: those in its report and appeal stages instead, where they keep their justification and their
#: author — and where a disqualification can carry the mark that only a verdict record produces.
#: Pasting them now would apply each sanction twice, the stages adding to columns the paste had
#: already filled, so the format is refused rather than ignored.
_RETIRED_SANCTION_COLUMNS = (
    "Amending a round now takes the **same format as a first submission** — without the "
    "post-race and appeal penalty columns. Sanctions are no longer pasted: the amendment "
    "replays the round's report and appeal stages, where you keep or change each one with "
    "its justification intact."
)


def _field_count_error(parts: list[str], expected: int, line: str) -> str:
    """The error for a row of the wrong width, naming the retired columns where that is why."""
    got = len(parts)
    message = f"Expected {expected} comma-separated fields, got {got}: `{line.strip()}`"
    if got == expected + 2:
        message += f"\n{_RETIRED_SANCTION_COLUMNS}"
    return message


def _validate_qualifying_row_wizard(line: str) -> ParsedQualifyingRow | str:
    """Parse and validate a single qualifying-result line for the main submission wizard (6 fields).

    Fields: Position, Driver mention, Team (its shorthand), Tyre, Best Lap, Gap
    Postrace and appeal penalty fields default to N/A; outcome is derived from Best Lap.
    """
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) != 6:
        return _field_count_error(parts, 6, line)

    pos_str, driver_str, team_str, tyre, best_lap, gap = parts

    if not pos_str.isdigit():
        return f"Position must be a positive integer, got `{pos_str}`"
    position = int(pos_str)

    driver_user_id = parse_user_mention(driver_str)
    if driver_user_id is None:
        return f"Driver must be a Discord member mention (<@user_id>), got `{driver_str}`"

    # A team is typed by its shorthand (#381), and which team a text names is the division's
    # business, settled by `validate_submission_block`. Only an empty field is refused here.
    if not team_str:
        return "Team must be named, by its shorthand."
    team_role_id = parse_role_mention(team_str)

    tyre_error = _tyre_error(tyre)
    if tyre_error is not None:
        return tyre_error

    best_lap_upper = best_lap.upper()
    if best_lap_upper not in _OUTCOME_LITERALS and parse_time(best_lap) is None:
        return f"Best Lap must be a time (e.g. 1:23.456) or DNS/DNF/DSQ, got `{best_lap}`"

    if position != 1:
        gap_upper = gap.upper()
        if (
            gap_upper != "N/A"
            and parse_gap(gap) is None
            and parse_time(gap) is None
        ):
            return f"Gap must be a delta time (e.g. +1:23.456), an absolute time, or N/A, got `{gap}`"

    outcome = _parse_outcome(best_lap)
    return ParsedQualifyingRow(
        position=position,
        driver_user_id=driver_user_id,
        team_role_id=team_role_id,
        team_typed=team_str,
        tyre=canonicalise_tyre(tyre),
        best_lap=best_lap,
        gap=gap,
        outcome=outcome,
    )


def _validate_race_row_wizard(line: str, is_first: bool) -> ParsedRaceRow | str:
    """Parse and validate a single race-result line for the main submission wizard (6 fields).

    Fields: Position, Driver mention, Team (its shorthand), Total Time, Fastest Lap,
    Ingame Penalties
    Postrace and appeal penalty fields default to N/A; outcome is derived from Total Time.
    """
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) != 6:
        return _field_count_error(parts, 6, line)

    pos_str, driver_str, team_str, total_time, fastest_lap, ingame_penalties = parts

    if not pos_str.isdigit():
        return f"Position must be a positive integer, got `{pos_str}`"
    position = int(pos_str)

    driver_user_id = parse_user_mention(driver_str)
    if driver_user_id is None:
        return f"Driver must be a Discord member mention (<@user_id>), got `{driver_str}`"

    # A team is typed by its shorthand (#381), and which team a text names is the division's
    # business, settled by `validate_submission_block`. Only an empty field is refused here.
    if not team_str:
        return "Team must be named, by its shorthand."
    team_role_id = parse_role_mention(team_str)

    total_upper = total_time.upper()
    if is_first:
        if parse_time(total_time) is None:
            return (
                f"1st-place Total Time must be an absolute time (e.g. 1:23:45.678), "
                f"got `{total_time}`"
            )
    else:
        valid = (
            total_upper in _OUTCOME_LITERALS
            or parse_time(total_time) is not None
            or parse_gap(total_time) is not None
            or parse_lap_gap(total_time) is not None
        )
        if not valid:
            return (
                f"Total Time must be a time, delta (+M:SS.mmm), lap gap (x Laps), "
                f"or DNS/DNF/DSQ, got `{total_time}`"
            )

    fl_upper = fastest_lap.upper()
    if total_upper not in _OUTCOME_LITERALS:
        if fl_upper != "N/A" and parse_time(fastest_lap) is None:
            return f"Fastest Lap must be a time (e.g. 1:23.456) or N/A, got `{fastest_lap}`"

    ip_upper = ingame_penalties.upper()
    if ip_upper != "N/A" and parse_time(ingame_penalties) is None:
        return (
            f"Time Penalties must be a time (e.g. 5.000, 0:05.000) or N/A, "
            f"got `{ingame_penalties}`"
        )

    outcome = _parse_outcome(total_time)
    return ParsedRaceRow(
        position=position,
        driver_user_id=driver_user_id,
        team_role_id=team_role_id,
        team_typed=team_str,
        total_time=total_time,
        fastest_lap=fastest_lap,
        ingame_penalties=ingame_penalties,
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Validation — block-level function
# ---------------------------------------------------------------------------

def extract_fl_override(lines: list[str]) -> tuple[int | None, list[str]]:
    """Strip an optional ``FL: <@user_id>`` header from the start of *lines*.

    Returns ``(fl_driver_id, remaining_lines)``.  If the first line does not
    match the header pattern, returns ``(None, lines)`` unchanged.
    Only meaningful for race sessions; callers should ignore the override for
    qualifying submissions.
    """
    if not lines:
        return None, lines
    m = _FL_OVERRIDE_RE.match(lines[0].strip())
    if m is None:
        return None, lines
    return int(m.group(1)), lines[1:]


def extract_current_fl_override(
    lines: list[str], session_type: SessionType, current_of: Mapping[int, int]
) -> tuple[int | None, list[str]]:
    """`extract_fl_override` for a race session, the holder moved onto their current account.

    The rows of the paste are moved onto current accounts by `validate_submission_block`, so
    the override has to be moved the same way or `FL: <@old>` would not be found among rows
    that now read `<@new>` (issue #243). A qualifying session carries no override.
    """
    if session_type.is_qualifying:
        return None, lines
    fl_override, lines = extract_fl_override(lines)
    if fl_override is not None:
        fl_override = current_of.get(fl_override, fl_override)
    return fl_override, lines


_Row = TypeVar("_Row", ParsedQualifyingRow, ParsedRaceRow)


def split_validation(
    result: list[ParsedQualifyingRow | ParsedRaceRow] | list[str],
) -> tuple[list[str], list[ParsedQualifyingRow | ParsedRaceRow]]:
    """`validate_submission_block`'s errors and its rows, of which one is always empty."""
    errors = [item for item in result if isinstance(item, str)]
    rows = [item for item in result if not isinstance(item, str)]
    return errors, rows


def _rows_of_kind(
    rows: list[ParsedQualifyingRow | ParsedRaceRow], kind: type[_Row]
) -> list[_Row]:
    """*rows*, every one of which the validator parsed as *kind* for this session.

    A session is qualifying or a race, and the validator parses each of its rows as that one
    kind; a row of the other kind here is raised by name rather than silently dropped (#228).
    """
    typed = [row for row in rows if isinstance(row, kind)]
    if len(typed) != len(rows):
        raise RuntimeError(f"a session of {kind.__name__}s held a row of another kind")
    return typed


def validate_submission_block(
    lines: list[str],
    session_type: SessionType,
    division_driver_ids: set[int],
    team_of_role: Mapping[int, int],
    reserve_team_role_id: int | None,
    driver_team_map: dict[int, int],
    reserve_driver_ids: set[int] | None = None,
    other_active_assignments: dict[int, tuple[int, str]] | None = None,
    current_of: Mapping[int, int] | None = None,
    team_names: Mapping[int, str] | None = None,
    team_of_shorthand: Mapping[str, int] | None = None,
) -> list[ParsedQualifyingRow | ParsedRaceRow] | list[str]:
    """Validate all result lines for a session.

    Returns a list of parsed rows on success, or a list of error strings on failure.
    Checks: field formats; sequential positions from 1; drivers in division; valid team
    roles; each driver assigned to their own team (reserves may sub for any team);
    max 2 drivers per team per session; and, where *other_active_assignments* is given, that
    no driver disagrees with another ACTIVE session of the same round about which team they
    raced for.

    **A team is compared as the division's team, never as its role** (#375). *team_of_role*
    maps each non-reserve team's role to that team's id, and *team_of_shorthand* each such
    team's shorthand, casefolded, to the same id. A team is typed by its shorthand alone
    (#381): a role mention in the team column is refused. Each row's team is resolved through
    ``resolve_team_reference`` once, onto ``team_instance_id``; *driver_team_map* and
    *other_active_assignments* name teams by the same id. A league may give a team another
    role mid-season, and the rounds recorded before stay the team's all the same. So a
    refusal names a team by its name, from *team_names*: the role a driver's team holds now is
    not necessarily the one a round was recorded under. What was typed is echoed as typed.

    *current_of* maps a driver's past accounts to their current one (issue #243). Any account
    names the driver, so each row is moved onto the current account before anything is
    checked: the seats are held under it, and the row is stored under it. Two rows naming
    one driver by two accounts are then the same driver twice, and refused as such.
    """
    if reserve_driver_ids is None:
        reserve_driver_ids = set()
    is_qualifying = session_type.is_qualifying
    non_empty = [ln for ln in lines if ln.strip()]

    if not non_empty:
        return ["No result lines found — please submit at least one driver line."]

    errors: list[str] = []
    # Collected by kind, so that what only one kind has is read off rows of that kind.
    qualifying_rows: list[ParsedQualifyingRow] = []
    race_rows: list[ParsedRaceRow] = []

    for i, line in enumerate(non_empty, start=1):
        if is_qualifying:
            qualifying = _validate_qualifying_row_wizard(line)
            if isinstance(qualifying, str):
                errors.append(f"Row {i}: {qualifying}")
            else:
                qualifying_rows.append(qualifying)
        else:
            race = _validate_race_row_wizard(line, is_first=(i == 1))
            if isinstance(race, str):
                errors.append(f"Row {i}: {race}")
            else:
                race_rows.append(race)
    parsed_rows: list[ParsedQualifyingRow | ParsedRaceRow] = [*qualifying_rows, *race_rows]

    if errors:
        return errors

    if current_of:
        for row in parsed_rows:
            row.driver_user_id = current_of.get(row.driver_user_id, row.driver_user_id)

    # Positions must be contiguous from 1
    positions = sorted(r.position for r in parsed_rows)
    for expected, actual in enumerate(positions, start=1):
        if actual != expected:
            errors.append(
                f"Position gap: expected position {expected}, got {actual}. "
                "All positions must be sequential starting from 1."
            )
            break

    if errors:
        return errors

    # Each driver must appear exactly once
    seen_driver_ids: set[int] = set()
    for row in parsed_rows:
        if row.driver_user_id in seen_driver_ids:
            errors.append(
                f"Row {row.position}: driver <@{row.driver_user_id}> "
                "appears more than once in this submission."
            )
        seen_driver_ids.add(row.driver_user_id)

    if errors:
        return errors

    # Each driver must be in the division
    for row in parsed_rows:
        if row.driver_user_id not in division_driver_ids:
            errors.append(
                f"Row {row.position}: driver <@{row.driver_user_id}> "
                "is not registered in this division."
            )

    # Each row's team must be a non-reserve team of the division, typed by its shorthand, and
    # is resolved here to that team (#375, #381). Reserves sub *into* a real team, so the
    # reserve team is never among those that may be named. Nor is a team with no role, as it
    # never has been (reconfirmed 2026-09-22): `team_of_shorthand` holds neither.
    role_of_team = {team: role for role, team in team_of_role.items()}
    namable = [
        {"id": team, "name": shorthand} for shorthand, team in (team_of_shorthand or {}).items()
    ]

    def _team(team_id: int) -> str:
        # By name where one is known, as every caller supplies; the team's role otherwise.
        name = (team_names or {}).get(team_id)
        return f"**{name}**" if name else f"<@&{role_of_team.get(team_id)}>"

    for row in parsed_rows:
        reference = resolve_team_reference(row.team_typed, namable, scope=" of this division")
        row.team_instance_id = reference.team["id"] if reference.team is not None else None
        if row.team_instance_id is None:
            errors.append(f"Row {row.position}: {reference.refusal}")

    # Driver must be assigned to the stated team — unless the driver is a reserve,
    # in which case they may sub for any valid non-reserve team.
    for row in parsed_rows:
        if row.driver_user_id in reserve_driver_ids:
            # Reserve driver: only check that the target team role is a real team
            # (already validated above); no further team-match restriction.
            continue
        mapped_team = driver_team_map.get(row.driver_user_id)
        if mapped_team is None:
            continue  # already reported above as "not in division"
        if row.team_instance_id != mapped_team:
            errors.append(
                f"Row {row.position}: driver <@{row.driver_user_id}> "
                f"submitted as {row.team_typed} "
                f"but is assigned to {_team(mapped_team)}."
            )

    # A driver already recorded under a different team by another ACTIVE session of this
    # round is rejected here too. The seat-based check above exempts a reserve, since a
    # reserve may legitimately sub for any team across different rounds — this closes the
    # gap it leaves within one round: a reserve (or anyone else) recorded for team A in one
    # session and team B in another of the same round is never legitimate.
    if other_active_assignments:
        for row in parsed_rows:
            existing = other_active_assignments.get(row.driver_user_id)
            if existing is not None and existing[0] != row.team_instance_id:
                existing_team, existing_session = existing
                existing_label = existing_session.replace("_", " ").title()
                errors.append(
                    f"Row {row.position}: driver <@{row.driver_user_id}> was recorded under "
                    f"{_team(existing_team)} in {existing_label} of this round, but is "
                    f"submitted here as {row.team_typed}."
                )

    # Max 2 drivers per team (counting reserve subs)
    team_driver_counts: dict[int, int] = {}
    for row in parsed_rows:
        if row.team_instance_id is not None:
            team_driver_counts[row.team_instance_id] = (
                team_driver_counts.get(row.team_instance_id, 0) + 1
            )
    for team, count in team_driver_counts.items():
        if count > 2:
            errors.append(
                f"Team {_team(team)} has {count} drivers submitted — maximum is 2."
            )

    if errors:
        return errors

    # Race ordering: positions must respect time-type hierarchy.
    # Lead-lap times (abs/delta) → lapped drivers (lap gap) → non-finishers (DNS/DNF/DSQ).
    # A lower position number must never have a higher category than a subsequent driver.
    if not is_qualifying:
        def _race_time_category(row: "ParsedRaceRow") -> int:
            # A postrace/appeal DSQ puts the driver in the DSQ group
            # regardless of what total_time says.
            if row.outcome == OutcomeModifier.DSQ:
                return 4
            tt = row.total_time.upper()
            if tt == "DNS":
                return 3
            if tt == "DNF":
                return 2
            if parse_lap_gap(row.total_time) is not None:
                return 1  # lapped finisher
            return 0      # lead-lap finisher (absolute or delta time)

        _CATEGORY_LABEL = {0: "lead-lap time", 1: "lap gap (+x Laps)", 2: "DNF", 3: "DNS", 4: "DSQ"}
        rows_by_pos = sorted(race_rows, key=lambda r: r.position)
        max_cat = 0
        for row in rows_by_pos:
            cat = _race_time_category(row)
            if cat < max_cat:
                errors.append(
                    f"Row {row.position}: driver <@{row.driver_user_id}> has a "
                    f"{_CATEGORY_LABEL[cat]} but appears after a "
                    f"{_CATEGORY_LABEL[max_cat]} entry. "
                    "Finishing order must be: lead-lap finishers first, "
                    "then lapped drivers (+x Laps), then DNS/DNF/DSQ."
                )
                break
            max_cat = max(max_cat, cat)

        # Within lapped drivers, lap counts must be non-decreasing with position.
        _LAP_INT_RE = re.compile(r"(\d+)")
        prev_lap_count: int | None = None
        for row in rows_by_pos:
            if parse_lap_gap(row.total_time) is not None:
                m = _LAP_INT_RE.search(row.total_time)
                if m:
                    lap_count = int(m.group(1))
                    if prev_lap_count is not None and lap_count < prev_lap_count:
                        errors.append(
                            f"Row {row.position}: driver <@{row.driver_user_id}> "
                            f"is {lap_count} lap(s) behind but appears after a driver "
                            f"who is {prev_lap_count} lap(s) behind. "
                            "Lap counts must be non-decreasing with position."
                        )
                        break
                    prev_lap_count = lap_count

    if errors:
        return errors

    # Qualifying ordering: CLASSIFIED → DNF → DNS → DSQ
    # Uses derived outcome so penalty-field DSQ is handled correctly.
    if is_qualifying:
        def _qual_outcome_category(row: "ParsedQualifyingRow") -> int:
            # 0=CLASSIFIED, 1=DNF, 2=DNS, 3=DSQ
            if row.outcome == OutcomeModifier.DSQ:
                return 3
            if row.outcome == OutcomeModifier.DNS:
                return 2
            if row.outcome == OutcomeModifier.DNF:
                return 1
            return 0

        _QUAL_CATEGORY_LABEL = {0: "classified", 1: "DNF", 2: "DNS", 3: "DSQ"}
        qualifying_by_pos = sorted(qualifying_rows, key=lambda r: r.position)
        max_qual_cat = 0
        for qualifying_row in qualifying_by_pos:
            cat = _qual_outcome_category(qualifying_row)
            if cat < max_qual_cat:
                errors.append(
                    f"Row {qualifying_row.position}: driver <@{qualifying_row.driver_user_id}> has outcome "
                    f"{_QUAL_CATEGORY_LABEL[cat]} but appears after a "
                    f"{_QUAL_CATEGORY_LABEL[max_qual_cat]} entry. "
                    "Qualifying order must be: classified first, then DNF, then DNS, then DSQ."
                )
                break
            max_qual_cat = max(max_qual_cat, cat)

    if errors:
        return errors

    # G1: derive Best Lap for qualifying DNF entries that have a valid gap
    if is_qualifying:
        p1_row = next((r for r in qualifying_rows if r.position == 1), None)
        if (
            p1_row is not None
            and p1_row.best_lap.upper() not in _OUTCOME_LITERALS
            and parse_time(p1_row.best_lap) is not None
        ):
            try:
                p1_ms = _parse_time_to_ms(p1_row.best_lap)
                for row in qualifying_rows:
                    if row.best_lap.upper() == "DNF" and (
                        parse_gap(row.gap) is not None or parse_time(row.gap) is not None
                    ):
                        gap_ms = _parse_time_to_ms(row.gap)
                        row.best_lap = _format_time_ms(p1_ms + gap_ms)
            except ValueError:
                pass  # parsing failed; leave best_lap unchanged

    return parsed_rows


# ---------------------------------------------------------------------------
# Time parsing helpers (for DNF best-lap derivation)
# ---------------------------------------------------------------------------

def _parse_time_to_ms(s: str) -> int:
    """A time, or a gap to the leader, in milliseconds — read by the shared parsers (#362)."""
    ms = parse_gap(s) if s.strip().startswith("+") else parse_time(s)
    if ms is None:
        raise ValueError(f"Cannot parse time string {s!r}")
    return ms


def _format_time_ms(total_ms: int) -> str:
    """Format milliseconds to 'M:SS.mmm' or 'SS.mmm'."""
    ms = total_ms % 1000
    total_s = total_ms // 1000
    mins = total_s // 60
    secs = total_s % 60
    if mins == 0:
        return f"{secs}.{ms:03d}"
    return f"{mins}:{secs:02d}.{ms:03d}"


# ---------------------------------------------------------------------------
# Config selection view
# ---------------------------------------------------------------------------

class _ConfigSelectView(LeagueView):
    """Button view for selecting an attached points config.

    **A league manager's, and it asked nothing at all until 2026-09-10.** This view is
    posted publicly into the submission and amendment channels — never ephemerally — from
    three call sites, so every member who could read one of those channels could decide
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
    """

    def __init__(
        self,
        config_names: list[str],
        config: Any | None = None,
        *,
        session: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.selected: str | None = None
        self._config = config
        self._session = session
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
                self.selected = _name
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

    def _what(self, name: str) -> str:
        """The button as the log channel names it, with the choice it belongs to."""
        named = f"the “{name}” button of the points-configuration choice"
        return f"{named} for {self._session}" if self._session else named

    async def _record_choice(
        self, interaction: discord.Interaction, what: str, name: str
    ) -> None:
        """Write the one line a choice leaves in the log channel, in the success form."""
        router = getattr(getattr(interaction, "client", None), "output_router", None)
        if router is None:
            return
        await router.post_log(
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
# Private helpers
# ---------------------------------------------------------------------------

async def _get_round_context(db_path: str, round_id: int) -> dict:
    """Return season_id, season_number, round_number, round_format and
    division_name for a round.

    `_resubmit_collection_task` reads `season_id` and `round_format`, and neither was selected
    until issue #210: the task raised `KeyError` on its first line, inside a background task
    nobody awaited, so a resubmission never collected anything.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT s.id AS season_id, s.season_number, r.round_number,
                   r.format AS round_format, d.name AS division_name
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"Round {round_id} not found or has no season")
    return dict(row)


class DivisionValidationData(NamedTuple):
    """What a division's seats say a submission may record, as ``validate_submission_block``
    reads it. Every team is named by its id in the division (#375)."""

    division_driver_ids: set[int]
    #: Each non-reserve team's Discord role -> that team's id: the one place a role typed in a
    #: submission is resolved to the team it names.
    team_of_role: dict[int, int]
    reserve_team_role_id: int | None
    #: Seated driver -> the id of the team they sit in.
    driver_team_map: dict[int, int]
    #: Drivers seated in the reserve team. They appear in ``division_driver_ids`` and
    #: ``driver_team_map`` too, but may stand in for any team.
    reserve_driver_ids: set[int]
    #: Team id -> its name, for the messages that name a team.
    team_names: dict[int, str]
    #: Each non-reserve team's shorthand, casefolded -> that team's id: how a submission names
    #: a team (#381). Only a team with a role is here, as in ``team_of_role``: a team with no
    #: role stays out of a submission (reconfirmed 2026-09-22).
    team_of_shorthand: dict[str, int]


async def _build_division_validation_data(division_id: int, bot: LeagueBot) -> DivisionValidationData:
    """Build validation structures for the given division.

    A team whose role is not mapped cannot be typed, and is left out. Its drivers are then
    outside ``division_driver_ids`` too, and refused as not in the division.
    """
    # Only drivers whose placements are confirmed may be scored (issue #220).
    div_teams = await bot.team_service.get_division_teams(division_id, committed_only=True)
    teams_with_roles = await bot.team_service.get_teams_with_roles()

    name_to_role: dict[str, int] = {
        t["name"]: t["role_id"]
        for t in teams_with_roles
        if t["role_id"] is not None
    }

    division_driver_ids: set[int] = set()
    team_of_role: dict[int, int] = {}
    reserve_team_role_id: int | None = None
    driver_team_map: dict[int, int] = {}
    reserve_driver_ids: set[int] = set()
    team_names: dict[int, str] = {}
    team_of_shorthand: dict[str, int] = {}

    for team in div_teams:
        role_id = name_to_role.get(team["name"])
        if role_id is None:
            continue
        team_names[team["id"]] = team["name"]
        if team["is_reserve"]:
            reserve_team_role_id = role_id
        else:
            team_of_role[role_id] = team["id"]
            team_of_shorthand[team["name"].casefold()] = team["id"]
        for seat in team["seats"]:
            uid_str = seat.get("discord_user_id")
            if uid_str is not None:
                uid = int(uid_str)
                division_driver_ids.add(uid)
                driver_team_map[uid] = team["id"]
                if team["is_reserve"]:
                    reserve_driver_ids.add(uid)

    return DivisionValidationData(
        division_driver_ids, team_of_role, reserve_team_role_id, driver_team_map,
        reserve_driver_ids, team_names, team_of_shorthand,
    )


async def other_active_team_assignments(
    db_path: str,
    round_id: int,
    exclude_session_type: SessionType,
    *,
    also_exclude: "tuple[SessionType, ...] | list[SessionType]" = (),
) -> dict[int, tuple[int, str]]:
    """driver_user_id -> (team_instance_id, session_type value) recorded by another ACTIVE
    session of *round_id*.

    Feeds the cross-session team check in ``validate_submission_block``: a driver's team
    must agree across every session of one round, and this is what the second session's
    validation reads to catch a disagreement the first session already recorded.

    *also_exclude* leaves out further sessions — those an amendment is replacing, whose recorded
    rows are about to go and must not be held against their own correction (#345).
    """
    excluded = [exclude_session_type.value, *(st.value for st in also_exclude)]
    marks = _placeholders(excluded)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"""
            SELECT sr.session_type, x.driver_user_id, x.team_instance_id
            FROM session_results sr
            JOIN qualifying_session_results x ON x.session_result_id = sr.id
            WHERE sr.round_id = ? AND sr.status = 'ACTIVE' AND sr.session_type NOT IN ({marks})
            UNION ALL
            SELECT sr.session_type, x.driver_user_id, x.team_instance_id
            FROM session_results sr
            JOIN race_session_results x ON x.session_result_id = sr.id
            WHERE sr.round_id = ? AND sr.status = 'ACTIVE' AND sr.session_type NOT IN ({marks})
            """,  # noqa: S608 — only placeholders are interpolated
            (round_id, *excluded, round_id, *excluded),
        )
        rows = await cursor.fetchall()

        # A session recorded before the driver changed account stands under the old one;
        # the check compares drivers, so both are read as the account in use (issue #243).
        from leaguebot.core.services.driver_service import current_account_map

        cursor = await db.execute(
            "SELECT 1 FROM rounds r JOIN divisions d ON d.id = r.division_id "
            "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?",
            (round_id,),
        )
        server_row = await cursor.fetchone()
        current_of = (
            await current_account_map(db) if server_row else {}
        )

    result: dict[int, tuple[int, str]] = {}
    for row in rows:
        uid = current_of.get(row["driver_user_id"], row["driver_user_id"])
        result.setdefault(uid, (row["team_instance_id"], row["session_type"]))
    return result


async def current_accounts(db_path: str) -> dict[int, int]:
    """`driver_service.current_account_map` on a connection of its own, for the paste paths."""
    from leaguebot.core.services.driver_service import current_account_map

    async with get_connection(db_path) as db:
        return await current_account_map(db)


def _make_slug(name: str) -> str:
    """Convert a division name to a Discord-channel-safe slug."""
    slug = name.lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return slug[:24]  # keep channel name short


# ---------------------------------------------------------------------------
# Points computation — applied after save_session_result
# ---------------------------------------------------------------------------

async def _apply_points_from_config(
    db_path: str,
    session_result_id: int,
    season_id: int,
    config_name: str,
    session_type: SessionType,
) -> None:
    """Load season config entries, compute points for each driver row, and UPDATE the DB.

    This is called after save_session_result so that the new result tables have
    points_awarded and fastest_lap_bonus populated from the chosen points configuration.
    """
    async with get_connection(db_path) as db:
        if await _apply_points_in_tx(db, session_result_id, season_id, config_name, session_type):
            await db.commit()


async def _apply_points_in_tx(
    db,
    session_result_id: int,
    season_id: int,
    config_name: str,
    session_type: SessionType,
) -> bool:
    """Do what `_apply_points_from_config` does on the caller's connection, without committing.

    Returns whether anything was written. Split out for `replace_round_results`, which scores
    the sessions it inserts in the same transaction, so a crash can never leave a round's new
    results in place with no points on them.
    """
    from leaguebot.results.services.standings_service import compute_points_for_session  # lazy import

    # Load config entries for (season, config, session_type)
    entries_cursor = await db.execute(
        "SELECT position, points FROM season_points_entries "
        "WHERE season_id = ? AND config_name = ? AND session_type = ? "
        "ORDER BY position",
        (season_id, config_name, session_type.value),
    )
    entry_rows = await entries_cursor.fetchall()

    # Load FL config
    fl_cursor = await db.execute(
        "SELECT fl_points, fl_position_limit FROM season_points_fl "
        "WHERE season_id = ? AND config_name = ? AND session_type = ?",
        (season_id, config_name, session_type.value),
    )
    fl_row = await fl_cursor.fetchone()

    # Load driver result rows for this session
    if session_type.is_qualifying:
        dsr_cursor = await db.execute(
            "SELECT id, driver_user_id, team_instance_id, finishing_position, "
            "outcome, NULL AS fastest_lap "
            "FROM qualifying_session_results "
            "WHERE session_result_id = ?",
            (session_result_id,),
        )
    else:
        dsr_cursor = await db.execute(
            "SELECT id, driver_user_id, team_instance_id, finishing_position, "
            "outcome, fastest_lap "
            "FROM race_session_results "
            "WHERE session_result_id = ?",
            (session_result_id,),
        )
    dsr_rows = await dsr_cursor.fetchall()

    # Load FL driver override (if any) from the session header
    fl_override_cursor = await db.execute(
        "SELECT fl_driver_override FROM session_results WHERE id = ?",
        (session_result_id,),
    )
    fl_override_row = await fl_override_cursor.fetchone()
    fl_driver_override: int | None = (
        fl_override_row["fl_driver_override"] if fl_override_row else None
    )

    if not entry_rows and fl_row is None:
        # No config data — nothing to compute (0 points stays as-is)
        return False

    config_entries = [
        PointsConfigEntry(
            id=0,
            config_id=0,
            session_type=session_type,
            position=r["position"],
            points=r["points"],
        )
        for r in entry_rows
    ]

    fl_config: PointsConfigFastestLap | None = None
    if fl_row is not None:
        fl_config = PointsConfigFastestLap(
            id=0,
            config_id=0,
            session_type=session_type,
            fl_points=fl_row["fl_points"],
            fl_position_limit=fl_row["fl_position_limit"],
        )

    driver_rows = [
        DriverSessionResult(
            id=r["id"],
            session_result_id=session_result_id,
            driver_user_id=r["driver_user_id"],
            team_instance_id=r["team_instance_id"],
            finishing_position=r["finishing_position"],
            outcome=OutcomeModifier(r["outcome"]),
            tyre=None,
            best_lap=None,
            gap=None,
            total_time=None,
            fastest_lap=r["fastest_lap"],
            time_penalties=None,
            post_steward_total_time=None,
            post_race_time_penalties=None,
            points_awarded=0,
            fastest_lap_bonus=0,
            is_superseded=False,
        )
        for r in dsr_rows
    ]

    # Mutates driver_rows in-place with computed points_awarded / fastest_lap_bonus
    compute_points_for_session(driver_rows, config_entries, fl_config, session_type, fl_override=fl_driver_override)

    # Persist to new tables (keyed by session_result_id + driver_user_id)
    if session_type.is_qualifying:
        for row in driver_rows:
            await db.execute(
                "UPDATE qualifying_session_results "
                "SET points_awarded = ? "
                "WHERE session_result_id = ? AND driver_user_id = ?",
                (row.points_awarded, session_result_id, row.driver_user_id),
            )
    else:
        for row in driver_rows:
            await db.execute(
                "UPDATE race_session_results "
                "SET points_awarded = ?, fastest_lap_bonus = ? "
                "WHERE session_result_id = ? AND driver_user_id = ?",
                (row.points_awarded, row.fastest_lap_bonus, session_result_id, row.driver_user_id),
            )
    return True




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


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

async def run_result_submission_job(round_id: int, bot: LeagueBot) -> None:
    """APScheduler job entry point — runs the full submission wizard for a round.

    Triggered at each round's scheduled start time. Creates a transient submission
    channel, collects results session by session with validation, prompts for config
    selection, persists everything, and then closes the channel.
    """
    db_path: str = bot.db_path

    # ------------------------------------------------------------------
    # 1. Load round context
    # ------------------------------------------------------------------
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.id           AS round_id,
                   r.division_id,
                   r.round_number,
                   r.format       AS round_format,
                   r.status       AS round_status,
                   d.name         AS division_name,
                   drc.results_channel_id,
                   s.id           AS season_id,
                   s.season_number
            FROM rounds r
            JOIN divisions d   ON d.id = r.division_id
            JOIN seasons s     ON s.id = d.season_id
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        ctx = await cursor.fetchone()

    if ctx is None:
        log.warning(
            "run_result_submission_job: round %s not found — skipping", round_id
        )
        return

    if ctx["round_status"] == "CANCELLED":
        log.info(
            "run_result_submission_job: round %s is CANCELLED — skipping", round_id
        )
        return

    division_id: int = ctx["division_id"]
    division_name: str = ctx["division_name"]
    round_number: int = ctx["round_number"]
    season_id: int = ctx["season_id"]
    season_number: int = ctx["season_number"]
    round_format = RoundFormat(ctx["round_format"])
    results_channel_id: int | None = ctx["results_channel_id"]

    # ------------------------------------------------------------------
    # 2. The round's date has arrived — move it off NOT_RUN, and the module guard
    # ------------------------------------------------------------------
    # This job fires at the round's scheduled time for every round, whatever the format and
    # whatever the modules, so it is where the one clock-driven transition belongs.
    #
    # Where results are enabled the round begins waiting for them. Where they are not, nobody
    # will ever enter a result, so the round has nothing to wait for and ends here — otherwise
    # it would sit outstanding for ever and its season could never be completed, which is issue
    # #154 over again for every league that does not run the results module.
    results_enabled = await bot.module_service.is_results_enabled()
    arrived_at = (
        RoundStatus.AWAITING_RESULTS.value if results_enabled else RoundStatus.FINAL.value
    )
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET status = ? WHERE id = ? AND status = ?",
            (arrived_at, round_id, RoundStatus.NOT_RUN.value),
        )
        await db.commit()

    if not results_enabled:
        # The round just reached a terminal state, so its division may now be finished.
        from leaguebot.core.services.season_service import SeasonService

        await SeasonService(db_path).refresh_division_status(division_id)

        # The division finishing may have been the season's last: a season with a window open or
        # placements to confirm is wound down and moves to Pending completion at once (#220).
        try:
            await bot.season_service.wind_down_ongoing(bot)
        except Exception:  # noqa: BLE001 — never fail the job on the season's next stage
            log.exception("could not wind the season down")
        log.info(
            "run_result_submission_job: results module disabled — round %s "
            "closed without results",
            round_id,
        )
        return

    # ------------------------------------------------------------------
    # 3. Get guild + results channel
    # ------------------------------------------------------------------
    guild = await league_guild(bot)
    if guild is None:
        log.error(
            "run_result_submission_job: the league's server is not in the cache for round %s",
            round_id,
        )
        return

    if results_channel_id is None:
        log.error(
            "run_result_submission_job: no results_channel_id for division %s (round %s)",
            division_id,
            round_id,
        )
        return

    results_channel = as_text_channel(guild.get_channel(results_channel_id))
    if results_channel is None:
        log.error(
            "run_result_submission_job: results channel %s not found (round %s)",
            results_channel_id,
            round_id,
        )
        return

    # ------------------------------------------------------------------
    # 4. Load validation data
    # ------------------------------------------------------------------
    try:
        (
            division_driver_ids,
            team_of_role,
            reserve_team_role_id,
            driver_team_map,
            reserve_driver_ids,
            team_names,
            team_of_shorthand,
        ) = await _build_division_validation_data(division_id, bot)
    except Exception:
        log.exception(
            "run_result_submission_job: failed to build validation data for round %s",
            round_id,
        )
        return

    # ------------------------------------------------------------------
    # 5. Create submission channel
    # ------------------------------------------------------------------
    # Look up both of the league's roles and the bot-command channel for channel setup
    server_cfg = await bot.config_service.get_server_config()
    interaction_role: discord.Role | None = None
    league_admin_role: discord.Role | None = None
    bot_cmd_channel_id: int | None = None
    if server_cfg is not None:
        bot_cmd_channel_id = server_cfg.interaction_channel_id
        if server_cfg.interaction_role_id:
            interaction_role = guild.get_role(server_cfg.interaction_role_id)
        if server_cfg.league_admin_role_id:
            league_admin_role = guild.get_role(server_cfg.league_admin_role_id)

    try:
        sub_channel = await create_submission_channel(
            guild,
            division_name,
            season_number,
            round_number,
            round_id,
            db_path,
            bot_cmd_channel_id=bot_cmd_channel_id,
            interaction_role=interaction_role,
            league_admin_role=league_admin_role,
        )
    except discord.HTTPException:
        log.exception(
            "run_result_submission_job: failed to create submission channel for round %s",
            round_id,
        )
        return

    # ------------------------------------------------------------------
    # 6. Opening message
    # ------------------------------------------------------------------
    sessions = get_sessions_for_format(round_format)
    is_sprint = round_format is RoundFormat.SPRINT
    session_list_str = ", ".join(
        results_formatter.format_session_label(s, is_sprint=is_sprint) for s in sessions
    )
    # The league managers enter the results, so they are the ones pinged. The division's role
    # cannot see this channel, and a mention there notified nobody (#136).
    mention_str = f" <@&{interaction_role.id}>" if interaction_role is not None else ""
    await sub_channel.send(
        f"✅ Results submission open for **Round {round_number}** ({division_name})"
        f" - {round_format.label}."
        f" Sessions: {session_list_str}.{mention_str}\n\n"
        "Submit results one driver per line (comma-separated), or type `CANCELLED` to skip a session."
    )

    # ------------------------------------------------------------------
    # 7. Load attached config names for this season
    # ------------------------------------------------------------------
    from leaguebot.results.services import season_points_service  # lazy import to avoid circular

    config_names = await season_points_service.get_attached_config_names(db_path, season_id)

    # ------------------------------------------------------------------
    # 8. Per-session collection loop
    # ------------------------------------------------------------------
    cancelled_sessions: set[SessionType] = set()
    last_author: discord.abc.User | None = None
    for session_type in sessions:
        label = results_formatter.format_session_label(session_type, is_sprint=is_sprint)

        if session_type.is_qualifying:
            format_hint = (
                "Format: `Position, @Driver, Team, Tyre, BestLap, Gap`\n"
                "Team: its shorthand.\n"
                f"Tyre: {tyre_compound_list()} (or blank for none recorded)."
            )
        else:
            format_hint = (
                "Format: `Position, @Driver, Team, TotalTime, FastestLap, TimePenalties`\n"
                "Team: its shorthand.\n"
                "Optional first line: `FL: @Driver` to designate the fastest-lap holder "
                "(use when two drivers share the same lap time)."
            )

        await sub_channel.send(
            f"📋 Submit **{label}** results (one driver per line), or type `CANCELLED`.\n"
            f"{format_hint}"
        )

        while True:
            msg = await bot.wait_for(
                "message",
                check=lambda m, ch=sub_channel: (
                    m.channel.id == ch.id and not m.author.bot
                ),
            )

            last_author = msg.author
            content = msg.content.strip()

            if content.upper() == "CANCELLED":
                held = await held_by_amendment(
                    db_path, round_id, division_id,
                    then=f"Type `CANCELLED` for **{label}** again then.",
                )
                if held:
                    await sub_channel.send(held)
                    continue
                await save_session_result(
                    db_path=db_path,
                    round_id=round_id,
                    division_id=division_id,
                    session_type=session_type,
                    status="CANCELLED",
                    config_name=None,
                    submitted_by=msg.author.id,
                    driver_rows=[],
                )
                await sub_channel.send(f"✅ **{label}** marked as CANCELLED.")
                await results_channel.send(
                    f"🚫 **Round {round_number} — {label}** ({division_name}): "
                    "This session was cancelled."
                )
                cancelled_sessions.add(session_type)
                break

            # Validate the block
            lines = content.splitlines()
            current_of = await current_accounts(db_path)
            fl_override, lines = extract_current_fl_override(lines, session_type, current_of)
            other_assignments = await other_active_team_assignments(
                db_path, round_id, session_type
            )
            result = validate_submission_block(
                lines,
                session_type,
                division_driver_ids,
                team_of_role,
                reserve_team_role_id,
                driver_team_map,
                reserve_driver_ids,
                other_active_assignments=other_assignments,
                current_of=current_of,
                team_names=team_names,
                team_of_shorthand=team_of_shorthand,
            )

            validation_errors, parsed_rows = split_validation(result)
            if validation_errors:
                error_list = "\n".join(f"• {e}" for e in validation_errors)
                await bot.output_router.post_log(
                    f"{msg.author.display_name} (<@{msg.author.id}>) | RESULT_SUBMISSION_REJECTED | \n"
                    f"  season: {season_number}, division: {division_name!r}\n"
                    f"  round: {round_number}, session: {session_type.value}\n"
                    f"  input:\n```\n{content[:500]}\n```",
                )
                await sub_channel.send(
                    f"❌ Validation failed:\n{error_list}\nPlease correct and resubmit."
                )
                continue

            # Validate FL override references a driver in the submitted results
            if fl_override is not None:
                submitted_driver_ids = {r.driver_user_id for r in parsed_rows}
                if fl_override not in submitted_driver_ids:
                    await sub_channel.send(
                        f"❌ FL override <@{fl_override}> is not in the submitted results. "
                        "Please correct and resubmit."
                    )
                    continue

            # Config selection
            selected_config: str | None = None
            if len(config_names) == 1:
                selected_config = config_names[0]
                await sub_channel.send(
                    f"✅ Auto-selected config **{selected_config}** (only one attached)."
                )
            elif len(config_names) > 1:
                view = _ConfigSelectView(
                    config_names,
                    server_cfg,
                    session=f"the {label} of round {round_number} ({division_name})",
                )
                config_msg = await sub_channel.send(
                    "🔧 Select the points configuration for this session:",
                    view=view,
                )
                await view.wait()
                selected_config = view.selected
                try:
                    await config_msg.edit(
                        content=f"🔧 Config selected: **{selected_config}**", view=None
                    )
                except discord.HTTPException:
                    pass
            else:
                # No configs attached — this should have been caught at approval but handle gracefully
                log.warning(
                    "run_result_submission_job: no configs attached to season %s", season_id
                )
                await sub_channel.send(
                    "⚠️ No points configuration attached to this season. "
                    "Results will be saved without a config."
                )

            held = await held_by_amendment(
                db_path, round_id, division_id, then=f"Paste **{label}** again then."
            )
            if held:
                await sub_channel.send(held)
                continue

            # Log accepted input (with raw content for auditability)
            await bot.output_router.post_log(
                f"{msg.author.display_name} (<@{msg.author.id}>) | RESULT_SUBMISSION_ACCEPTED | Success\n"
                f"  season: {season_number}, division: {division_name!r}\n"
                f"  round: {round_number}, session: {session_type.value}\n"
                f"  input:\n```\n{content[:500]}\n```",
            )

            # Convert parsed rows to dicts for DB insertion
            if session_type.is_qualifying:
                driver_rows_data = [
                    _row_dict_from_qualifying(r)
                    for r in _rows_of_kind(parsed_rows, ParsedQualifyingRow)
                ]
            else:
                driver_rows_data = [
                    _row_dict_from_race(r)
                    for r in _rows_of_kind(parsed_rows, ParsedRaceRow)
                ]

            await save_session_result(
                db_path=db_path,
                round_id=round_id,
                division_id=division_id,
                session_type=session_type,
                status="ACTIVE",
                config_name=selected_config,
                submitted_by=msg.author.id,
                driver_rows=driver_rows_data,
                fl_driver_override=fl_override,
            )

            # Compute and store points_awarded / fastest_lap_bonus from the chosen config
            if selected_config is not None:
                # Reload the session_result_id we just inserted
                async with get_connection(db_path) as _db:
                    _cur = await _db.execute(
                        "SELECT id FROM session_results WHERE round_id = ? AND session_type = ?",
                        (round_id, session_type.value),
                    )
                    _sr = await _cur.fetchone()
                if _sr is not None:
                    await _apply_points_from_config(
                        db_path, _sr["id"], season_id, selected_config, session_type
                    )

            await sub_channel.send(f"✅ **{label}** results saved.")
            break  # advance to next session

    # ------------------------------------------------------------------
    # 9+10. Enter penalty-review state (posts interim results/standings,
    #       keeps channel open, posts penalty review prompt).
    #       Skip entirely if every session was cancelled — nothing to review.
    # ------------------------------------------------------------------
    if cancelled_sessions == set(sessions):
        await sub_channel.send(
            "⏭️ All sessions for this round were cancelled — no penalty review required."
        )
        await close_submission_channel(sub_channel.id, round_id, guild, db_path)
        return
    # The review opens as a change on the queue, `results.review.open`, which posts the interim
    # results and then the review's prompt (#439).
    await ask_review_open(
        bot, sub_channel, round_id, round_number,
        {"label": "Provisional Results", "publish": True}, actor=last_author, say=True,
    )


async def ask_review_open(
    bot: LeagueBot,
    sub_channel: discord.TextChannel,
    round_id: int,
    round_number: int,
    payload: Mapping[str, Any],
    *,
    actor: Any = None,
    origin: ChangeOrigin = ChangeOrigin.MEMBER,
    say: bool = False,
) -> None:
    """Ask the queue to open round *round_id*'s penalty review, with *payload*'s keys beside the
    round's (`results.review.open`), in the name of *actor*, the member who pasted the last
    session or pressed Cancel; the bot's own request (a failure) names none.

    A paste answers no interaction, so with *say* the channel is told that the review is being
    opened, with its job number, and where the queue is stopped, at which job. Where the request
    is refused or no longer due, the channel is told the round is kept as it is and the review is
    put back when the bot next starts. The one place a round's review is asked for from a
    submission, for the first one and for a resubmission's return (#439).
    """
    change_id = await bot.change_queue.ask(
        "results.review.open",
        {"round_id": round_id, **payload},
        actor=actor,
        origin=origin,
        what=f"the penalty review of round {round_number}",
    )
    if change_id is None:
        line = (
            f"⚠️ The penalty review of round {round_number} could not be asked for. The results "
            "are saved; the review is put back when the bot next starts."
        )
    elif say:
        first_job, stopped_at = await bot.change_queue.job_numbers(change_id)
        line = f"⏳ The penalty review is being opened (job #{first_job})."
        if stopped_at is not None:
            line += (
                f" The queue is stopped at job #{stopped_at}, so it opens once that job is "
                "cleared."
            )
    else:
        return
    await sub_channel.send(line)


# ---------------------------------------------------------------------------
# Resubmission flow — triggered by the 🔄 Resubmit Initial Results button
# ---------------------------------------------------------------------------

@dataclass
class CollectedSession:
    """One session of a resubmission, validated and held until the last session is in."""

    session_type: SessionType
    status: str
    config_name: str | None
    submitted_by: int | None
    driver_rows: list[dict] = field(default_factory=list)
    fl_driver_override: int | None = None


async def replace_round_results(
    db_path: str,
    round_id: int,
    division_id: int,
    season_id: int,
    collected: list[CollectedSession],
) -> None:
    """Replace every session of a round with a resubmission's, in one transaction.

    A resubmission supersedes the round's results: the ones already submitted stand, published
    and counted, until every session has been entered again (issue #210). So nothing is written
    while the manager pastes, and this is the one write at the end. The delete of the old
    results, the insert of the new, their points and the clearing of the channel's
    `resubmitting` flag either all land or none do — a crash part-way cannot leave a round
    holding some of each, or new results with no points on them.

    `results_posted` is cleared in the same transaction. The round is still in penalty review,
    so a crash after the commit is recovered as a review whose results were never posted, and
    the new ones go out then.

    Raises `SeasonImmutableError` for an archived season, with the old results untouched.
    """
    async with get_connection(db_path) as db:
        try:
            await db.execute("DELETE FROM session_results WHERE round_id = ?", (round_id,))
            for session in collected:
                session_result_id = await _save_session_result_in_tx(
                    db, round_id, division_id, session.session_type, session.status,
                    session.config_name, session.submitted_by, session.driver_rows,
                    fl_driver_override=session.fl_driver_override,
                )
                if session.config_name is not None:
                    await _apply_points_in_tx(
                        db, session_result_id, season_id, session.config_name,
                        session.session_type,
                    )
            await db.execute(
                "UPDATE round_submission_channels SET resubmitting = 0, results_posted = 0, "
                "resubmit_prompt_message_id = NULL WHERE round_id = ?",
                (round_id,),
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise


class ResubmissionCancelView(LeagueView):
    """The **Cancel** button on a resubmission's announcement.

    Pressing it ends the resubmission and keeps the round's earlier results, which were never
    touched: nothing is written until the last session is in. Not persistent — a restart ends
    the resubmission itself, and the restart sweep takes the button down.
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
        from leaguebot.results.services.penalty_wizard import _button, _require_lm

        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        self.cancelled_by = interaction.user.id
        self.pressed.set()
        await interaction.response.send_message(
            "Resubmission cancelled. The earlier results stand.", ephemeral=True
        )
        self.stop()


async def _next_paste(bot: LeagueBot, sub_channel, cancel_view: ResubmissionCancelView | None):
    """The next message pasted into *sub_channel*, or None if Cancel was pressed first.

    Races the paste against the button, as the amend channel does. Where both land together
    the cancel wins: the manager has said to stop.
    """
    import asyncio

    paste = asyncio.ensure_future(
        bot.wait_for(
            "message",
            check=lambda m, ch=sub_channel: (m.channel.id == ch.id and not m.author.bot),
        )
    )
    if cancel_view is None:
        return await paste
    if cancel_view.cancelled:
        paste.cancel()
        return None
    pressed = asyncio.ensure_future(cancel_view.pressed.wait())
    await asyncio.wait({paste, pressed}, return_when=asyncio.FIRST_COMPLETED)
    for pending in (paste, pressed):
        if not pending.done():
            pending.cancel()
    if cancel_view.cancelled or not paste.done() or paste.cancelled():
        return None
    return paste.result()


CANCELLED_LINE = "↩️ **Resubmission cancelled.** The earlier results stand."


async def _take_down_cancel_button(cancel_view: ResubmissionCancelView | None) -> None:
    if cancel_view is None:
        return
    cancel_view.stop()
    if cancel_view.message is None:
        return
    await take_down_cancel_message(cancel_view.message)


async def take_down_cancel_message(message: discord.Message | discord.PartialMessage) -> None:
    """Take the Cancel button off the resubmission's announcement; one already gone is no fault.

    Called by `_take_down_cancel_button` and by the review's job doing the same (#439).
    """
    try:
        await message.edit(view=None)
    except (discord.NotFound, discord.HTTPException):
        pass


async def send_cancelled_line(sub_channel: discord.TextChannel) -> None:
    """Tell the submission channel that a resubmission was cancelled and the earlier results
    stand. The one place it is sent, for `_resubmit_collection_task` and the review's job."""
    await sub_channel.send(CANCELLED_LINE)


def _member_of(guild: discord.Guild, user_id: int | None) -> discord.abc.Snowflake | None:
    """The member *user_id* on *guild*, or a bare reference to them where they have left, so
    that a change asked in their name records who they were; nobody for no id."""
    if user_id is None:
        return None
    return guild.get_member(user_id) or discord.Object(id=user_id)


async def _ask_return_to_review(
    bot: LeagueBot,
    sub_channel: discord.TextChannel,
    round_id: int,
    round_number: int,
    cancel_view: ResubmissionCancelView | None,
    *,
    returning: str,
    actor: Any = None,
    origin: ChangeOrigin = ChangeOrigin.BOT,
) -> None:
    """End a resubmission without replacing anything, and ask for the penalty review back.

    Used when the manager cancels (*returning* ``"cancelled"``, in their name) and when the
    resubmission fails before the swap (``"failed"``, the bot's). Either way the round's results
    are the ones it held before Resubmit was pressed, and they are already posted, so the review
    is asked for with nothing published. What becomes of the request is `results.review.open`'s:
    it clears `resubmitting`, takes the Cancel button off the message named in the request, and
    puts the prompt back before it writes the cancel's line in the channel and the log (#439).
    """
    message_id = None
    if cancel_view is not None:
        cancel_view.stop()
        if cancel_view.message is not None:
            message_id = cancel_view.message.id
    await ask_review_open(
        bot, sub_channel, round_id, round_number,
        {"publish": False, "returning": returning, "cancel_message_id": message_id},
        actor=actor, origin=origin,
    )


async def enter_resubmit_flow(
    interaction: discord.Interaction,
    state,
    *,
    what: str | None = None,
) -> None:
    """Discard staged penalties and pardons and restart collection over the round's existing results.

    Called from the pw_resubmit button callback in PenaltyReviewView.

    Nothing is deleted here. A resubmission supersedes the round's results: they stand until
    every session has been entered again, and `replace_round_results` swaps them out then
    (issue #210). What changes now is the channel. `resubmitting` is set, which lets the
    manager's pastes through the review channel's message guard and tells a restart what was
    lost, and the penalty review prompt and any approval message are taken down, so that nobody
    can approve the results being replaced or press Resubmit a second time and start a second
    collector. Both would refuse if pressed regardless, as the review has moved on (#402).

    A submission channel that cannot be found refuses the resubmission before anything is
    discarded: with nowhere to collect in, the review is all the round has. The refusal is
    recorded as *what* refused — the Resubmit button and its review, named by the button that
    calls this. A call without one names the button from *state*, which only a test makes.
    """
    import asyncio
    import json as _json
    from leaguebot.results.services.penalty_wizard import (
        _button,
        _delete_review_message,
        _take_down_approval,
    )

    bot = state.bot
    db_path: str = bot.db_path
    round_id = state.round_id
    division_id = state.division_id
    sub_channel = bot.get_channel(state.submission_channel_id)
    if sub_channel is None:
        await refuse(
            interaction,
            "❌ The submission channel could not be found, so the results cannot be resubmitted.",
            what=what or _button("🔄 Resubmit Initial Results", state),
        )
        return

    discarded_count = len(state.staged)
    discarded_detail = [
        {
            "driver_user_id": sp.driver_user_id,
            "session_type": sp.session_type.value,
            "penalty_type": sp.penalty_type,
            "penalty_seconds": sp.penalty_seconds,
        }
        for sp in state.staged
    ]
    # The pardons go with them, and went unrecorded until #356. The justification is logged
    # as it was when the pardon was staged: the log channel is the one place it may appear.
    discarded_pardons = [
        {
            "driver_user_id": sp.driver_user_id,
            "pardon_type": sp.pardon_type,
            "justification": sp.justification,
        }
        for sp in state.staged_pardons
    ]

    # One line for the press, written before anything is discarded: once they are gone there
    # is no other record of what was staged. It names the penalties and the pardons both.
    try:
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM seasons s JOIN divisions d ON d.season_id = s.id WHERE d.id = ?",
                (division_id,),
            )
            srv_row = await cursor.fetchone()
        if srv_row:
            await bot.output_router.post_log(
                f"{interaction_member(interaction)} | RESULTS_RESUBMISSION | Started\n"
                f"  round_id: {round_id} ({state.division_name})\n"
                f"  discarded_count: {discarded_count}\n"
                f"  discarded: {_json.dumps(discarded_detail)}\n"
                f"  discarded_pardons_count: {len(discarded_pardons)}\n"
                f"  discarded_pardons: {_json.dumps(discarded_pardons)}",
            )
    except Exception:
        log.exception("enter_resubmit_flow: error writing resubmission audit log (round %s)", round_id)

    state.staged.clear()
    state.staged_pardons.clear()

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET resubmitting = 1, resubmit_started_by = ? "
            "WHERE round_id = ?",
            (interaction.user.id, round_id),
        )
        await db.commit()

    # The prompt and any approval message go together: the approval message outlived the prompt
    # once, and its Approve finalised the round on the results being replaced (#402). The
    # collection depends on neither.
    await _delete_review_message(state, state.prompt_message_id)
    await _take_down_approval(state)

    cancel_view = ResubmissionCancelView(state)
    announcement = await sub_channel.send(
        "⚠️ **Results resubmission started.** "
        "The results already submitted stand until every session has been entered again. "
        "Press **Cancel** to stop and keep them.",
        view=cancel_view,
    )
    cancel_view.message = announcement
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE round_submission_channels SET resubmit_prompt_message_id = ? "
                "WHERE round_id = ?",
                (announcement.id, round_id),
            )
            await db.commit()
    except Exception:
        # Only the restart sweep reads it, to take the button down.
        log.exception("enter_resubmit_flow: could not record the announcement (round %s)", round_id)

    asyncio.create_task(
        _resubmit_collection_task(
            round_id,
            division_id,
            bot,
            sub_channel,
            cancel_view,
            interaction=interaction,
            what=what,
        ),
        name=f"resubmit_r{round_id}",
    )

    await interaction.followup.send(
        "🔄 Resubmission started. Please re-enter the session results in the submission channel.",
        ephemeral=True,
    )


def _collected_team_assignments(
    collected: list[CollectedSession],
    exclude_session_type: SessionType,
) -> dict[int, tuple[int, str]]:
    """What `other_active_team_assignments` answers, read from a resubmission in progress.

    driver_user_id -> (team_instance_id, session_type value) from every ACTIVE session collected so
    far other than *exclude_session_type*.
    """
    result: dict[int, tuple[int, str]] = {}
    for session in collected:
        if session.status != "ACTIVE" or session.session_type is exclude_session_type:
            continue
        for row in session.driver_rows:
            result.setdefault(
                row["driver_user_id"], (row["team_instance_id"], session.session_type.value)
            )
    return result


async def _resubmit_collection_task(
    round_id: int,
    division_id: int,
    bot: LeagueBot,
    sub_channel: discord.TextChannel | None,
    cancel_view: ResubmissionCancelView | None = None,
    *,
    interaction: discord.Interaction | None = None,
    what: str | None = None,
) -> None:
    """Re-run the session collection loop against an existing submission channel.

    Mirrors run_result_submission_job but skips channel creation, and writes nothing until
    the last session is in: each validated session is held as a `CollectedSession`, and
    `replace_round_results` swaps the lot for the round's existing results in one transaction.
    The earlier results stand until then (issue #210). On completion asks the queue for
    `results.review.open`, publishing the new results as "Provisional Results (amended)".

    *cancel_view* is the announcement's Cancel button. Pressed, or where the resubmission fails
    before the swap, the review is asked for back (`_ask_return_to_review`) with the results the
    round had.

    *interaction* is the Resubmit press that started this, and *what* names its button and
    review, as the press's refusals do; without one the button alone is named. A resubmission
    that fails before any paste (the round is gone, or the division's data cannot be read) is
    recorded as that button's failure, through `report_failure`, naming who pressed it and
    saying the earlier results stand (#482). The notices to the submission channel stay.
    Without an *interaction*, which only a test makes, nothing is recorded.
    """
    import asyncio

    async def _record_failure(error: BaseException) -> None:
        if interaction is not None:
            await report_failure(
                interaction,
                error,
                what=what or "the “🔄 Resubmit Initial Results” button",
                outcome="The earlier results stand.",
            )

    if sub_channel is None:
        log.error("_resubmit_collection_task: sub_channel not found for round %s", round_id)
        return

    db_path: str = bot.db_path
    # `_get_round_context` raises rather than returning None. Uncaught, that is one more
    # exception inside a background task nobody awaits, and the manager who has just been told
    # to paste the results again would be left pasting into a channel nothing reads.
    try:
        ctx = await _get_round_context(db_path, round_id)
    except ValueError as exc:
        log.exception("_resubmit_collection_task: round %s not found", round_id)
        await sub_channel.send("❌ Resubmission failed: this round could not be found.")
        await _record_failure(exc)
        return

    season_id: int = ctx["season_id"]
    division_name: str = ctx["division_name"]
    round_number: int = ctx["round_number"]
    round_format = RoundFormat(ctx["round_format"])

    # Read for `_ConfigSelectView`, which asks the league manager tier of whoever presses it.
    server_cfg = await bot.config_service.get_server_config()

    guild = await league_guild(bot)
    if guild is None:
        log.error("_resubmit_collection_task: the league's server was not found for round %s", round_id)
        return

    async def _cancelled() -> None:
        await _ask_return_to_review(
            bot, sub_channel, round_id, round_number, cancel_view,
            returning="cancelled",
            actor=_member_of(guild, cancel_view.cancelled_by if cancel_view is not None else None),
            origin=ChangeOrigin.MEMBER,
        )

    try:
        (
            division_driver_ids,
            team_of_role,
            reserve_team_role_id,
            driver_team_map,
            reserve_driver_ids,
            team_names,
            team_of_shorthand,
        ) = await _build_division_validation_data(division_id, bot)
    except Exception as exc:
        log.exception("_resubmit_collection_task: failed to build validation data for round %s", round_id)
        await sub_channel.send(
            "❌ Resubmission failed: could not load division data. The earlier results still stand."
        )
        await _record_failure(exc)
        await _ask_return_to_review(
            bot, sub_channel, round_id, round_number, cancel_view, returning="failed"
        )
        return

    from leaguebot.results.services import season_points_service
    config_names = await season_points_service.get_attached_config_names(db_path, season_id)

    sessions = get_sessions_for_format(round_format)
    is_sprint = round_format is RoundFormat.SPRINT
    session_list_str = ", ".join(
        results_formatter.format_session_label(s, is_sprint=is_sprint) for s in sessions
    )
    await sub_channel.send(
        f"🔄 Resubmitting results for **Round {round_number}** ({division_name})."
        f" Sessions: {session_list_str}.\n\n"
        "Submit results one driver per line (comma-separated), or type `CANCELLED` to skip a session."
    )

    cancelled_sessions: set[SessionType] = set()
    collected: list[CollectedSession] = []
    last_author_id: int | None = None
    for session_type in sessions:
        label = results_formatter.format_session_label(session_type, is_sprint=is_sprint)
        if session_type.is_qualifying:
            format_hint = (
                "Format: `Position, @Driver, Team, Tyre, BestLap, Gap`\n"
                "Team: its shorthand.\n"
                f"Tyre: {tyre_compound_list()} (or blank for none recorded)."
            )
        else:
            format_hint = (
                "Format: `Position, @Driver, Team, TotalTime, FastestLap, TimePenalties`\n"
                "Team: its shorthand.\n"
                "Optional first line: `FL: @Driver` to override fastest-lap holder."
            )

        await sub_channel.send(
            f"📋 Submit **{label}** results (one driver per line), or type `CANCELLED`.\n{format_hint}"
        )

        while True:
            msg = await _next_paste(bot, sub_channel, cancel_view)
            if msg is None:
                await _cancelled()
                return
            content = msg.content.strip()

            if content.upper() == "CANCELLED":
                held = await held_by_amendment(
                    db_path, round_id, division_id,
                    then=f"Type `CANCELLED` for **{label}** again then.",
                )
                if held:
                    await sub_channel.send(held)
                    continue
                last_author_id = msg.author.id
                collected.append(
                    CollectedSession(session_type, "CANCELLED", None, msg.author.id)
                )
                await sub_channel.send(f"✅ **{label}** marked as CANCELLED.")
                cancelled_sessions.add(session_type)
                break

            lines = content.splitlines()
            current_of = await current_accounts(db_path)
            fl_override, lines = extract_current_fl_override(lines, session_type, current_of)
            # Checked against the sessions of this resubmission, not the round's stored
            # results: those are the ones being replaced, and may be wrong in exactly the way
            # that made the manager resubmit.
            other_assignments = _collected_team_assignments(collected, session_type)
            result = validate_submission_block(
                lines, session_type, division_driver_ids, team_of_role,
                reserve_team_role_id, driver_team_map, reserve_driver_ids,
                other_active_assignments=other_assignments,
                current_of=current_of,
                team_names=team_names,
                team_of_shorthand=team_of_shorthand,
            )

            validation_errors, parsed_rows = split_validation(result)
            if validation_errors:
                error_list = "\n".join(f"• {e}" for e in validation_errors)
                await sub_channel.send(f"❌ Validation failed:\n{error_list}\nPlease correct and resubmit.")
                continue

            if fl_override is not None:
                submitted_ids = {r.driver_user_id for r in parsed_rows}
                if fl_override not in submitted_ids:
                    await sub_channel.send(
                        f"❌ FL override <@{fl_override}> is not in the submitted results. Please correct and resubmit."
                    )
                    continue

            selected_config: str | None = None
            if len(config_names) == 1:
                selected_config = config_names[0]
                await sub_channel.send(f"✅ Auto-selected config **{selected_config}**.")
            elif len(config_names) > 1:
                view = _ConfigSelectView(
                    config_names,
                    server_cfg,
                    session=f"the {label} of round {round_number} ({division_name})",
                )
                config_msg = await sub_channel.send("🔧 Select the points configuration for this session:", view=view)
                # **Raced against Cancel**, as the amendment's choice is. Waited on alone, a
                # Cancel pressed here was answered "cancelled" while the resubmission sat on
                # a choice nobody would make. Both waits are held by name and the one left
                # pending is cancelled; where both land together the cancel wins.
                choice_wait = asyncio.ensure_future(view.wait())
                cancel_wait = (
                    asyncio.ensure_future(cancel_view.pressed.wait())
                    if cancel_view is not None
                    else None
                )
                waits = {choice_wait} if cancel_wait is None else {choice_wait, cancel_wait}
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                for pending in waits:
                    if not pending.done():
                        pending.cancel()
                stopped = cancel_view is not None and cancel_view.cancelled
                selected_config = None if stopped else view.selected
                try:
                    await config_msg.edit(
                        content=(
                            "🔧 Resubmission cancelled."
                            if stopped
                            else f"🔧 Config selected: **{selected_config}**"
                        ),
                        view=None,
                    )
                except discord.HTTPException:
                    pass
                if stopped:
                    await _cancelled()
                    return

            held = await held_by_amendment(
                db_path, round_id, division_id, then=f"Paste **{label}** again then."
            )
            if held:
                await sub_channel.send(held)
                continue

            if session_type.is_qualifying:
                driver_rows_data = [
                    _row_dict_from_qualifying(r)
                    for r in _rows_of_kind(parsed_rows, ParsedQualifyingRow)
                ]
            else:
                driver_rows_data = [
                    _row_dict_from_race(r) for r in _rows_of_kind(parsed_rows, ParsedRaceRow)
                ]

            last_author_id = msg.author.id
            collected.append(
                CollectedSession(
                    session_type, "ACTIVE", selected_config, msg.author.id,
                    driver_rows_data, fl_override,
                )
            )
            await sub_channel.send(f"✅ **{label}** results received.")
            break

    # The points configuration may have been chosen while Cancel was pressed.
    if cancel_view is not None and cancel_view.cancelled:
        await _cancelled()
        return

    try:
        await replace_round_results(db_path, round_id, division_id, season_id, collected)
    except Exception:
        log.exception("_resubmit_collection_task: failed to replace results for round %s", round_id)
        await sub_channel.send(
            "❌ Resubmission failed: the new results could not be saved. "
            "The earlier results still stand."
        )
        await _ask_return_to_review(
            bot, sub_channel, round_id, round_number, cancel_view, returning="failed"
        )
        return
    await _take_down_cancel_button(cancel_view)

    if cancelled_sessions == set(sessions):
        await sub_channel.send("⏭️ All sessions were cancelled — no penalty review required.")
        await close_submission_channel(sub_channel.id, round_id, guild, db_path)
        return

    await ask_review_open(
        bot, sub_channel, round_id, round_number,
        {"label": "Provisional Results (amended)", "publish": True},
        actor=_member_of(guild, last_author_id), say=True,
    )



async def sweep_expired_amendments(bot: LeagueBot, *, now: datetime | None = None) -> int:
    """Revert every amendment whose stages went unapproved past their deadline.

    Returns how many were reverted. The round keeps the classification it raced rather than a
    half-amended one, and the league is told in the log channel — an amendment quietly undone
    would be worse than one left hanging.

    Each is claimed before it is reverted (:func:`_claim_amendment`), so a stage being approved
    at the moment the sweep runs is left to finish rather than reverted from under it. A revert
    that fails hands the deadline back, and the next sweep tries again.

    *now* is taken as a parameter so a test can pin it alongside the deadline it seeds; left out
    it is the wall clock.
    """
    # Normalised to UTC-aware, because the stored deadline always is: comparing it against a
    # naive *now* raises `TypeError` outside the guard below and aborts the whole sweep, leaving
    # every lapsed amendment unreverted rather than one (#345).
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT round_id, session_types, expires_at, started_by "
            "FROM round_amend_channels WHERE expires_at IS NOT NULL"
        )
        candidates = [dict(row) for row in await cursor.fetchall()]

    reverted = 0
    for row in candidates:
        try:
            deadline = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            continue
        if deadline > moment:
            continue

        round_id: int = row["round_id"]
        # A stage in hand on the queue, stopped included, is left alone ("Leave it while stuck"):
        # once a league admin discards it, the next sweep undoes the amendment.
        if await stage_in_hand(bot.db_path, round_id):
            continue
        sessions = await _amendment_sessions_of(bot.db_path, round_id)
        taken = await _claim_amendment(bot.db_path, round_id)
        if taken is None:
            continue
        try:
            await revert_abandoned_amendment(bot.db_path, round_id, bot)
        except Exception:  # noqa: BLE001 — one stuck round must not stop the rest
            log.exception("sweep_expired_amendments: could not revert round %s", round_id)
            await _rearm_amendment(bot.db_path, round_id, taken)
            continue
        reverted += 1

        await _close_amendment_channel(
            bot.db_path, await league_guild(bot), round_id, reason="Amendment abandoned",
        )

        # In the standard lapse form, naming the member who started it (`started_by`).
        try:
            rctx = await _get_round_context(bot.db_path, round_id)
            await record_abandoned(
                bot,
                row["started_by"],
                what=(
                    f"`/results rounds amend` of round {rctx['round_number']} "
                    f"({rctx['division_name']})"
                ),
                lapsed=True,
                detail=(
                    f"season: {rctx['season_number']}, sessions: {_sessions_text(sessions)}\n"
                    "The amendment's report and appeal stages were not approved in time, so the "
                    f"round has been put back as it was. {AMENDMENT_RE_RUN}"
                ),
            )
        except Exception:  # noqa: BLE001 — the revert stands whether or not it was announced
            log.exception(
                "sweep_expired_amendments: could not announce the revert of round %s", round_id
            )
    return reverted
