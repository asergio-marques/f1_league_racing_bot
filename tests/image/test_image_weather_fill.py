"""Weather projection onto a template — T020.

Group removal, silent surplus, emptied optionals and the icon families. Written against
specs/042-weather-image-generation/contracts/weather-catalogues.md and Constitution XIV.2,
XIV.3, XIV.12 and XIV.13.
"""
from __future__ import annotations

import pytest
from lxml import etree

from leaguebot.image.services.aspects.image_weather_service import (
    SessionDrawing,
    SlotDrawing,
    WeatherDataError,
    WeatherDrawing,
    build_fill_spec,
    resolve_drawing,
    weather_template_key,
)

HEADING = (
    '<text id="division_name">D</text>'
    '<text id="phase_description">P</text>'
    '<text id="round_number">1</text>'
    '<text id="track_name">T</text>'
    '<text id="race_name">R</text>'
    '<text id="country_name">C</text>'
    '<text id="rain_probability">0%</text>'
    '<text id="season_number">1</text>'
    '<text id="division_tier">1</text>'
    '<image id="track_flag"/>'
)

#: Each session as the picture draws it: its label, its weather type and Phase 3's slots, in the
#: order the sessions are run.
SPRINT_SESSIONS = (
    ("Sprint Qualifying", "Sunny", ("Clear", "Clear")),
    ("Sprint Race", "Mixed", ("Overcast",)),
    ("Feature Qualifying", "Rain", ("Light Cloud", "Wet")),
    ("Feature Race", "Mixed", ("Wet", "Very Wet", "Overcast")),
)
ENDURANCE_SESSIONS = (
    ("Qualifying", "Rain", ("Clear", "Light Cloud", "Overcast")),
    ("Race", "Sunny", ("Overcast", "Wet", "Very Wet", "Wet")),
)

_DESCRIPTIONS = {
    1: "Initial chance of rain",
    2: "Initial session forecast",
    3: "Final session forecast",
}

_SUMMARIES = {
    ("Clear", "Clear"): "Clear",
    ("Overcast",): "Overcast",
    ("Light Cloud", "Wet"): "Light Cloud → Wet",
    ("Wet", "Very Wet", "Overcast"): "Wet → Very Wet → Overcast",
    ("Clear", "Light Cloud", "Overcast"): "Clear → Light Cloud → Overcast",
    ("Overcast", "Wet", "Very Wet", "Wet"): "Overcast → Wet → Very Wet → Wet",
}


def _svg(body: str):
    return etree.fromstring(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        f"{body}</svg>".encode()
    )


def _p2_template(sessions: int, *, heading: str = HEADING):
    blocks = "".join(
        f'<g id="session_{n}_group">'
        f'<text id="session_{n}_name">S</text>'
        f'<text id="session_{n}_slot_type">Type</text>'
        f'<image id="session_{n}_slot_type_icon"/>'
        f"</g>"
        for n in range(1, sessions + 1)
    )
    return _svg(heading + blocks)


def _p3_template(sessions: int, slots: int, *, heading: str = HEADING):
    blocks = ""
    for n in range(1, sessions + 1):
        cells = "".join(
            f'<g id="session_{n}_slot_{m}_group">'
            f'<text id="session_{n}_slot_{m}_label">W</text>'
            f'<image id="session_{n}_slot_{m}_icon"/>'
            f"</g>"
            for m in range(1, slots + 1)
        )
        blocks += (
            f'<g id="session_{n}_group">'
            f'<text id="session_{n}_name">S</text>'
            f'<text id="session_{n}_slot_type">Type</text>'
            f'<image id="session_{n}_slot_type_icon"/>'
            f'<text id="session_{n}_summary">Sum</text>'
            f"{cells}</g>"
        )
    return _svg(heading + blocks)


def _drawing(phase, fmt, sessions, **kw):
    """The drawing of *phase* for a round of *fmt*, built as it stands once resolved: these tests
    project a drawing, and how one is resolved is ``test_image_weather_service``'s subject."""
    def value(name, default):
        return kw.pop(name, default) or None

    track = value("track_name", "Silverstone Circuit")
    return WeatherDrawing(
        template_key=weather_template_key(phase, fmt),
        phase=phase,
        division_name="Test Division",
        round_number="1",
        phase_description=_DESCRIPTIONS[phase],
        track_name=track,
        race_name=value("race_name", "British Grand Prix"),
        country_name=value("country_name", "United Kingdom"),
        track_datum=track,
        rain_probability=value("rain_probability", "30%"),
        division_tier=None if (tier := kw.pop("division_tier", 1)) is None else str(tier),
        season_number=str(kw.pop("season_number", 1)),
        sessions=[
            SessionDrawing(
                ordinal=ordinal,
                name=name,
                weather_type=weather_type,
                summary=_SUMMARIES[slots] if phase == 3 else None,
                slots=[SlotDrawing(ordinal=i, label=label) for i, label in enumerate(slots, 1)]
                if phase == 3 else [],
            )
            for ordinal, (name, weather_type, slots) in enumerate(sessions, start=1)
        ] if phase in (2, 3) else [],
    )


