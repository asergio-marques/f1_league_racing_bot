"""TemplateCapacityService — whether the image templates could draw a placement's driver (036 /
Constitution XIV.12).

The four capacity guards a placement is held to, and the test roster's: the reserve block, the attendance sheet's rows, the driver standings' rows and
whatever an image type declares in code. Each counts the seats the change would fill, reads that
count from the database on a connection of its own, and asks the image module's drawing, handed by
the builder (`image_drawing.ImageDrawing`), whether it could draw that many. It holds no template,
posts nothing, and is handed no bot: core reaches the image module through the drawing alone.

**The guards' one catch.** Reading the seats and asking the drawing are one guard, not two
(`_ask_drawing`). **The guards that run by caller:** the placement service asks `guard_image_capacity`
of an assignment and a move (`guard_assign`, `guard_move`), the test roster's writers ask
`guard_roster_capacity`, and the server's test-mode flag, a precondition of an assignment alone,
stays the placement service's.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.refusal import Refused
from leaguebot.core.services.image_drawing import CapacityProblem, ImageDrawing
from leaguebot.core.services.module_service import SwitchNotSignedUp

log = logging.getLogger(__name__)


class TemplateCapacityService:
    """See the module docstring. Built by the builder over the bot's database and the image
    module's drawing."""

    def __init__(self, db_path: str, *, drawing: ImageDrawing | None = None) -> None:
        self._db_path = db_path
        #: The image module's answers to core's drawing protocol (`image_drawing.ImageDrawing`),
        #: handed in by the builder. None in every unit test that exercises a placement alone:
        #: no guard asks anything, and a test roster of any size is accepted.
        self._drawing = drawing

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


    async def guard_image_capacity(
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

        The declared-capacity branch of ``guard_image_capacity`` is not run: every catalogue
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
