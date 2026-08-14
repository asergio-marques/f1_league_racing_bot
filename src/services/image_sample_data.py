"""Sample data for `/images test` (T064).

Reads nothing live (FR-036): no season, division, round, team or driver query. The test
command must work on a server with no season at all (SC-005).

The samples deliberately provoke each notice kind — a name long enough to trip an
`inline-size` bound, and prose long enough to reach the wrap floor — so the diagnostic
exercises the problem/notice distinction rather than assuming it.
"""
from __future__ import annotations

from utils.svg_document import FieldIndex
from utils.svg_fill import FillSpec

#: A Discord display name of the sort no league controls the length of. Any template
#: field carrying a driver name should be bounded, and this is what proves it.
LONG_DRIVER_NAME = "Bartholomew Fotheringay-Pemberton III"

#: Long enough to descend a wrapped field to its floor.
LONG_JUSTIFICATION = (
    "The stewards reviewed the incident at turn four involving car 44 and car 1. "
    "Having examined the available footage from both onboard cameras and the trackside "
    "feed, and having heard from both drivers and their representatives, the stewards "
    "determine that car 44 was predominantly at fault for the collision. The driver of "
    "car 44 attempted a move down the inside of turn four at a point where the corner "
    "was already occupied, and did not leave adequate racing room. "
) * 6

SAMPLE_DRIVERS = [
    ("Verstappen", "Apex Racing", "dutch"),
    ("Hamilton", "Meridian GP", "british"),
    (LONG_DRIVER_NAME, "Vanguard Racing", "british"),
    ("Leclerc", "Solstice Motorsport", "french"),
    ("Norris", "Ironclad Racing", "british"),
]

SAMPLE_ROUNDS = [
    ("Silverstone", "14 Jun 2026"),
    ("Monza", "28 Jun 2026"),
    ("Spa-Francorchamps", "12 Jul 2026"),
    ("Suzuka", "26 Jul 2026"),
    ("Monaco", "09 Aug 2026"),
]


#: Tracks the calendar sample draws on. The last has no image file of its own in any
#: shipped asset set, so the fallback is exercised and its notice can be read.
SAMPLE_CALENDAR_TRACKS = [
    ("Silverstone Circuit", "British Grand Prix", "United Kingdom"),
    ("Circuit Zandvoort", "Dutch Grand Prix", "Netherlands"),
    ("Circuit de Spa-Francorchamps", "Belgian Grand Prix", "Belgium"),
    ("Suzuka International Racing Course", "Japanese Grand Prix", "Japan"),
    ("Autódromo José Carlos Pace", "São Paulo Grand Prix", "Brazil"),
]

#: One of each, so a template author can read every shape on one image.
SAMPLE_CALENDAR_FORMATS = ["NORMAL", "SPRINT", "ENDURANCE", "MYSTERY"]


def build_calendar_drawing(root):
    """The fabricated division `/images test calendar` draws (FR-021).

    Holds **one round fewer** than the template declares, so the cut lands at a crop point
    that is *not* the last the template declares and can actually be judged. Where the
    template declares a single round, one is fabricated and the crop is evaluated at the
    declared height instead.

    Covers, as far as the round count allows: one round of each format including mystery;
    one whose track has no image file, exercising the fallback and its notice; and dates
    spanning more than one month. A round with **no time** is deliberately absent — a
    round records date and time as one moment by design, so the shape cannot be
    fabricated (see specs/037-calendar-image-generation/research.md § R5).
    """
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from models.image_catalogues import CapacityError, catalogue_for
    from services.image_calendar_service import CalendarDataError, resolve_drawing

    try:
        capacity = catalogue_for("calendar_template").capacity(root) or 0
    except CapacityError as exc:
        raise CalendarDataError(str(exc)) from exc

    count = max(1, capacity - 1) if capacity > 1 else 1

    start = datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc)
    rounds = []
    tracks = {}
    for index in range(1, count + 1):
        fmt = SAMPLE_CALENDAR_FORMATS[(index - 1) % len(SAMPLE_CALENDAR_FORMATS)]
        name, gp_name, country = SAMPLE_CALENDAR_TRACKS[
            (index - 1) % len(SAMPLE_CALENDAR_TRACKS)
        ]
        rounds.append(
            SimpleNamespace(
                round_number=index,
                format=fmt,
                track_name=None if fmt == "MYSTERY" else name,
                # Seven days apart, so any calendar of three rounds or more spans a month
                # boundary and the configured date format can be judged on real variety.
                scheduled_at=start + timedelta(days=7 * (index - 1)),
            )
        )
        tracks[name] = SimpleNamespace(name=name, gp_name=gp_name, country=country)

    return resolve_drawing(
        division_name="Test Division",
        division_tier=1,
        season_number=1,
        rounds=rounds,
        tracks=tracks,
    )


#: Nationalities the fabricated drivers carry. Every one is accepted by the signup wizard,
#: and the last is the value recorded for a driver who stated none — so the "Other" flag is
#: exercised as the ordinary datum it is rather than as an absence.
SAMPLE_LINEUP_NATIONALITIES = [
    "British",
    "Dutch",
    "Brazilian",
    "Japanese",
    "Other",
]

#: Fabricated driver ids. Well below the Discord snowflake epoch, so no portrait file can
#: resolve for them and the driver directory's `fallback.svg` is exercised with its notice
#: (wip-spec § "Lineup image generation" → Test data).
_SAMPLE_ID_BASE = 100_000


