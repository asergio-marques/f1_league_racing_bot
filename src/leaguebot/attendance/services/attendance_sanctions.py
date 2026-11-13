"""The sanctions attendance owes: who is owed one, and applying it.

A driver whose total reaches a threshold is owed an autoreserve or an autosack (`owed_sanctions`,
`check_sanction`); `apply_sanction_on` saves one on a connection, and `sync_hint` names the
command that finishes a run that did not all apply. The announcement of a
sanction is `sanctions_announce_change`'s.
"""

from __future__ import annotations

from typing import Any

from leaguebot.attendance.services import attendance_scoring, attendance_sheet
from leaguebot.core.db.database import get_connection


class SanctionNotApplicable(Exception):
    """A sanction that cannot be applied to a driver who is owed it: a division with no Reserve
    team to move them to. A failure of the sanction, never one passed over in silence."""


async def _thresholds_on(db) -> tuple[int | None, int | None]:
    """The (autoreserve, autosack) thresholds read on *db*, each None where unset or off."""
    cursor = await db.execute(
        "SELECT autoreserve_threshold, autosack_threshold FROM attendance_config"
    )
    row = await cursor.fetchone()
    if row is None:
        return None, None
    return row["autoreserve_threshold"] or None, row["autosack_threshold"] or None


async def _thresholds(db_path: str) -> tuple[int | None, int | None]:
    """The (autoreserve, autosack) thresholds, each None where unset or off (FR-027)."""
    async with get_connection(db_path) as db:
        return await _thresholds_on(db)


async def _display_name_on(db, profile_id: int) -> str | None:
    cursor = await db.execute(
        "SELECT test_display_name FROM driver_profiles WHERE id = ?", (profile_id,)
    )
    row = await cursor.fetchone()
    return None if row is None else row["test_display_name"]


async def _display_name_of(db_path: str, profile_id: int) -> str | None:
    async with get_connection(db_path) as db:
        return await _display_name_on(db, profile_id)


