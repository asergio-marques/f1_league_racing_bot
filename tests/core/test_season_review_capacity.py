"""The season review's capacity checks, asked of the image module through the protocol core
declares (#283).

`SeasonCog` measures no template. It counts what the season holds, from its own tables, and asks
the drawing on the bot whether the image module could draw that: each division's race teams and
the drivers seated in them for the lineup, and for the review's warnings the division holding the most rounds, the most
drivers and the most race teams, with the most sessions any round is run over. The image module
answers its own words: the lineup's problems as sentences, and every other answer as a
`CapacityProblem`. The rounds a division would hold, for `/round add` and the round imports, are
counted by their change type, which asks the drawing through
`leaguebot.core.services.round_add_changes.calendar_round_refusal`, at the ask and again as the
change starts (#283). Core keeps its own words around them: the round's refusal opens with ❌ and
says that the round was **not** added before the remedy, and each warning is a ⚠️ line, the
warnings of one check followed by a blank line. A check that cannot run, whether its counts or
the drawing fault, lets the season and the round through with the full error in the host's log,
and a fault anywhere in the attendance, standings and check-in warnings drops all three, as one
check.

Whether the images module, an output's switch and a valid template put a check in force, and the
measuring of the real templates, are the image module's, tested against its implementation under
`tests/image/`. Nothing here reads a template.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.season_service import SeasonService
from leaguebot.core.services.team_service import TeamService

SEASON_ID = 1
PROBLEM = "The stub template draws too few"
REMEDY = "Enlarge the stub template."
WARNING = f"  ⚠️ {PROBLEM}. {REMEDY}"


class _Drawing:
    """A stand-in for the image module's answers to the review's questions: *answers* maps a
    method to what it gives back, or to the error it raises; every other method answers that
    nothing is wrong. *asked* records each question and what it was handed."""

    def __init__(self, answers=None):
        self._answers = answers or {}
        self.asked: list[tuple] = []

    async def _answer(self, name: str, *handed, nothing=None):
        self.asked.append((name, *handed))
        answer = self._answers.get(name, nothing)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def lineup_review_problems(self, divisions):
        return await self._answer("lineup_review_problems", list(divisions), nothing=[])

    async def round_capacity_problem(self, rounds):
        return await self._answer("round_capacity_problem", rounds)

    async def calendar_review_problem(self, longest):
        return await self._answer("calendar_review_problem", longest)

    async def sheet_review_problem(self, longest):
        return await self._answer("sheet_review_problem", longest)

    async def standings_review_problems(self, drivers, teams, longest):
        return await self._answer(
            "standings_review_problems", drivers, teams, longest, nothing=[]
        )

    async def checkin_review_problem(self, sessions):
        return await self._answer("checkin_review_problem", sessions)


def _problem():
    from leaguebot.core.services.image_drawing import CapacityProblem

    return CapacityProblem(problem=PROBLEM, remedy=REMEDY)


async def _seed(tmp_path, divisions):
    """A season in setup holding *divisions*, each given in the order it is created as
    ``(name, tier, rounds, teams)``: *rounds* a list of round formats, *teams* a list of
    ``(team, seats, seated, is_reserve)``, *seated* the first seats of the team taken by a
    driver of the division."""
    db_path = str(tmp_path / "review_capacity.db")
    await run_migrations(db_path)
    user_id = 1000
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'SETUP')",
            (SEASON_ID,),
        )
        for name, tier, formats, teams in divisions:
            division_id = (
                await db.execute(
                    "INSERT INTO divisions (season_id, name, tier, mention_role_id) "
                    "VALUES (?, ?, ?, 555)",
                    (SEASON_ID, name, tier),
                )
            ).lastrowid
            for number, fmt in enumerate(formats, start=1):
                await db.execute(
                    "INSERT INTO rounds (division_id, round_number, scheduled_at, format) "
                    "VALUES (?, ?, '2026-02-01T18:00:00+00:00', ?)",
                    (division_id, number, fmt),
                )
            for team, seats, seated, is_reserve in teams:
                team_id = (
                    await db.execute(
                        "INSERT INTO team_instances (division_id, name, full_name, max_seats, "
                        "is_reserve) VALUES (?, ?, ?, ?, ?)",
                        (division_id, team, team, seats, int(is_reserve)),
                    )
                ).lastrowid
                for seat_number in range(1, seats + 1):
                    profile_id = None
                    if seat_number <= seated:
                        user_id += 1
                        profile_id = (
                            await db.execute(
                                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                                "VALUES (?, 'ASSIGNED')",
                                (str(user_id),),
                            )
                        ).lastrowid
                    seat_id = (
                        await db.execute(
                            "INSERT INTO team_seats (team_instance_id, seat_number, "
                            "driver_profile_id) VALUES (?, ?, ?)",
                            (team_id, seat_number, profile_id),
                        )
                    ).lastrowid
                    if profile_id is not None:
                        await db.execute(
                            "INSERT INTO driver_season_assignments (driver_profile_id, "
                            "season_id, division_id, team_seat_id) VALUES (?, ?, ?, ?)",
                            (profile_id, SEASON_ID, division_id, seat_id),
                        )
        await db.commit()
    return db_path


#: Two divisions, the second tier created first. Challenger holds the most rounds, one of them a
#: sprint; Elite seats the most drivers, its reserve among them, and fields the most race teams.
SEASON = [
    ("Challenger", 2, ["NORMAL"] * 4 + ["SPRINT"], [("Haas", 2, 1, False)]),
    (
        "Elite",
        1,
        ["NORMAL"] * 3,
        [
            ("Reserve", 1, 1, True),
            ("Red Bull", 2, 2, False),
            ("Ferrari", 3, 1, False),
        ],
    ),
]


def _round_refusal(cog, would_hold):
    """The round guard, asking the drawing on *cog*'s bot of *would_hold* rounds: the
    change type's own, which `/round add` and the round imports judge at the ask and again as the
    change starts."""
    from leaguebot.core.services.round_add_changes import calendar_round_refusal

    return calendar_round_refusal(cog.bot.image_drawing, would_hold)


def _cog(db_path, drawing):
    bot = MagicMock()
    bot.db_path = db_path
    bot.season_service = SeasonService(db_path)
    bot.team_service = TeamService(db_path)
    bot.image_drawing = drawing
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog


# ── The lineup (047 FR-022) ───────────────────────────────────────────────


async def test_the_lineup_check_asks_of_each_division_s_race_teams_in_tier_order(tmp_path):
    """Each division in tier order, its race teams in the order they were made and the drivers
    seated in each: an empty seat is not counted and the reserve team is not handed. What the
    drawing answers is the check's answer, unchanged."""
    from leaguebot.core.services.image_drawing import DivisionTeams, TeamSeats

    drawing = _Drawing({"lineup_review_problems": ["one problem", "another"]})
    cog = _cog(await _seed(tmp_path, SEASON), drawing)

    assert await cog._lineup_problems(SEASON_ID) == ["one problem", "another"]
    assert drawing.asked == [
        (
            "lineup_review_problems",
            [
                DivisionTeams("Elite", (TeamSeats("Red Bull", 2), TeamSeats("Ferrari", 1))),
                DivisionTeams("Challenger", (TeamSeats("Haas", 1),)),
            ],
        )
    ]


