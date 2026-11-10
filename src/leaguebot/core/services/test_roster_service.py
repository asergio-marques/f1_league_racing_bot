"""test_roster_service.py — Fake driver management for test mode.

The writers of the roster's changes (`test_roster_changes`) take the connection of the save they
belong to and commit nothing:
  - judge_add_on / seat_on:        judge a fake driver's seating without a write, then write it
  - judge_bulk_on / seat_bulk_on:  the same for a whole roster, seated at once or not at all
  - find_test_driver_on:           the fake driver holding a synthetic id, or the refusal
  - delete_test_drivers_on:        delete fake drivers by profile id, the writer `roster remove` and
                                   `roster clear` take
  - remove_test_driver_on:         remove one fake driver by their synthetic user ID (kept for tests)
  - clear_test_drivers_in_division_on: remove all fake drivers from a division (kept for tests)
  - clear_all_test_drivers_on:     remove all fake drivers from every division (toggle-off)
  - list_test_drivers:             retrieve all fake drivers in a division (for cheat sheet), a
                                   read on a connection of its own

A synthetic driver is inserted straight into `driver_profiles` at ASSIGNED, flagged as a test
driver, and not through `write_transition`: the core specification's test-mode transition (Not
Signed Up to Assigned, under test mode alone) is this seeding, and the state machine has no route
a seeded driver could have taken to reach it.

Synthetic user IDs are guaranteed to be outside real Discord snowflake space by
using a base of 9_000_000_000_000_000_000, well above any real snowflake value
reachable in the foreseeable future (current IDs are ~1.3×10^18 as of 2026).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

import aiosqlite

from leaguebot.core.db.database import get_connection, inserted_id, sole_row
from leaguebot.core.models.refusal import Refused
from leaguebot.core.services.team_service import (
    division_team_references,
    resolve_team_reference,
)
from leaguebot.core.utils.hooks import AwaitedHook
from leaguebot.core.utils.input_validator import NAME, parse_nationality

log = logging.getLogger(__name__)

# Synthetic IDs start here — safely above all real Discord snowflakes.
_SYNTHETIC_ID_BASE = 9_000_000_000_000_000_000


# ─── Return types ────────────────────────────────────────────────────────────

class TestDriverInfo(TypedDict):
    profile_id: int
    discord_user_id: int
    display_name: str
    team_name: str
    nationality: str | None


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
    on a server that held two it seated a mock driver in one season's divisions while
    `/season placements-review` drew the other's: a full `roster list` beside an empty lineup, with
    neither command reporting a fault.
    """
    cursor = await db.execute(
        "SELECT id FROM seasons WHERE status IN ('ACTIVE', 'SETUP') "
        "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
    )
    row = await cursor.fetchone()
    return row["id"] if row else None


async def _get_active_season_id(db_path: str) -> int | None:
    """`_get_active_season_id_on`, on a connection of its own."""
    async with get_connection(db_path) as db:
        return await _get_active_season_id_on(db)


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


async def _get_division_id(
    season_id: int, division_name: str, db_path: str
) -> int | None:
    """Return the division ID for a named division in the active season, or None."""
    async with get_connection(db_path) as db:
        row = await _get_division_on(db, season_id, division_name)
    return row["id"] if row else None


@dataclass(frozen=True)
class Located:
    """Where a fake driver is to be seated: the live season, the division named and the team of it
    whose shorthand was typed."""

    season_id: int
    division_id: int
    division_name: str
    team: Mapping[str, Any]


async def locate_on(
    db: aiosqlite.Connection, division_name: str, team_name: str
) -> Located | str:
    """The season, division and team a fake driver is to be seated in, or the refusal, in its words.

    A team is typed by its shorthand; a role, a member or a name no team has is refused.
    """
    season_id = await _get_active_season_id_on(db)
    if season_id is None:
        return "No active or setup season found."
    division = await _get_division_on(db, season_id, division_name)
    if division is None:
        return f"Division '{division_name}' not found in the active season."
    reference = resolve_team_reference(
        team_name, await division_team_references(db, division["id"]), scope=" of this division"
    )
    if reference.team is None:
        return f"{reference.refusal} (division '{division_name}')"
    return Located(season_id, division["id"], division["name"], reference.team)


