"""Season points service — attach/detach configs, snapshot, validate, view."""
from __future__ import annotations

import logging
from itertools import groupby

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.results.models.points_changed import PointsChanged, ValueChange
from leaguebot.results.models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from leaguebot.results.services import points_config_service
from leaguebot.results.utils.points_ordering import ordering_message, ordering_violations

log = logging.getLogger(__name__)


class SeasonNotInSetupError(Exception):
    pass


class ConfigAlreadyAttachedError(Exception):
    pass


class ConfigNotAttachedError(Exception):
    pass


class StagedPointsNotApprovable(Exception):
    """The staged points cannot be installed, and nothing was written (#507).

    Carries the *reply* the admin is given and the *reason* the refusal's log line is written
    beneath, as the change queue's refusal takes them.
    """

    def __init__(self, reply: str, reason: str) -> None:
        super().__init__(reply)
        self.reply = reply
        self.reason = reason


async def attach_config(
    db_path: str,
    season_id: int,
    config_name: str,
    season_status: str,
) -> None:
    """Attach one of the league's points configurations to a season in setup.

    Raises :class:`points_config_service.ConfigNotFoundError` for a name the server's store
    does not hold. **The check is here because nothing below it can make one** (#132):
    ``season_points_links.config_name`` is bare ``TEXT`` with no foreign key, so a mistyped
    name inserts as happily as a real one and is reported as attached by `/season placements-review`.
    The first thing that ever noticed was the snapshot at approval, which raised in the
    middle of a deferred command — and with no error handler on the tree, said nothing at
    all. Refusing at the moment the name is typed is the only place the manager still knows
    what they meant.
    """
    if season_status != "SETUP":
        raise SeasonNotInSetupError(
            f"Config attachment is only allowed for seasons in SETUP (status: {season_status})"
        )
    if not await points_config_service.config_exists(db_path, config_name):
        raise points_config_service.ConfigNotFoundError(config_name)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (season_id, config_name),
        )
        await db.commit()


async def detach_config(
    db_path: str,
    season_id: int,
    config_name: str,
    season_status: str,
) -> None:
    if season_status != "SETUP":
        raise SeasonNotInSetupError(
            f"Config detachment is only allowed for seasons in SETUP (status: {season_status})"
        )
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM season_points_links WHERE season_id = ? AND config_name = ?",
            (season_id, config_name),
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise ConfigNotAttachedError(config_name)


async def get_attached_config_names(db_path: str, season_id: int) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT config_name FROM season_points_links WHERE season_id = ? ORDER BY config_name",
            (season_id,),
        )
        rows = await cursor.fetchall()
    return [r["config_name"] for r in rows]


async def missing_attached_configs(
    db_path: str,
    season_id: int,
) -> list[str]:
    """Every name this season is linked to that the server's points store does not hold.

    `attach_config` refuses to make such a link, but two doors are still open to one: a
    database written before that refusal existed, and `/results config remove`, which takes
    the configuration out from under any season that is not in setup. So the approval gate
    reads this rather than trusting the attachment to have been checked.

    Sorted, so a manager fixing several typos reads them in the same order twice running.
    """
    missing: list[str] = []
    for name in await get_attached_config_names(db_path, season_id):
        if not await points_config_service.config_exists(db_path, name):
            missing.append(name)
    return sorted(missing)


async def snapshot_configs_to_season_on(db: aiosqlite.Connection, season_id: int) -> None:
    """Copy the season's attached configurations onto it, on the connection it is handed.

    The season's approval writes this inside its one save, so nothing is committed here and
    nothing outside *db* is read. Results' own switch is read on *db* too, and while results is
    off (or has never been switched on) nothing is written, so the save need not ask whether
    results is on. A name attached but no longer held by the server's store raises
    `ConfigNotFoundError`, as the committing form did, and the save rolls back.
    """
    cursor = await db.execute("SELECT module_enabled FROM results_module_config")
    switch = await cursor.fetchone()
    if switch is None or not switch["module_enabled"]:
        return
    cursor = await db.execute(
        "SELECT config_name FROM season_points_links WHERE season_id = ? ORDER BY config_name",
        (season_id,),
    )
    config_names = [r["config_name"] for r in await cursor.fetchall()]
    for config_name in config_names:
        cursor = await db.execute(
            "SELECT id FROM points_config_store WHERE config_name = ?", (config_name,)
        )
        stored = await cursor.fetchone()
        if stored is None:
            raise points_config_service.ConfigNotFoundError(config_name)
        config_id = stored["id"]
        await db.execute(
            """
            INSERT OR REPLACE INTO season_points_entries
                (season_id, config_name, session_type, position, points)
            SELECT ?, ?, session_type, position, points
            FROM points_config_entries WHERE config_id = ?
            """,
            (season_id, config_name, config_id),
        )
        await db.execute(
            """
            INSERT OR REPLACE INTO season_points_fl
                (season_id, config_name, session_type, fl_points, fl_position_limit)
            SELECT ?, ?, session_type, fl_points, fl_position_limit
            FROM points_config_fl WHERE config_id = ?
            """,
            (season_id, config_name, config_id),
        )