def build_lineup_drawing(root, teams):
    """The fabricated division `/images test lineup` draws (FR-029).

    *teams* is the **server's own team configuration** — records carrying ``name``,
    ``max_seats`` and ``is_reserve``. Reading the league's real teams rather than inventing
    some is what makes this command a genuine rehearsal: a lineup template is keyed to a
    league's own teams, so a preview drawn against invented ones would prove nothing.

    Every team but one is filled to its seat count and one is left wholly unoccupied, so
    unoccupied seats can be judged. Reserve drivers are fabricated to one fewer than the
    template's reserve slots, so an unfilled reserve slot can be judged too.
    """
    from types import SimpleNamespace

    from models.image_catalogues import CapacityError, catalogue_for
    from services.image_lineup_service import LineupDataError, resolve_drawing

    configurable = [t for t in teams if not getattr(t, "is_reserve", False)]
    if not configurable:
        raise LineupDataError(
            "the server holds no team beyond the reserve team, so there is no lineup to "
            "be drawn. Add a team with `/team add` first."
        )

    try:
        reserve_slots = catalogue_for("lineup_template").capacity(root) or 0
    except CapacityError as exc:
        raise LineupDataError(str(exc)) from exc

    counter = 0

    def _driver():
        nonlocal counter
        counter += 1
        return SimpleNamespace(
            discord_user_id=str(_SAMPLE_ID_BASE + counter),
            server_display_name=f"Test Driver {counter}",
            discord_username=f"testdriver{counter}",
            test_display_name=None,
            nationality=SAMPLE_LINEUP_NATIONALITIES[
                (counter - 1) % len(SAMPLE_LINEUP_NATIONALITIES)
            ],
        )

    def _seat(number: int, occupied: bool):
        base = SimpleNamespace(seat_number=number, discord_user_id=None)
        if not occupied:
            return base
        driver = _driver()
        return SimpleNamespace(seat_number=number, **vars(driver))

    fabricated = []
    # The last team is the one left empty, so a league reading the image finds the gap
    # where it expects it rather than at the top of the graphic.
    empty_index = len(configurable) - 1
    for index, record in enumerate(configurable):
        seats = int(getattr(record, "max_seats", 0) or 0)
        fabricated.append(
            SimpleNamespace(
                name=getattr(record, "name", ""),
                is_reserve=False,
                seats=[
                    _seat(number, occupied=index != empty_index)
                    for number in range(1, seats + 1)
                ],
            )
        )

    reserve_count = max(reserve_slots - 1, 0)
    fabricated.append(
        SimpleNamespace(
            name="Reserve",
            is_reserve=True,
            seats=[_seat(number, occupied=True) for number in range(1, reserve_count + 1)],
        )
    )

    return resolve_drawing(
        division_name="Test Division",
        division_tier=1,
        season_number=1,
        teams=fabricated,
        display_names={},
        nationality_collected=True,
    )


#: The track `/images test results` draws. Silverstone stands on every server's track list,
#: so this satisfies the wip-spec's "a track of the server's track list" without the command
#: reading anything live (FR-036).
SAMPLE_RESULTS_TRACK = "British Grand Prix"


