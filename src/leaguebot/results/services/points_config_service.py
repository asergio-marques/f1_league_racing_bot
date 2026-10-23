"""Points configuration service — server-level named config CRUD."""
from __future__ import annotations

import logging
from collections.abc import Mapping

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import AuditRecord
from leaguebot.core.services.setting_change import Saved
from leaguebot.results.models.points_config import (
    PointsConfigEntry,
    PointsConfigFastestLap,
    PointsConfigStore,
    SessionType,
)
from leaguebot.results.utils.points_ordering import ordering_violations

log = logging.getLogger(__name__)

#: What a manager loses by leaving a table out of order, at each of the two ends. The
#: rule is one rule; only the moment it is enforced differs, so only this clause does.
BLOCKS_APPROVAL = "The change has been saved, but a season cannot be approved on this table."
BLOCKS_AMENDMENT = (
    "The change has been staged, but this amendment cannot be approved on this table."
)
#: The same two clauses for a request that changed nothing (#482): there is no change to say
#: was saved or staged, only who will refuse the table as it stands.
STANDS_BLOCKS_APPROVAL = "A season cannot be approved on this table."
STANDS_BLOCKS_AMENDMENT = "This amendment cannot be approved on this table."


def ordering_notice(
    config_name: str,
    session_label: str,
    violations: list[str],
    consequence: str = BLOCKS_APPROVAL,
) -> str:
    """The block appended to a points edit's reply that has left a table out of order.

    Empty string when there is nothing wrong, so a caller can concatenate it
    unconditionally.

    The edit itself has already been applied by the time this is written — a points
    edit warns rather than refuses (decided 2026-09-14). The notice therefore has one
    job beyond naming the fault: saying who *will* refuse, so a manager knows this is
    something to fix rather than a note they can read past. *consequence* is which of
    the two refusals is coming, the season's approval or the amendment's: one of the four
    clauses above. It is the service's, not the cog's, so that the points settings, which
    form the replies, and the commands that still answer at once share it.
    """
    if not violations:
        return ""
    bullets = "\n".join(f"  • {v}" for v in violations)
    return (
        f"\n\u26a0\ufe0f **{config_name}**'s {session_label} table is now out of order:\n"
        f"{bullets}\n"
        f"A lower position cannot be worth as much as the one above it. {consequence}"
    )


class ConfigAlreadyExistsError(Exception):
    pass


class ConfigNotFoundError(Exception):
    pass


class InvalidSessionTypeError(Exception):
    pass


def points_value_refusal(
    *, position: int | None = None, points: int | None = None, limit: int | None = None
) -> str | None:
    """Why a single position, points value or fastest-lap limit is out of range, or None.

    The bulk paste's rule, held to one value: a position or a limit below 1, or points below 0.
    The position is judged before the points. Pure: the settings' checks call it with what the
    command was given, so a value that could never be stored is refused at the ask.
    """
    if position is not None and position < 1:
        return f"\u274c Position must be >= 1, got {position}."
    if points is not None and points < 0:
        return f"\u274c Points must be >= 0, got {points}."
    if limit is not None and limit < 1:
        return f"\u274c Position limit must be >= 1, got {limit}."
    return None


async def create_config_on(db: aiosqlite.Connection, config_name: str) -> Saved:
    """Add the points configuration *config_name* on *db*, committing nothing.

    One `POINTS_CONFIG_ADD` entry, from nothing to the name. Raises
    :class:`ConfigAlreadyExistsError` where the name is taken: the setting's check refuses that
    first, so this is the write's own guard behind it.
    """
    try:
        await db.execute(
            "INSERT INTO points_config_store (config_name) VALUES (?)",
            (config_name,),
        )
    except aiosqlite.IntegrityError:
        raise ConfigAlreadyExistsError(config_name)
    return Saved(
        changed=True,
        audits=(AuditRecord("POINTS_CONFIG_ADD", {}, {"config": config_name}),),
        details=(f"config: {config_name}",),
        result={"config": config_name},
    )


