"""The amendment mode of a season's points, in results.

Results owns the mode's tables (`season_amendment_state`, `season_modification_entries`,
`season_modification_fl`), so it holds the readers of them here. A season in the mode edits a
modification store rather than the configurations it scores on. The readers say whether the
mode is on and whether it is modified, and whether a staged table reads out of order. The writers (`set_amendment_mode_on`,
`revert_on`, `stage_points_on`, `stage_fastest_lap_on`) write on the connection a settings change
hands them, commit nothing, and answer what they did as a `Saved`: whether anything changed and
the audit entries the queue saves with it. A request for what the store already holds changes
nothing, leaves the modified flag as it stood and records nothing. That the mode is on, that the
season is the live one and that no approval is in hand are the settings' checks to judge, not the
writers'.
"""
from __future__ import annotations

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import AuditRecord
from leaguebot.core.services.setting_change import Saved
from leaguebot.results.models.amendment_state import SeasonAmendmentState
from leaguebot.results.utils.points_ordering import ordering_violations


async def get_amendment_state(db_path: str, season_id: int) -> SeasonAmendmentState | None:
    """Return SeasonAmendmentState or None if no record exists."""
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


async def modification_ordering_warnings_on(
    db: aiosqlite.Connection,
    season_id: int,
    config_name: str,
    session_type: str,
) -> list[str]:
    """How one staged session's table now reads out of order, if it does, read on *db* so a save
    can judge the table it has just written.

    The modification store's answer to
    :func:`leaguebot.results.services.points_config_service.ordering_warnings_on`, and given on
    the same terms: a staged edit that breaks the ordering **warns and still applies**. A manager
    restructuring a table mid-season moves through the same transient states as one building it in
    the first place, and the refusal waits for the approval, which is where the table would stop
    being staged and start scoring a championship.
    """
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


_COPY_ENTRIES = """
    INSERT INTO season_modification_entries (season_id, config_name, session_type, position, points)
    SELECT season_id, config_name, session_type, position, points
    FROM season_points_entries WHERE season_id = ?
"""
_COPY_FL = """
    INSERT INTO season_modification_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
    SELECT season_id, config_name, session_type, fl_points, fl_position_limit
    FROM season_points_fl WHERE season_id = ?
"""