def build_results_drawing(root, template_key: str, teams):
    """The fabricated session `/images test results` draws (wip-spec § "Test data").

    One entry fewer than the rows the template declares, so the rendering of an unused row
    can be judged; exactly one where the template declares a single row, and then no unused
    row is evaluated. Teams are the server's own configuration, so a league reads its own
    liveries rather than invented ones.

    The enumerated cases are assigned to entries **in order**, and any that the declared row
    count cannot reach are simply not drawn — which is what "insofar as the number of rows
    declared allows" means.
    """
    from models.image_catalogues import CapacityError, catalogue_for
    from models.points_config import SessionType
    from models.session_result import (
        OutcomeModifier,
        QualifyingSessionResult,
        RaceSessionResult,
    )
    from services.image_results_service import (
        QUALIFYING_TEMPLATE_KEY,
        ResultsDataError,
        resolve_drawing,
    )

    configurable = [t for t in teams or [] if not getattr(t, "is_reserve", False)]
    if not configurable:
        raise ResultsDataError(
            "the server holds no team beyond the reserve team, so there is no "
            "classification to be drawn. Add a team with `/team add` first."
        )

    try:
        capacity = catalogue_for(template_key).capacity(root) or 0
    except CapacityError as exc:
        raise ResultsDataError(str(exc)) from exc

    # One fewer than the template declares, so an unused row is visible — except where it
    # declares a single row, when one entry is drawn and no unused row is left to judge.
    count = 1 if capacity <= 1 else capacity - 1
    is_qualifying = template_key == QUALIFYING_TEMPLATE_KEY

    driver_names: dict[int, str] = {}
    team_names: dict[int, str] = {}
    nationalities: dict[int, str | None] = {}
    points_map: dict[int, int] = {}
    dsq_phase_map: dict[int, str] = {}

    def register(index: int) -> tuple[int, int]:
        """Fabricate the driver and team of entry *index*, returning their two ids."""
        user_id = _SAMPLE_ID_BASE + index
        record = configurable[(index - 1) % len(configurable)]
        role_id = 900_000 + ((index - 1) % len(configurable))
        # The long name is given to the second entry, so a template author can read an
        # `inline-size` bound being reached on a row that is not the first.
        driver_names[user_id] = LONG_DRIVER_NAME if index == 2 else f"Test Driver {index}"
        team_names[role_id] = getattr(record, "name", "") or f"Team {index}"
        nationalities[user_id] = SAMPLE_LINEUP_NATIONALITIES[
            (index - 1) % len(SAMPLE_LINEUP_NATIONALITIES)
        ]
        return user_id, role_id

    rows: list = []

    if is_qualifying:
        # index -> (best lap, tyre, outcome, points, dsq phase)
        cases = [
            ("1:23.000", "Soft", OutcomeModifier.CLASSIFIED, 25, None),
            ("1:23.400", "Medium", OutcomeModifier.CLASSIFIED, 18, None),  # gap < 1s
            ("2:29.500", "Hard", OutcomeModifier.CLASSIFIED, 15, None),  # gap > 1 min
            ("1:24.100", None, OutcomeModifier.CLASSIFIED, 12, None),  # no tyre recorded
            (None, "Soft", OutcomeModifier.DNS, 0, None),  # set no time
            ("1:24.900", "Soft", OutcomeModifier.DSQ, 0, "PENALTY"),
            ("1:25.100", "Medium", OutcomeModifier.DSQ, 0, "APPEAL"),
            ("1:25.400", "Hard", OutcomeModifier.CLASSIFIED, 6, None),  # neither phase
            ("1:25.900", "Soft", OutcomeModifier.CLASSIFIED, 0, None),  # no points
        ]
        for index in range(1, count + 1):
            best_lap, tyre, outcome, points, phase = cases[(index - 1) % len(cases)]
            # A template declaring more rows than there are cases cycles through them
            # again. Each cycle is pushed a second further back so the filler rows read as
            # a classification rather than as a field of drivers tied with the leader.
            cycle = (index - 1) // len(cases)
            if cycle and best_lap:
                from utils.results_formatter import parse_lap_time, render_lap_time

                parsed = parse_lap_time(best_lap)
                if parsed is not None:
                    best_lap = render_lap_time(parsed + cycle * 1_000)
            user_id, role_id = register(index)
            points_map[user_id] = points
            if phase is not None:
                dsq_phase_map[index] = phase
            rows.append(
                QualifyingSessionResult(
                    id=index,
                    session_result_id=1,
                    driver_user_id=user_id,
                    team_role_id=role_id,
                    finishing_position=index,
                    outcome=outcome,
                    tyre=tyre,
                    best_lap=best_lap,
                    points_awarded=points,
                )
            )
        session_type = SessionType.FEATURE_QUALIFYING
    else:
        # A total race time of more than an hour for the leader, so the hours branch of the
        # lap-time rendering is exercised.
        leader_ms = 3_725_500
        # index -> (base time, laps behind, outcome, ingame ms, postrace ms, points, phase)
        cases = [
            # An in-game penalty is added into an entry's total time, so the entry drawn to
            # show a sub-second interval carries none: giving it one would make its interval
            # the penalty rather than the gap the case is meant to exhibit.
            (leader_ms, None, OutcomeModifier.CLASSIFIED, 0, 0, 25, None),
            (leader_ms + 400, None, OutcomeModifier.CLASSIFIED, 0, 0, 18, None),
            (leader_ms + 91_000, None, OutcomeModifier.CLASSIFIED, 5_000, 0, 15, None),
            (None, 1, OutcomeModifier.CLASSIFIED, 750, 0, 12, None),  # a lap behind
            (None, 3, OutcomeModifier.CLASSIFIED, 0, 5_500, 10, None),  # laps behind
            (None, None, OutcomeModifier.DNF, 0, 0, 1, None),  # holds the bonus below
            (None, None, OutcomeModifier.DNS, 0, 0, 0, None),
            (None, None, OutcomeModifier.DSQ, 0, 10_000, 0, "PENALTY"),
            (leader_ms + 120_000, None, OutcomeModifier.CLASSIFIED, 0, 5_000, 4, "APPEAL"),
            (leader_ms + 150_000, None, OutcomeModifier.CLASSIFIED, 0, 0, 0, None),
        ]
        # The fastest-lap bonus is held by the entry that did not finish rather than by the
        # first-placed one, which the wip-spec asks for expressly: the fabricated points
        # configuration confers it with no limit upon the holder's position, so an entry
        # renumbered to the bottom can still carry it.
        dnf_index = 6
        for index in range(1, count + 1):
            base, laps, outcome, ingame, postrace, points, phase = cases[
                (index - 1) % len(cases)
            ]
            # As above: each repeat of the case list is pushed a second further back.
            cycle = (index - 1) // len(cases)
            if cycle and base is not None:
                base += cycle * 1_000
            user_id, role_id = register(index)
            points_map[user_id] = points
            if phase is not None:
                dsq_phase_map[index] = phase
            rows.append(
                RaceSessionResult(
                    id=index,
                    session_result_id=1,
                    driver_user_id=user_id,
                    team_role_id=role_id,
                    finishing_position=index,
                    outcome=outcome,
                    base_time_ms=base,
                    laps_behind=laps,
                    ingame_time_penalties_ms=ingame,
                    postrace_time_penalties_ms=postrace,
                    appeal_time_penalties_ms=0,
                    fastest_lap="1:21.345" if index == dnf_index else "1:23.456",
                    fastest_lap_bonus=1 if index == dnf_index else 0,
                    points_awarded=points,
                )
            )
        session_type = SessionType.FEATURE_RACE

    return resolve_drawing(
        session_type=session_type,
        is_sprint=False,
        result_status="FINAL",
        division_name="Test Division",
        division_tier=1,
        season_number=1,
        round_number=1,
        race_name=SAMPLE_RESULTS_TRACK,
        driver_rows=rows,
        points_map=points_map,
        driver_names=driver_names,
        team_names=team_names,
        nationalities=nationalities,
        dsq_phase_map=dsq_phase_map,
        fastest_lap_colour="#A020F0",
        nationality_collected=True,
    )


# -- Attendance (041) ------------------------------------------------------

#: The five rounds the fabricated division holds, standing after the third -- so a round
#: already finalised and a round yet to be run are both on the sheet. Round 2 is of the
#: mystery format, and the last has no image file of its own in any shipped asset set, so the
#: track fallback and its notice can both be read (wip-spec section "Test data").
SAMPLE_SHEET_ROUNDS = [
    ("Silverstone Circuit", False),
    (None, True),
    ("Circuit de Spa-Francorchamps", False),
    ("Suzuka International Racing Course", False),
    ("Autódromo José Carlos Pace", False),
]

#: The round the fabricated sheet stands after: the third of five.
SAMPLE_SHEET_STANDS_AFTER = 3


