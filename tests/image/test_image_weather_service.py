"""Weather resolution and template selection — T019, T021, T026, T048.

Written against specs/042-weather-image-generation/contracts/weather-posting.md and
Constitution XIV.7, XIV.10 and XIV.16.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_weather_service import (  # noqa: E402
    MYSTERY_TEMPLATE_KEY,
    PHASE_DESCRIPTIONS,
    WeatherDataError,
    resolve_drawing,
    weather_template_key,
)
from utils.message_builder import (  # noqa: E402
    format_rain_probability,
    format_session_weather_type,
    format_slot_sequence,
    session_type_label,
)

SPRINT_SESSIONS = [
    {"session_type": "SHORT_SPRINT_QUALIFYING", "slot_type": "sunny", "slots": ["Clear", "Clear"]},
    {"session_type": "LONG_SPRINT_RACE", "slot_type": "mixed", "slots": ["Overcast"]},
    {"session_type": "SHORT_FEATURE_QUALIFYING", "slot_type": "rain", "slots": ["Light Cloud", "Wet"]},
    {"session_type": "LONG_FEATURE_RACE", "slot_type": "mixed", "slots": ["Wet", "Very Wet", "Overcast"]},
]

ENDURANCE_SESSIONS = [
    {"session_type": "FULL_QUALIFYING", "slot_type": "rain", "slots": ["Clear", "Light Cloud", "Overcast"]},
    {"session_type": "FULL_RACE", "slot_type": "sunny", "slots": ["Overcast", "Wet", "Very Wet", "Wet"]},
]


def _drawing(phase, fmt="ENDURANCE", sessions=None, **kw):
    return resolve_drawing(
        phase=phase,
        division_name="Test Division",
        round_number=1,
        round_format=fmt,
        track_name=kw.pop("track_name", "Circuit de Spa-Francorchamps"),
        race_name=kw.pop("race_name", "Belgian Grand Prix"),
        country_name=kw.pop("country_name", "Belgium"),
        rain_probability=kw.pop("rain_probability", 0.3047),
        sessions=sessions if sessions is not None else ENDURANCE_SESSIONS,
        division_tier=kw.pop("division_tier", 1),
        season_number=kw.pop("season_number", 1),
        **kw,
    )


# ── 1. Template selection: the selecting datum (FR-012, XIV.10) ───────────


@pytest.mark.parametrize(
    "phase,fmt,expected",
    [
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


def test_phase_one_draws_one_template_for_every_format():
    """It holds no session, so no variant arises for it."""
    keys = {weather_template_key(1, f) for f in ("NORMAL", "SPRINT", "ENDURANCE")}
    assert keys == {"weather_p1_template"}


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


@pytest.mark.parametrize("phase,text", sorted(PHASE_DESCRIPTIONS.items()))
def test_the_phase_description_is_fixed_text(phase, text):
    assert _drawing(phase).phase_description == text


def test_the_phase_descriptions_are_the_three_the_spec_names():
    assert PHASE_DESCRIPTIONS == {
        1: "Initial chance of rain",
        2: "Initial session forecast",
        3: "Final session forecast",
    }


def test_the_rain_probability_is_rendered_by_the_shared_renderer():
    """FR-020, FR-023 — the graphic and the message cannot disagree."""
    assert _drawing(1).rain_probability == format_rain_probability(0.3047)
    assert _drawing(1).rain_probability == "30%"


def test_phases_two_and_three_carry_the_same_stored_likelihood():
    """FR-023 — a value the text path published in another message of the same flow."""
    one = _drawing(1).rain_probability
    assert _drawing(2).rain_probability == one
    assert _drawing(3).rain_probability == one


def test_the_likelihood_is_never_recomputed():
    """Whatever is handed in is what is rendered; no arithmetic of the graphic's own."""
    assert _drawing(2, rain_probability=0.75).rain_probability == "75%"
    assert _drawing(2, rain_probability=None).rain_probability is None


def test_session_names_carry_no_qualifier_of_length():
    """FR-025 — "Sprint Qualifying", never "Short Sprint Qualifying"."""
    drawing = _drawing(2, fmt="SPRINT", sessions=SPRINT_SESSIONS)
    assert [s.name for s in drawing.sessions] == [
        "Sprint Qualifying",
        "Sprint Race",
        "Feature Qualifying",
        "Feature Race",
    ]
    assert [s.name for s in _drawing(2).sessions] == ["Qualifying", "Race"]


