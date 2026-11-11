"""PlacementService — the seats: driver placement, the lineup it leaves and the sack."""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection, sole_row
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.models.driver_profile import DriverProfile, DriverState
from leaguebot.core.models.refusal import Refused
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.services.driver_service import write_transition
from leaguebot.core.services.image_drawing import CapacityProblem, ImageDrawing
from leaguebot.core.services.module_service import SwitchNotSignedUp
from leaguebot.core.services.standing_post_handler import post_standing
from leaguebot.core.services.team_role_service import TeamRoleService
from leaguebot.core.utils.in_a_job import posts_in_a_job

log = logging.getLogger(__name__)

@dataclass
class SackSaved:
    """A sack written on the caller's save, and what is left for a job of its own to do.

    *divisions* are those the driver left, whose lineups the caller asks for. *revoke_role_ids*
    are every role the driver's placements entitled them to, read before the placements were
    removed (the driver role included), for the job that takes them back; empty for a test
    driver, who holds none. *user_id* is the driver's Discord account. *audit* is the old and new
    value of the `DRIVER_SACK` audit entry, for the caller to write.
    """

    divisions: list[int]
    revoke_role_ids: list[int]
    user_id: int
    is_test_driver: bool
    audit: dict


@dataclass
class LineupPosted:
    """A lineup posted: the message sent and the one it replaces, for the jobs after the post.

    *previous* is the division's recorded lineup message as it stood when the post was made, and
    *form* is "image" for a picture and "text" for the embed. *problem* is the image module's
    words for why a picture could not be drawn, carried for the job's own line where the lineup
    fell back to text, or None where nothing went wrong. Nothing is saved: the post job's
    `record` saves *message_id* (`lineup_posting.record_lineup_on`) and the job after it deletes
    *previous*.
    """

    message_id: int
    channel_id: int
    previous: int | None
    division_name: str
    form: str
    problem: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def uncommitted_placements_on(db: aiosqlite.Connection, season_id: int) -> list[dict]:
    """Every placement of *season_id* not yet committed, ordered by division and team, read on
    *db* so that a save sees the placements it has written and not yet committed.

    Each row: driver_profile_id, discord_user_id, is_test_driver, test_display_name,
    division_id, division_name, division_role_id, team_name (the shorthand, which keys the
    role mapping) and team_full_name (what a review shows).
    """
    cursor = await db.execute(
        """
        SELECT dsa.driver_profile_id, dp.discord_user_id, dp.is_test_driver,
               dp.test_display_name, d.id AS division_id, d.name AS division_name,
               d.mention_role_id AS division_role_id, ti.name AS team_name,
               ti.full_name AS team_full_name
        FROM driver_season_assignments dsa
        JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
        JOIN divisions d ON d.id = dsa.division_id
        LEFT JOIN team_seats ts ON ts.id = dsa.team_seat_id
        LEFT JOIN team_instances ti ON ti.id = ts.team_instance_id
        WHERE dsa.season_id = ? AND dsa.committed = 0
        ORDER BY d.tier, ti.is_reserve, ti.full_name, dp.discord_user_id
        """,
        (season_id,),
    )
    return [dict(row) for row in await cursor.fetchall()]