# ─── Public API ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AddPlan:
    """A fake driver the roster would seat: judged, and not yet written. *seat_id* is the free seat
    they would take, None where a reserve team grows one."""

    driver_name: str
    nationality: str | None
    located: Located
    synthetic_uid: int
    team_instance_id: int
    seat_id: int | None


async def judge_add_on(
    db: aiosqlite.Connection,
    driver_name: str,
    team_name: str,
    division_name: str,
    nationality: str | None = None,
    placement_service=None,
) -> AddPlan | str:
    """Judge a fake driver's seating in *team_name* in *division_name* on *db*, writing nothing:
    the plan to write it, or the refusal the member can act on, in its words.

    *nationality* is optional and accepted in the form the signup wizard accepts it — a
    nationality adjective, a country name, or "other" — and stored canonically. A mock
    driver created without one records none, and is drawn without a flag.

    *placement_service* holds the driver to the template capacities a real placement into
    the same team is held to, and the driver is refused where one would be. The change always
    passes it; left None, as tests of the seating alone do, the check is skipped.

    *driver_name* is drawn on graphics as a real driver's name is, so it is held to the rules
    every name a league types is held to.
    """
    refusal = NAME.check("driver name", driver_name).refusal
    if refusal is not None:
        return refusal

    canonical_nationality: str | None = None
    if nationality is not None and nationality.strip():
        canonical_nationality = parse_nationality(nationality)
        if canonical_nationality is None:
            return (
                f"Invalid nationality '{nationality.strip()}'. Give a full nationality "
                "(e.g. 'British') or country name (e.g. 'United Kingdom'), or 'other'."
            )

    located = await locate_on(db, division_name, team_name)
    if isinstance(located, str):
        return located

    cursor = await db.execute(
        "SELECT id, name, max_seats, is_reserve FROM team_instances WHERE id = ?",
        (located.team["id"],),
    )
    team_row = await sole_row(cursor)

    if placement_service is not None:
        try:
            await placement_service.guard_roster_capacity(
                located.division_id, {team_row["name"]: 1}
            )
        except Refused as exc:
            return str(exc)

    # Find a free seat (driver_profile_id IS NULL); a reserve team, which has unlimited seats,
    # grows one.
    seat_cursor = await db.execute(
        "SELECT id FROM team_seats "
        "WHERE team_instance_id = ? AND driver_profile_id IS NULL "
        "ORDER BY seat_number "
        "LIMIT 1",
        (team_row["id"],),
    )
    seat_row = await seat_cursor.fetchone()
    if seat_row is None and not team_row["is_reserve"]:
        return f"No free seats available in team '{team_name}'."
    return AddPlan(
        driver_name=driver_name,
        nationality=canonical_nationality,
        located=located,
        synthetic_uid=await _next_synthetic_id_on(db),
        team_instance_id=team_row["id"],
        seat_id=None if seat_row is None else seat_row["id"],
    )


async def seat_on(db: aiosqlite.Connection, plan: AddPlan) -> TestDriverInfo:
    """Write the fake driver *plan* describes, on *db*, committing nothing: the save it belongs to
    does, so that the driver, the seat and the audit entry are one."""
    uid_str = str(plan.synthetic_uid)
    seat_id = plan.seat_id
    if seat_id is None:
        max_cursor = await db.execute(
            "SELECT MAX(seat_number) FROM team_seats WHERE team_instance_id = ?",
            (plan.team_instance_id,),
        )
        max_row = await sole_row(max_cursor)
        new_seat_cursor = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (?, ?, NULL)",
            (plan.team_instance_id, (max_row[0] or 0) + 1),
        )
        seat_id = inserted_id(new_seat_cursor)

    profile_cursor = await db.execute(
        "INSERT INTO driver_profiles "
        "(discord_user_id, current_state, former_driver, is_test_driver, "
        " test_display_name, test_nationality) "
        "VALUES (?, 'ASSIGNED', 0, 1, ?, ?)",
        (uid_str, plan.driver_name, plan.nationality),
    )
    profile_id: int = inserted_id(profile_cursor)
    await _reattach_history(db, uid_str, profile_id)
    await db.execute(
        "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
        (profile_id, seat_id),
    )
    await db.execute(
        "INSERT INTO driver_season_assignments "
        "(driver_profile_id, season_id, division_id, team_seat_id) "
        "VALUES (?, ?, ?, ?)",
        (profile_id, plan.located.season_id, plan.located.division_id, seat_id),
    )
    return TestDriverInfo(
        profile_id=profile_id,
        discord_user_id=plan.synthetic_uid,
        display_name=plan.driver_name,
        # What a reply shows is the full name; the shorthand is what was typed.
        team_name=plan.located.team["full_name"],
        nationality=plan.nationality,
    )