def test_session_names_come_from_the_shared_renderer():
    for entry in SPRINT_SESSIONS:
        expected = session_type_label(entry["session_type"])
        assert expected in [s.name for s in _drawing(2, "SPRINT", SPRINT_SESSIONS).sessions]


def test_the_weather_type_is_read_as_phase_two_drew_it():
    """FR-026 — one of exactly three, through the shared renderer."""
    drawing = _drawing(2, "SPRINT", SPRINT_SESSIONS)
    assert [s.weather_type for s in drawing.sessions] == [
        format_session_weather_type(e["slot_type"]) for e in SPRINT_SESSIONS
    ]
    assert set(s.weather_type for s in drawing.sessions) <= {"Sunny", "Mixed", "Rain"}


def test_phase_three_carries_the_type_phase_two_drew():
    p2 = _drawing(2, "SPRINT", SPRINT_SESSIONS)
    p3 = _drawing(3, "SPRINT", SPRINT_SESSIONS)
    assert [s.weather_type for s in p3.sessions] == [s.weather_type for s in p2.sessions]


def test_phase_two_draws_no_slot():
    """A session holds one slot at phase 2; the sequence is phase 3's subject."""
    for session in _drawing(2, "SPRINT", SPRINT_SESSIONS).sessions:
        assert session.slots == []
        assert session.summary is None


def test_phase_three_draws_the_sequence_in_the_order_drawn():
    """FR-030."""
    drawing = _drawing(3, "SPRINT", SPRINT_SESSIONS)
    feature_race = drawing.sessions[3]
    assert [s.label for s in feature_race.slots] == ["Wet", "Very Wet", "Overcast"]
    assert [s.ordinal for s in feature_race.slots] == [1, 2, 3]


def test_the_summary_comes_from_the_shared_renderer_unadorned():
    """FR-029 — the emphasis is the channel's instruction, not part of the value."""
    drawing = _drawing(3, "SPRINT", SPRINT_SESSIONS)
    for session, entry in zip(drawing.sessions, SPRINT_SESSIONS):
        assert session.summary == format_slot_sequence(entry["slots"])
        assert "*" not in session.summary


def test_a_session_of_one_weather_is_summarised_by_that_weather_alone():
    drawing = _drawing(3, "SPRINT", SPRINT_SESSIONS)
    assert drawing.sessions[0].summary == "Clear"  # ["Clear", "Clear"]


def test_a_session_of_a_single_slot_is_summarised_by_that_slot():
    drawing = _drawing(3, "SPRINT", SPRINT_SESSIONS)
    assert drawing.sessions[1].summary == "Overcast"  # ["Overcast"]
    assert drawing.sessions[1].slot_count == 1


def test_sessions_are_numbered_from_one_in_the_order_they_are_run():
    drawing = _drawing(3, "SPRINT", SPRINT_SESSIONS)
    assert [s.ordinal for s in drawing.sessions] == [1, 2, 3, 4]


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
        rain_probability=0.5,
        sessions=SPRINT_SESSIONS,
    )
    assert drawing.sessions == []
    assert drawing.rain_probability is None
    assert drawing.track_name is None


# ── 4. What no weather graphic carries (FR-011) ───────────────────────────


def test_no_drawing_carries_a_date_a_time_a_driver_or_a_team():
    fields = set(vars(_drawing(3, "SPRINT", SPRINT_SESSIONS)))
    for forbidden in ("round_date", "round_time", "driver_name", "team_name", "mention"):
        assert forbidden not in fields


def test_no_drawing_carries_a_phase_number_as_a_value():
    """The phase is named in words by `phase_description` and nowhere else."""
    drawing = _drawing(3)
    assert drawing.phase_description == "Final session forecast"
    assert "3" not in (drawing.phase_description or "")


def test_the_division_tier_is_carried_as_given_and_emptied_when_absent():
    """FR-031 — emptied rather than drawn as a placeholder."""
    assert _drawing(1, division_tier=None).division_tier is None
    assert _drawing(1, division_tier=2).division_tier == "2"
