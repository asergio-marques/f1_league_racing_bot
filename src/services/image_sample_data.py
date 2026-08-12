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


def build_spec(template_key: str, root) -> FillSpec:
    """Build a FillSpec for *template_key* against the template's actual ids.

    The template is the authority on what exists (Constitution XIV.2), so the sample
    fills whatever addressable ids it declares that this module knows how to populate,
    rather than assuming a field catalogue that has not been ratified yet. That keeps
    `/images test` useful against a league's own templates from day one.
    """
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