def build_attendance_drawing(root, teams, *, limits: bool = True):
    """The fabricated sheet ``/images test attendance`` draws (wip-spec "Test data").

    One driver fewer than the rows the template declares, so the rendering of an unused row can
    be judged; exactly one where the template declares a single row. Teams are the server's own
    configuration, so a league reads its own liveries rather than invented ones.

    *limits* draws the sheet with both point limits configured or with both switched off, which
    is the pair the command produces -- the second showing the two blocks **removed** rather
    than merely emptied (XIV.4, a configured absence).

    The enumerated driver cases are assigned **in order** and any the declared row count cannot
    reach are simply not drawn, which is what "insofar as the number of rows declared allows"
    means.
    """
    from models.image_catalogues import CapacityError, catalogue_for
    from services.image_attendance_service import (
        ATTENDANCE_TEMPLATE_KEY,
        AttendanceDataError,
        DriverRecord,
        RoundHeading,
        resolve_drawing,
    )

    configurable = [t for t in teams or [] if not getattr(t, "is_reserve", False)]
    if not configurable:
        raise AttendanceDataError(
            "the server holds no team beyond the reserve team, so there is no attendance "
            "sheet to be drawn. Add a team with `/team add` first."
        )
    reserve_team = next((t for t in teams or [] if getattr(t, "is_reserve", False)), None)
    reserve_name = getattr(reserve_team, "name", None) or "Reserve"

    catalogue = catalogue_for(ATTENDANCE_TEMPLATE_KEY)
    try:
        capacity = catalogue.capacity(root) or 0
        round_capacity = catalogue.column_capacity(root) or 0
    except CapacityError as exc:
        raise AttendanceDataError(str(exc)) from exc

    count = 1 if capacity <= 1 else capacity - 1
    rounds_drawn = min(round_capacity, len(SAMPLE_SHEET_ROUNDS))

    # A round of the mystery format is drawn from the datum "Mystery" like any other round
    # (wip-spec "A round of the mystery format"), never left without an image.
    headings = [
        RoundHeading(
            ordinal=index,
            number=str(index),
            track="Mystery" if mystery else name,
        )
        for index, (name, mystery) in enumerate(
            SAMPLE_SHEET_ROUNDS[:rounds_drawn], start=1
        )
    ]

    finalised = min(SAMPLE_SHEET_STANDS_AFTER, rounds_drawn)

    def points_for(index):
        """The per-round cells of driver *index*, over the rounds already finalised.

        Case 1 holds nothing at all -- every cell empty. Case 4's round was pardoned in its
        entirety, so the persisted figure is already nought and the cell is empty with no
        trace of the pardon. Case 7 took no part in round 1 and holds no record for it.
        """
        cells = {}
        for ordinal in range(1, finalised + 1):
            if index == 1:
                cells[ordinal] = 0
            elif index == 4 and ordinal == 1:
                cells[ordinal] = 0
            elif index == 7 and ordinal == 1:
                continue
            else:
                cells[ordinal] = (index + ordinal) % 3
        return cells

    display_names = {}
    team_names = {}
    nationalities = {}
    records = []

    # The reserve driver distributed into a seat for one of the rounds run.
    reserve_index = 6 if count >= 6 else None

    for index in range(1, count + 1):
        key = _SAMPLE_ID_BASE + 700 + index
        display_names[key] = "Test Driver %d" % index
        team_names[key] = (
            reserve_name
            if index == reserve_index
            else configurable[(index - 1) % len(configurable)].name
        )
        nationalities[key] = SAMPLE_LINEUP_NATIONALITIES[
            (index - 1) % len(SAMPLE_LINEUP_NATIONALITIES)
        ]

        if index == 1:
            total = 0
        elif index == 3:
            total = 10
        elif index in (4, 5):
            total = 4
        else:
            total = max(0, 9 - index)

        records.append(
            DriverRecord(
                key=key,
                total=total,
                round_points=points_for(index),
                sanctioned=(index == 3 and limits),
            )
        )

    return resolve_drawing(
        division_name="Test Division",
        round_number=finalised or 1,
        records=records,
        display_names=display_names,
        team_names=team_names,
        nationalities=nationalities,
        rounds=headings,
        autoreserve_threshold=10 if limits else None,
        autosack_threshold=20 if limits else None,
        division_tier=1,
        season_number=1,
        race_name=(headings[finalised - 1].track if finalised and headings else None),
        nationality_collected=True,
    )


# -- Check-in call (041) ---------------------------------------------------

#: The five cases ``/images test rsvp`` draws, each a round the template must be able to
#: carry (wip-spec "Test data"). The dates span more than one month and more than one half
#: of the day, so the configured date and time formats are both exercised.
SAMPLE_RSVP_CASES = (
    ("sprint", "SPRINT", "Silverstone Circuit", "British Grand Prix", "United Kingdom", 6),
    ("normal", "NORMAL", "Circuit Zandvoort", "Dutch Grand Prix", "Netherlands", 6),
    ("mystery", "MYSTERY", None, None, None, 6),
    ("no_image", "NORMAL", "Autódromo José Carlos Pace", "São Paulo Grand Prix", "Brazil", 6),
    ("no_deadline", "NORMAL", "Circuit de Spa-Francorchamps", "Belgian Grand Prix", "Belgium", 0),
)