class PlacementService:
    def __init__(
        self,
        db_path: str,
        *,
        drawing: ImageDrawing | None = None,
        roles: TeamRoleService | None = None,
    ) -> None:
        self._db_path = db_path
        #: The team-role service the builder hands every caller of the roles, the one on the bot.
        #: A seat's write reads the role mapping through it, and a placement made
        #: or sacked outside the queue gives and takes the roles through its logging helpers.
        #: Built over the same database where a unit test exercises placement alone.
        self._roles = roles if roles is not None else TeamRoleService(db_path)
        #: The image module's answers to core's drawing protocol (`image_drawing.ImageDrawing`),
        #: handed in by the builder as results is handed attendance's. The four capacity
        #: guards ask it rather than reach image's services through the bot. None in
        #: every unit test that exercises placement alone: no guard asks anything, and a test
        #: roster of any size is accepted.
        self._drawing = drawing

    # ------------------------------------------------------------------
    # Confirming placements mid-season
    # ------------------------------------------------------------------

    async def uncommitted_placements(self, season_id: int) -> list[dict]:
        """Every placement of *season_id* not yet committed (see
        :func:`uncommitted_placements_on`), read on a connection of its own."""
        async with get_connection(self._db_path) as db:
            return await uncommitted_placements_on(db, season_id)

    async def division_lineup(self, division_id: int) -> list[dict]:
        """Every driver assigned in *division_id*, committed or not, for the season review.

        One row each, ordered by the team's full name and then the driver: *discord_user_id*,
        *is_test_driver*, *test_display_name*, *team_name* (the team's full name) and
        *is_reserve*. Read on a connection of its own.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT dp.discord_user_id, dp.is_test_driver, dp.test_display_name,
                       ti.full_name AS team_name, ti.is_reserve
                FROM driver_season_assignments dsa
                JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
                JOIN team_seats ts ON ts.id = dsa.team_seat_id
                JOIN team_instances ti ON ti.id = ts.team_instance_id
                WHERE dsa.division_id = ? AND dp.current_state = 'ASSIGNED'
                ORDER BY ti.full_name, dp.discord_user_id
                """,
                (division_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    # ------------------------------------------------------------------
    # Image template capacity (036 / Constitution XIV.12)
    # ------------------------------------------------------------------

    async def _is_reserve_team(self, division_id: int, team_name: str) -> bool | None:
        """Whether *team_name* is the division's reserve team, or None where there is no such team.

        The capacity guards below measure two different populations — the reserve block and
        the classification — and a driver joins exactly one of them. Neither guard can tell
        which without this, and both run in a change's check (`guard_assign`, `guard_move`),
        before its save opens a transaction, where the same flag is read again from the row it
        needs in hand anyway. `guard_roster_capacity` also runs inside the roster changes' save,
        which judges once more on its own connection.
        """
        async with get_connection(self._db_path) as db:
            row = await (
                await db.execute(
                    "SELECT is_reserve FROM team_instances "
                    "WHERE division_id = ? AND name = ?",
                    (division_id, team_name),
                )
            ).fetchone()
        return None if row is None else bool(row["is_reserve"])

    async def _ask_drawing(
        self,
        guard: str,
        count: Callable[[], Awaitable[int | None]],
        ask: Callable[[ImageDrawing, int], Awaitable[CapacityProblem | None]],
    ) -> CapacityProblem | None:
        """Read the guard's own count with *count*, *ask* the image module's handed drawing
        about it, and hand back what it says, or None where nothing is handed, the count
        reports the guard does not apply (None), or either step could not run. *guard* names
        the guard in the host's log.

        **The capacity guards' one catch.** Reading the seats and asking the drawing are one
        guard, not two: a fault in either must not block a placement, only a genuine
        over-capacity may, so the fault goes to the host's log in full and the guard that
        asked answers as if told nothing was wrong (architecture.md, "A catch-all error
        handler", a stated exception beside ``amend_end_changes._names_step``).
        """
        if self._drawing is None:
            return None
        try:
            seated = await count()
            if seated is None:
                return None
            return await ask(self._drawing, seated)
        except SwitchNotSignedUp:
            raise  # a module that never signed up is a fault in the bot's assembly, not a reading
        except Exception as exc:  # noqa: BLE001 — a stated exception, see the docstring
            log.error("%s capacity guard could not run: %s", guard, exc, exc_info=True)
            return None

    async def _guard_reserve_capacity(
        self, division_id: int, team_name: str, *, adding: int = 1
    ) -> None:
        """Refuse a reserve placement that would outgrow the lineup template (FR/R8).

        The reserve block is the one lineup collection whose slots the template fixes, so
        it is the one to which overflow applies. XIV.12 requires overflow to be rejected
        at the earliest moment it can be detected, with the change unapplied — which is
        this command, not the render.

        **It bounds reserve placements and nothing else.** A driver going into an ordinary
        race team never joins the reserve block, so a full block says nothing about them.
        So it is told which team is being filled: counting the division's reserves on every
        assignment would leave a division carrying as many reserves as its template draws slots
        for unable to seat anybody at all — the refusal blaming the reserve block for a
        placement that was never part of it.

        *adding* is how many reserves the change seats — one for a placement, more for a test
        roster seated whole (``guard_roster_capacity``).

        Reads its own count and asks the image module's handed drawing (``_ask_drawing``)
        under the one catch: never raises for its own reasons, only a genuine over-capacity
        the drawing reports may.
        """

        async def _reserves_seated() -> int | None:
            # Not a reserve placement, or no such team — `judge_assign_on` reports a team
            # that does not exist in its own words, and this guard stays quiet.
            if not await self._is_reserve_team(division_id, team_name):
                return None
            async with get_connection(self._db_path) as db:
                row = await (
                    await db.execute(
                        "SELECT COUNT(*) AS seated FROM driver_season_assignments dsa "
                        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
                        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
                        "WHERE dsa.division_id = ? AND ti.is_reserve = 1",
                        (division_id,),
                    )
                ).fetchone()
            return ((row["seated"] if row else 0) or 0) + adding

        problem = await self._ask_drawing(
            "reserve", _reserves_seated,
            lambda drawing, seated: drawing.reserve_capacity_problem(seated),
        )
        if problem is not None:
            raise Refused(
                f"{problem.problem}. The driver was **not** assigned. {problem.remedy}"
            )

    async def _guard_sheet_capacity(self, division_id: int, *, adding: int = 1) -> None:
        """Refuse a placement that would outgrow the attendance sheet template (FR-042).

        The sheet draws every driver of the division, and its rows are counted from the file
        rather than declared as a number — so this reads the configured template exactly as
        the reserve guard does, and for the same reason: XIV.12 rejects overflow at the
        earliest moment it can be detected, with the change unapplied. That moment is this
        command. Discovering it at a posting means the league has already lost its sheet.

        **It counts reserves, and that is deliberate — do not make it match the standings
        guard.** The two look alike and measure different things. A reserve holds no row in a
        classification ever, so ``_guard_standings_capacity`` excludes them; but the sheet
        draws a reserve the moment one is allocated to a round, and
        ``attendance_service`` selects on ``ti.is_reserve = 1 AND dra.assigned_team_id IS NOT
        NULL`` to do it. In the worst case every reserve on the books is allocated and takes a
        row, so counting them all is the correct ceiling rather than an over-count — and the
        team being filled is likewise immaterial here, a reserve placement being exactly the
        one that might later want a row. Pinned by
        ``test_placement_sheet_capacity_guard.py``.

        *adding* is how many drivers the change seats, of either kind — one for a placement,
        more for a test roster seated whole (``guard_roster_capacity``).

        Reads its own count and asks the image module's handed drawing (``_ask_drawing``)
        under the one catch: never raises for its own reasons, only a genuine over-capacity
        the drawing reports may.
        """

        async def _drivers_seated() -> int | None:
            async with get_connection(self._db_path) as db:
                row = await (
                    await db.execute(
                        "SELECT COUNT(*) AS seated FROM driver_season_assignments "
                        "WHERE division_id = ?",
                        (division_id,),
                    )
                ).fetchone()
            return ((row["seated"] if row else 0) or 0) + adding

        problem = await self._ask_drawing(
            "sheet", _drivers_seated,
            lambda drawing, seated: drawing.sheet_capacity_problem(seated),
        )
        if problem is not None:
            raise Refused(
                f"{problem.problem}. The driver was **not** assigned. {problem.remedy}"
            )

    async def _guard_standings_capacity(
        self, division_id: int, team_name: str, *, adding: int = 1
    ) -> None:
        """Refuse a placement that would outgrow the driver standings template (FR-044).

        The standings draw every driver of the division's classification, and their rows are
        counted from the file rather than declared as a number — so this reads the configured
        template exactly as the reserve and sheet guards do, and for the same reason: XIV.12
        rejects overflow at the earliest moment it can be detected, with the change unapplied.

        **A reserve is not an entry of a classification.** They stand in for an absent driver
        and add no car to the grid, and ``standings_service`` filters ``ti.is_reserve = 0`` out
        of every classification it builds. So the count below excludes them, and a placement
        *into* the reserve team is not measured at all — it grows no classification. Counting
        every assignment of the division, and adding one whatever team is being filled, would
        refuse an ordinary placement one seat early for each reserve on the books, and a reserve
        outright once the classified drivers filled the rows.

        The **constructors** ceiling is not checked here. Seating a driver adds no team, so no
        driver assignment can breach it; it is checked at ``/season placements-review``, which is where a
        division's team count is settled.

        *adding* is how many classified drivers the change seats — one for a placement, more
        for a test roster seated whole (``guard_roster_capacity``).

        Reads its own count and asks the image module's handed drawing (``_ask_drawing``)
        under the one catch: never raises for its own reasons, only a genuine over-capacity
        the drawing reports may.
        """

        async def _classified_seated() -> int | None:
            # A reserve placement adds no entry to the classification, and no such team —
            # `judge_assign_on` reports a team that does not exist in its own words.
            if await self._is_reserve_team(division_id, team_name) is not False:
                return None
            async with get_connection(self._db_path) as db:
                row = await (
                    await db.execute(
                        "SELECT COUNT(*) AS seated FROM driver_season_assignments dsa "
                        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
                        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
                        "WHERE dsa.division_id = ? AND ti.is_reserve = 0",
                        (division_id,),
                    )
                ).fetchone()
            return ((row["seated"] if row else 0) or 0) + adding

        problem = await self._ask_drawing(
            "standings", _classified_seated,
            lambda drawing, seated: drawing.standings_capacity_problem(seated),
        )
        if problem is not None:
            raise Refused(
                f"{problem.problem}. The driver was **not** assigned. {problem.remedy}"
            )

    async def _guard_test_mode(self, driver_profile_id: int) -> None:
        """Refuse to seat a *real* driver while the server is in test mode.

        Test mode and a real league may not share a server, so a real driver never enters
        a division while the flag is on: their signup is refused at the button, and their
        placement here. A fake driver is untouched by this — the roster commands seat them
        by another path, but attendance autoreserve moves them through this very call.

        Unlike the capacity guards above, this is a genuine precondition rather than a
        best-effort check, so it does **not** swallow its own errors: a fault reading the
        flag must not be the thing that lets a real driver through.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT (SELECT test_mode_active FROM server_configs) "
                "           AS test_mode_active, "
                "       dp.is_test_driver "
                "FROM driver_profiles dp WHERE dp.id = ?",
                (driver_profile_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            # No such profile: `judge_assign_on` reports that itself, in its own words.
            return
        if row["is_test_driver"]:
            return
        if row["test_mode_active"]:
            raise Refused(
                "Test mode is active — a real driver cannot be assigned to a team. "
                "Turn it off with `/test-mode toggle` first."
            )

    async def _guard_image_capacity(
        self, division_id: int, season_id: int, team_name: str
    ) -> None:
        """Refuse a placement that would outgrow any image template whose capacity is fixed
        in code, asking the image module's handed drawing (``_ask_drawing``).

        While no image type declares a capacity the image module answers nothing, and this
        guard is silent — it activates **by data**, the moment the first image type declares
        one, with no further code change.

        Reads its own count and asks the image module's handed drawing (``_ask_drawing``)
        under the one catch: never raises for its own reasons, only a genuine over-capacity
        the drawing reports may.
        """
        # The reserve block is guarded separately: it counts reserve drivers, not every
        # seated driver, and its capacity comes from the template rather than from here.
        # It needs the team, because only a placement into the reserve team joins it.
        await self._guard_reserve_capacity(division_id, team_name)

        # The attendance sheet's rows are likewise counted from the template rather than
        # declared in code, so they are invisible to the declared-capacity question below.
        await self._guard_sheet_capacity(division_id)

        # And so are the driver standings' — both standings catalogues declare no capacity
        # and derive their rows from the file. It needs the team too, because a reserve
        # joins no classification.
        await self._guard_standings_capacity(division_id, team_name)

        async def _division_seated() -> int | None:
            async with get_connection(self._db_path) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) AS seated FROM driver_season_assignments "
                    "WHERE season_id = ? AND division_id = ?",
                    (season_id, division_id),
                )
                row = await cursor.fetchone()
            return ((row["seated"] if row else 0) or 0) + 1

        problem = await self._ask_drawing(
            "declared", _division_seated,
            lambda drawing, seated: drawing.declared_capacity_problem(seated),
        )
        if problem is not None:
            raise Refused(f"{problem.problem}. {problem.remedy}")

    async def guard_roster_capacity(
        self, division_id: int, added: Mapping[str, int]
    ) -> None:
        """Refuse a test roster that a real placement into the same teams would be refused for.

        *added* maps each team of *division_id* to the number of drivers the change seats in
        it. Test mode seats its fake drivers without passing through ``guard_assign``, so
        without this a roster larger than the league's templates could hold would be accepted,
        and the rehearsal would pass a configuration a real season would refuse.

        **The count is per division, summed across its teams.** A bulk roster seats a whole
        division at once, and two teams that each fit on their own can together outgrow the
        standings. The three template guards run once each, measuring the reserves, every
        driver, and the classified drivers respectively. A team the division does not hold is
        skipped: the roster service refuses it in its own words.

        The declared-capacity branch of ``_guard_image_capacity`` is not run: every catalogue
        declares ``capacity=None`` today, so it bounds no placement, real or test.

        Raises Refused naming the template, as a real placement's refusal does.
        """
        reserve_team: str | None = None
        classified_team: str | None = None
        reserves = classified = 0
        for team_name, count in added.items():
            is_reserve = await self._is_reserve_team(division_id, team_name)
            if is_reserve is None or count <= 0:
                continue
            if is_reserve:
                reserve_team, reserves = team_name, reserves + count
            else:
                classified_team, classified = team_name, classified + count

        if reserve_team is not None:
            await self._guard_reserve_capacity(division_id, reserve_team, adding=reserves)
        if reserves + classified:
            await self._guard_sheet_capacity(division_id, adding=reserves + classified)
        if classified_team is not None:
            await self._guard_standings_capacity(
                division_id, classified_team, adding=classified
            )

    # ------------------------------------------------------------------
    # Assign driver
    # ------------------------------------------------------------------

    @staticmethod
    async def _team_full_name(db, division_id: int, team_name: str) -> str:
        """The full name of the division's team held under the shorthand *team_name*.

        What a reply and a log line show. The shorthand is what every key here is, so the two
        are read apart: a command answers with the name a league reads, and writes the other.
        """
        row = await (
            await db.execute(
                "SELECT full_name FROM team_instances WHERE division_id = ? AND name = ?",
                (division_id, team_name),
            )
        ).fetchone()
        return row["full_name"] if row else team_name

    @staticmethod
    async def _find_free_seat(db, division_id: int, team_name: str) -> tuple[int | None, bool]:
        """The first free seat of *team_name* in *division_id*, and whether it is Reserve, read
        only: None for the seat where the reserve team has none free and would make another.

        A race team's seats are finite and fill in number order; the reserve team makes another
        seat rather than refusing. Raises Refused for a team the division does not hold, or a
        race team with no seat free.
        """
        cursor = await db.execute(
            """
            SELECT ti.is_reserve FROM team_instances ti
            WHERE ti.division_id = ? AND ti.name = ?
            """,
            (division_id, team_name),
        )
        ti_row = await cursor.fetchone()
        if ti_row is None:
            raise Refused(f"Team **{team_name}** not found in this division.")
        is_reserve = bool(ti_row["is_reserve"])

        cursor = await db.execute(
            """
            SELECT ts.id FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE ti.division_id = ? AND ti.name = ? AND ts.driver_profile_id IS NULL
            ORDER BY ts.seat_number ASC
            LIMIT 1
            """,
            (division_id, team_name),
        )
        seat_row = await cursor.fetchone()
        if seat_row is not None:
            return seat_row["id"], is_reserve
        if not is_reserve:
            raise Refused(f"**{team_name}** in this division has no available seats.")
        return None, is_reserve

    async def _free_seat(self, db, division_id: int, team_name: str) -> tuple[int, bool]:
        """The first free seat of *team_name* in *division_id*, and whether it is Reserve,
        making the reserve team another seat where it has none free.

        Shared by placing a driver and moving one, so the two cannot disagree about where a
        driver may sit. Raises Refused as `_find_free_seat` does.
        """
        found, is_reserve = await self._find_free_seat(db, division_id, team_name)
        if found is not None:
            return found, is_reserve

        # Reserve has unlimited seats; create a new one
        cursor = await db.execute(
            "SELECT MAX(ts.seat_number) FROM team_seats ts "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE ti.division_id = ? AND ti.name = ?",
            (division_id, team_name),
        )
        max_row = await cursor.fetchone()
        next_seat = (max_row[0] or 0) + 1
        cursor = await db.execute(
            "SELECT id FROM team_instances WHERE division_id = ? AND name = ?",
            (division_id, team_name),
        )
        ti_id = (await cursor.fetchone())["id"]
        cursor = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (?, ?, NULL)",
            (ti_id, next_seat),
        )
        return cursor.lastrowid, is_reserve

    async def judge_move_on(
        self,
        db,
        *,
        driver_profile_id: int,
        season_id: int,
        from_division_id: int,
        to_division_id: int,
        team_name: str,
    ) -> dict:
        """Judge a move of a driver on *db*, writing nothing: what `move_seat_on` would refuse,
        in its words, and the rows it then moves by.

        Refused where the driver holds no committed placement in the division
        left, where they already hold a seat in a different division moved into, where the team
        is the one they already sit in, and where the team has no seat free. Returns the profile's
        account (*user_id*), whether it is a test driver and the placement left (*source*).
        """
        cursor = await db.execute(
            "SELECT discord_user_id, current_state, is_test_driver FROM driver_profiles "
            "WHERE id = ?",
            (driver_profile_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise Refused("Driver profile not found.")

        cursor = await db.execute(
            """
            SELECT dsa.id, dsa.team_seat_id, dsa.committed, ti.name AS team_name,
                   ti.full_name AS team_full_name
            FROM driver_season_assignments dsa
            LEFT JOIN team_seats ts ON ts.id = dsa.team_seat_id
            LEFT JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE dsa.driver_profile_id = ? AND dsa.season_id = ? AND dsa.division_id = ?
            """,
            (driver_profile_id, season_id, from_division_id),
        )
        source = await cursor.fetchone()
        if source is None:
            raise Refused("Driver holds no seat in the division they are moved from.")
        if not source["committed"]:
            raise Refused(
                "That placement is not yet confirmed. Change it with `/driver unassign` "
                "and `/driver assign`."
            )
        if from_division_id == to_division_id and source["team_name"] == team_name:
            raise Refused(f"Driver already sits in **{team_name}** in this division.")
        if from_division_id != to_division_id:
            cursor = await db.execute(
                "SELECT 1 FROM driver_season_assignments "
                "WHERE driver_profile_id = ? AND season_id = ? AND division_id = ?",
                (driver_profile_id, season_id, to_division_id),
            )
            if await cursor.fetchone() is not None:
                raise Refused(
                    "Driver already holds a seat in the division they would move into."
                )

        await self._find_free_seat(db, to_division_id, team_name)
        return {
            "user_id": int(row["discord_user_id"]),
            "is_test_driver": bool(row["is_test_driver"]),
            "source": source,
        }

    async def move_seat_on(
        self,
        db,
        *,
        driver_profile_id: int,
        season_id: int,
        from_division_id: int,
        to_division_id: int,
        team_name: str,
    ) -> dict:
        """Move a committed driver from their seat to a team of the same or another division, on
        *db*, leaving the audit entry to the caller.

        Everything `move_on` writes but the audit entry, written on the connection handed and
        committed by no one here, so that a change on the queue writes the entry with the queue's
        clock (`audit`). It awaits nothing but *db*: no member is fetched, no role asked of
        Discord, no lineup posted, no capacity guarded (`guard_move`, an ACT's, reads templates
        off the loop). Refused as `judge_move_on` refuses.

        Returns the summary (from_division, to_division, from_team, to_team, lineups), the audit
        entry's *audit* (its old and new values and the division), what is left for the roles:
        *revoke* and *grant*, the role ids to take back and give, empty for a test driver, the
        driver's account *user_id*, and *is_test_driver*.
        """
        judged = await self.judge_move_on(
            db, driver_profile_id=driver_profile_id, season_id=season_id,
            from_division_id=from_division_id, to_division_id=to_division_id,
            team_name=team_name,
        )
        source = judged["source"]
        is_test_driver = judged["is_test_driver"]

        new_seat_id, _ = await self._free_seat(db, to_division_id, team_name)
        to_team_full_name = await self._team_full_name(db, to_division_id, team_name)

        cursor = await db.execute(
            "SELECT id, name, mention_role_id FROM divisions WHERE id IN (?, ?)",
            (from_division_id, to_division_id),
        )
        divisions = {r["id"]: r for r in await cursor.fetchall()}

        await db.execute(
            "UPDATE team_seats SET driver_profile_id = NULL WHERE id = ?",
            (source["team_seat_id"],),
        )
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
            (driver_profile_id, new_seat_id),
        )
        if from_division_id == to_division_id:
            await db.execute(
                "UPDATE driver_season_assignments SET team_seat_id = ? WHERE id = ?",
                (new_seat_id, source["id"]),
            )
        else:
            # A placement in the division left goes with the seat; the points already
            # scored there stay in that division's results, which is where they count.
            await db.execute(
                "UPDATE driver_season_assignments "
                "SET division_id = ?, team_seat_id = ? "
                "WHERE id = ?",
                (to_division_id, new_seat_id, source["id"]),
            )
        audit = {
            "old_value": {"division": divisions[from_division_id]["name"],
                          "team": source["team_name"]},
            "new_value": {"division": divisions[to_division_id]["name"], "team": team_name},
            "division_id": to_division_id,
        }

        revoke: list[int] = []
        grant: list[int] = []
        if not is_test_driver:
            # Which team roles the driver still holds a seat for, now the move is written.
            cursor = await db.execute(
                """
                SELECT DISTINCT ti.name FROM driver_season_assignments dsa
                JOIN team_seats ts ON ts.id = dsa.team_seat_id
                JOIN team_instances ti ON ti.id = ts.team_instance_id
                WHERE dsa.driver_profile_id = ? AND dsa.season_id = ?
                """,
                (driver_profile_id, season_id),
            )
            teams_held = {r["name"] for r in await cursor.fetchall()}
            old_role = (
                await self._roles.team_role_on(db, source["team_name"])
                if source["team_name"] else None
            )
            new_role = await self._roles.team_role_on(db, team_name)
            held_role_ids = set()
            for held in teams_held:
                held_role = await self._roles.team_role_on(db, held)
                if held_role is not None:
                    held_role_ids.add(held_role)
            if from_division_id != to_division_id:
                revoke.append(divisions[from_division_id]["mention_role_id"])
            if old_role is not None and old_role not in held_role_ids:
                revoke.append(old_role)
            grant.append(divisions[to_division_id]["mention_role_id"])
            if new_role is not None:
                grant.append(new_role)

        return {
            "from_division": divisions[from_division_id]["name"],
            "to_division": divisions[to_division_id]["name"],
            "from_team": source["team_full_name"] or source["team_name"],
            "to_team": to_team_full_name,
            # The lineups the move touched, for the caller to post.
            "lineups": list(dict.fromkeys((from_division_id, to_division_id))),
            "audit": audit,
            "revoke": revoke,
            "grant": grant,
            "user_id": judged["user_id"],
            "is_test_driver": is_test_driver,
        }

    async def move_on(
        self,
        db,
        *,
        driver_profile_id: int,
        season_id: int,
        from_division_id: int,
        to_division_id: int,
        team_name: str,
        acting_user_id: int,
        acting_user_name: str,
    ) -> dict:
        """Move a committed driver from their seat to a team of the same or another division, on *db*.

        `move_seat_on`, and the `DRIVER_MOVE` audit entry written with it on the same
        connection, stamped with the wall clock and committed by no one here, so that a
        sanction's save carries the move with the rest of its writes. Refused and
        returned as `move_seat_on` refuses and returns.
        """
        summary = await self.move_seat_on(
            db, driver_profile_id=driver_profile_id, season_id=season_id,
            from_division_id=from_division_id, to_division_id=to_division_id,
            team_name=team_name,
        )
        audit = summary["audit"]
        await db.execute(
            "INSERT INTO audit_entries "
            "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
            "VALUES (?, ?, ?, 'DRIVER_MOVE', ?, ?, ?)",
            (
                acting_user_id, acting_user_name, audit["division_id"],
                json.dumps(audit["old_value"]), json.dumps(audit["new_value"]),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return summary

    async def guard_move(self, to_division_id: int, season_id: int, team_name: str) -> None:
        """Refuse a move into *team_name* of *to_division_id* that would outgrow a template.

        The capacity guards `move_driver` makes before it writes: the reserve block, the
        attendance sheet's rows, the driver standings' and an image template's. Public so that
        a sanction's check, a job of its own, can ask them before the move is saved, since they
        read templates off the loop and cannot run inside a save. Raises Refused.
        """
        await self._guard_image_capacity(to_division_id, season_id, team_name)

    async def move_driver(
        self,
        driver_profile_id: int,
        season_id: int,
        from_division_id: int,
        to_division_id: int,
        team_name: str,
        acting_user_id: int,
        acting_user_name: str,
        guild: discord.Guild | None,
        discord_user_id: str,
    ) -> dict:
        """Move a committed driver from their seat in one division to a team of the same or another.

        One change: the old seat freed and the new one taken in a single transaction (`move_on`),
        the roles of the seat left revoked where no other seat of the driver maps to them, the roles
        of the seat taken granted, and the lineup of each division touched posted once. A driver
        moved to another division leaves the points they scored in the division they left, those
        being counted per division. The roles are changed swallowing Discord's refusals: this is the
        committing form `/attendance sync` moves a driver through, with no queue to report to.
        `/driver move` is a change on the queue (`driver_changes`), and a sanction's own role change
        raises.

        Refused as `move_on` and `guard_move` refuse.

        Returns a summary dict: from_division, to_division, from_team, to_team, lineups.
        """
        await self.guard_move(to_division_id, season_id, team_name)

        async with get_connection(self._db_path) as db:
            summary = await self.move_on(
                db, driver_profile_id=driver_profile_id, season_id=season_id,
                from_division_id=from_division_id, to_division_id=to_division_id,
                team_name=team_name, acting_user_id=acting_user_id,
                acting_user_name=acting_user_name,
            )
            await db.commit()

        if guild is not None and not summary["is_test_driver"]:
            member = guild.get_member(int(discord_user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(discord_user_id))
                except discord.HTTPException:
                    member = None
            if member is not None:
                if summary["revoke"]:
                    await self._roles._revoke_roles(member, *summary["revoke"])
                await self._roles._grant_roles(member, *summary["grant"])

        return {
            key: summary[key]
            for key in ("from_division", "to_division", "from_team", "to_team", "lineups")
        }

    async def guard_assign(
        self, division_id: int, season_id: int, team_name: str, driver_profile_id: int
    ) -> None:
        """Refuse an assignment that would outgrow a template or seat a real driver in test mode.

        The guards `/driver assign` makes before it writes: the image templates' capacity
        (`_guard_image_capacity`), then the server's test-mode flag (`_guard_test_mode`). Public
        so that the assignment's check, a job of its own, can ask them at the ask and again as the
        change starts, since they read templates off the loop and cannot run inside a save.
        Raises Refused.
        """
        await self._guard_image_capacity(division_id, season_id, team_name)
        await self._guard_test_mode(driver_profile_id)

    async def judge_assign_on(
        self,
        db,
        *,
        driver_profile_id: int,
        division_id: int,
        team_name: str,
        season_id: int,
        uncommitted_only: bool = False,
    ) -> dict:
        """Judge the assignment of a driver to a team of a division on *db*, writing nothing: what
        `assign_on` would refuse, in its words, and the state it would assign from.

        Raises Refused where the profile is not found, is neither Unassigned nor Assigned,
        with *uncommitted_only* already holds a confirmed placement in the season, already holds
        a seat in the division, or the team is not in the division or has no free seat. Returns
        the profile's *current_state*.
        """
        cursor = await db.execute(
            "SELECT current_state FROM driver_profiles WHERE id = ?", (driver_profile_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise Refused("Driver profile not found.")
        current_state = DriverState(row["current_state"])
        if current_state not in (DriverState.UNASSIGNED, DriverState.ASSIGNED):
            raise Refused(
                f"Driver must be Unassigned or Assigned to be placed "
                f"(current state: {current_state.value})."
            )

        if uncommitted_only:
            cursor = await db.execute(
                "SELECT 1 FROM driver_season_assignments "
                "WHERE driver_profile_id = ? AND season_id = ? AND committed = 1 LIMIT 1",
                (driver_profile_id, season_id),
            )
            if await cursor.fetchone() is not None:
                raise Refused(
                    "Driver already holds a confirmed placement this season. Move them "
                    "with `/driver move`, or release them from a division with "
                    "`/driver release`."
                )

        cursor = await db.execute(
            "SELECT id FROM driver_season_assignments "
            "WHERE driver_profile_id = ? AND season_id = ? AND division_id = ?",
            (driver_profile_id, season_id, division_id),
        )
        if await cursor.fetchone() is not None:
            cursor = await db.execute("SELECT name FROM divisions WHERE id = ?", (division_id,))
            div_row = await cursor.fetchone()
            div_name = div_row["name"] if div_row else str(division_id)
            raise Refused(f"Driver is already assigned to a team in **{div_name}**.")

        await self._find_free_seat(db, division_id, team_name)
        return {"current_state": current_state}

    async def assign_on(
        self,
        db,
        *,
        driver_profile_id: int,
        division_id: int,
        team_name: str,
        season_id: int,
        uncommitted_only: bool = False,
    ) -> dict:
        """Assign a driver to a team seat of a division on *db*, leaving the audit entry to the
        caller.

        Seats the driver in the team's first free seat (the reserve team making another where it
        has none), writes the assignment unconfirmed, and moves an Unassigned driver to Assigned,
        on the connection handed and committed by no one here, so that a change on the queue
        writes the entry with the queue's clock (*audit*). An unconfirmed placement stands outside
        the championship: it asks no role and no lineup, which follow when placements are
        confirmed. Nothing is awaited but *db*, and the guards (`guard_assign`) are the caller's.
        *uncommitted_only* refuses a driver who already holds a confirmed placement in the
        season. Refused as `judge_assign_on` refuses.

        Returns the summary (was_unassigned, team_name, division_name) and the audit entry's
        *audit*: its old and new values and the division.
        """
        judged = await self.judge_assign_on(
            db, driver_profile_id=driver_profile_id, division_id=division_id,
            team_name=team_name, season_id=season_id, uncommitted_only=uncommitted_only,
        )
        current_state = judged["current_state"]

        seat_id, _ = await self._free_seat(db, division_id, team_name)
        team_full_name = await self._team_full_name(db, division_id, team_name)
        cursor = await db.execute("SELECT name FROM divisions WHERE id = ?", (division_id,))
        div_name = (await sole_row(cursor))["name"]

        was_unassigned = current_state == DriverState.UNASSIGNED
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
            (driver_profile_id, seat_id),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
            "VALUES (?, ?, ?, ?, 0)",
            (driver_profile_id, season_id, division_id, seat_id),
        )
        if was_unassigned:
            await write_transition(
                db, driver_profile_id, DriverState.UNASSIGNED, DriverState.ASSIGNED
            )
        return {
            "was_unassigned": was_unassigned,
            "team_name": team_full_name,
            "division_name": div_name,
            "audit": {
                "old_value": {"state": current_state.value},
                "new_value": {
                    "team": team_name,
                    "division": div_name,
                    "seat_id": seat_id,
                    "new_state": DriverState.ASSIGNED.value,
                },
                "division_id": division_id,
            },
        }

    # ------------------------------------------------------------------
    # Unassign driver
    # ------------------------------------------------------------------

    async def judge_release_on(
        self, db, *, driver_profile_id: int, division_id: int, season_id: int
    ) -> dict:
        """Judge the release of a committed driver from one division on *db*, writing nothing:
        what `unassign_on` would refuse of it, in its words, and the facts it then unassigns by.

        Refused for a division where the driver holds no seat, for an uncommitted
        placement, which is unassigned instead, for a driver's only confirmed seat, which is
        sacked or moved instead, and as `judge_unassign_on` refuses.
        """
        cursor = await db.execute(
            "SELECT committed FROM driver_season_assignments "
            "WHERE driver_profile_id = ? AND season_id = ? AND division_id = ?",
            (driver_profile_id, season_id, division_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise Refused("Driver holds no seat in that division.")
        if not row["committed"]:
            raise Refused(
                "That placement is not yet confirmed. Remove it with `/driver unassign`."
            )
        # Only a confirmed seat counts: a driver left holding nothing but an unconfirmed
        # placement has been taken out of the championship, which moving them does instead.
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_season_assignments "
            "WHERE driver_profile_id = ? AND season_id = ? AND division_id != ? "
            "AND committed = 1",
            (driver_profile_id, season_id, division_id),
        )
        if (await sole_row(cursor))["n"] == 0:
            raise Refused(
                "That is the driver's only seat. Sack them with `/driver sack`, or move "
                "them with `/driver move`."
            )
        return await self.judge_unassign_on(
            db, driver_profile_id=driver_profile_id, division_id=division_id,
            season_id=season_id,
        )

    async def judge_unassign_on(
        self,
        db,
        *,
        driver_profile_id: int,
        division_id: int,
        season_id: int,
        uncommitted_only: bool = False,
    ) -> dict:
        """Judge the removal of a driver's assignment from one division on *db*, writing nothing:
        what `unassign_on` would refuse, in its words, and what it would then remove.

        Raises Refused where the profile is not found, is not Assigned, holds no assignment
        in the division, or, with *uncommitted_only*, holds a confirmed one. Returns the
        profile's account (*user_id*), whether it is a test driver, and the assignment: its
        row (*assignment_id*), its seat (*seat_id*) and whether it was confirmed
        (*was_committed*).
        """
        cursor = await db.execute(
            "SELECT discord_user_id, current_state, is_test_driver FROM driver_profiles "
            "WHERE id = ?",
            (driver_profile_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise Refused("Driver profile not found.")
        current_state = DriverState(row["current_state"])
        if current_state != DriverState.ASSIGNED:
            raise Refused(
                f"Driver must be in Assigned state to be unassigned "
                f"(current state: {current_state.value})."
            )

        cursor = await db.execute(
            "SELECT id, team_seat_id, committed FROM driver_season_assignments "
            "WHERE driver_profile_id = ? AND season_id = ? AND division_id = ?",
            (driver_profile_id, season_id, division_id),
        )
        asgn_row = await cursor.fetchone()
        if asgn_row is None:
            cursor = await db.execute("SELECT name FROM divisions WHERE id = ?", (division_id,))
            div_row = await cursor.fetchone()
            div_name = div_row["name"] if div_row else str(division_id)
            raise Refused(f"Driver is not assigned to any team in **{div_name}**.")
        was_committed = bool(asgn_row["committed"])
        if uncommitted_only and was_committed:
            raise Refused(
                "That placement has been confirmed. Move the driver with `/driver move`, "
                "or release them from the division with `/driver release`."
            )
        return {
            "user_id": int(row["discord_user_id"]),
            "is_test_driver": bool(row["is_test_driver"]),
            "assignment_id": asgn_row["id"],
            "seat_id": asgn_row["team_seat_id"],
            "was_committed": was_committed,
        }

    async def unassign_on(
        self,
        db,
        *,
        driver_profile_id: int,
        division_id: int,
        season_id: int,
        uncommitted_only: bool = False,
    ) -> dict:
        """Remove a driver's assignment from one division on *db*, leaving the audit entry to the
        caller.

        Everything the removal writes but the audit entry, written on the connection
        handed and committed by no one here, so that a change on the queue writes the entry with
        the queue's clock (*audit*). It awaits nothing but *db*: no member is fetched and no role
        asked of Discord. Roles and the lineup are owed only where the placement was committed,
        and a test driver holds no roles. Refused as `judge_unassign_on` refuses.

        Returns the summary (division_name, has_remaining_assignments, team_name, lineups), the
        audit entry's *audit* (its old and new values and the division), the driver's account
        *user_id*, and *revoke*, the role ids to take back, empty where none is owed.
        """
        judged = await self.judge_unassign_on(
            db, driver_profile_id=driver_profile_id, division_id=division_id,
            season_id=season_id, uncommitted_only=uncommitted_only,
        )
        asgn_id = judged["assignment_id"]
        seat_id = judged["seat_id"]
        was_committed = judged["was_committed"]

        # The team's shorthand (for its role) and full name (for what a reply shows).
        team_name: str | None = None
        team_full_name: str | None = None
        if seat_id is not None:
            cursor = await db.execute(
                "SELECT ti.name, ti.full_name FROM team_instances ti "
                "JOIN team_seats ts ON ts.team_instance_id = ti.id "
                "WHERE ts.id = ?",
                (seat_id,),
            )
            team_row = await cursor.fetchone()
            team_name = team_row["name"] if team_row else None
            team_full_name = team_row["full_name"] if team_row else None

        cursor = await db.execute(
            "SELECT name, mention_role_id FROM divisions WHERE id = ?", (division_id,)
        )
        div_info = await sole_row(cursor)
        div_name = div_info["name"]
        div_role_id = div_info["mention_role_id"]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_season_assignments "
            "WHERE driver_profile_id = ? AND season_id = ? AND division_id != ?",
            (driver_profile_id, season_id, division_id),
        )
        has_remaining = (await sole_row(cursor))[0] > 0

        # The team's role goes only where the driver holds no other seat in a team mapped to it.
        team_role_id_to_revoke: int | None = None
        if team_name is not None:
            team_role_id = await self._roles.team_role_on(db, team_name)
            if team_role_id is not None:
                cursor = await db.execute(
                    """
                    SELECT COUNT(*) FROM driver_season_assignments dsa
                    JOIN team_seats ts ON ts.id = dsa.team_seat_id
                    JOIN team_instances ti ON ti.id = ts.team_instance_id
                    JOIN team_role_configs trc
                        ON trc.team_name = ti.name
                    WHERE dsa.driver_profile_id = ?
                      AND dsa.season_id = ?
                      AND dsa.division_id != ?
                      AND trc.role_id = ?
                    """,
                    (driver_profile_id, season_id, division_id, team_role_id),
                )
                if (await sole_row(cursor))[0] == 0:
                    team_role_id_to_revoke = team_role_id

        # Atomically: free the seat, delete the assignment, update the state if needed.
        if seat_id is not None:
            await db.execute(
                "UPDATE team_seats SET driver_profile_id = NULL WHERE id = ?", (seat_id,)
            )
        await db.execute("DELETE FROM driver_season_assignments WHERE id = ?", (asgn_id,))
        if not has_remaining:
            await write_transition(
                db, driver_profile_id, DriverState.ASSIGNED, DriverState.UNASSIGNED
            )

        revoke: list[int] = []
        if not judged["is_test_driver"] and was_committed:
            revoke = [div_role_id]
            if team_role_id_to_revoke is not None:
                revoke.append(team_role_id_to_revoke)
        return {
            "division_name": div_name,
            "has_remaining_assignments": has_remaining,
            # The name a reply shows is the full one; the shorthand stays a key.
            "team_name": team_full_name,
            # The lineup to post, for the caller to post: none while the placement was
            # uncommitted.
            "lineups": [division_id] if was_committed else [],
            "audit": {
                "old_value": {"team": team_name, "seat_id": seat_id},
                "new_value": {
                    "new_state": (
                        DriverState.ASSIGNED if has_remaining else DriverState.UNASSIGNED
                    ).value,
                    "has_remaining": has_remaining,
                },
                "division_id": division_id,
            },
            "user_id": judged["user_id"],
            "revoke": revoke,
        }

    # ------------------------------------------------------------------
    # Revoke all placement roles
    # ------------------------------------------------------------------

    async def revoke_all_placement_roles(
        self,
        driver_profile_id: int,
        season_id: int | None,
        member: discord.Member,
    ) -> None:
        """Revoke all division and team roles for a driver across all active assignments.

        Reusable by future ban management commands (FR-029).
        """
        if season_id is None:
            return

        async with get_connection(self._db_path) as db:
            all_role_ids = await self._placement_role_ids_on(db, driver_profile_id, season_id)
        if all_role_ids:
            await self._roles._revoke_roles(member, *all_role_ids)

    @staticmethod
    async def _placement_role_ids_on(db, driver_profile_id: int, season_id: int) -> set[int]:
        """The division and team roles of the driver's placements in the season, read on *db*."""
        cursor = await db.execute(
            """
            SELECT DISTINCT d.mention_role_id
            FROM driver_season_assignments dsa
            JOIN divisions d ON d.id = dsa.division_id
            WHERE dsa.driver_profile_id = ? AND dsa.season_id = ?
            """,
            (driver_profile_id, season_id),
        )
        div_role_rows = await cursor.fetchall()
        # Team role IDs (via team_role_configs keyed on team name)
        cursor = await db.execute(
            """
            SELECT DISTINCT trc.role_id
            FROM driver_season_assignments dsa
            JOIN team_seats ts ON ts.id = dsa.team_seat_id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN team_role_configs trc
                ON trc.team_name = ti.name
            WHERE dsa.driver_profile_id = ? AND dsa.season_id = ?
            """,
            (driver_profile_id, season_id),
        )
        team_role_rows = await cursor.fetchall()
        return {r["mention_role_id"] for r in div_role_rows} | {
            r["role_id"] for r in team_role_rows
        }

    # ------------------------------------------------------------------
    # A driver's roles follow their current account
    # ------------------------------------------------------------------

    async def driver_role_ids(self, driver_profile_id: int) -> set[int]:
        """`driver_role_ids_on` on a connection of its own."""
        async with get_connection(self._db_path) as db:
            return await self.driver_role_ids_on(db, driver_profile_id)

    async def driver_role_ids_on(
        self, db: aiosqlite.Connection, driver_profile_id: int
    ) -> set[int]:
        """The roles the driver's standing entitles them to, read from state, not from Discord.

        The driver role while they are Unassigned or Assigned, and the division and team
        roles of every confirmed placement in the live season — exactly what approval and
        confirming placements grant. A test-mode driver holds no roles. Read from the league's
        own record so that it answers even when the account holding the roles has left.
        """
        cursor = await db.execute(
            "SELECT current_state, is_test_driver FROM driver_profiles WHERE id = ?",
            (driver_profile_id,),
        )
        profile = await cursor.fetchone()
        if profile is None or profile["is_test_driver"]:
            return set()
        roles: set[int] = set()
        if profile["current_state"] in (
            DriverState.UNASSIGNED.value, DriverState.ASSIGNED.value
        ):
            cursor = await db.execute("SELECT driver_role_id FROM server_configs")
            row = await cursor.fetchone()
            if row is not None and row["driver_role_id"]:
                roles.add(int(row["driver_role_id"]))
        cursor = await db.execute(
            """
            SELECT d.mention_role_id AS division_role, trc.role_id AS team_role
            FROM driver_season_assignments dsa
            JOIN seasons s ON s.id = dsa.season_id
            JOIN divisions d ON d.id = dsa.division_id
            LEFT JOIN team_seats ts ON ts.id = dsa.team_seat_id
            LEFT JOIN team_instances ti ON ti.id = ts.team_instance_id
            LEFT JOIN team_role_configs trc
                ON trc.team_name = ti.name
            WHERE dsa.driver_profile_id = ? AND dsa.committed = 1 AND s.status IN ('SETUP', 'ACTIVE')
            """,
            (driver_profile_id,),
        )
        for row in await cursor.fetchall():
            for role_id in (row["division_role"], row["team_role"]):
                if role_id:
                    roles.add(int(role_id))
        return roles

    # ------------------------------------------------------------------
    # Sack driver
    # ------------------------------------------------------------------

    async def judge_sack_on(self, db, *, driver_profile_id: int, season_id: int) -> dict:
        """Judge a sack of a driver on *db*, writing nothing: what `sack_seats_on` would refuse,
        in its words, and what it would then sack by.

        Raises Refused where the profile is not found, is neither Unassigned nor Assigned, or
        holds no confirmed placement in the season. Returns the profile's account (*user_id*), its
        state, whether it is a former driver and a test driver, and the divisions it is placed in
        (*division_ids*).
        """
        cursor = await db.execute(
            "SELECT discord_user_id, current_state, former_driver, is_test_driver "
            "FROM driver_profiles WHERE id = ?",
            (driver_profile_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise Refused("Driver profile not found.")
        current_state = DriverState(row["current_state"])
        if current_state not in (DriverState.UNASSIGNED, DriverState.ASSIGNED):
            raise Refused(
                f"Driver must be Unassigned or Assigned to be sacked "
                f"(current state: {current_state.value})."
            )

        cursor = await db.execute(
            "SELECT division_id, committed FROM driver_season_assignments "
            "WHERE driver_profile_id = ? AND season_id = ?",
            (driver_profile_id, season_id),
        )
        asgn_rows = await cursor.fetchall()
        if not any(r["committed"] for r in asgn_rows):
            raise Refused(
                "Only a driver whose placement is confirmed can be sacked. Remove an "
                "unconfirmed placement with `/driver unassign`, and turn down an "
                "Unassigned driver with `/driver reject`."
            )
        return {
            "user_id": int(row["discord_user_id"]),
            "state": current_state,
            "former_driver": bool(row["former_driver"]),
            "is_test_driver": bool(row["is_test_driver"]),
            "division_ids": [r["division_id"] for r in asgn_rows],
        }

    async def sack_seats_on(
        self, db, *, driver_profile_id: int, season_id: int
    ) -> SackSaved:
        """Sack a committed driver on *db*: free every seat, return to Not Signed Up, leaving the
        audit entry to the caller.

        Everything `sack_on` writes but the audit entry, written on the connection handed and
        committed by no one here, so that a change on the queue writes the entry with the queue's
        clock (*audit* of the result). It awaits nothing but *db*: no member is fetched and no
        role asked of Discord. The roles to take back are read **before** the placements go,
        since the placements are what they are computed from, and handed back in the result for a
        job of its own to take.

        Refused as `judge_sack_on` refuses.
        """
        judged = await self.judge_sack_on(
            db, driver_profile_id=driver_profile_id, season_id=season_id
        )
        current_state = judged["state"]
        is_test_driver = judged["is_test_driver"]
        division_ids = judged["division_ids"]

        revoke: set[int] = set()
        if not is_test_driver:
            revoke = await self._placement_role_ids_on(db, driver_profile_id, season_id)
            # The driver role granted at approval is not a placement role.
            cur = await db.execute("SELECT driver_role_id FROM server_configs")
            cfg_row = await cur.fetchone()
            if cfg_row and cfg_row["driver_role_id"]:
                revoke.add(int(cfg_row["driver_role_id"]))

        # The seats of this season alone: a former driver's seats in a completed season
        # are the archive, which a sack never changes.
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = NULL "
            "WHERE driver_profile_id = ? AND team_instance_id IN ("
            "    SELECT ti.id FROM team_instances ti "
            "    JOIN divisions d ON d.id = ti.division_id WHERE d.season_id = ?)",
            (driver_profile_id, season_id),
        )
        await db.execute(
            "DELETE FROM driver_season_assignments "
            "WHERE driver_profile_id = ? AND season_id = ?",
            (driver_profile_id, season_id),
        )
        await write_transition(db, driver_profile_id, current_state, DriverState.NOT_SIGNED_UP)
        return SackSaved(
            divisions=list(division_ids),
            revoke_role_ids=sorted(revoke),
            user_id=judged["user_id"],
            is_test_driver=is_test_driver,
            audit={
                "old_value": {"state": current_state.value, "divisions": division_ids},
                "new_value": {
                    "new_state": DriverState.NOT_SIGNED_UP.value,
                    "former_driver": judged["former_driver"],
                },
            },
        )

    async def sack_on(
        self,
        db,
        *,
        driver_profile_id: int,
        season_id: int,
        acting_user_id: int,
        acting_user_name: str,
    ) -> SackSaved:
        """Sack a committed driver on *db*: free every seat, return to Not Signed Up, audit it.

        `sack_seats_on`, and the `DRIVER_SACK` audit entry written with it on the same
        connection, stamped with the wall clock and committed by no one here, so that a
        sanction's save carries the sack with the rest of its writes. Refused and
        returned as `sack_seats_on` refuses and returns.
        """
        saved = await self.sack_seats_on(
            db, driver_profile_id=driver_profile_id, season_id=season_id
        )
        await db.execute(
            "INSERT INTO audit_entries "
            "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
            "VALUES (?, ?, NULL, 'DRIVER_SACK', ?, ?, ?)",
            (
                acting_user_id,
                acting_user_name,
                json.dumps(saved.audit["old_value"]),
                json.dumps(saved.audit["new_value"]),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return saved

    async def sack_driver(
        self,
        driver_profile_id: int,
        season_id: int,
        acting_user_id: int,
        acting_user_name: str,
        guild: discord.Guild,
        discord_user_id: str,
    ) -> list[int]:
        """Sack a committed driver: revoke every role, free every seat, return to Not Signed Up.

        Returns the divisions the driver left, whose lineups the caller asks the change queue
        to post; the sack posts none.

        Sacking is for a driver whose placement is confirmed — an uncommitted one is unassigned or
        rejected instead — and is available only while the season is ongoing, which the command
        checks. The profile is **never deleted here**: a driver without the former-driver flag who
        reaches Not Signed Up is pending deletion, and is deleted by the driver pass that ends the
        season. Their attendance, results and standings rows are therefore kept, and so is every
        signup they made.

        The season's placements are removed (`sack_on`), so the driver holds no seat and gains
        no history entry for the season. The roles are revoked first, swallowing Discord's
        refusals: this is the committing form `/attendance sync` sacks a driver through, with no
        queue to report to. `/driver sack` is a change on the queue (`driver_changes`), which
        takes the roles after the save, and a sanction's own role change raises. Raises
        Refused as `judge_sack_on` does.
        """
        async with get_connection(self._db_path) as db:
            judged = await self.judge_sack_on(
                db, driver_profile_id=driver_profile_id, season_id=season_id,
            )
        is_test_driver: bool = judged["is_test_driver"]

        # Revoke all roles before DB mutation: the placements are what they are computed from.
        member = guild.get_member(int(discord_user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(discord_user_id))
            except discord.HTTPException:
                member = None

        if member is not None and not is_test_driver:
            await self.revoke_all_placement_roles(driver_profile_id, season_id, member)
            # Revoke the driver role granted at approval
            async with get_connection(self._db_path) as db:
                cur = await db.execute("SELECT driver_role_id FROM server_configs")
                cfg_row = await cur.fetchone()
            if cfg_row and cfg_row["driver_role_id"]:
                driver_role = guild.get_role(cfg_row["driver_role_id"])
                if driver_role is not None and driver_role in member.roles:
                    await self._roles._revoke_roles(member, driver_role.id)

        async with get_connection(self._db_path) as db:
            saved = await self.sack_on(
                db, driver_profile_id=driver_profile_id, season_id=season_id,
                acting_user_id=acting_user_id, acting_user_name=acting_user_name,
            )
            await db.commit()

        return saved.divisions

    # ------------------------------------------------------------------
    # Division resolution helper (used by cogs)
    # ------------------------------------------------------------------

    async def refresh_lineup(
        self, guild: discord.Guild, division_id: int, *, as_text: bool = False
    ) -> LineupPosted | None:
        """Post the division's lineup afresh, **raising** where it could not be posted.

        What the post job of a change on the change queue calls: a lineup channel the division was
        given and the guild no longer holds, or a send Discord refuses, raises
        `StepFailedOnDiscord`, for the queue to stop on and try again; so does a division never
        given a lineup channel, the message naming `/division lineup-channel`. A division no longer
        there posts nothing and gives None. The post is all it does: the job's `record` saves the
        new id and a job after it deletes the message replaced.

        *as_text* skips the picture and posts the textual lineup: a job tried again goes as text
        (Constitution XIV, rule 8).
        """
        return await self._refresh_lineup_post(
            guild, division_id, as_text=as_text, require_channel=True
        )

    @posts_in_a_job
    async def _refresh_lineup_post(
        self,
        guild: discord.Guild,
        division_id: int,
        *,
        as_text: bool = False,
        require_channel: bool = False,
    ) -> LineupPosted | None:
        """Post the division's lineup: as a graphic where the drawing draws it, else as the
        embed.

        *as_text* skips asking the drawing altogether, whatever it would answer (Constitution
        XIV, rule 8: a job tried again goes as text). *require_channel* raises for a division
        never given a lineup channel, as the post job of a change must; without it such a
        division posts nothing, since configuring one is optional.

        **The picture is asked of the drawing in front of an untouched body.** Where the drawing
        the builder hands this service (core's drawing protocol) answers that the league
        draws its lineup, it draws the division's picture and this method posts it through the
        standing-post handler with no mentions, then releases the file. A picture that cannot be
        drawn — the drawing raises, or hands back a problem — falls through to the text embed
        below, carrying what went wrong for the job's own line. Where the league does not draw
        its lineup at all, the text embed is the lineup.

        Either way the lineup is posted before the message it replaces is deleted, by a job after
        it (Constitution XIV rule 8; image spec :706): this method deletes nothing and saves
        nothing, and a refusal raises `StepFailedOnDiscord` for the queue to stop on. A picture
        Discord refuses stands aside, so the text is posted in the same try; only a refused text
        post stops the lane.
        """
        async with get_connection(self._db_path) as db:
            cur = await db.execute(
                "SELECT d.name AS div_name, d.lineup_channel_id, d.lineup_message_id "
                "FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
            div_row = await cur.fetchone()

        if div_row is None:
            return None
        if div_row["lineup_channel_id"] is None:
            if not require_channel:
                return None
            raise StepFailedOnDiscord(
                f"{div_row['div_name'] or division_id} has no lineup channel set: set one with "
                "/division lineup-channel, then press Retry"
            )

        lineup_channel_id: int = div_row["lineup_channel_id"]
        lineup_message_id: int | None = div_row["lineup_message_id"]
        div_name: str = div_row["div_name"] or str(division_id)

        # Resolve the channel. Needed for both forms, read once.
        channel = as_text_channel(guild.get_channel(lineup_channel_id))
        if channel is None:
            try:
                channel = as_text_channel(await guild.fetch_channel(lineup_channel_id))
            except (discord.NotFound, discord.HTTPException) as exc:
                log.error(
                    "_refresh_lineup_post: lineup channel %s not found for division %s",
                    lineup_channel_id, division_id,
                )
                raise StepFailedOnDiscord(
                    f"the lineup channel (id {lineup_channel_id}) is not in the server"
                ) from exc
        if not isinstance(channel, discord.TextChannel):
            raise StepFailedOnDiscord(
                f"the lineup channel (id {lineup_channel_id}) is not a text channel"
            )

        image_path: Path | None = None
        problem: str | None = None
        drawing = self._drawing
        # Asked before the drawing's catch-all: the gate stands aside from its own faults, and a
        # switch nobody signed up for is a fault in the bot's assembly, not a picture not drawn.
        if not as_text and drawing is not None and await drawing.lineup_wanted():
            try:
                picture = await drawing.draw_lineup(guild, division_id)
                if picture.problem is not None:
                    problem = picture.problem
                elif picture.path is not None:
                    image_path = picture.path
            except Exception as exc:  # noqa: BLE001 — never block a placement on this
                log.error("_refresh_lineup_post: image path failed: %s", exc, exc_info=True)
                problem = str(exc)

        # The drawn path is released whichever way this ends — posted, refused and fallen
        # back to text, or lost to a Discord fault that stops the queue at the posting — so it
        # is kept apart from *send_as_image*, which only says whether the picture is still the
        # one to try sending.
        drawn_path = image_path
        send_as_image = image_path is not None

        try:
            if send_as_image:
                assert image_path is not None  # true by construction: send_as_image says so
                attachment = discord.File(str(image_path), filename=image_path.name)
                try:
                    new_msg_id = await post_standing(
                        channel, mentions=discord.AllowedMentions.none(), file=attachment
                    )
                except discord.HTTPException:
                    # Discord refused the picture: fall to the text in the same try.
                    send_as_image = False
                else:
                    return LineupPosted(
                        new_msg_id, lineup_channel_id, lineup_message_id, div_name, "image",
                    )
                finally:
                    attachment.close()

            embed = self._lineup_embed(div_name, await self._lineup_rows(division_id))
            try:
                new_msg_id = await post_standing(
                    channel, mentions=discord.AllowedMentions.none(), embed=embed
                )
            except discord.HTTPException as exc:
                log.error("_refresh_lineup_post: failed to post embed: %s", exc)
                raise StepFailedOnDiscord(f"the lineup could not be posted: {exc}") from exc

            return LineupPosted(
                new_msg_id, lineup_channel_id, lineup_message_id, div_name, "text",
                problem=problem,
            )
        finally:
            if drawing is not None:
                drawing.discard_picture(drawn_path)

    async def _lineup_rows(self, division_id: int) -> list[aiosqlite.Row]:
        """The division's committed, assigned seats, for the textual lineup."""
        async with get_connection(self._db_path) as db:
            cur = await db.execute(
                """
                SELECT ti.full_name AS team_name, ti.is_reserve,
                       dp.discord_user_id,
                       dp.is_test_driver, dp.test_display_name
                FROM driver_season_assignments dsa
                JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
                JOIN team_seats ts ON ts.id = dsa.team_seat_id
                JOIN team_instances ti ON ti.id = ts.team_instance_id
                JOIN seasons s ON s.id = dsa.season_id
                WHERE dsa.division_id = ? AND dp.current_state = 'ASSIGNED'
                  -- A placement not yet confirmed mid-season is not posted.
                  AND (dsa.committed = 1 OR s.status != 'ACTIVE')
                ORDER BY ti.is_reserve ASC, ti.full_name ASC
                """,
                (division_id,),
            )
            return list(await cur.fetchall())

    @staticmethod
    def _lineup_embed(div_name: str, assign_rows: Any) -> discord.Embed:
        """The textual lineup embed: regular teams, then reserves after a separator."""
        regular: dict[str, list[str]] = {}
        reserve: dict[str, list[str]] = {}
        for row in assign_rows:
            uid = int(row["discord_user_id"])
            mention = f"<@{uid}>"
            if row["is_test_driver"] and row["test_display_name"]:
                mention = f"<@{uid}> ({row['test_display_name']})"
            target = reserve if row["is_reserve"] else regular
            target.setdefault(row["team_name"], []).append(mention)

        parts: list[str] = [
            f"**{t}**: {', '.join(labels)}" for t, labels in regular.items()
        ]
        if reserve:
            if parts:
                parts.append("---")
            parts.extend(
                f"**{t}**: {', '.join(labels)}" for t, labels in reserve.items()
            )

        description = "\n".join(parts) if parts else "*(no drivers assigned)*"
        return discord.Embed(
            title=f"\U0001f4cb {div_name} Lineup",
            description=description,
            color=discord.Color.blurple(),
        )

    async def resolve_division(
        self, season_id: int, division_input: str
    ) -> tuple[int, str] | None:
        """Resolve a division by tier number or name. Returns (division_id, name) or None."""
        async with get_connection(self._db_path) as db:
            # Try as integer tier first
            try:
                tier = int(division_input)
                cursor = await db.execute(
                    "SELECT id, name FROM divisions WHERE season_id = ? AND tier = ?",
                    (season_id, tier),
                )
            except ValueError:
                cursor = await db.execute(
                    "SELECT id, name FROM divisions WHERE season_id = ? AND name = ?",
                    (season_id, division_input),
                )
            row = await cursor.fetchone()
        if row is None:
            return None
        return row["id"], row["name"]
