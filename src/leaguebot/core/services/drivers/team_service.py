"""TeamService — the server's team list, and the division teams seeded from it."""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import aiosqlite

from leaguebot.core.db.database import get_connection, inserted_id
from leaguebot.core.models.refusal import Refused
from leaguebot.core.models.team import DefaultTeam, TeamInstance
from leaguebot.core.utils.asset_slug import normalise
from leaguebot.core.utils.input_validator import NAME, parse_role_mention, parse_user_mention

log = logging.getLogger(__name__)

RESERVE_NAME = "Reserve"

#: The normalised form the Reserve team owns. No configurable team may claim it: the
#: reserve is a singleton whose name is reserved, and a team normalising to the same word
#: would seek the same badge file (Constitution IX, XIV.11).
_RESERVE_KEY = "reserve"

#: The longest a team's shorthand may be. A shorthand is a short alias typed wherever a team is
#: entered, and names the team's artwork file.
SHORTHAND_MAX = 16

#: The longest a team's full name may be. It is what every post and graphic shows, and a longer name
#: set down on a results row cannot be read — the templates carry a team column sized for exactly
#: this much. The signup wizard's buttons, whose labels Discord caps at 80, are nowhere near it.
FULL_NAME_MAX = 32


def validate_team_name(name: str, existing_keys: dict[str, str] | None = None) -> str | None:
    """Why *name* cannot be a team's **shorthand**, or None where it can.

    The shorthand is the team's short alias: typed wherever a team is entered, and the
    name of its artwork file. So it is held to two rules of its own besides the filename rules
    below — at most ``SHORTHAND_MAX`` characters, and no comma, because a shorthand is typed
    into the comma-separated rows of a results submission and a test roster.

    The normalised shorthand is the filename under which every graphic that draws a team
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

    Before any of them, the name is held to the rules every name a league types is held to
    (``NAME``): no group mention, no emoji and no markup, being posted as text and drawn on
    graphics, and no mention of a member.

    Returns a message ready to show a user, or None.
    """
    trimmed = (name or "").strip()
    if not trimmed:
        return "A team's shorthand cannot be empty."

    if len(trimmed) > SHORTHAND_MAX:
        return (
            f'"{trimmed}" is {len(trimmed)} characters long. A shorthand may be at most '
            f"{SHORTHAND_MAX}."
        )

    if "," in trimmed:
        return (
            f'"{trimmed}" holds a comma. A shorthand is typed into the comma-separated rows '
            "of a results submission and a test roster, so it cannot hold one."
        )

    refusal = NAME.check("shorthand", trimmed).refusal
    if refusal is not None:
        return refusal

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


def validate_full_name(full_name: str, taken: dict[str, str] | None = None) -> str | None:
    """Why *full_name* cannot be a team's **full name**, or None where it can.

    The full name is what every post and graphic shows. It is never typed to name a
    team, so it holds no rule of a reference: only the rules every name a league types is held
    to (``NAME``), a length, and uniqueness — two teams showing the same name could not be told
    apart in a standings table.

    *taken* maps an already-taken full name, casefolded, to the full name that holds it, the
    Reserve team's included. Omit it to check only the properties of the name itself.

    Returns a message ready to show a user, or None.
    """
    trimmed = (full_name or "").strip()
    if not trimmed:
        return "A team's full name cannot be empty."

    if len(trimmed) > FULL_NAME_MAX:
        return (
            f"The full name is {len(trimmed)} characters long. A full name may be at most "
            f"{FULL_NAME_MAX}."
        )

    refusal = NAME.check("full name", trimmed).refusal
    if refusal is not None:
        return refusal

    clash = (taken or {}).get(trimmed.casefold())
    if clash is not None:
        return f'"{clash}" is already the full name of a team. A full name names one team only.'

    return None


