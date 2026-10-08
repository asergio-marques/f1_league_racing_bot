"""The mid-season points amendment's workflow.

A round amendment is a change on the queue (`round_amend_change`), not a method here.
"""

from __future__ import annotations

import logging
from datetime import datetime
from itertools import groupby

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.audit_service import record_change_on
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.results.utils.points_ordering import ordering_message, ordering_violations
from leaguebot.core.utils.league_server import league_guild

log = logging.getLogger(__name__)


# ===========================================================================
# Mid-season points amendment workflow (T024)
# ===========================================================================

class AmendmentNotActiveError(Exception):
    """Raised when a modification store operation is attempted with amendment_active=0."""


class AmendmentModifiedError(Exception):
    """Raised when disabling amendment mode while modified_flag=1."""


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


async def modification_ordering_warnings(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
) -> list[str]:
    """Return how one staged session's table now reads out of order, if it does.

    The modification store's answer to
    :func:`leaguebot.results.services.points_config_service.ordering_warnings`, and given on the same
    terms: a staged edit that breaks the ordering **warns and still applies**. A manager
    restructuring a table mid-season moves through the same transient states as one
    building it in the first place, and the refusal waits for
    the approval, which is where the table would stop being staged and start scoring a
    championship.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT position, points FROM season_modification_entries "
            "WHERE season_id = ? AND config_name = ? AND session_type = ?",
            (season_id, config_name, session_type),
        )
        rows = await cursor.fetchall()

    return [
        f"position {position} ({points} pts) < position {next_position} ({next_points} pts)"
        for position, points, next_position, next_points in ordering_violations(
            [(r["position"], r["points"]) for r in rows]
        )
    ]


async def get_amendment_state(db_path: str, season_id: int):
    """Return SeasonAmendmentState or None if no record exists."""
    from leaguebot.results.models.amendment_state import SeasonAmendmentState
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT season_id, amendment_active, modified_flag FROM season_amendment_state WHERE season_id = ?",
            (season_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return SeasonAmendmentState(
        season_id=row["season_id"],
        amendment_active=bool(row["amendment_active"]),
        modified_flag=bool(row["modified_flag"]),
    )


async def enable_amendment_mode(db_path: str, season_id: int) -> None:
    """Copy season_points_entries/fl to modification store; set amendment_active=1."""
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO season_amendment_state (season_id, amendment_active, modified_flag)
            VALUES (?, 1, 0)
            """,
            (season_id,),
        )
        await db.execute(
            "DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            """
            INSERT INTO season_modification_entries (season_id, config_name, session_type, position, points)
            SELECT season_id, config_name, session_type, position, points
            FROM season_points_entries WHERE season_id = ?
            """,
            (season_id,),
        )
        await db.execute(
            """
            INSERT INTO season_modification_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
            SELECT season_id, config_name, session_type, fl_points, fl_position_limit
            FROM season_points_fl WHERE season_id = ?
            """,
            (season_id,),
        )
        await db.commit()


async def disable_amendment_mode(db_path: str, season_id: int) -> None:
    """Disable amendment mode. Raises AmendmentModifiedError if modified_flag=1."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT modified_flag FROM season_amendment_state WHERE season_id = ?",
            (season_id,),
        )
        row = await cursor.fetchone()
    if row and row["modified_flag"]:
        raise AmendmentModifiedError("Cannot disable — uncommitted changes exist.")

    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "UPDATE season_amendment_state SET amendment_active = 0, modified_flag = 0 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


async def revert_modification_store(db_path: str, season_id: int) -> None:
    """Reset the modification store to the current season points, clear modified_flag."""
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            """
            INSERT INTO season_modification_entries (season_id, config_name, session_type, position, points)
            SELECT season_id, config_name, session_type, position, points
            FROM season_points_entries WHERE season_id = ?
            """,
            (season_id,),
        )
        await db.execute(
            """
            INSERT INTO season_modification_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
            SELECT season_id, config_name, session_type, fl_points, fl_position_limit
            FROM season_points_fl WHERE season_id = ?
            """,
            (season_id,),
        )
        await db.execute(
            "UPDATE season_amendment_state SET modified_flag = 0 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


async def _require_amendment_active(db_path: str, season_id: int) -> None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT amendment_active FROM season_amendment_state WHERE season_id = ?",
            (season_id,),
        )
        row = await cursor.fetchone()
    if not row or not row["amendment_active"]:
        raise AmendmentNotActiveError("Amendment mode is not active for this season.")