def build_rsvp_drawing(root, *, case: str = "sprint"):
    """The fabricated check-in call ``/images test rsvp`` draws (wip-spec "Test data").

    Five cases, one image each: a sprint round naming four sessions, a normal round naming
    two, a mystery round carrying no track, a round whose track has no image file, and a round
    whose deadline is configured to ``0`` and therefore stands at the round's own start.

    The deadline is produced by ``attendance_service.derive_checkin_deadline`` -- the same
    derivation the real thing calls -- so the preview exercises the arithmetic rather than
    imitating it (Constitution XIV.7).
    """
    from datetime import datetime, timezone

    from services.attendance_service import derive_checkin_deadline
    from services.image_rsvp_service import resolve_drawing

    chosen = next(
        (entry for entry in SAMPLE_RSVP_CASES if entry[0] == case), SAMPLE_RSVP_CASES[0]
    )
    _, fmt, track, race, country, deadline_hours = chosen

    starts = {
        "sprint": datetime(2026, 5, 17, 14, 0, tzinfo=timezone.utc),
        "normal": datetime(2026, 6, 21, 20, 30, tzinfo=timezone.utc),
        "mystery": datetime(2026, 7, 5, 9, 15, tzinfo=timezone.utc),
        "no_image": datetime(2026, 8, 9, 22, 45, tzinfo=timezone.utc),
        "no_deadline": datetime(2026, 9, 13, 11, 0, tzinfo=timezone.utc),
    }
    scheduled_at = starts.get(case, starts["sprint"])

    return resolve_drawing(
        division_name="Test Division",
        round_number=1,
        round_format=fmt,
        scheduled_at=scheduled_at,
        deadline_at=derive_checkin_deadline(scheduled_at, deadline_hours),
        track_name=track,
        race_name=race,
        country_name=country,
        is_mystery=(fmt == "MYSTERY"),
        division_tier=1,
        season_number=1,
    )


#: The fabricated weather each of the six templates draws (wip-spec "Test data").
#:
#: The sprint and endurance rounds are not two arbitrary examples: between them they reach the
#: greatest session count the module can produce (four) and the greatest slot count (four, the
#: endurance race being the only session that may be drawn so many).
#:
#: The phase 3 sequences are chosen so that every case the wip-spec enumerates is visible, so
#: far as the sessions and slots the templates declare allow (FR-064) — a session of a single
#: slot, a session of one weather throughout, a session whose slots differ, a session at its
#: type's greatest slot count, and each of the five concrete weathers at least once.
SAMPLE_WEATHER_SPRINT = (
    # session type,               phase 2 draw, phase 3 sequence
    ("SHORT_SPRINT_QUALIFYING", "sunny", ["Clear", "Clear"]),
    ("LONG_SPRINT_RACE", "mixed", ["Overcast"]),
    ("SHORT_FEATURE_QUALIFYING", "rain", ["Light Cloud", "Wet"]),
    ("LONG_FEATURE_RACE", "mixed", ["Wet", "Very Wet", "Overcast"]),
)

SAMPLE_WEATHER_ENDURANCE = (
    ("FULL_QUALIFYING", "rain", ["Clear", "Light Cloud", "Overcast"]),
    ("FULL_RACE", "sunny", ["Overcast", "Wet", "Very Wet", "Wet"]),
)

#: Deliberately **not** a whole percentage, so that the rounding can be judged (FR-062). It
#: renders as "30%": the graphic and the phase 1 message round to the nearest whole number.
SAMPLE_RAIN_PROBABILITY = 0.3047

_SAMPLE_WEATHER_ROUNDS = {
    "SPRINT": ("SPRINT", "Silverstone Circuit", "British Grand Prix", "United Kingdom",
               SAMPLE_WEATHER_SPRINT),
    "ENDURANCE": ("ENDURANCE", "Circuit de Spa-Francorchamps", "Belgian Grand Prix",
                  "Belgium", SAMPLE_WEATHER_ENDURANCE),
}


def build_weather_drawing(root, template_key: str):
    """The fabricated forecast ``/images test weather-*`` draws.

    Six images across four commands, one per template. Phases 2 and 3 draw a sprint round from
    their sprint template and an endurance round from their plain one; phase 1 and the mystery
    notice draw one apiece.
    """
    from services.image_weather_service import resolve_drawing

    sprint = template_key.endswith("_sprint_template")
    fmt, track, race, country, sessions = _SAMPLE_WEATHER_ROUNDS[
        "SPRINT" if sprint else "ENDURANCE"
    ]

    if template_key == "weather_mystery_template":
        return resolve_drawing(
            phase=1,
            template_key=template_key,
            division_name="Test Division",
            round_number=1,
            round_format="MYSTERY",
            division_tier=1,
            season_number=1,
        )

    phase = 3 if "_p3" in template_key else (2 if "_p2" in template_key else 1)

    return resolve_drawing(
        phase=phase,
        template_key=template_key,
        division_name="Test Division",
        round_number=1,
        round_format=fmt,
        track_name=track,
        race_name=race,
        country_name=country,
        rain_probability=SAMPLE_RAIN_PROBABILITY,
        sessions=[
            {"session_type": session_type, "slot_type": slot_type, "slots": slots}
            for session_type, slot_type, slots in sessions
        ],
        division_tier=1,
        season_number=1,
    )


# ── Verdicts (043) ────────────────────────────────────────────────────────
#
# Six images from one template: the three kinds of verdict, and both signs of a time
# penalty. The free text is fabricated at five lengths, because the wrapping of a steward's
# prose is the whole of this type's difficulty and the only way to judge it is by eye.

#: The six cases `/images test verdicts` draws, in the order they are returned.
SAMPLE_VERDICT_CASES = (
    "penalty_added_sprint",
    "penalty_removed",
    "penalty_dsq",
    "appeal",
    "autosack",
    "autoreserve",
)

#: One line of prose, comfortably inside any rectangle a league would draw.
_VERDICT_TEXT_SHORT = "Contact at turn four."

#: Enough to fill a six-line box at the size the packaged template declares.
_VERDICT_TEXT_FULL = (
    "The stewards reviewed onboard footage from both cars and from the car following. "
    "Car 14 was alongside at the apex and had the corner. The contact was avoidable."
)

#: A little more than the box admits, so the reduction of the font size can be judged.
_VERDICT_TEXT_OVER = _VERDICT_TEXT_FULL + (
    " The driver was warned for the same manoeuvre in the preceding round, which the panel "
    "took into account when setting the length of the penalty."
)

#: An order of magnitude too much, so the floor, the cut and the notice can be judged. It
#: carries the steward's own paragraph breaks, which the graphic keeps as the message does.
_VERDICT_TEXT_HUGE = "\n\n".join([_VERDICT_TEXT_OVER] * 12)

