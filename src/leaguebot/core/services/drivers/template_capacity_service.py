"""TemplateCapacityService — whether the image templates could draw a placement's driver (036 /
Constitution XIV.12).

The four capacity guards a placement is held to, and the test roster's: the reserve block, the attendance sheet's rows, the driver standings' rows and
whatever an image type declares in code. Each counts the seats the change would fill, reads that
count from the database, and asks the image module's drawing, handed by
the builder (`image_drawing.ImageDrawing`), whether it could draw that many. It holds no template,
posts nothing, and is handed no bot: core reaches the image module through the drawing alone.

**Which connection it reads on.** A placement's guard runs in a change's check and reads on a
connection of its own. The test roster's is in two halves, because the roster change judges inside
its save and a save waits on nothing but its own connection: the seats are counted on a handed
connection (`roster_questions_on`, awaiting that connection alone), and the drawing is asked
before the save opens, in the change's check (`hold_roster`), whose answers the judge then reads
back (`RosterCapacity`). The save judges once more with no drawing asked: the drawing reads other
connections and parses every template, which takes seconds on the Raspberry Pi, and the lane runs
one change at a time, so what the check was told still holds as the save begins.

**The guards' one catch.** Reading the seats and asking the drawing are one guard, not two
(`_ask_drawing`). **The guards that run by caller:** the placement service asks `guard_image_capacity`
of an assignment and a move (`guard_assign`, `guard_move`), the test roster's check asks
`hold_roster`, and the server's test-mode flag, a precondition of an assignment alone,
stays the placement service's.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.refusal import Refused
from leaguebot.core.services.modules.image_drawing import CapacityProblem, ImageDrawing
from leaguebot.core.services.modules.module_service import SwitchNotSignedUp

log = logging.getLogger(__name__)

#: How each guard puts its question to the drawing.
_QUESTIONS: dict[str, Callable[[ImageDrawing, int], Awaitable[CapacityProblem | None]]] = {
    "reserve": lambda drawing, seated: drawing.reserve_capacity_problem(seated),
    "sheet": lambda drawing, seated: drawing.sheet_capacity_problem(seated),
    "standings": lambda drawing, seated: drawing.standings_capacity_problem(seated),
    "declared": lambda drawing, seated: drawing.declared_capacity_problem(seated),
}


def _refusal(guard: str, problem: CapacityProblem) -> str:
    """The words a guard refuses in: the image module's problem and remedy, and core's own say
    that the driver was not assigned, which the declared capacity's refusal leaves out."""
    if guard == "declared":
        return f"{problem.problem}. {problem.remedy}"
    return f"{problem.problem}. The driver was **not** assigned. {problem.remedy}"


def _counted(seated: int) -> Callable[[], Awaitable[int | None]]:
    """A count already taken, in the shape `_ask_drawing` reads one."""

    async def count() -> int | None:
        return seated

    return count


@dataclass(frozen=True)
class CapacityQuestion:
    """What a test roster would ask the drawing: whether *seated* drivers fit the template *guard*
    names (`reserve`, `sheet` or `standings`)."""

    guard: str
    seated: int


class RosterCapacity:
    """The test roster's capacity guard as the roster change's judge reads it, in the two passes
    the change's check makes (`TemplateCapacityService.hold_roster`).

    The first pass is handed one with no verdicts: the judge counts the seats it would seat on its
    own connection (`refusal_on`) and the questions are recorded, nothing refused. The drawing is
    asked about them, and the second pass is handed the answers: the same judge, the same counts,
    refusing where the drawing said an overflow. A judge handed none holds the roster to no
    capacity, as the save's does.
    """

    def __init__(
        self, service: TemplateCapacityService, verdicts: Mapping[CapacityQuestion, str] | None = None
    ) -> None:
        self._service = service
        self._verdicts = verdicts or {}
        #: What `refusal_on` was asked, in the order it was asked.
        self.questions: list[CapacityQuestion] = []

    async def refusal_on(
        self, db: aiosqlite.Connection, division_id: int, added: Mapping[str, int]
    ) -> str | None:
        """The refusal a real placement into the same teams would meet, as the drawing has said it,
        or None. Counts the seats on *db*, the connection the judge reads on, and awaits nothing
        else.

        *added* maps each team of *division_id* to the number of drivers the roster seats in it. A
        team the division does not hold is skipped: the roster service refuses it in its own words.
        """
        questions = await self._service.roster_questions_on(db, division_id, added)
        self.questions.extend(questions)
        for question in questions:
            refusal = self._verdicts.get(question)
            if refusal is not None:
                return refusal
        return None