async def _state_on(db: aiosqlite.Connection, season_id: int) -> tuple[bool, bool]:
    """``(amendment_active, modified_flag)`` of the season on *db*; both False with no record."""
    cursor = await db.execute(
        "SELECT amendment_active, modified_flag FROM season_amendment_state WHERE season_id = ?",
        (season_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False, False
    return bool(row["amendment_active"]), bool(row["modified_flag"])


async def _copy_season_points_on(db: aiosqlite.Connection, season_id: int) -> None:
    """Empty the season's modification store and fill it from the points the season scores on."""
    await db.execute("DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,))
    await db.execute("DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,))
    await db.execute(_COPY_ENTRIES, (season_id,))
    await db.execute(_COPY_FL, (season_id,))


async def set_amendment_mode_on(db: aiosqlite.Connection, season_id: int, active: bool) -> Saved:
    """Turn amendment mode on or off for *season_id* on *db*, committing nothing.

    On: the store is filled with a copy of the season's points and the modified flag is clear.
    Off: the store is emptied and the flag cleared; that nothing is staged is the setting's check
    to judge, so no staged change is thrown away here unrecorded. A season already in the state
    asked for changes nothing (turning it on again would throw away what is staged). One
    `POINTS_AMENDMENT_MODE_SET` entry, from the old state to the new.
    """
    was_active, _ = await _state_on(db, season_id)
    if was_active == active:
        return Saved(changed=False)
    if active:
        await db.execute(
            "INSERT OR REPLACE INTO season_amendment_state "
            "(season_id, amendment_active, modified_flag) VALUES (?, 1, 0)",
            (season_id,),
        )
        await _copy_season_points_on(db, season_id)
    else:
        await db.execute("DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,))
        await db.execute("DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,))
        await db.execute(
            "UPDATE season_amendment_state SET amendment_active = 0, modified_flag = 0 "
            "WHERE season_id = ?",
            (season_id,),
        )
    return Saved(
        changed=True,
        audits=(AuditRecord(
            "POINTS_AMENDMENT_MODE_SET",
            {"season_id": season_id, "amendment_active": was_active},
            {"season_id": season_id, "amendment_active": active},
        ),),
    )


async def _staged_rows_on(db: aiosqlite.Connection, season_id: int) -> list[dict[str, object]]:
    """Every row of the store that differs from the points the season scores on, as the store
    holds it: a position's points (None where the store holds none) or a session's fastest-lap
    bonus and limit."""
    rows: list[dict[str, object]] = []
    for table, key, value in (
        ("entries", ("config_name", "session_type", "position"), ("points",)),
        ("fl", ("config_name", "session_type"), ("fl_points", "fl_position_limit")),
    ):
        columns = ", ".join(key + value)
        live: dict[tuple[object, ...], tuple[object, ...]] = {}
        staged: dict[tuple[object, ...], tuple[object, ...]] = {}
        for name, held in (("season_points", live), ("season_modification", staged)):
            cursor = await db.execute(
                f"SELECT {columns} FROM {name}_{table} WHERE season_id = ?", (season_id,)
            )
            for row in await cursor.fetchall():
                held[tuple(row[c] for c in key)] = tuple(row[c] for c in value)
        for found in sorted(live.keys() | staged.keys(), key=repr):
            if live.get(found) == staged.get(found):
                continue
            held_values = staged.get(found, (None,) * len(value))
            rows.append({**dict(zip(key, found)), **dict(zip(value, held_values))})
    return rows


async def revert_on(db: aiosqlite.Connection, season_id: int) -> Saved:
    """Put the season's modification store back to its points on *db*, committing nothing.

    The store is emptied and refilled from the points the season scores on, and the modified
    flag cleared. Nothing staged (the flag clear) changes nothing. One `POINTS_AMENDMENT_REVERTED`
    entry whose old value lists each row of the store that differed from the season's points, as
    the store held it, and whose new value lists none.
    """
    _, modified = await _state_on(db, season_id)
    if not modified:
        return Saved(changed=False)
    staged = await _staged_rows_on(db, season_id)
    await _copy_season_points_on(db, season_id)
    await db.execute(
        "UPDATE season_amendment_state SET modified_flag = 0 WHERE season_id = ?", (season_id,)
    )
    return Saved(
        changed=True,
        audits=(AuditRecord(
            "POINTS_AMENDMENT_REVERTED",
            {"season_id": season_id, "staged": staged},
            {"season_id": season_id, "staged": []},
        ),),
    )


async def _flag_modified_on(db: aiosqlite.Connection, season_id: int) -> None:
    await db.execute(
        "UPDATE season_amendment_state SET modified_flag = 1 WHERE season_id = ?", (season_id,)
    )


def _staged_position(
    season_id: int, config_name: str, session_type: str, position: int, points: int | None
) -> dict[str, object]:
    """One staged position as an audit entry records it."""
    return {
        "season_id": season_id, "config": config_name, "session_type": session_type,
        "position": position, "points": points,
    }


async def stage_points_on(
    db: aiosqlite.Connection,
    season_id: int,
    config_name: str,
    session_type: str,
    pairs: list[tuple[int, int]],
) -> Saved:
    """Stage every ``(position, points)`` of *pairs* in the store on *db*, committing nothing.

    One `POINTS_AMENDMENT_STAGED` entry for each position whose staged points change, from what
    to what, and the modified flag set when any does; positions already staged at what is asked
    write nothing, and where none changes the answer is unchanged and the flag stands as it was.
    A position repeated in *pairs* takes its last value.
    """
    audits = []
    for position, points in pairs:
        cursor = await db.execute(
            "SELECT points FROM season_modification_entries "
            "WHERE season_id = ? AND config_name = ? AND session_type = ? AND position = ?",
            (season_id, config_name, session_type, position),
        )
        row = await cursor.fetchone()
        old = None if row is None else row["points"]
        if old == points:
            continue
        await db.execute(
            "INSERT OR REPLACE INTO season_modification_entries "
            "(season_id, config_name, session_type, position, points) VALUES (?, ?, ?, ?, ?)",
            (season_id, config_name, session_type, position, points),
        )
        audits.append(AuditRecord(
            "POINTS_AMENDMENT_STAGED",
            _staged_position(season_id, config_name, session_type, position, old),
            _staged_position(season_id, config_name, session_type, position, points),
        ))
    if audits:
        await _flag_modified_on(db, season_id)
    return Saved(changed=bool(audits), audits=tuple(audits))


def _staged_fastest_lap_value(
    season_id: int, config_name: str, session_type: str, fl_points: int | None, limit: int | None
) -> dict[str, object]:
    """One staged fastest-lap row as an audit entry records it."""
    return {
        "season_id": season_id, "config": config_name, "session_type": session_type,
        "fl_points": fl_points, "fl_position_limit": limit,
    }


async def stage_fastest_lap_on(
    db: aiosqlite.Connection,
    season_id: int,
    config_name: str,
    session_type: str,
    *,
    fl_points: int | None = None,
    fl_position_limit: int | None = None,
) -> Saved:
    """Stage a race session's fastest-lap bonus and/or position limit on *db*, committing nothing.

    Only the column given is staged; the other stays as the store holds it, the bonus being 0
    and the limit unset where the store has no row yet. A value the store already holds changes
    nothing: no write, no entry, the modified flag as it stood. Otherwise one
    `POINTS_AMENDMENT_FL_STAGED` entry records the row as it was and as it is, and the flag is set.
    """
    cursor = await db.execute(
        "SELECT fl_points, fl_position_limit FROM season_modification_fl "
        "WHERE season_id = ? AND config_name = ? AND session_type = ?",
        (season_id, config_name, session_type),
    )
    existing = await cursor.fetchone()
    old = (existing["fl_points"], existing["fl_position_limit"]) if existing else (None, None)
    held = {"fl_points": old[0], "fl_position_limit": old[1]}
    asked = {"fl_points": fl_points, "fl_position_limit": fl_position_limit}
    if existing is not None and all(
        held[name] == value for name, value in asked.items() if value is not None
    ):
        return Saved(changed=False)
    new_points = fl_points if fl_points is not None else (old[0] if existing else 0)
    new_limit = fl_position_limit if fl_position_limit is not None else old[1]
    await db.execute(
        """
        INSERT INTO season_modification_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (season_id, config_name, session_type)
        DO UPDATE SET fl_points = excluded.fl_points, fl_position_limit = excluded.fl_position_limit
        """,
        (season_id, config_name, session_type, new_points, new_limit),
    )
    await _flag_modified_on(db, season_id)
    return Saved(
        changed=True,
        audits=(AuditRecord(
            "POINTS_AMENDMENT_FL_STAGED",
            _staged_fastest_lap_value(season_id, config_name, session_type, old[0], old[1]),
            _staged_fastest_lap_value(season_id, config_name, session_type, new_points, new_limit),
        ),),
    )