# ── The round a division would hold (XIV.12) ──────────────────────────────


@pytest.mark.parametrize("found,refusal", [
    pytest.param(
        True, f"❌ {PROBLEM}. The round was **not** added.\n{REMEDY}", id="past the calendar"
    ),
    pytest.param(False, None, id="within the calendar"),
])
async def test_a_round_past_the_calendar_is_refused_in_core_s_words(tmp_path, found, refusal):
    """`/round add` and the round imports ask, through their change type's guard, of the rounds
    the division would hold. Core says the round was **not** added; the problem and its remedy
    are the image module's."""
    drawing = _Drawing({"round_capacity_problem": _problem() if found else None})
    cog = _cog(await _seed(tmp_path, SEASON), drawing)

    assert await _round_refusal(cog, 25) == refusal
    assert drawing.asked == [("round_capacity_problem", 25)]


# ── The review's warnings (Constitution XIV.9) ────────────────────────────


@pytest.mark.parametrize("names,longest", [
    pytest.param(("Division 1", "Premier"), "Premier", id="longest sorts last"),
    pytest.param(("Short", "Long"), "Long", id="longest sorts first"),
])
async def test_the_calendar_and_the_sheet_ask_of_the_division_holding_the_most_rounds(
    tmp_path, names, longest
):
    """Which division is drawn is decided later, so the review asks the calendar and the
    attendance sheet of the longest calendar alone: the second division made, with twenty-five
    rounds against the first's ten, whether its name sorts after the other's or before it. Each
    answer is a warning, never a refusal."""
    from leaguebot.core.services.image_drawing import Held

    shorter, _ = names
    drawing = _Drawing({
        "calendar_review_problem": _problem(), "sheet_review_problem": _problem(),
    })
    cog = _cog(
        await _seed(tmp_path, [(shorter, 1, ["NORMAL"] * 10, []), (longest, 2, ["NORMAL"] * 25, [])]),
        drawing,
    )

    assert await cog._calendar_capacity_warning(SEASON_ID) == [WARNING, ""]
    assert await cog._attendance_capacity_warning(SEASON_ID) == [WARNING, ""]
    assert drawing.asked[:2] == [
        ("calendar_review_problem", Held(longest, 25)),
        ("sheet_review_problem", Held(longest, 25)),
    ]


