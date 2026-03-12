"""TeamService — default team and season team CRUD, plus division seeding."""
from __future__ import annotations

import logging

from db.database import get_connection
from models.team import DefaultTeam, TeamInstance

log = logging.getLogger(__name__)

_RESERVE_NAME = "Reserve"


class TeamService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Default teams (US4)
    # ------------------------------------------------------------------

    async def get_default_teams(self, server_id: int) -> list[DefaultTeam]:
        """Return all default teams for this server."""
        async with get_connection(self._db_path) as db:
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
            existing = await db.execute(
                "SELECT 1 FROM default_teams WHERE server_id = ? AND name = ?",
                (server_id, name),
            )
            if await existing.fetchone():
                raise ValueError(f'A default team named "{name}" already exists.')
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
        """Return team instances with their seats for a division, for review output."""
        async with get_connection(self._db_path) as db:
            instance_rows = await (
                await db.execute(
                    "SELECT id, name, max_seats, is_reserve "
                    "FROM team_instances WHERE division_id = ? ORDER BY is_reserve ASC, name ASC",
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