async def config_exists(db_path: str, config_name: str) -> bool:
    """Whether the league's points store holds a configuration under this name.

    Asked by the attach setting's check, which asks before a season's link to a configuration is
    recorded by name into a column carrying no foreign key. Nothing beneath it objects
    to a name that was never created, so the check has to be made above (#132).
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM points_config_store WHERE config_name = ?",
            (config_name,),
        )
        return await cursor.fetchone() is not None


async def setup_seasons_linking_on(
    db: aiosqlite.Connection, config_name: str
) -> list[tuple[int, int]]:
    """The ``(season_id, season_number)`` of every season in setup attached to this name, read
    on *db*."""
    cursor = await db.execute(
        """
        SELECT s.id AS id, s.season_number AS season_number
        FROM season_points_links AS l
        JOIN seasons AS s ON s.id = l.season_id
        WHERE l.config_name = ? AND s.status = 'SETUP'
        ORDER BY s.season_number, s.id
        """,
        (config_name,),
    )
    return [(r["id"], r["season_number"]) for r in await cursor.fetchall()]


async def setup_seasons_linking(
    db_path: str, config_name: str
) -> list[tuple[int, int]]:
    """The ``(season_id, season_number)`` of every season in setup attached to this name.

    Read before `/results config remove` acts, so the confirmation can say which seasons
    the removal will take the configuration away from rather than asking a blind yes.
    """
    async with get_connection(db_path) as db:
        return await setup_seasons_linking_on(db, config_name)


async def remove_config_on(db: aiosqlite.Connection, config_name: str) -> Saved:
    """Delete the points configuration *config_name* on *db*, committing nothing.

    What the removal throws away is the audit entry's old value (Constitution V): the name,
    every position it paid by session type, every fastest-lap bonus and limit, and the numbers
    of the setup seasons it was detached from. Raises :class:`ConfigNotFoundError` where there
    is no such configuration.

    **Why the links go with it, and why only those of a season in setup** (decided
    2026-09-15, issue #132). A link is a bare name in ``season_points_links`` with no
    foreign key, so deleting the store row on its own left a season pointing at nothing:
    the approval's prerequisite still counted the link, and the snapshot then raised
    `ConfigNotFoundError` mid-command. A season that was fine became unapprovable without
    anyone touching it.

    A season **in setup** has taken no copy of the configuration yet — the snapshot runs at
    approval — so the link is the only thing that connects the two, and it is worthless once
    the configuration is gone. A season that has been **approved** — ACTIVE, or COMPLETED
    and kept as history — is the opposite case, and its link is deliberately left alone: it
    scores from its own `season_points_entries` copy, taken at approval and independent of
    the server's store from that moment, and the link is what offers that copy as a choice
    when results are submitted. Clearing it would take a running season's points
    configuration off the submission buttons — a live regression in place of a setup-time
    one — and would rewrite what a finished season is recorded as having run on.

    Pinned by ``test_remove_config_leaves_an_approved_seasons_link_alone``: the scoping is
    the whole of the decision and reads like an oversight without it.

    The deletes share the caller's transaction with the store row, so a removal cannot
    half-happen and leave the orphan it exists to prevent.
    """
    config_id = await _get_config_id(db, config_name)
    positions: dict[str, dict[int, int]] = {}
    cursor = await db.execute(
        "SELECT session_type, position, points FROM points_config_entries "
        "WHERE config_id = ? ORDER BY session_type, position",
        (config_id,),
    )
    for row in await cursor.fetchall():
        positions.setdefault(row["session_type"], {})[row["position"]] = row["points"]
    fastest_laps: dict[str, dict[str, int | None]] = {}
    cursor = await db.execute(
        "SELECT session_type, fl_points, fl_position_limit FROM points_config_fl "
        "WHERE config_id = ? ORDER BY session_type",
        (config_id,),
    )
    for row in await cursor.fetchall():
        fastest_laps[row["session_type"]] = {
            "fl_points": row["fl_points"], "fl_position_limit": row["fl_position_limit"],
        }
    detached_from = [number for _, number in await setup_seasons_linking_on(db, config_name)]
    await db.execute("DELETE FROM points_config_store WHERE id = ?", (config_id,))
    await db.execute(
        """
        DELETE FROM season_points_links
        WHERE config_name = ?
          AND season_id IN (
              SELECT id FROM seasons WHERE status = 'SETUP'
          )
        """,
        (config_name,),
    )
    return Saved(
        changed=True,
        audits=(AuditRecord(
            "POINTS_CONFIG_REMOVE",
            {
                "config": config_name,
                "positions": positions,
                "fastest_laps": fastest_laps,
                "detached_from": detached_from,
            },
            {},
        ),),
        details=(f"config: {config_name}",),
        result={"config": config_name, "detached_from": detached_from},
    )


async def _get_config_id(db: aiosqlite.Connection, config_name: str) -> int:
    cursor = await db.execute(
        "SELECT id FROM points_config_store WHERE config_name = ?",
        (config_name,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ConfigNotFoundError(config_name)
    return row["id"]


async def ordering_warnings_on(
    db: aiosqlite.Connection,
    config_name: str,
    session_type: SessionType,
) -> list[str]:
    """:func:`ordering_warnings` read on *db*, so a save can judge the table it has just written."""
    try:
        config_id = await _get_config_id(db, config_name)
    except ConfigNotFoundError:
        return []
    cursor = await db.execute(
        "SELECT position, points FROM points_config_entries "
        "WHERE config_id = ? AND session_type = ?",
        (config_id, session_type.value),
    )
    rows = await cursor.fetchall()
    return [
        f"position {position} ({points} pts) < position {next_position} ({next_points} pts)"
        for position, points, next_position, next_points in ordering_violations(
            [(r["position"], r["points"]) for r in rows]
        )
    ]


async def ordering_warnings(
    db_path: str,
    config_name: str,
    session_type: SessionType,
) -> list[str]:
    """Return how one session's table now reads out of order, if it does.

    Called after a write, not before one. A points edit that breaks the ordering
    **warns and still applies** (decided 2026-09-14): a manager filling a table in
    position by position passes through states that are momentarily out of order —
    setting second place before first, or repairing a table from the bottom up — and
    refusing the write would make ordinary ways of building a table impossible to
    follow. The refusal belongs at the two moments a table is committed to a season,
    the confirmation of placements and `/results amend review`, where there is nothing transient
    left about it.

    Returns an empty list for a config that does not exist: the caller has just been
    told so by :class:`ConfigNotFoundError` and does not need telling twice.

    Nothing in ``src/`` reads through ``db_path`` any more: the settings judge the table on the
    connection that wrote it, through :func:`ordering_warnings_on`. This form stays for the tests
    that read a table the way a manager would see it.
    """
    async with get_connection(db_path) as db:
        return await ordering_warnings_on(db, config_name, session_type)


async def config_fastest_lap_on(
    db: aiosqlite.Connection,
    config_name: str,
    session_type: SessionType,
    *,
    fl_points: int | None = None,
    fl_position_limit: int | None = None,
) -> Saved:
    """Set a race session's fastest-lap bonus and/or position limit on *db*, committing nothing.

    Only the column given is set; the other is kept as it stands, the bonus being 0 and the limit
    unset where the session has no row yet. A value the session already holds changes nothing: no
    write, no entry. Otherwise one `POINTS_CONFIG_FL_SET` entry records the bonus and the limit as
    they were and as they are, in the shape the import records them in. Raises
    :class:`InvalidSessionTypeError` for a qualifying session and :class:`ConfigNotFoundError`
    for a configuration that does not exist: the setting's check refuses both first.
    """
    if session_type.is_qualifying:
        raise InvalidSessionTypeError(
            "Fastest-lap bonus and position limit cannot be set for qualifying session type: "
            f"{session_type.value}"
        )
    config_id = await _get_config_id(db, config_name)
    cursor = await db.execute(
        "SELECT fl_points, fl_position_limit FROM points_config_fl "
        "WHERE config_id = ? AND session_type = ?",
        (config_id, session_type.value),
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
        INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(config_id, session_type)
        DO UPDATE SET fl_points = excluded.fl_points,
                      fl_position_limit = excluded.fl_position_limit
        """,
        (config_id, session_type.value, new_points, new_limit),
    )
    return Saved(
        changed=True,
        audits=(AuditRecord(
            "POINTS_CONFIG_FL_SET",
            _fastest_lap_value(config_name, session_type, old[0], old[1]),
            _fastest_lap_value(config_name, session_type, new_points, new_limit),
        ),),
    )