async def add_test_driver_on(
    db: aiosqlite.Connection,
    driver_name: str,
    team_name: str,
    division_name: str,
    nationality: str | None = None,
    placement_service=None,
) -> TestDriverInfo | str:
    """Judge a fake driver (`judge_add_on`) and seat them (`seat_on`) on *db*: the driver, or the
    refusal, in which case nothing was written. Commits nothing."""
    plan = await judge_add_on(
        db, driver_name, team_name, division_name, nationality, placement_service
    )
    if isinstance(plan, str):
        return plan
    return await seat_on(db, plan)


@dataclass(frozen=True)
class BulkSeat:
    """One driver of a roster, judged: the row of the file, the division's stored name, the team's
    id and full name, the free seat they take (None where a reserve team grows one) and the
    nationality stored for them."""

    driver: Any
    division_id: int
    division_name: str
    team_instance_id: int
    team_full_name: str
    seat_id: int | None
    nationality: str | None


@dataclass(frozen=True)
class BulkPlan:
    """A whole roster the import would seat: judged, and not yet written, one `BulkSeat` for each
    driver of the file, in the file's order."""

    season_id: int
    seats: list[BulkSeat]


async def judge_bulk_on(
    db: aiosqlite.Connection,
    drivers: list,
    placement_service=None,
) -> BulkPlan | list[str]:
    """Judge a whole roster on *db*, writing nothing: the plan to seat it, or every fault met.

    *drivers* are `leaguebot.core.utils.roster_import.ParsedDriver` rows, already checked for the things
    a file can be wrong about on its own. What is checked here is what needs the database:
    the division exists, the team exists within it, the team has a free seat, the
    nationality is one the bot accepts, and no driver already holds the ID the file names.

    **A plan and the faults are exclusive**: a fault seats nobody. A roster is one artefact — the
    sibling generator scripts read the whole CSV and expect every driver in it — so a
    half-applied import is a state no script can work against, and pasting the file again after
    fixing one line would duplicate everything that landed the first time.

    **The IDs the file names are the IDs written.** The single add (`judge_add_on`, then
    `seat_on`) allocates `MAX(existing) + 1`, which lines up with the generator's own numbering
    only on a clean season; the sibling scripts key on those IDs absolutely, so here the file
    is authoritative. See `core/utils/roster_import.py`.

    **A division that already holds drivers is refused**, rather than appended to. The CSV
    describes a whole grid, and importing it twice — or over a roster seated by hand —
    would put a division's seats somewhere the file does not describe, with the file still
    looking like the record of what happened.

    *placement_service* holds each division the roster seats to the template capacities a
    real placement is held to, counting every driver the roster gives the division at once.
    The change always passes it; left None, as tests of the seating alone do, the check is
    skipped.
    """
    from leaguebot.core.utils.roster_import import divisions_named

    if not drivers:
        return ["There were no drivers to add."]

    season_id = await _get_active_season_id_on(db)
    if season_id is None:
        return ["No active or setup season found."]

    errors: list[str] = []

    # Nationalities first: the check needs no database, so a file full of misspelt ones is
    # reported without a single query.
    canonical: dict[int, str | None] = {}
    for driver in drivers:
        if driver.nationality is None:
            canonical[driver.line] = None
            continue
        resolved = parse_nationality(driver.nationality)
        if resolved is None:
            errors.append(
                f"Line {driver.line}: `{driver.nationality}` is not a nationality the bot "
                f"knows. Give one like 'British', a country like 'United Kingdom', or "
                f"'other'."
            )
        canonical[driver.line] = resolved

    # Every division named must exist, and must be empty.
    division_ids: dict[str, int] = {}
    division_names: dict[str, str] = {}
    for name in divisions_named(drivers):
        division = await _get_division_on(db, season_id, name)
        if division is None:
            errors.append(f"Division '{name}' is not in the current season.")
            continue
        division_ids[name] = division["id"]
        division_names[name] = division["name"]

        cursor = await db.execute(
            "SELECT COUNT(*) AS seated FROM driver_season_assignments "
            "WHERE season_id = ? AND division_id = ?",
            (season_id, division["id"]),
        )
        row = await cursor.fetchone()
        if row and row["seated"]:
            errors.append(
                f"Division '{name}' already holds {row['seated']} driver(s). Clear it "
                f"with `/test-mode roster clear` before importing a roster into it."
            )

    # An ID already in the database would collide on insert, and the collision is the
    # one thing worth naming precisely: it means the file has already been imported.
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

    # A team is typed by its shorthand, and a line naming it any other way — a role, a member —
    # is refused on that line. Each division's teams are read once.
    teams_of: dict[int, list[dict]] = {
        division_id: await division_team_references(db, division_id)
        for division_id in division_ids.values()
    }
    team_key_of_line: dict[int, str] = {}
    for driver in drivers:
        division_id = division_ids.get(driver.division_name)
        if division_id is None:
            continue
        reference = resolve_team_reference(
            driver.team_name, teams_of[division_id], scope=" of this division"
        )
        if reference.team is None:
            errors.append(f"Line {driver.line}: {reference.refusal}")
            continue
        team_key_of_line[driver.line] = reference.team["name"].lower()

    # Teams and their seats, counted across the whole import: two drivers of one team
    # need two seats, and each row passing on its own would still overfill it.
    wanted: dict[tuple[int, str], int] = {}
    for driver in drivers:
        division_id = division_ids.get(driver.division_name)
        team_key = team_key_of_line.get(driver.line)
        if division_id is None or team_key is None:
            continue
        wanted[(division_id, team_key)] = wanted.get((division_id, team_key), 0) + 1

    teams: dict[tuple[int, str], aiosqlite.Row] = {}
    seats: dict[tuple[int, str], list[int]] = {}
    # Per division, the drivers each of its teams is given, under the team's own name.
    added: dict[int, dict[str, int]] = {}
    for (division_id, team_key), needed in wanted.items():
        cursor = await db.execute(
            "SELECT id, max_seats, is_reserve, name, full_name FROM team_instances "
            "WHERE division_id = ? AND LOWER(name) = ?",
            (division_id, team_key),
        )
        team_row = await cursor.fetchone()
        if team_row is None:
            errors.append(f"Team '{team_key}' is not in the division that names it.")
            continue

        seat_cursor = await db.execute(
            "SELECT id FROM team_seats WHERE team_instance_id = ? "
            "AND driver_profile_id IS NULL ORDER BY seat_number",
            (team_row["id"],),
        )
        free = [r["id"] for r in await seat_cursor.fetchall()]
        if len(free) < needed and not team_row["is_reserve"]:
            errors.append(
                f"Team '{team_row['name']}' has {len(free)} free seat(s) but the "
                f"roster gives it {needed} driver(s)."
            )
        teams[(division_id, team_key)] = team_row
        seats[(division_id, team_key)] = free
        added.setdefault(division_id, {})[team_row["name"]] = needed

    # The template capacities are measured over the whole of a division's roster, since
    # it is seated at once. They run even where something else failed, so every fault is
    # named together.
    if placement_service is not None:
        for name, division_id in division_ids.items():
            if division_id not in added:
                continue
            try:
                await placement_service.guard_roster_capacity(division_id, added[division_id])
            except Refused as exc:
                errors.append(f"Division '{name}': {exc}")

    if errors:
        return errors

    # Every check passed: each driver takes the next free seat of their team, in the file's
    # order, and a reserve team, which has no seat limit, grows one where none is left — the
    # only case reaching here, since every other shortfall was refused above.
    planned: list[BulkSeat] = []
    for driver in drivers:
        division_id = division_ids[driver.division_name]
        key = (division_id, team_key_of_line[driver.line])
        free = seats[key]
        team_row = teams[key]
        planned.append(
            BulkSeat(
                driver=driver,
                division_id=division_id,
                division_name=division_names[driver.division_name],
                team_instance_id=team_row["id"],
                team_full_name=team_row["full_name"],
                seat_id=free.pop(0) if free else None,
                nationality=canonical[driver.line],
            )
        )
    return BulkPlan(season_id=season_id, seats=planned)


