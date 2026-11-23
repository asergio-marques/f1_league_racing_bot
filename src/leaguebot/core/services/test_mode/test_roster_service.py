"""test_roster_service.py — Fake driver management for test mode.

The writers of the roster's changes (`test_roster_changes`) take the connection of the save they
belong to and commit nothing:
  - judge_add_on / seat_on:        judge a fake driver's creation without a write, then write it
  - judge_bulk_on / seat_bulk_on:  the same for a whole roster, created at once or not at all
  - find_test_driver_on:           the fake driver holding a synthetic id, or the refusal
  - delete_test_drivers_on:        delete fake drivers by profile id, the writer `roster remove` and
                                   `roster clear` take
  - remove_test_driver_on:         remove one fake driver by their synthetic user ID (kept for tests)
  - clear_test_drivers_in_division_on: remove all fake drivers from a division (kept for tests)
  - clear_all_test_drivers_on:     remove all fake drivers from every division (toggle-off)
  - list_test_drivers:             every fake driver, or those seated in a division, with its state,
                                   its signup's nationality and each seat it holds (for cheat
                                   sheet), a read on a connection of its own

A fake driver is created with an ID and a name alone, at Not Signed Up: no seat, no assignment, no
nationality. Its division, team and seat are decided as any driver's, by `/driver assign` once its
signup is approved, and its nationality is the one its signup gave. It is inserted straight into
`driver_profiles`, flagged as a test driver, and not through `write_transition`: the core
specification's test-mode driver comes into being at Not Signed Up, and no state machine route
leads into that state.

Synthetic user IDs are guaranteed to be outside real Discord snowflake space by
using a base of 9_000_000_000_000_000_000, well above any real snowflake value
reachable in the foreseeable future (current IDs are ~1.3×10^18 as of 2026).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

import aiosqlite

from leaguebot.framework.db.database import get_connection, inserted_id
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.drivers.driver_names import read_driver_records
from leaguebot.framework.utils.hooks import AwaitedHook
from leaguebot.core.utils.input_validator import NAME

log = logging.getLogger(__name__)

# Synthetic IDs start here — safely above all real Discord snowflakes.
_SYNTHETIC_ID_BASE = 9_000_000_000_000_000_000


# ─── Return types ────────────────────────────────────────────────────────────

class TestDriverInfo(TypedDict):
    """A fake driver as its creation names it."""

    profile_id: int
    discord_user_id: int
    display_name: str


@dataclass(frozen=True)
class ListedDriver:
    """A fake driver as `roster list` shows it: its ID, name and state, the nationality its signup
    gave (None where none) and each seat it holds as (team's full name, division's name), none
    where it holds none."""

    discord_user_id: int
    display_name: str
    state: str
    nationality: str | None
    seats: tuple[tuple[str, str], ...]


# ─── Internal helpers ────────────────────────────────────────────────────────

async def _next_synthetic_id_on(db: aiosqlite.Connection) -> int:
    """Return the next available synthetic driver ID."""
    cursor = await db.execute(
        "SELECT MAX(CAST(discord_user_id AS INTEGER)) AS mx "
        "FROM driver_profiles "
        "WHERE is_test_driver = 1 "
        "  AND CAST(discord_user_id AS INTEGER) >= ?",
        (_SYNTHETIC_ID_BASE,),
    )
    row = await cursor.fetchone()
    current_max = row["mx"] if (row and row["mx"] is not None) else None
    return _SYNTHETIC_ID_BASE + 1 if current_max is None else current_max + 1


async def _reattach_history(db, discord_user_id: str, profile_id: int) -> None:
    """Give a driver created by test mode the history an earlier one of the same identifier left.

    Test mode deletes its drivers when it is switched off, and keeps their history entries.
    A driver created again under the same identifier in a later season holds that history as their
    own.
    """
    await db.execute(
        "UPDATE driver_history_entries SET driver_profile_id = ? "
        "WHERE discord_user_id = ? AND driver_profile_id IS NULL",
        (profile_id, discord_user_id),
    )


async def _get_active_season_id_on(db: aiosqlite.Connection) -> int | None:
    """Return the live season ID for a server, or None if it has none.

    A server holds at most one live (SETUP or ACTIVE) season — the partial unique index
    `idx_seasons_one_live` enforces it, and `/season setup` refuses to start a second — so this
    reads at most one row and needs no precedence between the two states.

    The ordering is written in regardless, because it costs nothing and the query used to
    be a bare ``LIMIT 1`` over both states. Which row that returned was uncontracted, and
    on a server that held two it listed one season's drivers beside the other's lineup, with
    neither command reporting a fault.
    """
    cursor = await db.execute(
        "SELECT id FROM seasons WHERE status IN ('ACTIVE', 'SETUP') "
        "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
    )
    row = await cursor.fetchone()
    return row["id"] if row else None


async def _get_division_on(
    db: aiosqlite.Connection, season_id: int, division_name: str
) -> aiosqlite.Row | None:
    """Return the id and name of a named division in the active season, or None."""
    cursor = await db.execute(
        """
        SELECT d.id, d.name
        FROM divisions d
        JOIN seasons s ON s.id = d.season_id
        WHERE s.id = ?
          AND LOWER(d.name) = LOWER(?)
          AND d.status != 'CANCELLED'
        """,
        (season_id, division_name),
    )
    return await cursor.fetchone()


# ─── Public API ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AddPlan:
    """A fake driver the roster would create: judged, and not yet written."""

    driver_name: str
    synthetic_uid: int


async def judge_add_on(db: aiosqlite.Connection, driver_name: str) -> AddPlan | str:
    """Judge a fake driver's creation on *db*, writing nothing: the plan to write it, or the
    refusal the member can act on, in its words.

    *driver_name* is drawn on graphics as a real driver's name is, so it is held to the rules
    every name a league types is held to. A driver is created only while the server holds a live
    season.
    """
    refusal = NAME.check("driver name", driver_name).refusal
    if refusal is not None:
        return refusal
    if await _get_active_season_id_on(db) is None:
        return "No active or setup season found."
    return AddPlan(driver_name=driver_name, synthetic_uid=await _next_synthetic_id_on(db))


async def _insert_driver_on(db: aiosqlite.Connection, discord_user_id: int, name: str) -> int:
    """Write the fake driver *name* under *discord_user_id*, at Not Signed Up, and return its
    profile id."""
    uid_str = str(discord_user_id)
    profile_cursor = await db.execute(
        "INSERT INTO driver_profiles "
        "(discord_user_id, current_state, former_driver, is_test_driver, test_display_name) "
        "VALUES (?, ?, 0, 1, ?)",
        (uid_str, DriverState.NOT_SIGNED_UP.value, name),
    )
    profile_id: int = inserted_id(profile_cursor)
    await _reattach_history(db, uid_str, profile_id)
    return profile_id


async def seat_on(db: aiosqlite.Connection, plan: AddPlan) -> TestDriverInfo:
    """Write the fake driver *plan* describes, on *db*, committing nothing: the save it belongs to
    does, so that the driver and the audit entry are one."""
    profile_id = await _insert_driver_on(db, plan.synthetic_uid, plan.driver_name)
    return TestDriverInfo(
        profile_id=profile_id,
        discord_user_id=plan.synthetic_uid,
        display_name=plan.driver_name,
    )


async def add_test_driver_on(db: aiosqlite.Connection, driver_name: str) -> TestDriverInfo | str:
    """Judge a fake driver (`judge_add_on`) and create them (`seat_on`) on *db*: the driver, or the
    refusal, in which case nothing was written. Commits nothing."""
    plan = await judge_add_on(db, driver_name)
    if isinstance(plan, str):
        return plan
    return await seat_on(db, plan)


@dataclass(frozen=True)
class BulkPlan:
    """A whole roster the import would create: judged, and not yet written, the rows of the file in
    its order (`leaguebot.core.utils.roster_import.ParsedDriver`)."""

    drivers: list[Any]


async def judge_bulk_on(db: aiosqlite.Connection, drivers: list) -> BulkPlan | list[str]:
    """Judge a whole roster on *db*, writing nothing: the plan to create it, or every fault met.

    *drivers* are `leaguebot.core.utils.roster_import.ParsedDriver` rows, already checked for the things
    a file can be wrong about on its own. What is checked here is what needs the database: a live
    season, and that no driver already holds the ID the file names.

    **A plan and the faults are exclusive**: a fault creates nobody. A roster is one artefact — the
    sibling generator scripts read the whole CSV and expect every driver in it — so a
    half-applied import is a state no script can work against, and pasting the file again after
    fixing one line would duplicate everything that landed the first time.

    **The IDs the file names are the IDs written.** The single add (`judge_add_on`, then
    `seat_on`) allocates `MAX(existing) + 1`, which lines up with the generator's own numbering
    only on a clean season; the sibling scripts key on those IDs absolutely, so here the file
    is authoritative. See `core/utils/roster_import.py`.
    """
    if not drivers:
        return ["There were no drivers to add."]
    if await _get_active_season_id_on(db) is None:
        return ["No active or setup season found."]

    # An ID already in the database would collide on insert, and the collision is the
    # one thing worth naming precisely: it means the file has already been imported.
    errors: list[str] = []
    for driver in drivers:
        cursor = await db.execute(
            "SELECT 1 FROM driver_profiles WHERE discord_user_id = ?",
            (str(driver.discord_user_id),),
        )
        if await cursor.fetchone():
            errors.append(
                f"Line {driver.line}: a driver with ID {driver.discord_user_id} is "
                f"already on this server."
            )
    return errors or BulkPlan(drivers=list(drivers))


async def seat_bulk_on(db: aiosqlite.Connection, plan: BulkPlan) -> int:
    """Write the roster *plan* describes, on *db*, committing nothing: the save it belongs to
    does, so that the drivers and their audit entries are one. Returns the number created."""
    for driver in plan.drivers:
        await _insert_driver_on(db, driver.discord_user_id, driver.driver_name)
    log.info("roster import: created %d test drivers", len(plan.drivers))
    return len(plan.drivers)


async def add_test_drivers_in_bulk_on(
    db: aiosqlite.Connection, drivers: list
) -> tuple[int, list[str]]:
    """Judge a whole roster (`judge_bulk_on`) and create it (`seat_bulk_on`) on *db*: the number
    created and the faults met, **the two exclusive**, a fault in which case creating nobody.
    Commits nothing."""
    plan = await judge_bulk_on(db, drivers)
    if isinstance(plan, list):
        return 0, plan
    return await seat_bulk_on(db, plan), []


async def list_test_drivers(
    division_name: str | None,
    db_path: str,
    driver_names_read: AwaitedHook,
) -> list[ListedDriver] | str:
    """Every fake driver, or those seated in *division_name*, in the order they were created, each
    with its state, the nationality its signup gave and each seat it holds.

    Returns an error string if there is no live season or the division does not exist in it. The
    nationality is read from the driver's signup through *driver_names_read*, as a real driver's is.
    """
    async with get_connection(db_path) as db:
        season_id = await _get_active_season_id_on(db)
        if season_id is None:
            return "No active or setup season found."
        division_id: int | None = None
        if division_name is not None:
            division = await _get_division_on(db, season_id, division_name)
            if division is None:
                return f"Division '{division_name}' not found in the active season."
            division_id = division["id"]

        cursor = await db.execute(
            "SELECT id, discord_user_id, test_display_name, current_state "
            "FROM driver_profiles WHERE is_test_driver = 1 ORDER BY id"
        )
        profiles = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT ts.driver_profile_id AS profile_id, ti.full_name AS team_name, "
            "       d.id AS division_id, d.name AS division_name "
            "FROM team_seats ts "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "JOIN divisions d ON d.id = ti.division_id "
            "WHERE ts.driver_profile_id IN "
            "      (SELECT id FROM driver_profiles WHERE is_test_driver = 1) "
            "ORDER BY ts.driver_profile_id, d.tier, d.id"
        )
        seat_rows = await cursor.fetchall()

    seats: dict[int, list[tuple[str, str]]] = {}
    in_division: set[int] = set()
    for row in seat_rows:
        seats.setdefault(row["profile_id"], []).append((row["team_name"], row["division_name"]))
        if row["division_id"] == division_id:
            in_division.add(row["profile_id"])

    shown = [p for p in profiles if division_id is None or p["id"] in in_division]
    records = await read_driver_records(
        db_path,
        driver_names_read,
        [p["discord_user_id"] for p in shown],
        season_id=season_id,
    )
    listed: list[ListedDriver] = []
    for profile in shown:
        record = records.get(str(profile["discord_user_id"]))
        listed.append(
            ListedDriver(
                discord_user_id=int(profile["discord_user_id"]),
                display_name=profile["test_display_name"] or f"Driver {profile['id']}",
                state=profile["current_state"],
                nationality=record.signup_nationality if record else None,
                seats=tuple(seats.get(profile["id"], ())),
            )
        )
    return listed



@dataclass(frozen=True)
class FakeDriver:
    """A fake driver as the roster's removals and the audit entries of them name them: the profile,
    the synthetic id as stored, the name, its state, whether the former-driver flag is set, the
    team's full name and the division they are seated in (both None where they hold no seat)."""

    profile_id: int
    discord_user_id: str
    display_name: str
    state: str
    former_driver: bool
    team_name: str | None
    division_id: int | None
    division_name: str | None