#: What the textual announcement carries where the steward entered neither, **without** the
#: channel emphasis that message applies (XIV.16).
VERDICT_TEXT_NOT_PROVIDED = "(not provided)"


def build_verdict_drawing(root, *, case: str = "penalty_added_sprint"):
    """One fabricated verdict. `root` is unused: the type declares no collection to count."""
    from services.image_verdict_service import (
        VerdictDrawing,
        VerdictKind,
        resolve_mentions,
        sanction_text,
    )

    driver = "Ada Lovelace"
    common = dict(
        season_number=1,
        division_name="Test Division",
        division_tier=1,
        round_number=1,
        race_name=SAMPLE_RESULTS_TRACK,
        driver_name=driver,
    )

    if case == "penalty_added_sprint":
        return VerdictDrawing(
            kind=VerdictKind.PENALTY,
            session_name="Sprint Race",
            team_name="Test Team A",
            driver_nationality=SAMPLE_LINEUP_NATIONALITIES[0],
            penalty=sanction_text("TIME_PENALTY", 5),
            description=_VERDICT_TEXT_SHORT,
            justification=_VERDICT_TEXT_FULL,
            **common,
        )

    if case == "penalty_removed":
        return VerdictDrawing(
            kind=VerdictKind.PENALTY,
            session_name="Race",
            team_name="Test Team B",
            driver_nationality=SAMPLE_LINEUP_NATIONALITIES[1],
            penalty=sanction_text("TIME_PENALTY", -3),
            description=_VERDICT_TEXT_FULL,
            justification=_VERDICT_TEXT_OVER,
            **common,
        )

    if case == "penalty_dsq":
        return VerdictDrawing(
            kind=VerdictKind.PENALTY,
            session_name="Qualifying",
            team_name="Test Team A",
            driver_nationality=SAMPLE_LINEUP_NATIONALITIES[2],
            penalty=sanction_text("DSQ", None),
            description=_VERDICT_TEXT_SHORT,
            justification=_VERDICT_TEXT_HUGE,
            **common,
        )

    if case == "appeal":
        return VerdictDrawing(
            kind=VerdictKind.APPEAL,
            session_name="Feature Race",
            team_name="Test Team B",
            driver_nationality=SAMPLE_LINEUP_NATIONALITIES[3],
            penalty=sanction_text("TIME_PENALTY", -5),
            description=VERDICT_TEXT_NOT_PROVIDED,
            justification=VERDICT_TEXT_NOT_PROVIDED,
            **common,
        )

    # The two attendance sanctions: no session, no team, and a justification the attendance
    # module composes around a Discord mention, which reaches the canvas as a name alone.
    sacked = case == "autosack"
    composed = (
        f"<@{_SAMPLE_ID_BASE + 1}> ({driver}) has reached the 12 attendance point limit "
        f"in order to be removed from their full-time seat. Therefore, they have been "
        + (
            "removed from all driving seats effective immediately, and their current "
            "full-time seat will be offered to another driver."
            if sacked
            else "demoted to a reserve driver effective immediately, and their current "
            "full-time seat will be offered to another driver."
        )
    )
    return VerdictDrawing(
        kind=VerdictKind.ATTENDANCE_SANCTION,
        session_name=None,
        team_name=None,
        driver_nationality=SAMPLE_LINEUP_NATIONALITIES[4],
        penalty="Sacked" if sacked else "Moved to Reserve",
        description=(
            "Sacked due to accumulation of attendance points."
            if sacked
            else "Moved to Reserve due to accumulation of attendance points."
        ),
        justification=resolve_mentions(composed, lambda _user_id: driver),
        **common,
    )