async def validate_monotonic_ordering(db_path: str, season_id: int) -> list[str]:
    """Return a list of error strings for any non-monotonic config/session/position groups."""
    errors: list[str] = []
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT config_name, session_type, position, points
            FROM season_points_entries
            WHERE season_id = ?
            ORDER BY config_name, session_type, position
            """,
            (season_id,),
        )
        rows = await cursor.fetchall()

    for (config_name, session_type), group in groupby(
        rows, key=lambda r: (r["config_name"], r["session_type"])
    ):
        pairs = [(r["position"], r["points"]) for r in group]
        errors.extend(
            ordering_message(config_name, session_type, violation)
            for violation in ordering_violations(pairs)
        )
    return errors


async def validate_attached_config_ordering(
    db_path: str,
    season_id: int,
) -> list[str]:
    """Return ordering errors in the server-level configs this season will snapshot.

    The companion to :func:`validate_monotonic_ordering`, and the one that can speak
    before a season has been approved for the first time.

    **Why this reads the source rather than the season's own copy.** A season's points
    live in ``season_points_entries``, and the only thing that writes them on the
    approval path is :func:`snapshot_configs_to_season_on`, which runs *after* every gate —
    deliberately, because it is a write and writes belong after the backup offer. So a
    gate that reads the season's copy reads an empty table on a first approval and
    passes whatever the league built. The snapshot is a straight copy of the attached
    configs, so checking those checks exactly the points the season is about to take,
    and needs nothing undone when the answer is no.

    A config name attached but never created is **skipped, not raised**. It is
    :func:`missing_attached_configs` that reports one, and the approval gate and `/season
    review` both read that — so the fault is named as what it is rather than disguised as an
    ordering complaint about a table that does not exist (#132).
    """
    errors: list[str] = []
    for config_name in await get_attached_config_names(db_path, season_id):
        try:
            entries, _ = await points_config_service.get_config_entries(
                db_path, config_name
            )
        except points_config_service.ConfigNotFoundError:
            continue
        by_session: dict[str, list[tuple[int, int]]] = {}
        for entry in entries:
            by_session.setdefault(entry.session_type.value, []).append(
                (entry.position, entry.points)
            )
        for session_type in sorted(by_session):
            errors.extend(
                ordering_message(config_name, session_type, violation)
                for violation in ordering_violations(by_session[session_type])
            )
    return errors



async def get_season_points_view(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type_filter: SessionType | None = None,
) -> dict[str, dict]:
    """
    Return points tables and FL data for a config in the season store.

    Returns a dict keyed by session_type label, each value being:
        {"entries": [(position_label, points), ...], "fl": (fl_points, fl_position_limit) | None}

    Trailing zero positions are collapsed to a single "{n}th+: 0" sentinel.
    """
    async with get_connection(db_path) as db:
        query = """
            SELECT config_name, session_type, position, points
            FROM season_points_entries
            WHERE season_id = ? AND config_name = ?
        """
        params: list = [season_id, config_name]
        if session_type_filter is not None:
            query += " AND session_type = ?"
            params.append(session_type_filter.value)
        query += " ORDER BY session_type, position"
        cursor = await db.execute(query, params)
        entry_rows = await cursor.fetchall()

        fl_query = """
            SELECT session_type, fl_points, fl_position_limit
            FROM season_points_fl
            WHERE season_id = ? AND config_name = ?
        """
        fl_params: list = [season_id, config_name]
        if session_type_filter is not None:
            fl_query += " AND session_type = ?"
            fl_params.append(session_type_filter.value)
        fl_cursor = await db.execute(fl_query, fl_params)
        fl_rows = await fl_cursor.fetchall()

    fl_map: dict[str, tuple[int, int | None]] = {
        r["session_type"]: (r["fl_points"], r["fl_position_limit"]) for r in fl_rows
    }

    result: dict[str, dict] = {}
    for session_type, group in groupby(entry_rows, key=lambda r: r["session_type"]):
        raw = [(r["position"], r["points"]) for r in group]
        collapsed = _collapse_trailing_zeros(raw)
        result[session_type] = {
            "entries": collapsed,
            "fl": fl_map.get(session_type),
        }
    return result


def _collapse_trailing_zeros(rows: list[tuple[int, int]]) -> list[tuple[str, int]]:
    """
    Given [(pos, pts), ...] in ascending position order, collapse trailing zeros.

    Returns labelled tuples: [("1", 25), ("2", 18), ("3+", 0)] etc.
    """
    if not rows:
        return []

    # Find the last position with points > 0
    last_nonzero = -1
    for i, (_, pts) in enumerate(rows):
        if pts > 0:
            last_nonzero = i

    if last_nonzero == -1:
        # All zeros — collapse everything
        first_pos = rows[0][0]
        return [(f"{first_pos}+", 0)]

    result: list[tuple[str, int]] = []
    for i, (pos, pts) in enumerate(rows):
        if i <= last_nonzero:
            result.append((str(pos), pts))
        else:
            # First trailing zero — emit sentinel and stop
            result.append((f"{pos}+", 0))
            break

    return result


async def list_season_configs_with_sessions(
    db_path: str,
    season_id: int,
) -> list[tuple[str, list[SessionType]]]:
    """Every configuration this season holds, with the session types that carry entries.

    The season's own store, not the server's. The two diverge the moment
    ``snapshot_configs_to_season_on`` copies the server's tables across: editing a server
    configuration afterwards does not change what the season scores by. That divergence is
    why ``/results config list`` makes the manager name the store rather than guessing one
    (#200), and it is pinned by
    ``test_list_configs_reads_the_season_store_after_snapshot_diverges``.

    Reads ``season_points_links`` rather than ``season_points_entries`` alone, so a
    configuration attached but never filled is reported as attached-and-empty rather than
    vanishing — the same trap the server-side listing exists to expose. Shares
    ``group_sessions_by_config`` with the server store so both scopes render identically.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT l.config_name AS config_name, e.session_type AS session_type "
            "FROM season_points_links l "
            "LEFT JOIN season_points_entries e "
            "  ON e.config_name = l.config_name AND e.season_id = l.season_id "
            "WHERE l.season_id = ? "
            "GROUP BY l.config_name, e.session_type "
            "ORDER BY l.config_name",
            (season_id,),
        )
        rows = await cursor.fetchall()

    return points_config_service.group_sessions_by_config(rows)


async def get_season_config_names(db_path: str, season_id: int) -> list[str]:
    """Return all config names attached to the given season."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT config_name FROM season_points_links WHERE season_id = ? ORDER BY config_name",
            (season_id,),
        )
        rows = await cursor.fetchall()
    return [r["config_name"] for r in rows]


async def install_staged_points_on(db: aiosqlite.Connection, season_id: int) -> PointsChanged:
    """Make the season's staged points table its own, on *db*, and commit nothing.

    The write an approval of a mid-season amendment is made of: the working copy replaces
    `season_points_entries` and `season_points_fl`, is emptied, and amendment mode goes off,
    all on the connection the approval's one save holds, so that they land with the rescoring
    and the standings or not at all. It returns each value it changed, before and after.

    **It reads before it writes, and refuses having written nothing** (#507): where amendment
    mode is off, or the working copy is out of order, it raises :class:`StagedPointsNotApprovable`.
    A panel drawn earlier, or an approval already made, finds the working copy emptied, and
    installing an empty table would empty the season's points, so every round scored
    afterwards would score nothing and a penalty's recalculation would move no points. The
    check at the press catches it first; this one is the backstop for whatever writes the
    database between that check and the save.
    """
    cursor = await db.execute(
        "SELECT amendment_active FROM season_amendment_state WHERE season_id = ?", (season_id,)
    )
    state = await cursor.fetchone()
    if state is None or not state["amendment_active"]:
        raise StagedPointsNotApprovable(
            "❌ Amendment mode is not active. Nothing was changed: these changes were already "
            "approved, or amendment mode was turned off after this panel was drawn.",
            "amendment mode is not active",
        )

    staged = await _points_table(db, "season_modification_entries", "season_modification_fl", season_id)
    errors = [
        ordering_message(config_name, session_type, violation)
        for (config_name, session_type), (positions, _fl) in sorted(staged.items())
        for violation in ordering_violations(sorted(positions.items()))
    ]
    if errors:
        bullets = "\n• ".join(errors)
        raise StagedPointsNotApprovable(
            "❌ Amendment not approved — the points would be out of order:\n"
            f"• {bullets}\n"
            "Nothing has been changed. The staged changes are still there to repair.",
            "the points would be out of order:\n" + "\n".join(errors),
        )

    current = await _points_table(db, "season_points_entries", "season_points_fl", season_id)

    await db.execute("DELETE FROM season_points_entries WHERE season_id = ?", (season_id,))
    await db.execute(
        "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
        "SELECT season_id, config_name, session_type, position, points "
        "FROM season_modification_entries WHERE season_id = ?",
        (season_id,),
    )
    await db.execute("DELETE FROM season_points_fl WHERE season_id = ?", (season_id,))
    await db.execute(
        "INSERT INTO season_points_fl (season_id, config_name, session_type, fl_points, "
        "fl_position_limit) SELECT season_id, config_name, session_type, fl_points, "
        "fl_position_limit FROM season_modification_fl WHERE season_id = ?",
        (season_id,),
    )
    await db.execute("DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,))
    await db.execute("DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,))
    await db.execute(
        "UPDATE season_amendment_state SET amendment_active = 0, modified_flag = 0 "
        "WHERE season_id = ?",
        (season_id,),
    )
    return _changed(current, staged)


_Table = dict[tuple[str, str], tuple[dict[int, int], tuple[int | None, int | None] | None]]


async def _points_table(
    db: aiosqlite.Connection, entries: str, fastest_laps: str, season_id: int
) -> _Table:
    """A season's points as one table keyed by configuration and session type: each
    position's points, and the fastest lap's points and position limit where it has them."""
    table: _Table = {}
    cursor = await db.execute(
        f"SELECT config_name, session_type, position, points FROM {entries} WHERE season_id = ?",
        (season_id,),
    )
    for row in await cursor.fetchall():
        key = (row["config_name"], row["session_type"])
        table.setdefault(key, ({}, None))[0][int(row["position"])] = int(row["points"])
    cursor = await db.execute(
        f"SELECT config_name, session_type, fl_points, fl_position_limit FROM {fastest_laps} "
        "WHERE season_id = ?",
        (season_id,),
    )
    for row in await cursor.fetchall():
        key = (row["config_name"], row["session_type"])
        positions = table.setdefault(key, ({}, None))[0]
        limit = None if row["fl_position_limit"] is None else int(row["fl_position_limit"])
        table[key] = (positions, (int(row["fl_points"]), limit))
    return table


def _changed(before: _Table, after: _Table) -> PointsChanged:
    """Each value that differs between two tables, in table order."""
    changes: list[ValueChange] = []
    for key in sorted(before.keys() | after.keys()):
        old_positions, old_fl = before.get(key, ({}, None))
        new_positions, new_fl = after.get(key, ({}, None))
        config_name, session_type = key
        for position in sorted(old_positions.keys() | new_positions.keys()):
            old, new = old_positions.get(position), new_positions.get(position)
            if old != new:
                changes.append(ValueChange(config_name, session_type, f"P{position}", old, new))
        for index, what in enumerate(("fastest lap", "fastest lap position limit")):
            old = None if old_fl is None else old_fl[index]
            new = None if new_fl is None else new_fl[index]
            if old != new:
                changes.append(ValueChange(config_name, session_type, what, old, new))
    return PointsChanged(tuple(changes))