async def staged_position_points(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
    position: int,
) -> dict[str, int | None]:
    """The points the modification store holds for one position, read so that the results cog
    can judge whether a request changes nothing (#482).

    ``{"points": None}`` where nothing is held, and always where the season is not in
    amendment mode: a request made outside the mode can never stand, so it reaches the
    store's own refusal, which comes before "nothing changed".
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT e.points FROM season_modification_entries e "
            "JOIN season_amendment_state s ON s.season_id = e.season_id AND s.amendment_active = 1 "
            "WHERE e.season_id = ? AND e.config_name = ? AND e.session_type = ? "
            "AND e.position = ?",
            (season_id, config_name, session_type, position),
        )
        row = await cursor.fetchone()
    return {"points": None if row is None else row["points"]}


async def staged_fastest_lap(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
) -> dict[str, int | None]:
    """The fastest-lap bonus and position limit the modification store holds for one session,
    both ``None`` where it holds no row or the season is not in amendment mode (#482). See
    :func:`staged_position_points`."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT f.fl_points, f.fl_position_limit FROM season_modification_fl f "
            "JOIN season_amendment_state s ON s.season_id = f.season_id AND s.amendment_active = 1 "
            "WHERE f.season_id = ? AND f.config_name = ? AND f.session_type = ?",
            (season_id, config_name, session_type),
        )
        row = await cursor.fetchone()
    if row is None:
        return {"fl_points": None, "fl_position_limit": None}
    return {"fl_points": row["fl_points"], "fl_position_limit": row["fl_position_limit"]}


async def modify_session_points(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
    pairs: list[tuple[int, int]],
    *,
    actor_id: int,
    actor_name: str,
    now: datetime,
) -> None:
    """Stage every ``(position, points)`` of *pairs* in the modification store, in one
    transaction, recording each staged change.

    `/results amend session` stages one pair and `/results amend bulk-session` a whole paste,
    which is all or nothing: every pair is staged, with an audit entry by *actor_id* at *now*
    for each position whose staged points changed, from what to what, or — where anything
    fails before the commit — none is, and no entry either. Raises
    :class:`AmendmentNotActiveError` where the season is not in amendment mode.
    """
    await _require_amendment_active(db_path, season_id)
    async with get_connection(db_path) as db:
        for position, points in pairs:
            cursor = await db.execute(
                "SELECT points FROM season_modification_entries "
                "WHERE season_id = ? AND config_name = ? AND session_type = ? AND position = ?",
                (season_id, config_name, session_type, position),
            )
            row = await cursor.fetchone()
            old = None if row is None else row["points"]
            await db.execute(
                """
                INSERT OR REPLACE INTO season_modification_entries
                    (season_id, config_name, session_type, position, points)
                VALUES (?, ?, ?, ?, ?)
                """,
                (season_id, config_name, session_type, position, points),
            )
            if old != points:
                await record_change_on(
                    db,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    change_type="POINTS_AMENDMENT_STAGED",
                    old_value={
                        "season_id": season_id, "config": config_name,
                        "session_type": session_type, "position": position, "points": old,
                    },
                    new_value={
                        "season_id": season_id, "config": config_name,
                        "session_type": session_type, "position": position, "points": points,
                    },
                    now=now,
                )
        await db.execute(
            "UPDATE season_amendment_state SET modified_flag = 1 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


async def modify_fl_bonus(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
    fl_points: int,
) -> None:
    await _require_amendment_active(db_path, season_id)
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO season_modification_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
            VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT (season_id, config_name, session_type)
            DO UPDATE SET fl_points = excluded.fl_points
            """,
            (season_id, config_name, session_type, fl_points),
        )
        await db.execute(
            "UPDATE season_amendment_state SET modified_flag = 1 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


async def modify_fl_position_limit(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
    limit: int,
) -> None:
    await _require_amendment_active(db_path, season_id)
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO season_modification_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT (season_id, config_name, session_type)
            DO UPDATE SET fl_position_limit = excluded.fl_position_limit
            """,
            (season_id, config_name, session_type, limit),
        )
        await db.execute(
            "UPDATE season_amendment_state SET modified_flag = 1 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


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
