"""TeamService — default team and season team CRUD, plus division seeding."""
from __future__ import annotations

import logging

from db.database import get_connection
from models.team import DefaultTeam, TeamInstance
from utils.asset_resolver import normalise

log = logging.getLogger(__name__)

_RESERVE_NAME = "Reserve"

#: The normalised form the Reserve team owns. No configurable team may claim it: the
#: reserve is a singleton whose name is reserved, and a team normalising to the same word
#: would seek the same badge file (Constitution IX, XIV.11).
_RESERVE_KEY = "reserve"


def validate_team_name(name: str, existing_keys: dict[str, str] | None = None) -> str | None:
    """Why *name* cannot become an asset **filename**, or None where it can.

    The normalised team name is the filename under which every graphic that draws a team
    badge seeks that team's image (Constitution XIV.13) — the lineup, both results graphics,
    both standings graphics, the attendance sheet and the verdict. Constraining the datum is
    the business of the module that owns it (Principle IX); discovering the collision at
    render time is not.

    **Three** rules since v6.0.0, and they bind **whether or not the image module is
    enabled**: a name is cheapest to constrain at the one moment it is set, and a league
    enabling the module later would otherwise hold names it could not correct without
    losing that team's history.

    A fourth rule stood here — that the normalised form begin with a **letter** — and is
    withdrawn. It held only while that form had to serve as the `@id` of a node in an XML
    document, which a template addressed a team's block by. Templates address teams by
    ordinal now, the name reaches the module as a filename and in no other way, and a
    filename may begin with a digit. "2Fast Motorsport" is admitted.

    *existing_keys* maps an already-taken normalised key to the name that holds it, within
    the scope being checked — the server for the server's team list, the division for the
    teams of a season. Omit it to check only the properties of the name itself.

    Returns a message ready to show a user, or None.
    """
    trimmed = (name or "").strip()
    if not trimmed:
        return "A team name cannot be empty."

    key = normalise(trimmed)
    if not key:
        return (
            f'"{trimmed}" holds no letter or digit, so it cannot name an image file. '
            f"Choose a name with at least one."
        )

    if key == _RESERVE_KEY:
        return (
            f'"{trimmed}" reduces to "{_RESERVE_KEY}", which is reserved for the Reserve '
            f"team of every division. Choose another name."
        )

    clash = (existing_keys or {}).get(key)
    if clash is not None:
        return (
            f'"{trimmed}" and "{clash}" both reduce to "{key}", so both would draw the '
            f"same team image. Choose a more distinct name."
        )

    return None