# ── 1. The heading ────────────────────────────────────────────────────────


def test_the_heading_fields_are_filled():
    root = _p2_template(4)
    spec = build_fill_spec(_drawing(2, "SPRINT", SPRINT_SESSIONS), root)
    assert spec.text["division_name"] == "Test Division"
    assert spec.text["round_number"] == "1"
    assert spec.text["phase_description"] == "Initial session forecast"
    assert spec.text["race_name"] == "British Grand Prix"
    assert spec.text["track_name"] == "Silverstone Circuit"
    assert spec.text["rain_probability"] == "30%"


def test_the_grand_prix_name_is_the_mandatory_one_of_the_two_names():
    """2026-09-01 — a forecast must name the race; naming the tarmac is its option.

    A circuit hosting two grand prix in a season identifies neither on its own, so the
    obligation sits on the name that does identify the round.
    """
    root = _p2_template(4)
    spec = build_fill_spec(_drawing(2, "SPRINT", SPRINT_SESSIONS, race_name=None), root)
    # Mandatory: emptied where the data hold none, never removed with a group.
    assert "race_name" in spec.empty_quietly
    assert "race_name" not in spec.remove


def test_the_circuit_name_leaves_with_its_group_where_the_round_records_none():
    body = (
        '<text id="division_name">D</text><text id="phase_description">P</text>'
        '<text id="round_number">1</text><text id="race_name">R</text>'
        '<g id="track_name_group"><text id="track_name">T</text></g>'
        '<g id="session_1_group"><text id="session_1_name">S</text>'
        '<text id="session_1_slot_type">T</text></g>'
        '<g id="session_2_group"><text id="session_2_name">S</text>'
        '<text id="session_2_slot_type">T</text></g>'
    )
    root = etree.fromstring(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        f"{body}</svg>".encode()
    )
    spec = build_fill_spec(
        _drawing(2, "ENDURANCE", ENDURANCE_SESSIONS, track_name=None), root
    )
    assert "track_name_group" in spec.remove


def test_the_round_flag_is_placed_by_its_datum_and_class():
    root = _p2_template(4)
    spec = build_fill_spec(_drawing(2, "SPRINT", SPRINT_SESSIONS), root)
    # A forecast heads a round rather than picturing it: country flag, no map (044).
    assert spec.image_data["track_flag"] == ("flag", "United Kingdom")
    assert "track_image" not in spec.image_data


def test_an_optional_without_a_value_leaves_with_its_group():
    body = (
        '<text id="division_name">D</text><text id="phase_description">P</text>'
        '<text id="round_number">1</text><text id="race_name">R</text>'
        '<g id="division_tier_group"><text id="division_tier">1</text></g>'
        '<g id="session_1_group"><text id="session_1_name">S</text>'
        '<text id="session_1_slot_type">T</text></g>'
        '<g id="session_2_group"><text id="session_2_name">S</text>'
        '<text id="session_2_slot_type">T</text></g>'
    )
    spec = build_fill_spec(
        _drawing(2, "ENDURANCE", ENDURANCE_SESSIONS, division_tier=None), _svg(body)
    )
    assert "division_tier_group" in spec.remove
    assert "division_tier" not in spec.text


def test_an_optional_without_a_group_is_emptied_rather_than_dashed():
    """FR-032 — never a placeholder."""
    spec = build_fill_spec(
        _drawing(2, "SPRINT", SPRINT_SESSIONS, country_name=None), _p2_template(4)
    )
    assert "country_name" in spec.empty
    assert "country_name" not in spec.text


# ── 2. Sessions (FR-036, FR-017) ──────────────────────────────────────────


def test_every_session_of_the_round_is_filled():
    spec = build_fill_spec(_drawing(2, "SPRINT", SPRINT_SESSIONS), _p2_template(4))
    assert spec.text["session_1_name"] == "Sprint Qualifying"
    assert spec.text["session_4_name"] == "Feature Race"
    assert spec.text["session_1_slot_type"] == "Sunny"
    assert spec.image_data["session_1_slot_type_icon"] == ("weather", "Sunny")


