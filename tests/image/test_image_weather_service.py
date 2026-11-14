"""Weather resolution and template selection — T019, T021, T026, T048.

Written against specs/042-weather-image-generation/contracts/weather-posting.md and
Constitution XIV.7, XIV.10 and XIV.16.
"""
from __future__ import annotations

import pytest

from leaguebot.image.services.image_weather_service import (
    MYSTERY_TEMPLATE_KEY,
    WeatherDataError,
    resolve_drawing,
    weather_template_key,
)

#: Each session as weather composes it: its label, its drawn weather type, Phase 3's slots and
#: their summary, in the order the sessions are run.
SPRINT_SESSIONS = (
    ("Sprint Qualifying", "Sunny", ("Clear", "Clear"), "Clear"),
    ("Sprint Race", "Mixed", ("Overcast",), "Overcast"),
    ("Feature Qualifying", "Rain", ("Light Cloud", "Wet"), "Light Cloud → Wet"),
    ("Feature Race", "Mixed", ("Wet", "Very Wet", "Overcast"), "Wet → Very Wet → Overcast"),
)

ENDURANCE_SESSIONS = (
    ("Qualifying", "Rain", ("Clear", "Light Cloud", "Overcast"),
     "Clear → Light Cloud → Overcast"),
    ("Race", "Sunny", ("Overcast", "Wet", "Very Wet", "Wet"), "Overcast → Wet → Very Wet → Wet"),
)

_DESCRIPTIONS = {
    1: "Initial chance of rain",
    2: "Initial session forecast",
    3: "Final session forecast",
}


def _values(phase, sessions=ENDURANCE_SESSIONS, rain_probability="30%"):
    """What weather hands the picture of *phase*, as it hands it: the phase's description and
    the likelihood of rain as text; from Phase 2 on each session's label and weather type; and
    at Phase 3 alone each session's slots and their summary (#287, W288)."""
    from leaguebot.image.models.forecast_values import ForecastSession, ForecastValues

    return ForecastValues(
        description=_DESCRIPTIONS[phase],
        rain_probability=rain_probability,
        sessions=tuple(
            ForecastSession(
                label=label,
                weather_type=weather_type,
                slots=slots if phase == 3 else (),
                summary=summary if phase == 3 else None,
            )
            for label, weather_type, slots, summary in (sessions if phase > 1 else ())
        ),
    )


def _drawing(phase, fmt="ENDURANCE", sessions=ENDURANCE_SESSIONS, **kw):
    return resolve_drawing(
        phase=phase,
        division_name="Test Division",
        round_number=1,
        round_format=fmt,
        track_name=kw.pop("track_name", "Circuit de Spa-Francorchamps"),
        race_name=kw.pop("race_name", "Belgian Grand Prix"),
        country_name=kw.pop("country_name", "Belgium"),
        values=_values(phase, sessions, kw.pop("rain_probability", "30%")),
        division_tier=kw.pop("division_tier", 1),
        season_number=kw.pop("season_number", 1),
        **kw,
    )


# ── 1. Template selection: the selecting datum (FR-012, XIV.10) ───────────


@pytest.mark.parametrize(
    "phase,fmt,expected",
    [
        # Phase 1 holds no session, so no variant arises for it.
        (1, "NORMAL", "weather_p1_template"),
        (1, "SPRINT", "weather_p1_template"),
        (1, "ENDURANCE", "weather_p1_template"),
        (2, "SPRINT", "weather_p2_sprint_template"),
        (2, "NORMAL", "weather_p2_template"),
        (2, "ENDURANCE", "weather_p2_template"),
        (3, "SPRINT", "weather_p3_sprint_template"),
        (3, "NORMAL", "weather_p3_template"),
        (3, "ENDURANCE", "weather_p3_template"),
    ],
)
def test_the_format_of_the_round_selects_the_template(phase, fmt, expected):
    assert weather_template_key(phase, fmt) == expected


def test_a_mystery_round_reaches_the_mystery_template_at_every_phase():
    for phase in (1, 2, 3):
        assert weather_template_key(phase, "MYSTERY") == MYSTERY_TEMPLATE_KEY


def test_selection_reads_nothing_but_its_two_arguments():
    """FR-012 — not a session count, not a configuration, not a fallback slot.

    Asserted by signature: a function of exactly two parameters cannot consult a third
    thing, and calling it twice with the same pair must give the same answer.
    """
    import inspect

    params = list(inspect.signature(weather_template_key).parameters)
    assert params == ["phase", "round_format"]
    assert weather_template_key(3, "SPRINT") == weather_template_key(3, "SPRINT")


def test_an_unknown_format_takes_the_plain_slot():
    """Every format but the sprint is served by the plain template."""
    assert weather_template_key(2, "SOMETHING_NEW") == "weather_p2_template"