def build_spec(template_key: str, root, *, teams=None, variant=None) -> FillSpec:
    """Build a FillSpec for *template_key* against the template's actual ids.

    The template is the authority on what exists (Constitution XIV.2), so the sample
    fills whatever addressable ids it declares that this module knows how to populate,
    rather than assuming a field catalogue that has not been ratified yet. That keeps
    `/images test` useful against a league's own templates from day one.

    The calendar is the exception: it has a ratified catalogue (037), so it is drawn
    through the same resolution the real thing uses rather than from loose sample ids.
    """
    if template_key == "calendar_template":
        from services.image_calendar_service import build_fill_spec
        from utils.paths import resolve_within_project_root

        # The packaged track directory. `/images test` reads no live data (FR-036), and
        # a preview must still resolve its assets — without a directory every round image
        # would report its asset class unconfigured and the preview would refuse to draw.
        try:
            track_directory = resolve_within_project_root("resources/tracks")
        except Exception:  # noqa: BLE001
            track_directory = None

        return build_fill_spec(
            build_calendar_drawing(root), root, track_directory=track_directory
        )

    if template_key == "lineup_template":
        from services.image_lineup_service import LineupDataError, build_fill_spec
        from utils.paths import resolve_within_project_root

        if teams is None:
            raise LineupDataError(
                "a lineup preview needs the server's team configuration; none was "
                "supplied to the sample builder"
            )

        # The packaged asset directories, for the same reason the calendar resolves its
        # track directory here: a preview must still resolve its assets.
        directories: dict[str, object] = {}
        for asset_class, relative in (
            ("team", "resources/teams"),
            ("flag", "resources/flags"),
            ("driver", "resources/drivers"),
        ):
            try:
                directories[asset_class] = resolve_within_project_root(relative)
            except Exception:  # noqa: BLE001
                pass

        return build_fill_spec(
            build_lineup_drawing(root, teams), root, asset_directories=directories
        )

    if template_key in ("results_qualifying_template", "results_race_template"):
        from services.image_results_service import ResultsDataError, build_fill_spec
        from utils.paths import resolve_within_project_root

        if teams is None:
            raise ResultsDataError(
                "a results preview needs the server's team configuration; none was "
                "supplied to the sample builder"
            )

        directories: dict[str, object] = {}
        for asset_class, relative in (
            ("team", "resources/teams"),
            ("flag", "resources/flags"),
            ("tyre", "resources/tyres"),
        ):
            try:
                directories[asset_class] = resolve_within_project_root(relative)
            except Exception:  # noqa: BLE001
                pass

        return build_fill_spec(
            build_results_drawing(root, template_key, teams),
            root,
            asset_directories=directories,
        )

    if template_key in (
        "standings_drivers_template",
        "standings_constructors_template",
    ):
        from services.image_standings_service import StandingsDataError, build_fill_spec
        from utils.paths import resolve_within_project_root

        if teams is None:
            raise StandingsDataError(
                "a standings preview needs the server's team configuration; none was "
                "supplied to the sample builder"
            )

        # The packaged asset directories, for the same reason the calendar resolves its
        # track directory here: a preview must still resolve its assets.
        directories: dict[str, object] = {}
        for asset_class, relative in (
            ("team", "resources/teams"),
            ("flag", "resources/flags"),
            ("track", "resources/tracks"),
            ("marker", "resources/markers"),
        ):
            try:
                directories[asset_class] = resolve_within_project_root(relative)
            except Exception:  # noqa: BLE001
                pass

        return build_fill_spec(
            build_standings_drawing(root, template_key, teams),
            root,
            asset_directories=directories,
        )

    if template_key == "attendance_template":
        from services.image_attendance_service import build_fill_spec
        from utils.paths import resolve_within_project_root

        directories = {}
        for asset_class, relative in (
            ("team", "resources/teams"),
            ("flag", "resources/flags"),
            ("track", "resources/tracks"),
        ):
            try:
                directories[asset_class] = resolve_within_project_root(relative)
            except Exception:  # noqa: BLE001
                pass

        return build_fill_spec(
            build_attendance_drawing(root, teams, limits=(variant != "no_limits")),
            root,
            asset_directories=directories,
        )

    if template_key == "rsvp_template":
        from services.image_rsvp_service import build_fill_spec
        from utils.paths import resolve_within_project_root

        directories = {}
        try:
            directories["track"] = resolve_within_project_root("resources/tracks")
        except Exception:  # noqa: BLE001
            pass

        return build_fill_spec(
            build_rsvp_drawing(root, case=variant or "sprint"),
            root,
            asset_directories=directories,
        )

    if template_key == "verdicts_template":
        from services.image_verdict_service import build_fill_spec
        from utils.paths import resolve_within_project_root

        directories = {}
        for asset_class, relative in (
            ("flag", "resources/flags"),
            ("team", "resources/teams"),
        ):
            try:
                directories[asset_class] = resolve_within_project_root(relative)
            except Exception:  # noqa: BLE001
                pass

        return build_fill_spec(
            build_verdict_drawing(root, case=variant or SAMPLE_VERDICT_CASES[0]),
            root,
            asset_directories=directories,
        )

    if template_key.startswith("weather_"):
        from services.image_weather_service import build_fill_spec
        from utils.paths import resolve_within_project_root

        directories = {}
        for asset_class, relative in (
            ("track", "resources/tracks"),
            ("weather", "resources/weather"),
        ):
            try:
                directories[asset_class] = resolve_within_project_root(relative)
            except Exception:  # noqa: BLE001
                pass

        return build_fill_spec(
            build_weather_drawing(root, template_key),
            root,
            asset_directories=directories,
        )

    # Ids *and* layer labels: a field may be addressed by either (Constitution XIV.2).
    # Indexing ids alone would make a template authored entirely with layer labels look
    # as though it declared nothing, and `/images test` would report every field unknown.
    declared = FieldIndex(root).declared()
    text: dict[str, str] = {}
    images: dict[str, str] = {}

    def put(field_id: str, value: str) -> None:
        if field_id in declared:
            text[field_id] = value

    # Heading fields common to most templates.
    put("season_name", "Season 12")
    put("division_name", "Division 1")
    put("title", _TITLES.get(template_key, "Preview"))
    put("subtitle", "Sample data — not a real session")
    put("round_name", "Round 3 — Spa-Francorchamps")
    put("track_name", "Spa-Francorchamps")
    put("session_name", _SESSION_NAMES.get(template_key, "Race"))
    put("date", "Sun 12 Jul 2026")
    put("time", "20:00")

    # Row-shaped templates: fill whatever numbered rows the template declares.
    for position in range(1, 27):
        driver, team, nationality = SAMPLE_DRIVERS[(position - 1) % len(SAMPLE_DRIVERS)]
        put(f"position_{position}", str(position))
        put(f"driver_{position}", driver)
        put(f"driver_{position}_name", driver)
        put(f"team_{position}", team)
        put(f"team_{position}_name", team)
        put(f"points_{position}", str(max(0, 26 - position)))
        put(f"time_{position}", f"1:2{position % 10}.{position % 10}45")
        if f"flag_{position}" in declared:
            images[f"flag_{position}"] = f"{nationality}.svg"

    for number, (track, date) in enumerate(SAMPLE_ROUNDS, start=1):
        put(f"round_{number}_name", track)
        put(f"round_{number}_track", track)
        put(f"round_{number}_date", date)

    # Verdict-shaped fields.
    put("driver_name", LONG_DRIVER_NAME)
    put("sanction", "10-second time penalty")
    put("description", "Causing a collision at turn four.")
    put("justification", LONG_JUSTIFICATION)

    # Weather-shaped fields.
    for slot in range(1, 5):
        put(f"slot_{slot}_label", f"Slot {slot}")
        put(f"slot_{slot}_condition", ("Clear", "Light cloud", "Overcast", "Rain")[slot - 1])
        put(f"slot_{slot}_temperature", f"{18 + slot}°C")

    return FillSpec(root=root, image_type=template_key, text=text, images=images)


