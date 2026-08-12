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


def build_spec(template_key: str, root, *, teams=None) -> FillSpec:
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