_FAKE_DRIVER_SELECT = """
    SELECT dp.id AS profile_id,
           dp.discord_user_id,
           dp.test_display_name,
           dp.current_state,
           dp.former_driver,
           ti.full_name AS team_name,
           d.id AS division_id,
           d.name AS division_name
    FROM driver_profiles dp
    LEFT JOIN team_seats ts ON ts.driver_profile_id = dp.id
    LEFT JOIN team_instances ti ON ti.id = ts.team_instance_id
    LEFT JOIN divisions d ON d.id = ti.division_id
"""


def _fake_driver(row: aiosqlite.Row) -> FakeDriver:
    return FakeDriver(
        profile_id=row["profile_id"],
        discord_user_id=str(row["discord_user_id"]),
        display_name=row["test_display_name"] or f"Driver {row['profile_id']}",
        state=row["current_state"],
        former_driver=bool(row["former_driver"]),
        team_name=row["team_name"],
        division_id=row["division_id"],
        division_name=row["division_name"],
    )


async def find_test_driver_on(
    db: aiosqlite.Connection, discord_user_id: int
) -> FakeDriver | str:
    """The fake driver holding the synthetic id *discord_user_id*, or the refusal, in its words:
    a real driver's id, and an id nobody holds, are both refused."""
    cursor = await db.execute(
        _FAKE_DRIVER_SELECT
        + "WHERE CAST(dp.discord_user_id AS INTEGER) = ? AND dp.is_test_driver = 1",
        (discord_user_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return "No test driver found with that user ID on this server."
    return _fake_driver(row)


async def delete_test_drivers_on(
    db: aiosqlite.Connection, profile_ids: list[int], *, profiles_deleted: AwaitedHook
) -> None:
    """Delete the fake drivers *profile_ids*, keeping their history, on *db*, committing nothing.

    Deleted by ``delete_driver_profiles``, the route `roster clear` and switching test mode
    off take, so a table that comes to hold a driver is let go of here as well.
    """
    from leaguebot.core.services.seasons.season_lifecycle_service import delete_driver_profiles

    await delete_driver_profiles(
        db, profile_ids, keep_history=True, profiles_deleted=profiles_deleted
    )


async def remove_test_driver_on(
    db: aiosqlite.Connection, discord_user_id: int, *, profiles_deleted: AwaitedHook
) -> str | dict:
    """Remove a single fake driver by their synthetic Discord user ID, keeping their history, on
    *db*, committing nothing: the save it belongs to does.

    Returns a dict with keys ``display_name`` and ``team_name`` on success,
    or an error string if the profile doesn't exist or is not a test driver.
    """
    found = await find_test_driver_on(db, discord_user_id)
    if isinstance(found, str):
        return found
    await delete_test_drivers_on(db, [found.profile_id], profiles_deleted=profiles_deleted)
    return {"display_name": found.display_name, "team_name": found.team_name}


async def clear_all_test_drivers_on(
    db: aiosqlite.Connection, *, profiles_deleted: AwaitedHook
) -> int:
    """Remove every driver created by test mode on the server, keeping their history, on *db*.

    Every one of them, seated or not and in whatever season — switching test mode off deletes
    every fake driver on the server. Their history entries are kept, naming them by identifier,
    so a driver created again under the same identifier holds that history.

    Committing nothing: a season's end deletes them inside the save that records its end.
    Returns the count removed.
    """
    from leaguebot.core.services.seasons.season_lifecycle_service import delete_driver_profiles

    cursor = await db.execute(
        "SELECT id FROM driver_profiles WHERE is_test_driver = 1",
    )
    profile_ids = [r["id"] for r in await cursor.fetchall()]
    await delete_driver_profiles(
        db, profile_ids, keep_history=True, profiles_deleted=profiles_deleted
    )
    return len(profile_ids)


async def locate_division_on(
    db: aiosqlite.Connection, division_name: str
) -> tuple[int, str] | str:
    """The id and stored name of the division *division_name* names in the live season, or the
    refusal, in its words."""
    season_id = await _get_active_season_id_on(db)
    if season_id is None:
        return "No active or setup season found."
    division = await _get_division_on(db, season_id, division_name)
    if division is None:
        return f"Division '{division_name}' not found in the active season."
    return division["id"], division["name"]


async def fake_drivers_in_division_on(
    db: aiosqlite.Connection, division_id: int
) -> list[FakeDriver]:
    """Every fake driver seated in *division_id*, in the order they were created."""
    cursor = await db.execute(
        _FAKE_DRIVER_SELECT + "WHERE dp.is_test_driver = 1 AND ti.division_id = ? ORDER BY dp.id",
        (division_id,),
    )
    return [_fake_driver(row) for row in await cursor.fetchall()]


async def clear_test_drivers_in_division_on(
    db: aiosqlite.Connection, division_id: int, *, profiles_deleted: AwaitedHook
) -> int:
    """Delete every fake driver seated in *division_id*, keeping their history, on *db*,
    committing nothing: the save it belongs to does. Returns the count removed."""
    drivers = await fake_drivers_in_division_on(db, division_id)
    await delete_test_drivers_on(
        db, [driver.profile_id for driver in drivers], profiles_deleted=profiles_deleted
    )
    return len(drivers)