_TITLES = {
    "calendar_template": "Calendar",
    "lineup_template": "Lineup",
    "results_qualifying_template": "Qualifying results",
    "results_race_template": "Race results",
    "standings_drivers_template": "Driver standings",
    "standings_constructors_template": "Constructor standings",
    "attendance_template": "Attendance",
    "rsvp_template": "Check-in",
    "weather_p1_template": "Weather — Phase 1",
    "weather_p2_template": "Weather — Phase 2",
    "weather_p3_template": "Weather — Phase 3",
    "weather_p2_sprint_template": "Weather — Phase 2 (Sprint)",
    "weather_p3_sprint_template": "Weather — Phase 3 (Sprint)",
    "weather_mystery_template": "Mystery round",
    "verdicts_template": "Stewards' verdict",
}

_SESSION_NAMES = {
    "results_qualifying_template": "Feature Qualifying",
    "results_race_template": "Feature Race",
}


def build_standings_drawing(root, template_key: str, teams):
    """The fabricated classification `/images test standings` draws (wip-spec § "Test data").

    One entry fewer than the rows the template declares, so the rendering of an unused row can
    be judged; exactly one where the template declares a single row. Teams are the server's own
    configuration, so a league reads its own liveries rather than invented ones.

    The enumerated cases are assigned **in order** and any the declared row count cannot reach
    are simply not drawn — which is what "insofar as the number of rows declared allows" means.
    The cases needing a round grid (a driver absent from a round, DNF/DNS/DSQ) arrive with the
    grid itself; this draws the classification.

    The three movement columns are produced by ``standings_service.derive_movement`` — the same
    derivation the real thing calls — so the preview exercises the arithmetic rather than
    imitating it (Constitution XIV.7).
    """
    from models.image_catalogues import CapacityError, catalogue_for
    from models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot
    from services.image_standings_service import (
        DRIVERS_TEMPLATE_KEY,
        StandingsDataError,
        resolve_drawing,
    )
    from services.standings_service import derive_gaps, derive_movement

    configurable = [t for t in teams or [] if not getattr(t, "is_reserve", False)]
    if not configurable:
        raise StandingsDataError(
            "the server holds no team beyond the reserve team, so there is no "
            "classification to be drawn. Add a team with `/team add` first."
        )
    reserve_team = next(
        (t for t in teams or [] if getattr(t, "is_reserve", False)), None
    )
    reserve_name = getattr(reserve_team, "name", None) or "Reserve"

    try:
        capacity = catalogue_for(template_key).capacity(root) or 0
    except CapacityError as exc:
        raise StandingsDataError(str(exc)) from exc

    count = 1 if capacity <= 1 else capacity - 1
    drivers = template_key == DRIVERS_TEMPLATE_KEY
    if not drivers:
        # A constructor classification cannot hold more teams than the server configures.
        count = min(count, len(configurable))

    # (points, previous position or None). In order: the leader who gained, one unchanged, one
    # level on points with it and fallen, one the preceding standings do not hold, and one on
    # no points at all. Between them they exercise all three markers, the empty leader gap and
    # the absent movement record.
    def case(index: int) -> tuple[int, int | None]:
        points = max(0, 50 - (index - 1) * 6)
        if index == 1:
            return points, 3
        if index == 2:
            return points, 2
        if index == 3:
            return points, 1
        if index == 4:
            return points, None
        if index == count and count >= 6:
            return 0, index
        return points, index + 1 if index % 2 else index

    display_names: dict[int, str] = {}
    team_names: dict[int, str] = {}
    nationalities: dict[int, str | None] = {}
    reserve_user_ids: set[int] = set()
    snapshots: list = []
    current: list[tuple[int, int, int]] = []
    previous: dict[int, int] = {}

    # The reserve driver of the wip-spec's enumeration, drawn because the toggle is on and
    # they hold points. Placed late so it does not displace the earlier cases.
    reserve_index = 5 if drivers and count >= 5 else None

    for index in range(1, count + 1):
        points, was = case(index)
        # Two entries level on points, separated by the countback the record already applied.
        if index == 3 and count >= 3:
            points = case(2)[0]

        if drivers:
            key = _SAMPLE_ID_BASE + index
            display_names[key] = (
                LONG_DRIVER_NAME if index == 2 else f"Test Driver {index}"
            )
            if index == reserve_index:
                reserve_user_ids.add(key)
                team_names[key] = reserve_name
            else:
                record = configurable[(index - 1) % len(configurable)]
                team_names[key] = getattr(record, "name", "") or f"Team {index}"
            nationalities[key] = SAMPLE_LINEUP_NATIONALITIES[
                (index - 1) % len(SAMPLE_LINEUP_NATIONALITIES)
            ]
            snapshots.append(
                DriverStandingsSnapshot(
                    id=0,
                    round_id=1,
                    division_id=1,
                    driver_user_id=key,
                    standing_position=index,
                    total_points=points,
                    finish_counts={},
                    first_finish_rounds={},
                    race_participant=True,
                )
            )
        else:
            key = 900_000 + index
            record = configurable[(index - 1) % len(configurable)]
            name = getattr(record, "name", "") or f"Team {index}"
            display_names[key] = name
            team_names[key] = name
            snapshots.append(
                TeamStandingsSnapshot(
                    id=0,
                    round_id=1,
                    division_id=1,
                    team_role_id=key,
                    standing_position=index,
                    total_points=points,
                    finish_counts={},
                    first_finish_rounds={},
                )
            )

        current.append((key, index, points))
        if was is not None:
            previous[key] = was

    movements = derive_movement(current, previous)
    gaps = derive_gaps(current)

    return resolve_drawing(
        template_key=template_key,
        division_name="Test Division",
        round_number=1,
        result_status="FINAL",
        snapshots=snapshots,
        display_names=display_names,
        team_names=team_names,
        movements=movements,
        gaps=gaps,
        nationalities=nationalities if drivers else None,
        reserve_user_ids=reserve_user_ids,
        show_reserves=True,
        division_tier=1,
        season_number=1,
        race_name="Test Grand Prix",
    )