class TeamService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Normalised-key lookups, for team-name validation (Principle IX)
    # ------------------------------------------------------------------

    @staticmethod
    async def _server_keys(db, *, exclude: str | None = None) -> dict[str, str]:
        """Normalised key → team name, across the server's team list.

        *exclude* drops one name from the comparison, so a rename does not collide with
        the team being renamed.
        """
        rows = await (
            await db.execute(
                "SELECT name FROM default_teams WHERE is_reserve = 0",
            )
        ).fetchall()
        return {
            normalise(r["name"]): r["name"]
            for r in rows
            if r["name"] != exclude and normalise(r["name"])
        }

    @staticmethod
    async def _server_full_names(db, *, exclude_id: int | None = None) -> dict[str, str]:
        """Casefolded full name → full name, across the server's team list, Reserve included.

        *exclude_id* drops one team, so a team keeping its own full name does not clash with
        itself.
        """
        rows = await (await db.execute("SELECT id, full_name FROM default_teams")).fetchall()
        return {
            r["full_name"].casefold(): r["full_name"]
            for r in rows
            if r["id"] != exclude_id
        }

    # ------------------------------------------------------------------
    # Default teams (US4)
    # ------------------------------------------------------------------

    async def get_default_teams(self) -> list[DefaultTeam]:
        """Return all default teams for this server, the Reserve team included."""
        async with get_connection(self._db_path) as db:
            return await self._read_default_teams(db)

    async def get_default_teams_on(self, db: aiosqlite.Connection) -> list[DefaultTeam]:
        """`get_default_teams` on *db*, a connection the caller holds."""
        return await self._read_default_teams(db)

    @staticmethod
    async def _read_default_teams(db: aiosqlite.Connection) -> list[DefaultTeam]:
        cursor = await db.execute(
            "SELECT id, name, full_name, max_seats, is_reserve "
            "FROM default_teams ORDER BY is_reserve ASC, name ASC",
        )
        return [_row_to_default_team(r) for r in await cursor.fetchall()]

    async def judge_add_on(self, db: aiosqlite.Connection, name: str, *, full_name: str) -> None:
        """Raise Refused, in the words a league is told, where a team under the shorthand
        *name* and the *full_name* cannot be added to the server's list, reading on *db* and
        writing nothing: the Reserve name, a shorthand already taken, a name the rules refuse."""
        if name == RESERVE_NAME:
            raise Refused(
                f'The team name "{RESERVE_NAME}" is protected and cannot be managed.'
            )
        existing = await db.execute("SELECT 1 FROM default_teams WHERE name = ?", (name,))
        if await existing.fetchone():
            raise Refused(f'A default team named "{name}" already exists.')

        # The normalised name must serve as a lineup template's field identifier
        # (Principle IX). Scope: the server's own team list.
        problem = validate_team_name(
            name, await self._server_keys(db, exclude=name)
        ) or validate_full_name(full_name, await self._server_full_names(db))
        if problem is not None:
            raise Refused(problem)

    async def add_default_team_on(
        self, db: aiosqlite.Connection, name: str, *, full_name: str, max_seats: int = 2
    ) -> DefaultTeam:
        """Add a team to the server's list under its shorthand *name* and its *full_name*, on
        *db*, a connection the caller holds: commits nothing and writes no audit entry, which the
        change that calls it records.

        Raises Refused on a duplicate or the Reserve name (`judge_add_on`).
        """
        await self.judge_add_on(db, name, full_name=full_name)
        cursor = await db.execute(
            "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
            "VALUES (?, ?, ?, 0)",
            (name, full_name, max_seats),
        )
        return DefaultTeam(
            id=inserted_id(cursor), name=name, full_name=full_name, max_seats=max_seats,
            is_reserve=False,
        )

    async def server_team_on(self, db: aiosqlite.Connection, name: str) -> dict | None:
        """The team of the server's list whose shorthand is *name*, with its mapped role, read on
        *db*: ``{name, full_name, max_seats, is_reserve, role_id}``, or None where none is."""
        return next((t for t in await self.teams_with_roles_on(db) if t["name"] == name), None)

    async def judge_modify_on(
        self,
        db: aiosqlite.Connection,
        current_name: str,
        *,
        shorthand: str | None = None,
        full_name: str | None = None,
    ) -> tuple[int, str, str]:
        """The id of the team whose shorthand is *current_name* and the shorthand and full name it
        would carry, read on *db* and writing nothing. Raises Refused, in the words a league is
        told, where the team cannot be changed so.

        As on a rename, only the **new** names are validated: a team named before a rule existed
        must still be correctable (FR-011). The Reserve team is protected.
        """
        if current_name == RESERVE_NAME:
            raise Refused(
                f'The team name "{RESERVE_NAME}" is protected and cannot be managed.'
            )
        row = await (
            await db.execute(
                "SELECT id, name, full_name, is_reserve FROM default_teams WHERE name = ?",
                (current_name,),
            )
        ).fetchone()
        if row is None:
            raise Refused(f'No default team named "{current_name}" found.')
        if row["is_reserve"]:
            raise Refused(f'The team "{current_name}" is protected and cannot be managed.')

        new_shorthand = (shorthand or row["name"]).strip()
        new_full_name = (full_name or row["full_name"]).strip()

        if new_shorthand != row["name"]:
            conflict = await (
                await db.execute("SELECT 1 FROM default_teams WHERE name = ?", (new_shorthand,))
            ).fetchone()
            if conflict:
                raise Refused(f'A default team named "{new_shorthand}" already exists.')
            problem = validate_team_name(
                new_shorthand, await self._server_keys(db, exclude=current_name)
            )
            if problem is not None:
                raise Refused(problem)

        if new_full_name != row["full_name"]:
            problem = validate_full_name(
                new_full_name, await self._server_full_names(db, exclude_id=row["id"])
            )
            if problem is not None:
                raise Refused(problem)
        return int(row["id"]), new_shorthand, new_full_name

    async def modify_default_team_on(
        self,
        db: aiosqlite.Connection,
        current_name: str,
        *,
        shorthand: str | None = None,
        full_name: str | None = None,
    ) -> dict:
        """Change a team's shorthand, its full name, or both, on *db*, a connection the
        caller holds. Returns both as they stand.

        Everything is judged before anything is written (`judge_modify_on`), so a refusal leaves
        the team exactly as it was. Commits nothing and writes no audit entry: the change that
        calls it records the names (`TEAM_NAMES`) and re-keys the role mapping
        (`rename_team_role_config_on`).
        """
        team_id, new_shorthand, new_full_name = await self.judge_modify_on(
            db, current_name, shorthand=shorthand, full_name=full_name
        )
        await db.execute(
            "UPDATE default_teams SET name = ?, full_name = ? WHERE id = ?",
            (new_shorthand, new_full_name, team_id),
        )
        return {"name": new_shorthand, "full_name": new_full_name}

    async def judge_remove_on(self, db: aiosqlite.Connection, name: str) -> int:
        """The id of the team of the server's list whose shorthand is *name*, read on *db*.
        Raises Refused where none is, or where it is the protected Reserve team."""
        if name == RESERVE_NAME:
            raise Refused(
                f'The team name "{RESERVE_NAME}" is protected and cannot be managed.'
            )
        row = await (
            await db.execute(
                "SELECT id, is_reserve FROM default_teams WHERE name = ?", (name,),
            )
        ).fetchone()
        if row is None:
            raise Refused(f'No default team named "{name}" found.')
        if row["is_reserve"]:
            raise Refused(f'The team "{name}" is protected and cannot be managed.')
        return int(row["id"])

    async def remove_default_team_on(self, db: aiosqlite.Connection, name: str) -> None:
        """Remove a default team on *db*, a connection the caller holds: commits nothing and
        writes no audit entry. Raises Refused if protected or not found."""
        team_id = await self.judge_remove_on(db, name)
        await db.execute("DELETE FROM default_teams WHERE id = ?", (team_id,))

    # ------------------------------------------------------------------
    # Division seeding (US4/US6)
    # ------------------------------------------------------------------

    async def seed_division_teams_on(self, db: aiosqlite.Connection, division_id: int) -> None:
        """Copy the league's list of teams into team_instances and pre-create seats for the
        division, on *db*, a connection the caller holds, the list read there too: commits
        nothing.

        Both names are copied, so the division keeps the names its season ran under whatever
        becomes of the server's list afterwards.
        """
        await self._seed_teams(db, division_id, await self.get_default_teams_on(db))

    @staticmethod
    async def _seed_teams(
        db: aiosqlite.Connection, division_id: int, defaults: list[DefaultTeam]
    ) -> None:
        for team in defaults:
            cursor = await db.execute(
                "INSERT INTO team_instances "
                "(division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    division_id, team.name, team.full_name, team.max_seats,
                    int(team.is_reserve),
                ),
            )
            instance_id = cursor.lastrowid
            if not team.is_reserve:
                for seat_num in range(1, team.max_seats + 1):
                    await db.execute(
                        "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                        "VALUES (?, ?, NULL)",
                        (instance_id, seat_num),
                    )

    # ------------------------------------------------------------------
    # Read helpers for /team list (016-team-cmd-qol)
    # ------------------------------------------------------------------

    async def get_teams_with_roles(self) -> list[dict]:
        """Return all server teams joined with their optional role mapping.

        Each entry: {name, full_name, max_seats, is_reserve, role_id} where role_id is
        int | None, and ``name`` is the team's shorthand.
        Ordered: non-reserve alphabetically first, Reserve last.

        The read behind `/team list`, `/team reserve-role` and team autocomplete: it writes
        nothing, the Reserve team being a row the baseline holds.
        """
        async with get_connection(self._db_path) as db:
            return await self.teams_with_roles_on(db)

    @staticmethod
    async def reserve_user_ids_on(db: aiosqlite.Connection, division_id: int) -> set[int]:
        """The accounts of the drivers seated in *division_id*'s reserve team, read on *db*.

        A seat held by a driver whose placement is not yet confirmed counts as empty (the
        uncommitted-placement rule). For a module reading core's tables only through core's
        services (architecture.md, "The database"), handed the connection of its own read: the
        reserves a query of its own took off a join of `team_seats`, `team_instances` and
        `driver_profiles` come from here. It writes nothing and commits nothing.
        """
        from leaguebot.core.services.seasons.season_lifecycle_service import uncommitted_seat_excluded

        cursor = await db.execute(
            f"""
            SELECT dp.discord_user_id
            FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
            WHERE ti.division_id = ? AND ti.is_reserve = 1
              AND ts.driver_profile_id IS NOT NULL
              AND {uncommitted_seat_excluded("ts")}
            """,
            (division_id,),
        )
        return {
            int(row["discord_user_id"])
            for row in await cursor.fetchall() if row["discord_user_id"]
        }

    @staticmethod
    async def teams_with_roles_on(db: aiosqlite.Connection) -> list[dict]:
        """`get_teams_with_roles` read on *db*, a connection the caller holds."""
        cursor = await db.execute(
            """
            SELECT dt.name, dt.full_name, dt.max_seats, dt.is_reserve, trc.role_id
            FROM default_teams dt
            LEFT JOIN team_role_configs trc
                   ON trc.team_name = dt.name
            ORDER BY dt.is_reserve ASC, dt.name ASC
            """,
        )
        return [
            {
                "name": r["name"],
                "full_name": r["full_name"],
                "max_seats": r["max_seats"],
                "is_reserve": bool(r["is_reserve"]),
                "role_id": r["role_id"],
            }
            for r in await cursor.fetchall()
        ]

    async def resolve_division_team(self, division_id: int, text: str | None) -> TeamReference:
        """The team of the division whose shorthand *text* is."""
        return await resolve_division_team(self._db_path, division_id, text)

    async def resolve_server_team(self, text: str | None) -> TeamReference:
        """The team of the server's list whose shorthand *text* is."""
        return resolve_team_reference(
            text, await self.get_teams_with_roles(), scope=" in the server's list"
        )

    async def get_setup_season_team_names(
        self, season_id: int
    ) -> set[str]:
        """Return unique non-reserve team names across all divisions of a SETUP season."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT DISTINCT ti.name
                FROM team_instances ti
                JOIN divisions d ON d.id = ti.division_id
                JOIN seasons s   ON s.id = d.season_id
                WHERE s.id = ? AND ti.is_reserve = 0
                """,
                (season_id,),
            )
            rows = await cursor.fetchall()
        return {r["name"] for r in rows}

    async def division_race_team_names(self, season_id: int) -> list[tuple[str, list[str]]]:
        """Each division of the season, in tier order, with the names of its race teams (the
        reserve team left out) in name order: what the season review checks the names of."""
        async with get_connection(self._db_path) as db:
            divisions = await (
                await db.execute(
                    "SELECT id, name FROM divisions WHERE season_id = ? ORDER BY tier",
                    (season_id,),
                )
            ).fetchall()
            return [
                (
                    division["name"],
                    [
                        row["name"]
                        for row in await (
                            await db.execute(
                                "SELECT name FROM team_instances "
                                "WHERE division_id = ? AND is_reserve = 0 ORDER BY name",
                                (division["id"],),
                            )
                        ).fetchall()
                    ],
                )
                for division in divisions
            ]

    # ------------------------------------------------------------------
    # Read helpers for review output (US6)
    # ------------------------------------------------------------------

    async def get_division_teams(
        self, division_id: int, *, committed_only: bool = False
    ) -> list[dict]:
        """Return team instances with their seats for a division, in **insertion** order.

        Ordered by ``id``, not by name. A lineup graphic addresses a team by the ordinal of its
        position in this list (XIV.11), and a team added must take the *next free* position so that
        the teams already drawn do not move. Sorted by name, adding a team whose name sorts early
        would shift every later team to a new block, and renaming one would move it — the coupling
        ordinal addressing exists to remove.

        The lineup posting path and the ``/images test`` preview path order identically, so
        the ordinal a team occupies on the graphic is its position in the text beside it
        (FR-009). The **server's** default team list is a separate thing and stays sorted
        by name: it is a configuration listing and no ordinal is read from it.
        """
        from leaguebot.core.services.seasons.season_lifecycle_service import uncommitted_seat_excluded

        async with get_connection(self._db_path) as db:
            instance_rows = await (
                await db.execute(
                    "SELECT id, name, full_name, max_seats, is_reserve "
                    "FROM team_instances WHERE division_id = ? ORDER BY is_reserve ASC, id ASC",
                    (division_id,),
                )
            ).fetchall()
            teams = []
            for inst in instance_rows:
                # *committed_only* reads a seat held by a driver whose placement is not yet
                # confirmed as empty: that driver is outside the championship.
                seat_rows = await (
                    await db.execute(
                        "SELECT ts.seat_number, dp.id AS driver_profile_id, dp.discord_user_id "
                        "FROM team_seats ts "
                        "LEFT JOIN driver_profiles dp ON dp.id = ts.driver_profile_id "
                        + (f"AND {uncommitted_seat_excluded('ts')} " if committed_only else "")
                        + "WHERE ts.team_instance_id = ? ORDER BY ts.seat_number",
                        (inst["id"],),
                    )
                ).fetchall()
                teams.append({
                    "id": inst["id"],
                    "name": inst["name"],
                    "full_name": inst["full_name"],
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
# Finding a team from what a league typed
# ---------------------------------------------------------------------------

#: A Discord ID typed out: 17 to 20 digits. Longer than any shorthand may be, so never one.
_DISCORD_ID_RE = re.compile(r"^\d{17,20}$", re.ASCII)

_HOW_TO_NAME_A_TEAM = "Name a team by its shorthand."


@dataclass(frozen=True)
class TeamReference:
    """What a typed team resolved to: the team, or why it names none."""

    team: Mapping | None = None
    refusal: str | None = None


def resolve_team_reference(
    text: str | None, teams: Sequence[Mapping], *, scope: str = ""
) -> TeamReference:
    """The team of *teams* whose shorthand *text* is, ignoring case, or why it names none.

    A team is typed by its **shorthand** and by nothing else: the commands offer it by autocomplete,
    and pasted text — a results submission, a test roster — takes it alone. The full name is only
    ever shown, and the role is only for mentioning the team, so neither names one here.

    A role mention, ``@everyone``, ``@here``, a mention of a member and a Discord ID typed out
    each look like something a league might type for a team, and each is refused saying what
    it is, rather than as a shorthand no team has.

    *teams* are mappings carrying ``name``, the shorthand, as ``TeamService.get_teams_with_roles``
    and ``resolve_division_team`` return them. *scope* finishes the refusal of a shorthand no
    team holds — " of this division".
    """
    typed = (text or "").strip()
    if not typed:
        return TeamReference(refusal=f"No team was named. {_HOW_TO_NAME_A_TEAM}")

    if typed.lower() in ("@everyone", "@here"):
        return TeamReference(
            refusal=f"`{typed.lower()}` is not a team. {_HOW_TO_NAME_A_TEAM}"
        )
    if parse_role_mention(typed) is not None:
        return TeamReference(
            refusal=f"{typed} is a role, and a role does not name a team. {_HOW_TO_NAME_A_TEAM}"
        )
    if parse_user_mention(typed) is not None:
        return TeamReference(refusal=f"{typed} is a member, not a team. {_HOW_TO_NAME_A_TEAM}")
    if _DISCORD_ID_RE.match(typed):
        return TeamReference(
            refusal=f"`{typed}` is a Discord ID, not a team. {_HOW_TO_NAME_A_TEAM}"
        )

    folded = typed.casefold()
    match = next((t for t in teams if (t.get("name") or "").casefold() == folded), None)
    if match is None:
        return TeamReference(refusal=f"No team{scope} has the shorthand `{typed}`.")
    return TeamReference(team=match)


async def division_team_references(db, division_id: int) -> list[dict]:
    """Every team of the division, as ``resolve_team_reference`` reads a team, on *db*.

    Each carries ``id``, ``name`` (its shorthand), ``full_name``, ``is_reserve`` and
    ``role_id`` — the last through the server's mapping, which is keyed by the shorthand, and
    None for a team with no role.
    """
    rows = await (
        await db.execute(
            "SELECT ti.id, ti.name, ti.full_name, ti.is_reserve, trc.role_id "
            "FROM team_instances ti "
            "LEFT JOIN team_role_configs trc ON trc.team_name = ti.name "
            "WHERE ti.division_id = ? ORDER BY ti.is_reserve, ti.id",
            (division_id,),
        )
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "full_name": r["full_name"],
            "is_reserve": bool(r["is_reserve"]),
            "role_id": r["role_id"],
        }
        for r in rows
    ]


async def resolve_division_team(db_path: str, division_id: int, text: str | None) -> TeamReference:
    """The team of the division whose shorthand *text* is.

    A team with no role is found like any other: the Reserve team's role may be cleared, and a
    driver must still be placed into it.
    """
    async with get_connection(db_path) as db:
        teams = await division_team_references(db, division_id)
    return resolve_team_reference(text, teams, scope=" of this division")


# ---------------------------------------------------------------------------
# Naming a recorded team
# ---------------------------------------------------------------------------

async def team_names_for_instances(db_path: str, instance_ids) -> dict[int, str]:
    """The full name of each division team in *instance_ids*, keyed by its id.

    The full name is what every post and graphic shows. A graphic finds the team's
    artwork by its shorthand instead, from ``team_artwork_keys_for_instances``.

    A result, and a constructors row, record the division's **team**, never its Discord role:
    a role is resolved to the team once, when a submission types it, and a league may replace it
    mid-season. So every post and graphic names a recorded team from here, and none asks Discord
    about a role that may since have been deleted. An id holding no team is simply absent from the
    answer.
    """
    ids = sorted({int(i) for i in instance_ids})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    async with get_connection(db_path) as db:
        rows = await (
            await db.execute(
                f"SELECT id, full_name FROM team_instances WHERE id IN ({marks})",  # noqa: S608
                ids,
            )
        ).fetchall()
    return {int(r["id"]): r["full_name"] for r in rows}


async def team_artwork_keys_for_instances(db_path: str, instance_ids) -> dict[int, str]:
    """The shorthand of each division team in *instance_ids*, keyed by its id.

    The shorthand names the team's artwork file: a graphic draws the full name, from
    ``team_names_for_instances``, and finds the badge by this. An id holding no team is simply
    absent from the answer.
    """
    ids = sorted({int(i) for i in instance_ids})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    async with get_connection(db_path) as db:
        rows = await (
            await db.execute(
                f"SELECT id, name FROM team_instances WHERE id IN ({marks})",  # noqa: S608
                ids,
            )
        ).fetchall()
    return {int(r["id"]): r["name"] for r in rows}


# ---------------------------------------------------------------------------
# Row helper
# ---------------------------------------------------------------------------

def _row_to_default_team(row: aiosqlite.Row) -> DefaultTeam:
    return DefaultTeam(
        id=row["id"],
        name=row["name"],
        full_name=row["full_name"],
        max_seats=row["max_seats"],
        is_reserve=bool(row["is_reserve"]),
    )