class TeamService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Normalised-key lookups, for team-name validation (Principle IX)
    # ------------------------------------------------------------------

    @staticmethod
    async def _server_keys(db, server_id: int, *, exclude: str | None = None) -> dict[str, str]:
        """Normalised key → team name, across the server's team list.

        *exclude* drops one name from the comparison, so a rename does not collide with
        the team being renamed.
        """
        rows = await (
            await db.execute(
                "SELECT name FROM default_teams WHERE server_id = ? AND is_reserve = 0",
                (server_id,),
            )
        ).fetchall()
        return {
            normalise(r["name"]): r["name"]
            for r in rows
            if r["name"] != exclude and normalise(r["name"])
        }

    @staticmethod
    async def _division_keys(db, division_id: int, *, exclude: str | None = None) -> dict[str, str]:
        """Normalised key → team name, within one division of a season."""
        rows = await (
            await db.execute(
                "SELECT name FROM team_instances WHERE division_id = ? AND is_reserve = 0",
                (division_id,),
            )
        ).fetchall()
        return {
            normalise(r["name"]): r["name"]
            for r in rows
            if r["name"] != exclude and normalise(r["name"])
        }

    # ------------------------------------------------------------------
    # Default teams (US4)
    # ------------------------------------------------------------------

    @staticmethod
    async def _ensure_reserve(db, server_id: int) -> bool:
        """Create the server's Reserve team where none is present. True where it was.

        Principle IX requires the Reserve team to exist in the server's team configuration
        as it does in every division, and FR-014 requires it to be created whenever that
        configuration is read or written and none is present. The older seeding fired only
        when the server had **no team at all**, so a configuration that lost its Reserve
        row — or predates the rule — never regained one.
        """
        found = await (
            await db.execute(
                "SELECT 1 FROM default_teams WHERE server_id = ? AND is_reserve = 1 LIMIT 1",
                (server_id,),
            )
        ).fetchone()
        if found:
            return False
        await db.execute(
            "INSERT INTO default_teams (server_id, name, max_seats, is_reserve) "
            "VALUES (?, ?, -1, 1)",
            (server_id, _RESERVE_NAME),
        )
        return True

    async def get_default_teams(self, server_id: int) -> list[DefaultTeam]:
        """Return all default teams for this server, the Reserve team included."""
        async with get_connection(self._db_path) as db:
            if await self._ensure_reserve(db, server_id):
                await db.commit()
            cursor = await db.execute(
                "SELECT id, server_id, name, max_seats, is_reserve "
                "FROM default_teams WHERE server_id = ? ORDER BY is_reserve ASC, name ASC",
                (server_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_default_team(r) for r in rows]

    async def add_default_team(
        self, server_id: int, name: str, max_seats: int = 2
    ) -> DefaultTeam:
        """Add a new default team.  Raises ValueError on duplicate or Reserve name."""
        if name == _RESERVE_NAME:
            raise ValueError(
                f'The team name "{_RESERVE_NAME}" is protected and cannot be managed.'
            )
        async with get_connection(self._db_path) as db:
            await self._ensure_reserve(db, server_id)
            existing = await db.execute(
                "SELECT 1 FROM default_teams WHERE server_id = ? AND name = ?",
                (server_id, name),
            )
            if await existing.fetchone():
                raise ValueError(f'A default team named "{name}" already exists.')

            # The normalised name must serve as a lineup template's field identifier
            # (Principle IX). Scope: the server's own team list.
            problem = validate_team_name(
                name, await self._server_keys(db, server_id, exclude=name)
            )
            if problem is not None:
                raise ValueError(problem)

            cursor = await db.execute(
                "INSERT INTO default_teams (server_id, name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, 0)",
                (server_id, name, max_seats),
            )
            await db.commit()
            row_id = cursor.lastrowid
        return DefaultTeam(id=row_id, server_id=server_id, name=name, max_seats=max_seats, is_reserve=False)

    async def rename_default_team(
        self, server_id: int, current_name: str, new_name: str
    ) -> None:
        """Rename a default team.  Raises ValueError if protected or name conflict."""
        if current_name == _RESERVE_NAME:
            raise ValueError(
                f'The team name "{_RESERVE_NAME}" is protected and cannot be managed.'
            )
        async with get_connection(self._db_path) as db:
            row = await (
                await db.execute(
                    "SELECT id, is_reserve FROM default_teams "
                    "WHERE server_id = ? AND name = ?",
                    (server_id, current_name),
                )
            ).fetchone()
            if row is None:
                raise ValueError(f'No default team named "{current_name}" found.')
            if row["is_reserve"]:
                raise ValueError(
                    f'The team "{current_name}" is protected and cannot be managed.'
                )
            conflict = await (
                await db.execute(
                    "SELECT 1 FROM default_teams WHERE server_id = ? AND name = ?",
                    (server_id, new_name),
                )
            ).fetchone()
            if conflict:
                raise ValueError(f'A default team named "{new_name}" already exists.')

            # Only the **new** name is validated. The current name identifies a team that
            # already exists, and validating it would leave a team named before this rule
            # impossible to rename or to remove (FR-011).
            problem = validate_team_name(
                new_name, await self._server_keys(db, server_id, exclude=current_name)
            )
            if problem is not None:
                raise ValueError(problem)

            await db.execute(
                "UPDATE default_teams SET name = ? WHERE id = ?",
                (new_name, row["id"]),
            )
            await db.commit()

    async def remove_default_team(self, server_id: int, name: str) -> None:
        """Remove a default team.  Raises ValueError if protected or not found."""
        if name == _RESERVE_NAME:
            raise ValueError(
                f'The team name "{_RESERVE_NAME}" is protected and cannot be managed.'
            )
        async with get_connection(self._db_path) as db:
            row = await (
                await db.execute(
                    "SELECT id, is_reserve FROM default_teams "
                    "WHERE server_id = ? AND name = ?",
                    (server_id, name),
                )
            ).fetchone()
            if row is None:
                raise ValueError(f'No default team named "{name}" found.')
            if row["is_reserve"]:
                raise ValueError(
                    f'The team "{name}" is protected and cannot be managed.'
                )
            await db.execute("DELETE FROM default_teams WHERE id = ?", (row["id"],))
            await db.commit()

    # ------------------------------------------------------------------
    # Division seeding (US4/US6)
    # ------------------------------------------------------------------

    async def seed_division_teams(self, division_id: int, server_id: int) -> None:
        """Copy default_teams into team_instances and pre-create seats for the division."""
        defaults = await self.get_default_teams(server_id)
        async with get_connection(self._db_path) as db:
            for team in defaults:
                cursor = await db.execute(
                    "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                    "VALUES (?, ?, ?, ?)",
                    (division_id, team.name, team.max_seats, int(team.is_reserve)),
                )
                instance_id = cursor.lastrowid
                if not team.is_reserve:
                    for seat_num in range(1, team.max_seats + 1):
                        await db.execute(
                            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                            "VALUES (?, ?, NULL)",
                            (instance_id, seat_num),
                        )
            await db.commit()

    # ------------------------------------------------------------------
    # /bot-init seeding (US4)
    # ------------------------------------------------------------------

    async def seed_default_teams_if_empty(self, server_id: int) -> None:
        """Insert the Reserve team if no teams exist yet for this server."""
        async with get_connection(self._db_path) as db:
            existing = await (
                await db.execute(
                    "SELECT 1 FROM default_teams WHERE server_id = ? LIMIT 1",
                    (server_id,),
                )
            ).fetchone()
            if existing:
                return
            await db.execute(
                "INSERT INTO default_teams (server_id, name, max_seats, is_reserve) "
                "VALUES (?, ?, -1, 1)",
                (server_id, _RESERVE_NAME),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Season team management (US5)
    # ------------------------------------------------------------------

    async def _get_setup_season_divisions(
        self, server_id: int, season_id: int
    ) -> list[int]:
        """Return division IDs for a SETUP season.  Raises if not in SETUP."""
        async with get_connection(self._db_path) as db:
            season_row = await (
                await db.execute(
                    "SELECT status FROM seasons WHERE id = ? AND server_id = ?",
                    (season_id, server_id),
                )
            ).fetchone()
            if season_row is None or season_row["status"] != "SETUP":
                raise ValueError(
                    "No season is currently in setup. "
                    "Team configuration can only be changed during season setup."
                )
            div_rows = await (
                await db.execute(
                    "SELECT id FROM divisions WHERE season_id = ?",
                    (season_id,),
                )
            ).fetchall()
        return [r["id"] for r in div_rows]

    async def season_team_add(
        self, server_id: int, season_id: int, name: str, max_seats: int = 2
    ) -> int:
        """Add a team to all divisions of a SETUP season.  Returns division count."""
        if name == _RESERVE_NAME:
            raise ValueError(
                f'The Reserve team is protected and cannot be modified.'
            )
        division_ids = await self._get_setup_season_divisions(server_id, season_id)
        async with get_connection(self._db_path) as db:
            for div_id in division_ids:
                conflict = await (
                    await db.execute(
                        "SELECT 1 FROM team_instances WHERE division_id = ? AND name = ?",
                        (div_id, name),
                    )
                ).fetchone()
                if conflict:
                    raise ValueError(
                        f'A team named "{name}" already exists in one or more divisions.'
                    )
                # Scope for uniqueness is the **division** for a season's teams.
                problem = validate_team_name(
                    name, await self._division_keys(db, div_id, exclude=name)
                )
                if problem is not None:
                    raise ValueError(problem)
            for div_id in division_ids:
                cursor = await db.execute(
                    "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                    "VALUES (?, ?, ?, 0)",
                    (div_id, name, max_seats),
                )
                instance_id = cursor.lastrowid
                for seat_num in range(1, max_seats + 1):
                    await db.execute(
                        "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                        "VALUES (?, ?, NULL)",
                        (instance_id, seat_num),
                    )
            await db.commit()
        return len(division_ids)

    async def season_team_rename(
        self, server_id: int, season_id: int, current_name: str, new_name: str
    ) -> int:
        """Rename a team across all divisions of a SETUP season.  Returns division count."""
        if current_name == _RESERVE_NAME:
            raise ValueError(
                f'The Reserve team is protected and cannot be modified.'
            )
        division_ids = await self._get_setup_season_divisions(server_id, season_id)
        async with get_connection(self._db_path) as db:
            # Only the new name is validated (FR-011), once per division since uniqueness
            # is division-scoped. Every division is checked before any is written, so a
            # rejection leaves the season exactly as it stood.
            for div_id in division_ids:
                problem = validate_team_name(
                    new_name, await self._division_keys(db, div_id, exclude=current_name)
                )
                if problem is not None:
                    raise ValueError(problem)
            for div_id in division_ids:
                await db.execute(
                    "UPDATE team_instances SET name = ? "
                    "WHERE division_id = ? AND name = ?",
                    (new_name, div_id, current_name),
                )
            await db.commit()
        return len(division_ids)

    async def season_team_remove(
        self, server_id: int, season_id: int, name: str
    ) -> int:
        """Remove a team from all divisions of a SETUP season.  Returns division count."""
        if name == _RESERVE_NAME:
            raise ValueError(
                f'The Reserve team is protected and cannot be modified.'
            )
        division_ids = await self._get_setup_season_divisions(server_id, season_id)
        async with get_connection(self._db_path) as db:
            for div_id in division_ids:
                instance_row = await (
                    await db.execute(
                        "SELECT id FROM team_instances WHERE division_id = ? AND name = ?",
                        (div_id, name),
                    )
                ).fetchone()
                if instance_row:
                    await db.execute(
                        "DELETE FROM team_seats WHERE team_instance_id = ?",
                        (instance_row["id"],),
                    )
                    await db.execute(
                        "DELETE FROM team_instances WHERE id = ?",
                        (instance_row["id"],),
                    )
            await db.commit()
        return len(division_ids)

    # ------------------------------------------------------------------
    # Read helpers for /team list (016-team-cmd-qol)
    # ------------------------------------------------------------------

    async def get_teams_with_roles(self, server_id: int) -> list[dict]:
        """Return all server teams joined with their optional role mapping.

        Each entry: {name, max_seats, is_reserve, role_id} where role_id is int | None.
        Ordered: non-reserve alphabetically first, Reserve last.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT dt.name, dt.max_seats, dt.is_reserve, trc.role_id
                FROM default_teams dt
                LEFT JOIN team_role_configs trc
                       ON trc.server_id = dt.server_id
                      AND trc.team_name = dt.name
                WHERE dt.server_id = ?
                ORDER BY dt.is_reserve ASC, dt.name ASC
                """,
                (server_id,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "name": r["name"],
                "max_seats": r["max_seats"],
                "is_reserve": bool(r["is_reserve"]),
                "role_id": r["role_id"],
            }
            for r in rows
        ]

    async def get_setup_season_team_names(
        self, server_id: int, season_id: int
    ) -> set[str]:
        """Return unique non-reserve team names across all divisions of a SETUP season."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT DISTINCT ti.name
                FROM team_instances ti
                JOIN divisions d ON d.id = ti.division_id
                JOIN seasons s   ON s.id = d.season_id
                WHERE s.server_id = ? AND s.id = ? AND ti.is_reserve = 0
                """,
                (server_id, season_id),
            )
            rows = await cursor.fetchall()
        return {r["name"] for r in rows}

    # ------------------------------------------------------------------
    # Read helpers for review output (US6)
    # ------------------------------------------------------------------

    async def get_division_teams(self, division_id: int) -> list[dict]:
        """Return team instances with their seats for a division, in **insertion** order.

        Ordered by ``id``, not by name. A lineup graphic addresses a team by the ordinal of
        its position in this list (XIV.11), and a team added must take the *next free*
        position so that the teams already drawn do not move (047 FR-008). Sorted by name,
        adding a team whose name sorts early would shift every later team to a new block,
        and renaming one would move it — the coupling ordinal addressing exists to remove.

        The lineup posting path and the ``/images test`` preview path order identically, so
        the ordinal a team occupies on the graphic is its position in the text beside it
        (FR-009). The **server's** default team list is a separate thing and stays sorted
        by name: it is a configuration listing and no ordinal is read from it.
        """
        async with get_connection(self._db_path) as db:
            instance_rows = await (
                await db.execute(
                    "SELECT id, name, max_seats, is_reserve "
                    "FROM team_instances WHERE division_id = ? ORDER BY is_reserve ASC, id ASC",
                    (division_id,),
                )
            ).fetchall()
            teams = []
            for inst in instance_rows:
                seat_rows = await (
                    await db.execute(
                        "SELECT ts.seat_number, ts.driver_profile_id, dp.discord_user_id "
                        "FROM team_seats ts "
                        "LEFT JOIN driver_profiles dp ON dp.id = ts.driver_profile_id "
                        "WHERE ts.team_instance_id = ? ORDER BY ts.seat_number",
                        (inst["id"],),
                    )
                ).fetchall()
                teams.append({
                    "name": inst["name"],
                    "max_seats": inst["max_seats"],
                    "is_reserve": bool(inst["is_reserve"]),
                    "seats": [
                        {
                            "seat_number": s["seat_number"],
                            "driver_profile_id": s["driver_profile_id"],
                            "discord_user_id": s["discord_user_id"],
                        }
                        for s in seat_rows
                    ],
                })
        return teams


# ---------------------------------------------------------------------------
# Row helper
# ---------------------------------------------------------------------------

def _row_to_default_team(row: object) -> DefaultTeam:
    return DefaultTeam(
        id=row["id"],
        server_id=row["server_id"],
        name=row["name"],
        max_seats=row["max_seats"],
        is_reserve=bool(row["is_reserve"]),
    )