async def test_the_attendance_warnings_ask_of_the_season_s_counts_in_review_order(tmp_path):
    """The attendance sheet of the division holding the most rounds; the two standings of the
    division seating the most drivers, the one fielding the most race teams and the one holding
    the most rounds; and the check-in call of a sprint round's four sessions. Every warning in
    the order asked, then one blank line."""
    from leaguebot.core.services.image_drawing import CapacityProblem, Held

    standings = [CapacityProblem("Drivers short", "Enlarge it."), CapacityProblem("Teams short", "Enlarge that.")]
    drawing = _Drawing({
        "sheet_review_problem": _problem(),
        "standings_review_problems": standings,
        "checkin_review_problem": CapacityProblem("Call short", "Enlarge the call."),
    })
    cog = _cog(await _seed(tmp_path, SEASON), drawing)

    assert await cog._attendance_capacity_warning(SEASON_ID) == [
        WARNING,
        "  ⚠️ Drivers short. Enlarge it.",
        "  ⚠️ Teams short. Enlarge that.",
        "  ⚠️ Call short. Enlarge the call.",
        "",
    ]
    assert drawing.asked == [
        ("sheet_review_problem", Held("Challenger", 5)),
        ("standings_review_problems", Held("Elite", 4), Held("Elite", 2), Held("Challenger", 5)),
        ("checkin_review_problem", 4),
    ]


async def test_a_season_with_no_divisions_is_asked_only_of_the_check_in_call(tmp_path):
    """No division holds a calendar, a sheet or standings, so nothing is asked of them; a round
    is run over two sessions at the least, so the check-in call is still asked of two."""
    drawing = _Drawing()
    cog = _cog(await _seed(tmp_path, []), drawing)

    assert await cog._calendar_capacity_warning(SEASON_ID) == []
    assert await cog._attendance_capacity_warning(SEASON_ID) == []
    assert drawing.asked == [("checkin_review_problem", 2)]


@pytest.mark.parametrize("formats", [
    pytest.param(["NORMAL", "ENDURANCE", "MYSTERY"], id="normal, endurance and mystery"),
    pytest.param(["MYSTERY"], id="mystery alone"),
])
async def test_a_season_with_no_sprint_asks_the_check_in_call_of_two_sessions(tmp_path, formats):
    """A sprint round is run over four sessions and every other format over two, a mystery
    round among them: a season holding rounds but no sprint asks the check-in call of two."""
    drawing = _Drawing()
    cog = _cog(await _seed(tmp_path, [("Pro", 1, formats, [])]), drawing)

    assert await cog._attendance_capacity_warning(SEASON_ID) == []
    assert drawing.asked[-1] == ("checkin_review_problem", 2)


async def test_a_season_the_templates_draw_earns_no_warning_and_no_blank_line(tmp_path):
    drawing = _Drawing()
    cog = _cog(await _seed(tmp_path, SEASON), drawing)

    assert await cog._calendar_capacity_warning(SEASON_ID) == []
    assert await cog._attendance_capacity_warning(SEASON_ID) == []
    assert [question[0] for question in drawing.asked] == [
        "calendar_review_problem",
        "sheet_review_problem",
        "standings_review_problems",
        "checkin_review_problem",
    ]


# ── A check that cannot run ───────────────────────────────────────────────


def _counts_fail(cog, monkeypatch):
    """The season's own counts cannot be read."""
    failing = AsyncMock(side_effect=RuntimeError("database down"))
    monkeypatch.setattr(cog.bot.season_service, "get_divisions", failing)
    monkeypatch.setattr(cog.bot.season_service, "capacity_counts", failing, raising=False)


#: Each check, the question it asks, how it is called, and what it answers where it cannot run.
CHECKS = [
    ("lineup", "lineup_review_problems", lambda cog: cog._lineup_problems(SEASON_ID), []),
    ("round", "round_capacity_problem", lambda cog: _round_refusal(cog, 25), None),
    ("calendar", "calendar_review_problem",
     lambda cog: cog._calendar_capacity_warning(SEASON_ID), []),
    ("check-in", "checkin_review_problem",
     lambda cog: cog._attendance_capacity_warning(SEASON_ID), []),
]

#: Each fault a check can meet: the drawing's, for every check, and the season's own counts, for
#: every check but the round's, whose count it is handed rather than reads.
FAULTS = [
    pytest.param(fault, *case[1:], id=f"{fault}-{case[0]}")
    for fault in ("drawing", "counts")
    for case in CHECKS
    if not (fault == "counts" and case[0] == "round")
]


@pytest.mark.parametrize("fault,question,check,nothing", FAULTS)
async def test_a_check_that_cannot_run_lets_the_season_through(
    tmp_path, monkeypatch, caplog, fault, question, check, nothing
):
    """A capacity check that cannot run must not block a season or a round: it answers that
    nothing is wrong, and the full error goes to the host's log. The attendance sheet answers a
    problem before the check-in call faults, and is dropped with it: the sheet, the standings
    and the check-in call are one check."""
    drawing = _Drawing({question: RuntimeError("drawing down"), "sheet_review_problem": _problem()})
    cog = _cog(await _seed(tmp_path, SEASON), drawing)
    if fault == "counts":
        _counts_fail(cog, monkeypatch)

    with caplog.at_level(logging.ERROR):
        assert await check(cog) == nothing

    assert "capacity check could not run" in caplog.text
    assert "down" in caplog.text
    assert bool(drawing.asked) is (fault == "drawing")