async def owed_sanctions(
    db_path: str, round_id: int, division_id: int, season_id: int | None = None, *, db=None
) -> tuple[list[dict], list[tuple[int, int]]]:
    """The sanctions the division's drivers owe at *round_id*, and who is passed over.

    The first list holds a candidate for each full-time driver whose total as at *round_id*
    is over a threshold and who is not already sanctioned: ``driver_profile_id``,
    ``driver_user_id``, ``sanction`` (``"AUTOSACK"``, which supersedes ``"AUTORESERVE"``,
    FR-025) and ``other_divisions``, the divisions an autosack reaches (issue #220), as the
    sheet of each is posted again. The second holds the ``(profile id, total)`` of each driver
    over the autosack threshold already signed off, the one refusal that is expected rather
    than a failure (I1); a driver already in the Reserve team is passed over without a word
    (FR-026). Both are empty where attendance sets no threshold (FR-027).

    A driver sanctioned drops out of the first list, which is what makes a sanction safe to try
    again: one run applies only what is still owed. Reads only; the one source of who is owed,
    for the review's jobs and for `/attendance sync`'s change (#439). *db* is the connection of
    a save that has just recalculated the totals: they are read there, uncommitted, where a
    connection of its own would find the old ones.
    """
    async with attendance_scoring._shared_or_own(db_path, db) as (db, _owned):
        autoreserve_threshold, autosack_threshold = await _thresholds_on(db)
        if not autoreserve_threshold and not autosack_threshold:
            return [], []
        if season_id is None:
            row = await (
                await db.execute("SELECT season_id FROM divisions WHERE id = ?", (division_id,))
            ).fetchone()
            if row is None:
                return [], []
            season_id = int(row["season_id"])
        cursor = await db.execute(
            """
            SELECT dra.driver_profile_id, dra.total_points_after,
                   dp.discord_user_id, dp.current_state
            FROM driver_round_attendance dra
            JOIN driver_season_assignments dsa
                ON dsa.driver_profile_id = dra.driver_profile_id
            JOIN team_seats ts ON ts.id = dsa.team_seat_id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN driver_profiles dp ON dp.id = dra.driver_profile_id
            WHERE dra.round_id = ?
              AND dra.division_id = ?
              AND ti.division_id = ?
              AND ti.is_reserve = 0
              AND dra.total_points_after IS NOT NULL
            """,
            (round_id, division_id, division_id),
        )
        driver_rows = await cursor.fetchall()

        owed: list[dict] = []
        signed_off: list[tuple[int, int]] = []
        for row in driver_rows:
            profile_id = row["driver_profile_id"]
            total = row["total_points_after"] or 0
            if autosack_threshold and total >= autosack_threshold:
                if row["current_state"] == "NOT_SIGNED_UP":
                    signed_off.append((profile_id, total))
                    continue
                # Autosack takes every seat in every division (issue #220), so every division
                # the driver sits in has its sheet posted again, not only this one.
                cursor = await db.execute(
                    "SELECT division_id FROM driver_season_assignments "
                    "WHERE driver_profile_id = ? AND season_id = ? AND division_id != ?",
                    (profile_id, season_id, division_id),
                )
                owed.append({
                    "driver_profile_id": profile_id,
                    "driver_user_id": int(row["discord_user_id"]),
                    "sanction": "AUTOSACK",
                    "other_divisions": [o["division_id"] for o in await cursor.fetchall()],
                })
                continue  # autosack supersedes autoreserve (FR-025)
            if autoreserve_threshold and total >= autoreserve_threshold:
                cursor = await db.execute(
                    """
                    SELECT ti.is_reserve
                    FROM driver_season_assignments dsa
                    JOIN team_seats ts ON ts.id = dsa.team_seat_id
                    JOIN team_instances ti ON ti.id = ts.team_instance_id
                    WHERE dsa.driver_profile_id = ?
                      AND dsa.season_id = ?
                      AND dsa.division_id = ?
                    """,
                    (profile_id, season_id, division_id),
                )
                seat_row = await cursor.fetchone()
                if seat_row and seat_row["is_reserve"]:
                    continue  # already in Reserve — skip (FR-026)
                owed.append({
                    "driver_profile_id": profile_id,
                    "driver_user_id": int(row["discord_user_id"]),
                    "sanction": "AUTORESERVE",
                    "other_divisions": [],
                })
    return owed, signed_off


def _sanction_line(
    candidate: dict, driver: str, total: int, threshold: int | None,
    reserve_team_name: str | None = None,
) -> str:
    """The log line of one driver's sanction: an autosack's, or an autoreserve's naming the
    Reserve team the driver was moved to."""
    profile_id = candidate["driver_profile_id"]
    if candidate["sanction"] == "AUTOSACK":
        return (
            f"ATTENDANCE_AUTOSACK | {driver}"
            f" | driver_profile_id={profile_id} | total={total} >= threshold={threshold}"
        )
    return (
        f"ATTENDANCE_AUTORESERVE | {driver}"
        f" | driver_profile_id={profile_id} | total={total} >= threshold={threshold}"
        f" → moved to {reserve_team_name}"
    )


async def _sanction_facts_on(db, division_id: int, round_id: int, candidate: dict,
                             season_id: int | None) -> tuple[int, int, str | None, str]:
    """The season, the driver's total, the division's Reserve team (None where it has none) and
    the driver as a line names them, read on *db*."""
    if season_id is None:
        row = await (
            await db.execute("SELECT season_id FROM divisions WHERE id = ?", (division_id,))
        ).fetchone()
        if row is None:
            raise SanctionNotApplicable(f"division {division_id} is not in a season")
        season_id = int(row["season_id"])
    profile_id = candidate["driver_profile_id"]
    row = await (await db.execute(
        "SELECT total_points_after FROM driver_round_attendance "
        "WHERE round_id = ? AND division_id = ? AND driver_profile_id = ?",
        (round_id, division_id, profile_id),
    )).fetchone()
    total = (row["total_points_after"] if row else None) or 0
    reserve_row = await (await db.execute(
        "SELECT name FROM team_instances WHERE division_id = ? AND is_reserve = 1 LIMIT 1",
        (division_id,),
    )).fetchone()
    driver = f"<@{candidate['driver_user_id']}>"
    display_name = await _display_name_on(db, profile_id)
    if display_name:
        driver += f" ({display_name})"
    return season_id, total, (reserve_row["name"] if reserve_row else None), driver