def test_a_phase_outside_the_pipeline_is_refused():
    with pytest.raises(WeatherDataError):
        weather_template_key(4, "NORMAL")


# ── 2. Resolution (FR-022 … FR-031) ───────────────────────────────────────


@pytest.mark.parametrize("phase", [1, 2, 3])
def test_the_phase_description_is_drawn_as_handed(phase):
    """The phase is named in the words weather hands the picture, and nowhere else (W288)."""
    assert _drawing(phase).phase_description == _DESCRIPTIONS[phase]


def test_the_likelihood_is_never_recomputed():
    """Whatever is handed in is what is drawn; no arithmetic or rounding of the graphic's own."""
    assert _drawing(2, rain_probability="75%").rain_probability == "75%"
    assert _drawing(2, rain_probability=None).rain_probability is None


def test_the_weather_type_is_read_as_phase_two_drew_it():
    """FR-026 — each session carries the type weather hands for it, in run order."""
    drawing = _drawing(2, "SPRINT", SPRINT_SESSIONS)
    assert [s.weather_type for s in drawing.sessions] == ["Sunny", "Mixed", "Rain", "Mixed"]


def test_phase_three_draws_the_sequence_in_the_order_drawn():
    """FR-030 — the slots as handed, numbered from one, each session holding its own count."""
    drawing = _drawing(3, "SPRINT", SPRINT_SESSIONS)
    feature_race = drawing.sessions[3]
    assert [s.label for s in feature_race.slots] == ["Wet", "Very Wet", "Overcast"]
    assert [s.ordinal for s in feature_race.slots] == [1, 2, 3]
    assert [s.slot_count for s in drawing.sessions] == [2, 1, 2, 3]


def test_the_summary_is_drawn_as_handed():
    """FR-029 — the summary weather composes is drawn as it is handed, and phase 2 has none."""
    drawing = _drawing(3, "SPRINT", SPRINT_SESSIONS)
    assert [s.summary for s in drawing.sessions] == [
        "Clear", "Overcast", "Light Cloud → Wet", "Wet → Very Wet → Overcast",
    ]
    assert [s.summary for s in _drawing(2, "SPRINT", SPRINT_SESSIONS).sessions] == [None] * 4


def test_sessions_are_numbered_from_one_in_the_order_they_are_run():
    drawing = _drawing(3, "SPRINT", SPRINT_SESSIONS)
    assert [(s.ordinal, s.name) for s in drawing.sessions] == [
        (1, "Sprint Qualifying"), (2, "Sprint Race"), (3, "Feature Qualifying"),
        (4, "Feature Race"),
    ]


# ── 3. The mystery notice (FR-006, T048) ──────────────────────────────────


def test_the_mystery_notice_carries_the_heading_fields_alone():
    drawing = resolve_drawing(
        phase=1,
        division_name="Test Division",
        round_number=7,
        round_format="MYSTERY",
        division_tier=2,
        season_number=3,
    )
    assert drawing.template_key == MYSTERY_TEMPLATE_KEY
    assert drawing.is_mystery
    assert drawing.division_name == "Test Division"
    assert drawing.round_number == "7"
    assert drawing.division_tier == "2"
    assert drawing.season_number == "3"

    # No track, no grand prix, no country, no likelihood, no session, no description.
    assert drawing.track_name is None
    assert drawing.race_name is None
    assert drawing.country_name is None
    assert drawing.track_datum is None
    assert drawing.rain_probability is None
    assert drawing.phase_description is None
    assert drawing.sessions == []


def test_a_mystery_round_ignores_any_forecast_handed_to_it():
    """It runs no phase, so there is nothing for a forecast to be of."""
    drawing = resolve_drawing(
        phase=3,
        division_name="D",
        round_number=1,
        round_format="MYSTERY",
        track_name="Silverstone Circuit",
        values=_values(3, SPRINT_SESSIONS, "50%"),
    )
    assert drawing.sessions == []
    assert drawing.rain_probability is None
    assert drawing.track_name is None
    assert drawing.phase_description is None


# ── 4. What no weather graphic carries (FR-011) ───────────────────────────


def test_no_drawing_carries_a_date_a_time_a_driver_or_a_team():
    fields = set(vars(_drawing(3, "SPRINT", SPRINT_SESSIONS)))
    for forbidden in ("round_date", "round_time", "driver_name", "team_name", "mention"):
        assert forbidden not in fields


def test_the_division_tier_is_carried_as_given_and_emptied_when_absent():
    """FR-031 — emptied rather than drawn as a placeholder."""
    assert _drawing(1, division_tier=None).division_tier is None
    assert _drawing(1, division_tier=2).division_tier == "2"