async def _is_reserve_team_on(
    db: aiosqlite.Connection, division_id: int, team_name: str
) -> bool | None:
    """Whether *team_name* is the division's reserve team, or None where there is no such team.

    The capacity guards below measure two different populations — the reserve block and
    the classification — and a driver joins exactly one of them. Neither guard can tell
    which without this.
    """
    row = await (
        await db.execute(
            "SELECT is_reserve FROM team_instances WHERE division_id = ? AND name = ?",
            (division_id, team_name),
        )
    ).fetchone()
    return None if row is None else bool(row["is_reserve"])


async def _reserves_on(db: aiosqlite.Connection, division_id: int) -> int:
    """How many reserves the division seats."""
    row = await (
        await db.execute(
            "SELECT COUNT(*) AS seated FROM driver_season_assignments dsa "
            "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE dsa.division_id = ? AND ti.is_reserve = 1",
            (division_id,),
        )
    ).fetchone()
    return (row["seated"] if row else 0) or 0


async def _drivers_on(db: aiosqlite.Connection, division_id: int) -> int:
    """How many drivers of either kind the division seats."""
    row = await (
        await db.execute(
            "SELECT COUNT(*) AS seated FROM driver_season_assignments WHERE division_id = ?",
            (division_id,),
        )
    ).fetchone()
    return (row["seated"] if row else 0) or 0


async def _classified_on(db: aiosqlite.Connection, division_id: int) -> int:
    """How many classified drivers, reserves left out, the division seats."""
    row = await (
        await db.execute(
            "SELECT COUNT(*) AS seated FROM driver_season_assignments dsa "
            "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE dsa.division_id = ? AND ti.is_reserve = 0",
            (division_id,),
        )
    ).fetchone()
    return (row["seated"] if row else 0) or 0