def test_a_surplus_session_leaves_by_its_group_and_reports_nothing():
    """The floor is the greatest the slot's formats demand; a shorter round reaches it by
    removal, which is the ordinary case and raises no notice (FR-036, FR-017)."""
    root = _p2_template(6)  # over-declared, which is admitted
    spec = build_fill_spec(_drawing(2, "SPRINT", SPRINT_SESSIONS), root)
    assert "session_5_group" in spec.remove
    assert "session_6_group" in spec.remove
    assert "session_5_name" in spec.off_canvas
    assert spec.empty == [] or "session_5_name" not in spec.empty


def test_a_removed_session_takes_its_slots_with_it():
    """FR-040 — containment carries them, the slot ids being built on the session's stem."""
    root = _p3_template(4, 4)
    spec = build_fill_spec(_drawing(3, "ENDURANCE", ENDURANCE_SESSIONS), root)
    assert "session_3_group" in spec.remove
    assert "session_3_slot_1_label" in spec.off_canvas
    assert "session_4_slot_2_icon" in spec.off_canvas


# ── 3. Slots (FR-038, FR-017) ─────────────────────────────────────────────


def test_every_slot_drawn_is_filled_with_its_label_and_icon():
    spec = build_fill_spec(_drawing(3, "ENDURANCE", ENDURANCE_SESSIONS), _p3_template(2, 4))
    assert spec.text["session_2_slot_1_label"] == "Overcast"
    assert spec.text["session_2_slot_4_label"] == "Wet"
    assert spec.image_data["session_2_slot_3_icon"] == ("weather", "Very Wet")


def test_a_surplus_slot_leaves_by_its_group_and_reports_nothing():
    """A normal round's qualifying on a plain template removes its third and fourth."""
    spec = build_fill_spec(_drawing(3, "ENDURANCE", ENDURANCE_SESSIONS), _p3_template(2, 4))
    # Full Qualifying drew three of the four the template declares.
    assert "session_1_slot_4_group" in spec.remove
    assert "session_1_slot_4_label" in spec.off_canvas
    assert "session_1_slot_3_group" not in spec.remove


def test_the_single_slot_session_removes_the_rest():
    spec = build_fill_spec(_drawing(3, "SPRINT", SPRINT_SESSIONS), _p3_template(4, 3))
    # Sprint Race drew one of the three declared.
    assert spec.text["session_2_slot_1_label"] == "Overcast"
    assert "session_2_slot_2_group" in spec.remove
    assert "session_2_slot_3_group" in spec.remove


def test_the_summary_reaches_the_template_without_emphasis():
    spec = build_fill_spec(_drawing(3, "SPRINT", SPRINT_SESSIONS), _p3_template(4, 3))
    assert spec.text["session_3_summary"] == "Light Cloud → Wet"
    for value in spec.text.values():
        assert "*" not in value


def test_the_session_count_is_reported_for_the_capacity_check():
    spec = build_fill_spec(_drawing(3, "SPRINT", SPRINT_SESSIONS), _p3_template(4, 3))
    assert spec.row_count == 4


# ── 4. The mystery notice ─────────────────────────────────────────────────


def test_the_mystery_notice_fills_its_four_fields_and_no_more():
    body = (
        '<text id="division_name">D</text><text id="round_number">1</text>'
        '<text id="season_number">1</text><text id="division_tier">1</text>'
    )
    drawing = resolve_drawing(
        phase=1,
        division_name="Test Division",
        round_number=4,
        round_format="MYSTERY",
        division_tier=1,
        season_number=1,
    )
    spec = build_fill_spec(drawing, _svg(body))
    assert spec.text == {
        "division_name": "Test Division",
        "round_number": "4",
        "season_number": "1",
        "division_tier": "1",
    }
    assert spec.image_data == {}
    assert spec.remove == []


# ── 5. Structural faults met at generation ────────────────────────────────


def test_a_template_below_its_floor_raises_at_projection():
    """It should never get this far — the floor refuses at every validity moment — but a
    template changed since it was named must fail rather than draw short."""
    with pytest.raises(WeatherDataError):
        build_fill_spec(_drawing(3, "SPRINT", SPRINT_SESSIONS), _p3_template(4, 2))


def test_a_gap_in_the_session_numbering_raises_at_projection():
    body = HEADING + (
        '<g id="session_1_group"><text id="session_1_name">S</text>'
        '<text id="session_1_slot_type">T</text></g>'
        '<g id="session_3_group"><text id="session_3_name">S</text>'
        '<text id="session_3_slot_type">T</text></g>'
    )
    with pytest.raises(WeatherDataError):
        build_fill_spec(_drawing(2, "ENDURANCE", ENDURANCE_SESSIONS), _svg(body))
