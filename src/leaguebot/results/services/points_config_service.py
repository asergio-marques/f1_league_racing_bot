"""Points configuration service — server-level named config CRUD."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiosqlite

from leaguebot.core.db.database import get_connection, inserted_id
from leaguebot.results.models.points_config import (
    PointsConfigEntry,
    PointsConfigFastestLap,
    PointsConfigStore,
    SessionType,
)
from leaguebot.results.utils.points_ordering import ordering_violations

if TYPE_CHECKING:
    from leaguebot.core.utils.xml_import import XmlImportPayload

log = logging.getLogger(__name__)


class ConfigAlreadyExistsError(Exception):
    pass


class ConfigNotFoundError(Exception):
    pass


class InvalidSessionTypeError(Exception):
    pass


async def create_config(db_path: str, config_name: str) -> PointsConfigStore:
    async with get_connection(db_path) as db:
        try:
            cursor = await db.execute(
                "INSERT INTO points_config_store (config_name) VALUES (?)",
                (config_name,),
            )
            await db.commit()
            row_id = inserted_id(cursor)
        except aiosqlite.IntegrityError:
            raise ConfigAlreadyExistsError(config_name)
    return PointsConfigStore(id=row_id, config_name=config_name)


async def config_exists(db_path: str, config_name: str) -> bool:
    """Whether the league's points store holds a configuration under this name.

    Asked by :func:`season_points_service.attach_config`, which records a season's link to a
    configuration by name into a column carrying no foreign key. Nothing beneath it objects
    to a name that was never created, so the check has to be made above (#132).
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM points_config_store WHERE config_name = ?",
            (config_name,),
        )
        return await cursor.fetchone() is not None


async def setup_seasons_linking(
    db_path: str, config_name: str
) -> list[tuple[int, int]]:
    """The ``(season_id, season_number)`` of every season in setup attached to this name.

    Read before `/results config remove` acts, so the confirmation can say which seasons
    the removal will take the configuration away from rather than asking a blind yes.
    """
    async with get_connection(db_path) as db:
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


async def remove_config(db_path: str, config_name: str) -> None:
    """Delete a named points configuration, and the setup-season links that named it.

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

    The deletes share one transaction with the store row, so a removal cannot half-happen
    and leave the orphan it exists to prevent.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM points_config_store WHERE config_name = ?",
            (config_name,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ConfigNotFoundError(config_name)
        await db.execute(
            "DELETE FROM points_config_store WHERE id = ?",
            (row["id"],),
        )
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
        await db.commit()


async def _get_config_id(db: aiosqlite.Connection, config_name: str) -> int:
    cursor = await db.execute(
        "SELECT id FROM points_config_store WHERE config_name = ?",
        (config_name,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ConfigNotFoundError(config_name)
    return row["id"]


async def set_session_points(
    db_path: str,
    config_name: str,
    session_type: SessionType,
    position: int,
    points: int,
) -> None:
    async with get_connection(db_path) as db:
        config_id = await _get_config_id(db, config_name)
        await db.execute(
            """
            INSERT INTO points_config_entries (config_id, session_type, position, points)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(config_id, session_type, position)
            DO UPDATE SET points = excluded.points
            """,
            (config_id, session_type.value, position, points),
        )
        await db.commit()


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
    """
    async with get_connection(db_path) as db:
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


async def set_fl_bonus(
    db_path: str,
    config_name: str,
    session_type: SessionType,
    fl_points: int,
) -> None:
    if session_type.is_qualifying:
        raise InvalidSessionTypeError(
            f"Fastest-lap bonus cannot be set for qualifying session type: {session_type.value}"
        )
    async with get_connection(db_path) as db:
        config_id = await _get_config_id(db, config_name)
        # Preserve existing fl_position_limit if row already exists
        cursor = await db.execute(
            "SELECT fl_position_limit FROM points_config_fl WHERE config_id = ? AND session_type = ?",
            (config_id, session_type.value),
        )
        existing = await cursor.fetchone()
        fl_position_limit = existing["fl_position_limit"] if existing else None
        await db.execute(
            """
            INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(config_id, session_type)
            DO UPDATE SET fl_points = excluded.fl_points
            """,
            (config_id, session_type.value, fl_points, fl_position_limit),
        )
        await db.commit()


async def set_fl_position_limit(
    db_path: str,
    config_name: str,
    session_type: SessionType,
    limit: int,
) -> None:
    if session_type.is_qualifying:
        raise InvalidSessionTypeError(
            f"Fastest-lap position limit cannot be set for qualifying session type: {session_type.value}"
        )
    async with get_connection(db_path) as db:
        config_id = await _get_config_id(db, config_name)
        cursor = await db.execute(
            "SELECT fl_points FROM points_config_fl WHERE config_id = ? AND session_type = ?",
            (config_id, session_type.value),
        )
        existing = await cursor.fetchone()
        fl_points = existing["fl_points"] if existing else 0
        await db.execute(
            """
            INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(config_id, session_type)
            DO UPDATE SET fl_position_limit = excluded.fl_position_limit
            """,
            (config_id, session_type.value, fl_points, limit),
        )
        await db.commit()


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


async def xml_import_config(
    db_path: str,
    config_name: str,
    payload: "XmlImportPayload",
) -> None:
    """Atomically upsert all position and fastest-lap rows from *payload*.

    Raises :class:`ConfigNotFoundError` if *config_name* does not exist.
    All writes happen inside a single DB connection; the
    aiosqlite context manager rolls back automatically on any exception before
    ``db.commit()``.
    """
    async with get_connection(db_path) as db:
        config_id = await _get_config_id(db, config_name)

        # --- position rows ------------------------------------------------
        for session_type, pos_dict in payload.positions.items():
            for position, points in pos_dict.items():
                await db.execute(
                    """
                    INSERT INTO points_config_entries (config_id, session_type, position, points)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(config_id, session_type, position)
                    DO UPDATE SET points = excluded.points
                    """,
                    (config_id, session_type.value, position, points),
                )

        # --- fastest-lap rows ---------------------------------------------
        for session_type, (fl_pts, fl_limit) in payload.fastest_laps.items():
            if fl_limit is None:
                # Preserve existing fl_position_limit if row already exists
                cursor = await db.execute(
                    "SELECT fl_position_limit FROM points_config_fl "
                    "WHERE config_id = ? AND session_type = ?",
                    (config_id, session_type.value),
                )
                existing = await cursor.fetchone()
                fl_limit = existing["fl_position_limit"] if existing else None

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

        await db.commit()
