"""The mid-season points amendment's workflow.

A round amendment is a change on the queue (`round_amend_change`), not a method here.
"""

from __future__ import annotations

import logging
from itertools import groupby

from leaguebot.core.db.database import get_connection
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.results.utils.points_ordering import ordering_message, ordering_violations
from leaguebot.core.utils.league_server import league_guild

log = logging.getLogger(__name__)


# ===========================================================================
# Mid-season points amendment workflow (T024)
# ===========================================================================

async def validate_modification_ordering(db_path: str, season_id: int) -> list[str]:
    """Return ordering errors in the modification store, in approval's own words.

    The mid-season counterpart to the season approval's ordering gate. Both guard the
    same thing — the table a championship is scored on — and a rule that held only at
    approval would be a rule a league could step around by amending afterwards, with
    every round rescored against the bad table on the spot.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT config_name, session_type, position, points "
            "FROM season_modification_entries WHERE season_id = ? "
            "ORDER BY config_name, session_type, position",
            (season_id,),
        )
        rows = await cursor.fetchall()

    errors: list[str] = []
    for (config_name, session_type), group in groupby(
        rows, key=lambda r: (r["config_name"], r["session_type"])
    ):
        pairs = [(r["position"], r["points"]) for r in group]
        errors.extend(
            ordering_message(config_name, session_type, violation)
            for violation in ordering_violations(pairs)
        )
    return errors


async def get_modification_store_diff(db_path: str, season_id: int) -> str:
    """Return a human-readable diff of season_points_entries vs modification store."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT config_name, session_type, position, points FROM season_points_entries WHERE season_id = ?",
            (season_id,),
        )
        current_rows = {
            (r["config_name"], r["session_type"], r["position"]): r["points"]
            for r in await cursor.fetchall()
        }
        cursor = await db.execute(
            "SELECT config_name, session_type, position, points FROM season_modification_entries WHERE season_id = ?",
            (season_id,),
        )
        mod_rows = {
            (r["config_name"], r["session_type"], r["position"]): r["points"]
            for r in await cursor.fetchall()
        }

    all_keys = sorted(set(current_rows) | set(mod_rows))
    lines = []
    changed = 0
    for key in all_keys:
        old_pts = current_rows.get(key)
        new_pts = mod_rows.get(key)
        if old_pts != new_pts:
            config, session, pos = key
            lines.append(
                f"{config}/{session}/P{pos}: {old_pts if old_pts is not None else '?'} → {new_pts if new_pts is not None else '(removed)'}"
            )
            changed += 1

    if not lines:
        return "No changes in modification store."
    header = f"**{changed} change{'s' if changed != 1 else ''} staged:**"
    return header + "\n" + "\n".join(lines)


async def _season_exists(db_path: str, season_id: int) -> bool:
    """Whether the season can be read, checked before the approval writes anything."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT 1 FROM seasons WHERE id = ?", (season_id,))
        return await cursor.fetchone() is not None


async def approval_faults(db_path: str, season_id: int, bot: LeagueBot) -> list[str]:
    """Everything that would stop an approved amendment being published (#187).

    Returns the faults as lines a league can read, and an empty list where the whole
    cascade could be carried out.

    **Called three times, from one reading.** `/results amend review` calls it to draw its
    panel, so a manager sees what is wrong while deciding rather than after pressing Approve;
    the points approval's check calls it again at the press and once more when the approval
    runs, before it writes anything, so the refusal cannot be stepped around by a panel drawn
    when the channels were still sound. This is how `validate_modification_ordering` is already
    used, and how `_placement_confirmation_faults` serves the season's own review and
    confirmation — the report and the refusal are the same reading, so they cannot drift.

    **A cancelled division is included** (owner, 2026-10-06, "Refuse at the press"): the
    approval rescores, reposts and recalculates its raced rounds as a live division's, so the
    results, standings, attendance and verdicts channels it configured are asked too, as the
    results specification's "every division's" requires.

    **Each module answers for its own channels.** The results module knows what its repost
    needs and the attendance module knows what its recalculation needs; this function only
    composes them, and reaches into neither's configuration itself.
    """
    from leaguebot.results.services import results_post_service

    if not await _season_exists(db_path, season_id):
        return ["The season could not be read, so nothing was changed."]

    guild = (await league_guild(bot)) if bot is not None else None
    faults = await results_post_service.repost_channel_faults(
        db_path, season_id, guild, bot
    )

    # The attendance module recalculates as part of the same cascade, so its channels are
    # part of the same question. Asked only where it is switched on: a league without it
    # must not be refused for an attendance channel it has never configured (#187).
    attendance_on = False
    try:
        attendance_on = await bot.module_service.is_attendance_enabled()
    except Exception:  # noqa: BLE001 — never refuse an amendment on this reader
        log.exception("approval_faults: could not read the attendance module's state")
    if attendance_on:
        from leaguebot.attendance.services import attendance_service

        faults += await attendance_service.recalculation_faults(
            db_path, season_id, guild, bot, cancelled_too=True
        )

    return faults