async def check_sanction(
    db_path: str, placement, round_id: int, division_id: int, candidate: dict,
) -> None:
    """Refuse a sanction that cannot be saved, before it is: an autoreserve in a division with no
    Reserve team (`SanctionNotApplicable`), or one the placement service's capacity guards refuse
    (`Refused`). Reads templates off the loop, which a save cannot; an autosack needs no check.
    """
    if candidate["sanction"] != "AUTORESERVE":
        return
    async with get_connection(db_path) as db:
        season_id, _total, reserve_team_name, _driver = await _sanction_facts_on(
            db, division_id, round_id, candidate, None
        )
    if reserve_team_name is None:
        raise SanctionNotApplicable("the division has no Reserve team")
    await placement.guard_move(division_id, season_id, reserve_team_name)


async def apply_sanction_on(
    db,
    placement,
    round_id: int,
    division_id: int,
    candidate: dict,
    *,
    actor_id: int,
    actor_name: str,
) -> dict[str, Any]:
    """Save one driver's sanction on *db* and return its line and the roles to change.

    Every read and every write is on the connection handed, which this commits nothing on and
    awaits nothing but: no role is changed, no member fetched, nothing posted and no capacity
    guarded (`check_sanction`'s). The result holds the log line and ``roles``: ``None`` for a
    test driver, who holds none, else the driver's ``user_id`` and ``profile_id`` with the
    role ids to ``revoke`` and to ``grant``, which a job of its own changes (#439).
    **Raises** `SanctionNotApplicable` for a division with no Reserve team, and what the
    placement service raises where the driver can no longer be sanctioned.
    """
    profile_id = candidate["driver_profile_id"]
    season_id, total, reserve_team_name, driver = await _sanction_facts_on(
        db, division_id, round_id, candidate, None
    )
    autoreserve_threshold, autosack_threshold = await _thresholds_on(db)
    user_id = int(candidate["driver_user_id"])

    if candidate["sanction"] == "AUTOSACK":
        saved = await placement.sack_on(
            db, driver_profile_id=profile_id, season_id=season_id,
            acting_user_id=actor_id, acting_user_name=actor_name,
        )
        roles = None if saved.is_test_driver else {
            "user_id": user_id, "profile_id": profile_id,
            "revoke": list(saved.revoke_role_ids), "grant": [],
        }
        return {
            "line": _sanction_line(candidate, driver, total, autosack_threshold),
            "roles": roles,
        }

    if reserve_team_name is None:
        raise SanctionNotApplicable("the division has no Reserve team")
    moved = await placement.move_on(
        db, driver_profile_id=profile_id, season_id=season_id,
        from_division_id=division_id, to_division_id=division_id,
        team_name=reserve_team_name, acting_user_id=actor_id, acting_user_name=actor_name,
    )
    roles = None if moved["is_test_driver"] else {
        "user_id": user_id, "profile_id": profile_id,
        "revoke": list(moved["revoke"]), "grant": list(moved["grant"]),
    }
    return {
        "line": _sanction_line(candidate, driver, total, autoreserve_threshold,
                               reserve_team_name),
        "roles": roles,
    }


def sync_hint_text(division_name: str, round_number: int | str) -> str:
    """The line telling a manager how to finish a run of sanctions that did not all apply, the
    division and the round named as they are typed."""
    return (
        f"Repair the cause, then run `/attendance sync division:{division_name} "
        f"round:{round_number}`."
    )


async def sync_hint(db_path: str, division_id: int, round_id: int) -> str:
    """`sync_hint_text` of the division and the round, read."""
    return sync_hint_text(
        await attendance_sheet._division_name(db_path, division_id),
        await attendance_sheet._round_number(db_path, round_id),
    )