async def seat_bulk_on(db: aiosqlite.Connection, plan: BulkPlan) -> int:
    """Write the roster *plan* describes, on *db*, committing nothing: the save it belongs to
    does, so that the drivers, their seats and their audit entries are one. Returns the number
    seated."""
    for planned in plan.seats:
        driver = planned.driver
        seat_id = planned.seat_id
        if seat_id is None:
            max_cursor = await db.execute(
                "SELECT MAX(seat_number) FROM team_seats WHERE team_instance_id = ?",
                (planned.team_instance_id,),
            )
            max_row = await sole_row(max_cursor)
            new_seat = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, NULL)",
                (planned.team_instance_id, (max_row[0] or 0) + 1),
            )
            seat_id = inserted_id(new_seat)

        profile_cursor = await db.execute(
            "INSERT INTO driver_profiles "
            "(discord_user_id, current_state, former_driver, is_test_driver, "
            " test_display_name, test_nationality) "
            "VALUES (?, 'ASSIGNED', 0, 1, ?, ?)",
            (str(driver.discord_user_id), driver.driver_name, planned.nationality),
        )
        profile_id = inserted_id(profile_cursor)
        await _reattach_history(db, str(driver.discord_user_id), profile_id)

        await db.execute(
            "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
            (profile_id, seat_id),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id) "
            "VALUES (?, ?, ?, ?)",
            (profile_id, plan.season_id, planned.division_id, seat_id),
        )
    log.info("roster import: seated %d test drivers", len(plan.seats))
    return len(plan.seats)