def _fastest_lap_value(
    config_name: str, session_type: SessionType, fl_points: int | None, fl_limit: int | None
) -> dict[str, object]:
    """One fastest-lap row as an audit entry records it."""
    return {
        "config": config_name, "session_type": session_type.value,
        "fl_points": fl_points, "fl_position_limit": fl_limit,
    }


async def get_config_entries(
    db_path: str,
    config_name: str,
) -> tuple[list[PointsConfigEntry], list[PointsConfigFastestLap]]:
    async with get_connection(db_path) as db:
        config_id = await _get_config_id(db, config_name)
        cursor = await db.execute(
            "SELECT id, config_id, session_type, position, points "
            "FROM points_config_entries WHERE config_id = ? ORDER BY session_type, position",
            (config_id,),
        )
        entry_rows = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT id, config_id, session_type, fl_points, fl_position_limit "
            "FROM points_config_fl WHERE config_id = ?",
            (config_id,),
        )
        fl_rows = await cursor.fetchall()

    entries = [
        PointsConfigEntry(
            id=r["id"],
            config_id=r["config_id"],
            session_type=SessionType(r["session_type"]),
            position=r["position"],
            points=r["points"],
        )
        for r in entry_rows
    ]
    fl_entries = [
        PointsConfigFastestLap(
            id=r["id"],
            config_id=r["config_id"],
            session_type=SessionType(r["session_type"]),
            fl_points=r["fl_points"],
            fl_position_limit=r["fl_position_limit"],
        )
        for r in fl_rows
    ]
    return entries, fl_entries


