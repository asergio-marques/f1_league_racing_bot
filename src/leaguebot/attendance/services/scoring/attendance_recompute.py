"""Recalculating a division's attendance from a round forward, and what stands in its way.

`_recalculate_forward` recomputes a round and carries the running total through every finalised
round after it on one connection, and `recalculation_faults` says what stands between a season and a recalculation.
"""

from __future__ import annotations

import aiosqlite

from leaguebot.attendance.services.scoring import attendance_scoring
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.division import DivisionStatus
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.seasons.season_service import division_round_states_on, season_divisions_on
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.results.services.module.division_channels import verdicts_channels_on


async def _recalculate_forward(
    db_path: str, round_id: int, division_id: int, *, recompute: str, db: aiosqlite.Connection
) -> list[int]:
    """Recompute *round_id* and carry the running total through every finalised round after
    it, in **one transaction** (#187). Returns the ids of the rounds it touched, in order.

    *recompute* says how much of the attended record is rebuilt from the results, and is one
    of three values. Every one of them redistributes the points of every round it touches;
    they differ only in whose attended flags are rebuilt first.

    ``"all"``
        Every round from this one on is rebuilt from its results, which is what
        `/attendance sync` asks for: it repairs a round whose recording failed, not only a
        total carried from an amended one.
    ``"round"``
        Only the named round is rebuilt; the rounds after it are redistributed (FR-030), as
        an amendment approval needs.
    ``"none"``
        None is. The caller has already recorded the attendance under the upgrade-only rule
        and wants the totals carried forward and nothing else.

        **It deliberately does not rebuild the attended flags.** On this path the recording
        was done by ``record_attendance_from_results_on``, which only ever upgrades a driver
        to present and never revokes it (FR-003): a driver dropped from the results was given
        no chance to justify themselves, and the record errs in their favour.
        ``record_attendance_from_results_full_recompute`` can flip present to absent, so it is
        not reached under ``"none"`` — by design, not by oversight. A driver's total is the sum
        of their rounds, but a copy of the answer is stored on every round's row as
        ``total_points_after``, and the sheet and the sanctions read a copy rather than the
        sum; carrying the totals forward keeps the season's final sheet, drawn against the
        last round with results, from publishing a figure worked out from an old one.

    The rebuild is a **full** one (FR-028), without the upgrade-only constraint, so it can
    flip a driver from present to absent. That is why it is not reached under ``"none"``.

    *db* is the transaction of the save that asks, which commits it: an approval's save hands
    its own, so the recalculation is saved with everything else the approval writes (#439).
    This opens and commits no connection of its own.

    **Why one transaction** (#187). Every step used to open and commit a connection of its own,
    so a failure in the middle of the propagation left a division's attendance points correct up
    to one round and stale from the next on, and nothing a league manager can run repeats it.
    Committing once means the recalculation lands whole or not at all. The posting that follows
    stays outside it: it is Discord I/O, and a write transaction held open across it would block
    every other writer for as long as Discord took to answer.

    **The rules of an amendment's recalculation** (FR-028–FR-031), carried out here with
    ``recompute="round"``: the upgrade-only rule does not apply, this being a deliberate
    correction that may flip *attended* in either direction (FR-028); existing pardons are
    preserved (FR-029); and ``total_points_after`` is propagated forward through every
    subsequent finalised round (FR-030). The sheet and the sanctions that follow (FR-031) are
    posted against the **last** round recalculated and not the one named (#238): each round's
    stored total is the driver's total as at that round, so the division's current standing is on
    the last of them.
    """
    if recompute not in {"all", "round", "none"}:
        raise ValueError(f"unknown recompute mode {recompute!r}")
    # FR-028: full recompute without upgrade-only constraint.
    if recompute in {"all", "round"}:
        await attendance_scoring.record_attendance_from_results_full_recompute(
            db_path, round_id, division_id, db=db
        )

    # FR-029: pardons are already persisted — just recompute points using them.
    await attendance_scoring.distribute_attendance_points(db_path, round_id, division_id, db=db)

    # FR-030: propagate total_points_after forward through subsequent rounds.
    # The rounds are core's to give: those of the division after this one that are
    # finalised or awaiting their appeal verdicts, in round order.
    states = await division_round_states_on(db, division_id)
    named_number = states[round_id][0] if round_id in states else None
    subsequent_rounds = [
        later_id
        for later_id, (number, status) in states.items()
        if named_number is not None
        and number > named_number
        and status in (RoundStatus.AWAITING_APPEAL_VERDICTS.value, RoundStatus.FINAL.value)
    ]

    for sub_round_id in subsequent_rounds:
        if recompute == "all":
            await attendance_scoring.record_attendance_from_results_full_recompute(
                db_path, sub_round_id, division_id, db=db
            )
        await attendance_scoring.distribute_attendance_points(db_path, sub_round_id, division_id, db=db)

    return [round_id, *subsequent_rounds]