async def add_test_drivers_in_bulk_on(
    db: aiosqlite.Connection,
    drivers: list,
    placement_service=None,
) -> tuple[int, list[str]]:
    """Judge a whole roster (`judge_bulk_on`) and seat it (`seat_bulk_on`) on *db*: the number
    seated and the faults met, **the two exclusive**, a fault in which case seating nobody.
    Commits nothing."""
    plan = await judge_bulk_on(db, drivers, placement_service)
    if isinstance(plan, list):
        return 0, plan
    return await seat_bulk_on(db, plan), []


async def list_test_drivers(
    division_name: str,
    db_path: str,
) -> list[TestDriverInfo] | str:
    """Return all fake drivers in *division_name*, ordered by team then profile ID.

    Returns an error string if the division does not exist.
    """
    season_id = await _get_active_season_id(db_path)
    if season_id is None:
        return "No active or setup season found."

    division_id = await _get_division_id(season_id, division_name, db_path)
    if division_id is None:
        return f"Division '{division_name}' not found in the active season."

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dp.id          AS profile_id,
                   dp.discord_user_id,
                   dp.test_display_name,
                   dp.test_nationality,
                   ti.full_name   AS team_name
            FROM driver_profiles dp
            JOIN team_seats ts     ON ts.driver_profile_id = dp.id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE dp.is_test_driver = 1
              AND ti.division_id = ?
            ORDER BY ti.is_reserve ASC, ti.full_name, dp.id
            """,
            (division_id,),
        )
        rows = await cursor.fetchall()

    return [
        TestDriverInfo(
            profile_id=r["profile_id"],
            discord_user_id=int(r["discord_user_id"]),
            display_name=r["test_display_name"] or f"Driver {r['profile_id']}",
            team_name=r["team_name"],
            nationality=r["test_nationality"] or None,
        )
        for r in rows
    ]


@dataclass(frozen=True)
class FakeDriver:
    """A fake driver as the roster's removals and the audit entries of them name them: the profile,
    the synthetic id as stored, the name, the team's full name, the nationality, and the division
    they are seated in (None where they hold no seat)."""

    profile_id: int
    discord_user_id: str
    display_name: str
    team_name: str
    nationality: str | None
    division_id: int | None
    division_name: str | None


_FAKE_DRIVER_SELECT = """
    SELECT dp.id AS profile_id,
           dp.discord_user_id,
           dp.test_display_name,
           dp.test_nationality,
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
        team_name=row["team_name"] or "(unknown team)",
        nationality=row["test_nationality"] or None,
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
    from leaguebot.core.services.season_lifecycle_service import delete_driver_profiles

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
    from leaguebot.core.services.season_lifecycle_service import delete_driver_profiles

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