class TemplateCapacityService:
    """See the module docstring. Built by the builder over the bot's database and the image
    module's drawing."""

    def __init__(self, db_path: str, *, drawing: ImageDrawing | None = None) -> None:
        self._db_path = db_path
        #: The image module's answers to core's drawing protocol (`image_drawing.ImageDrawing`),
        #: handed in by the builder. None in every unit test that exercises a placement alone:
        #: no guard asks anything, and a test roster of any size is accepted.
        self._drawing = drawing

    async def _ask_drawing(
        self, guard: str, count: Callable[[], Awaitable[int | None]]
    ) -> CapacityProblem | None:
        """Read the guard's own count with *count*, ask the image module's handed drawing
        about it, and hand back what it says, or None where nothing is handed, the count
        reports the guard does not apply (None), or either step could not run. *guard* names
        the guard in the question (`_QUESTIONS`) and in the host's log.

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
            return await _QUESTIONS[guard](self._drawing, seated)
        except SwitchNotSignedUp:
            raise  # a module that never signed up is a fault in the bot's assembly, not a reading
        except Exception as exc:  # noqa: BLE001 — a stated exception, see the docstring
            log.error("%s capacity guard could not run: %s", guard, exc, exc_info=True)
            return None

    async def _guard(
        self, guard: str, count: Callable[[], Awaitable[int | None]]
    ) -> None:
        """Ask the drawing (`_ask_drawing`) and refuse in the guard's words where it reports an
        overflow."""
        problem = await self._ask_drawing(guard, count)
        if problem is not None:
            raise Refused(_refusal(guard, problem))

    async def _guard_reserve_capacity(self, division_id: int, team_name: str) -> None:
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

        Reads its own count and asks the image module's handed drawing (``_ask_drawing``)
        under the one catch: never raises for its own reasons, only a genuine over-capacity
        the drawing reports may. A test roster is asked the same of the reserve block by
        ``roster_questions_on``.
        """

        async def reserves_seated() -> int | None:
            # Not a reserve placement, or no such team — `judge_assign_on` reports a team
            # that does not exist in its own words, and this guard stays quiet.
            async with get_connection(self._db_path) as db:
                if not await _is_reserve_team_on(db, division_id, team_name):
                    return None
                return await _reserves_on(db, division_id) + 1

        await self._guard("reserve", reserves_seated)

    async def _guard_sheet_capacity(self, division_id: int) -> None:
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

        Reads its own count and asks the image module's handed drawing (``_ask_drawing``)
        under the one catch: never raises for its own reasons, only a genuine over-capacity
        the drawing reports may.
        """

        async def drivers_seated() -> int | None:
            async with get_connection(self._db_path) as db:
                return await _drivers_on(db, division_id) + 1

        await self._guard("sheet", drivers_seated)

    async def _guard_standings_capacity(self, division_id: int, team_name: str) -> None:
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

        Reads its own count and asks the image module's handed drawing (``_ask_drawing``)
        under the one catch: never raises for its own reasons, only a genuine over-capacity
        the drawing reports may.
        """

        async def classified_seated() -> int | None:
            # A reserve placement adds no entry to the classification, and no such team —
            # `judge_assign_on` reports a team that does not exist in its own words.
            async with get_connection(self._db_path) as db:
                if await _is_reserve_team_on(db, division_id, team_name) is not False:
                    return None
                return await _classified_on(db, division_id) + 1

        await self._guard("standings", classified_seated)

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

        async def division_seated() -> int | None:
            async with get_connection(self._db_path) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) AS seated FROM driver_season_assignments "
                    "WHERE season_id = ? AND division_id = ?",
                    (season_id, division_id),
                )
                row = await cursor.fetchone()
            return ((row["seated"] if row else 0) or 0) + 1

        await self._guard("declared", division_seated)

    async def roster_questions_on(
        self, db: aiosqlite.Connection, division_id: int, added: Mapping[str, int]
    ) -> list[CapacityQuestion]:
        """What a test roster would ask the drawing for the division, in the order a real
        placement's guards ask it: the reserve block, the attendance sheet, the driver standings.

        *added* maps each team of *division_id* to the number of drivers the change seats in it.
        Test mode seats its fake drivers without passing through ``guard_assign``, so without these
        questions a roster larger than the league's templates could hold would be accepted, and the
        rehearsal would pass a configuration a real season would refuse.

        **The count is per division, summed across its teams.** A bulk roster seats a whole
        division at once, and two teams that each fit on their own can together outgrow the
        standings. The three guards are asked once each, measuring the reserves, every driver, and
        the classified drivers respectively. A team the division does not hold is skipped: the
        roster service refuses it in its own words. The declared-capacity guard of
        ``guard_image_capacity`` is not asked: every catalogue declares ``capacity=None`` today, so
        it bounds no placement, real or test.

        Reads on *db* and awaits nothing else, being run inside a save as well as in a check, and
        commits nothing. A fault reading the seats goes up with the judge's own reads; it is not
        the guard's catch (``_ask_drawing``), which is the drawing's.
        """
        reserves = classified = 0
        for team_name, count in added.items():
            is_reserve = await _is_reserve_team_on(db, division_id, team_name)
            if is_reserve is None or count <= 0:
                continue
            if is_reserve:
                reserves += count
            else:
                classified += count

        questions: list[CapacityQuestion] = []
        if reserves:
            questions.append(
                CapacityQuestion("reserve", await _reserves_on(db, division_id) + reserves)
            )
        if reserves + classified:
            questions.append(
                CapacityQuestion("sheet", await _drivers_on(db, division_id) + reserves + classified)
            )
        if classified:
            questions.append(
                CapacityQuestion("standings", await _classified_on(db, division_id) + classified)
            )
        return questions

    async def hold_roster(
        self, dry_run: Callable[[RosterCapacity], Awaitable[object]]
    ) -> RosterCapacity:
        """The capacity guard for a test roster's judge, with the drawing asked, before any save
        is open.

        *dry_run* is the judge, run once on a connection of the check's own and handed a
        `RosterCapacity` that records what it counts and refuses nothing. The drawing is then
        asked each question recorded (``_ask_drawing``, under the one catch), and what is handed
        back refuses as a real placement into the same teams would be refused: the caller runs the
        judge again with it, and the judge reads the answers. A service handed no drawing asks nothing, so the
        judge is not run for it.
        """
        if self._drawing is None:
            return RosterCapacity(self)
        recording = RosterCapacity(self)
        await dry_run(recording)
        verdicts: dict[CapacityQuestion, str] = {}
        for question in dict.fromkeys(recording.questions):
            problem = await self._ask_drawing(question.guard, _counted(question.seated))
            if problem is not None:
                verdicts[question] = _refusal(question.guard, problem)
        return RosterCapacity(self, verdicts)