async def recalculation_faults(
    db_path: str,
    season_id: int,
    guild,
    bot: LeagueBot | None = None,
    *,
    cancelled_too: bool = False,
) -> list[str]:
    """What stands between this season and recalculating its attendance (#187).

    Returns the faults as lines a league can read, and an empty list where every division's
    attendance posting could be made.

    Called, through `AttendanceAfterReview.recalculation_faults`, by results' `approval_faults`
    before an approved amendment writes anything, because the approval recalculates attendance
    as part of its cascade and a recalculation that cannot be posted used to be swallowed under
    the same false success as the reposting.

    **The verdicts channel is asked for only where sanctions could actually fall.**
    `owed_sanctions` is empty when both the autosack and
    autoreserve thresholds are unset, so a league using neither must not be refused for a
    channel it will never post to.

    **A cancelled division is passed over unless *cancelled_too*.** `/attendance sync` reads
    only live divisions and keeps that reading. The approval of a points amendment asks with
    *cancelled_too* (owner, 2026-10-06, "Refuse at the press"): it recalculates and reposts
    the raced rounds of a division since cancelled too, so the channels of that division are
    part of its question, and the results specification's "every division's" holds.
    """
    from leaguebot.image.services.image_validity_service import aspect_attaches_files
    from leaguebot.results.services.publishing import results_post_service

    if guild is None:
        # The results half already reports an absent guild; saying it twice would have a
        # manager repairing one thing from two lines.
        return []

    bot_member = results_post_service.bot_member(guild, bot)
    if bot_member is None:
        # The results half already refuses on an unresolvable bot member; saying it twice
        # would have a manager repairing one thing from two lines.
        return []

    async with get_connection(db_path) as db:
        # The division's own rows are core's, its verdicts channel results', and its attendance
        # channel attendance's: each is asked of its owner and the three are put together here.
        divisions = [
            d
            for d in await season_divisions_on(db, season_id)
            if cancelled_too or d.status != DivisionStatus.CANCELLED
        ]
        verdicts_channels = await verdicts_channels_on(db)
        cursor = await db.execute(
            "SELECT division_id, attendance_channel_id FROM attendance_division_config"
        )
        attendance_channels = {
            int(r["division_id"]): r["attendance_channel_id"] for r in await cursor.fetchall()
        }

        cursor = await db.execute(
            """
            SELECT autoreserve_threshold, autosack_threshold
            FROM attendance_config
            """,
        )
        thresholds = await cursor.fetchone()

    sanctions_possible = bool(
        thresholds
        and (thresholds["autoreserve_threshold"] or thresholds["autosack_threshold"])
    )
    attendance_graphics = await aspect_attaches_files(bot, "attendance")

    faults: list[str] = []
    for division in sorted(divisions, key=lambda d: (d.tier, d.id)):
        division_name = division.name or f"division {division.id}"
        attendance_channel = attendance_channels.get(division.id)
        if attendance_channel:
            fault = results_post_service.channel_fault(
                guild, bot_member, division_name, "attendance",
                int(attendance_channel),
                needs_attachment=attendance_graphics,
            )
            if fault is not None:
                faults.append(fault)
        verdicts_channel = verdicts_channels.get(division.id)
        if sanctions_possible and verdicts_channel:
            fault = results_post_service.channel_fault(
                guild, bot_member, division_name, "verdicts",
                verdicts_channel,
                needs_attachment=False,
            )
            if fault is not None:
                faults.append(fault)
    return faults