async def list_configs(db_path: str) -> list[PointsConfigStore]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, config_name FROM points_config_store ORDER BY config_name"
        )
        rows = await cursor.fetchall()
    return [
        PointsConfigStore(id=r["id"], config_name=r["config_name"])
        for r in rows
    ]


async def list_configs_with_sessions(
    db_path: str,
) -> list[tuple[str, list[SessionType]]]:
    """Every configuration the server holds, with the session types that carry entries.

    A configuration created and never filled in looks identical to a complete one from the
    outside, and snapshots into a season as empty points (#200). So the listing reports what
    each one actually carries rather than its name alone, and a configuration with no entries
    comes back with an empty list — present, but visibly empty — never absent.

    One ``LEFT JOIN`` rather than a query per configuration: a server may hold many, and the
    per-configuration form would cost a round trip each. The pairing is pinned by
    ``test_list_configs_with_sessions_names_only_sessions_that_carry_entries``.

    Ordered by name, and each session-type list in the running order of ``SessionType``, so a
    manager reads the same list twice running whatever the database hands back.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT s.config_name AS config_name, e.session_type AS session_type "
            "FROM points_config_store s "
            "LEFT JOIN points_config_entries e ON e.config_id = s.id "
            "GROUP BY s.config_name, e.session_type "
            "ORDER BY s.config_name"
        )
        rows = await cursor.fetchall()

    return group_sessions_by_config(rows)


def group_sessions_by_config(rows) -> list[tuple[str, list[SessionType]]]:
    """Fold ``(config_name, session_type)`` rows into one entry per configuration.

    Shared by the server store and a season's own, so both scopes of
    ``/results config list`` render through one formatter (#200). A NULL ``session_type`` is
    the ``LEFT JOIN`` reporting a configuration with no entries, and yields an empty list.
    """
    grouped: dict[str, list[SessionType]] = {}
    for row in rows:
        name = row["config_name"]
        sessions = grouped.setdefault(name, [])
        raw = row["session_type"]
        if raw is None:
            continue
        session = SessionType(raw)
        if session not in sessions:
            sessions.append(session)

    return [
        (name, sorted(sessions, key=lambda s: list(SessionType).index(s)))
        for name, sessions in sorted(grouped.items())
    ]


async def _position_points(
    db: aiosqlite.Connection, config_id: int, session_type: SessionType, position: int
) -> int | None:
    """What *position* of *session_type* pays in configuration *config_id*, or None if unset."""
    cursor = await db.execute(
        "SELECT points FROM points_config_entries "
        "WHERE config_id = ? AND session_type = ? AND position = ?",
        (config_id, session_type.value, position),
    )
    row = await cursor.fetchone()
    return None if row is None else row["points"]


def _position_value(
    config_name: str, session_type: SessionType, position: int, points: int | None
) -> dict[str, object]:
    """One position's points as an audit entry records them."""
    return {
        "config": config_name, "session_type": session_type.value,
        "position": position, "points": points,
    }


async def _write_position(
    db: aiosqlite.Connection,
    config_id: int,
    config_name: str,
    session_type: SessionType,
    position: int,
    points: int,
) -> AuditRecord | None:
    """Upsert one position's points; the audit entry for it where the points changed."""
    old = await _position_points(db, config_id, session_type, position)
    if old == points:
        return None
    await db.execute(
        """
        INSERT INTO points_config_entries (config_id, session_type, position, points)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(config_id, session_type, position)
        DO UPDATE SET points = excluded.points
        """,
        (config_id, session_type.value, position, points),
    )
    return AuditRecord(
        "POINTS_CONFIG_SESSION_SET",
        _position_value(config_name, session_type, position, old),
        _position_value(config_name, session_type, position, points),
    )


async def config_points_on(
    db: aiosqlite.Connection,
    config_name: str,
    session_type: SessionType,
    pairs: list[tuple[int, int]],
) -> Saved:
    """Set every ``(position, points)`` of *pairs* on *db*, committing nothing.

    One `POINTS_CONFIG_SESSION_SET` entry for each position whose points change, from what to
    what; a position already paying what is asked writes nothing, and where none changes the
    answer is unchanged. A position repeated in *pairs* takes its last value. The ordering of the
    table the writes leave behind is read on the same connection into ``result["warnings"]``
    (see :func:`ordering_warnings`), for the reply to warn with. Raises
    :class:`ConfigNotFoundError` if *config_name* does not exist.
    """
    config_id = await _get_config_id(db, config_name)
    audits = []
    for position, points in pairs:
        audit = await _write_position(db, config_id, config_name, session_type, position, points)
        if audit is not None:
            audits.append(audit)
    return Saved(
        changed=bool(audits),
        audits=tuple(audits),
        result={"warnings": await ordering_warnings_on(db, config_name, session_type)},
    )


async def xml_import_config_on(
    db: aiosqlite.Connection,
    config_name: str,
    positions: Mapping[SessionType, Mapping[int, int]],
    fastest_laps: Mapping[SessionType, tuple[int, int | None]],
) -> Saved:
    """Upsert every position and fastest-lap row of an import on *db*, committing nothing.

    One entry for each position whose points change and each fastest-lap row that changes, from
    what to what; a value already held writes nothing, and where nothing changes the answer is
    unchanged. A fastest-lap row naming no limit keeps the one it holds. Raises
    :class:`ConfigNotFoundError` if *config_name* does not exist.
    """
    config_id = await _get_config_id(db, config_name)
    audits = []
    for session_type, by_position in positions.items():
        for position, points in by_position.items():
            audit = await _write_position(
                db, config_id, config_name, session_type, position, points
            )
            if audit is not None:
                audits.append(audit)
    for session_type, (fl_pts, fl_limit) in fastest_laps.items():
        cursor = await db.execute(
            "SELECT fl_points, fl_position_limit FROM points_config_fl "
            "WHERE config_id = ? AND session_type = ?",
            (config_id, session_type.value),
        )
        existing = await cursor.fetchone()
        if fl_limit is None:
            # Preserve existing fl_position_limit if row already exists
            fl_limit = existing["fl_position_limit"] if existing else None
        old_fl = (
            (existing["fl_points"], existing["fl_position_limit"]) if existing else (None, None)
        )
        if old_fl == (fl_pts, fl_limit):
            continue
        await db.execute(
            """
            INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(config_id, session_type)
            DO UPDATE SET
                fl_points = excluded.fl_points,
                fl_position_limit = excluded.fl_position_limit
            """,
            (config_id, session_type.value, fl_pts, fl_limit),
        )
        audits.append(AuditRecord(
            "POINTS_CONFIG_FL_SET",
            _fastest_lap_value(config_name, session_type, old_fl[0], old_fl[1]),
            _fastest_lap_value(config_name, session_type, fl_pts, fl_limit),
        ))
    return Saved(changed=bool(audits), audits=tuple(audits))
