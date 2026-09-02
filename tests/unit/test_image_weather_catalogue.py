"""The six weather field catalogues — T008, T009, T056.

Written against specs/042-weather-image-generation/contracts/weather-catalogues.md and
Constitution XIV.3, XIV.10, XIV.11 and XIV.12.

Weather is the module's most divided aspect: six templates serve one toggle, across three
phases, two round-format variants and a kind of round that runs no phase at all.
"""
from __future__ import annotations

import os
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import (  # noqa: E402
    CATALOGUES,
    CapacityError,
    _canonical,
    catalogue_for,
    sibling_fields_declared,
    sibling_keys,
)
from models.image_constants import ASPECT_TEMPLATES  # noqa: E402
from models.session import MAX_SLOTS, SESSIONS_BY_FORMAT  # noqa: E402
from models.round import RoundFormat  # noqa: E402
from utils.svg_document import FieldIndex  # noqa: E402

WEATHER_KEYS = (
    "weather_p1_template",
    "weather_p2_template",
    "weather_p2_sprint_template",
    "weather_p3_template",
    "weather_p3_sprint_template",
    "weather_mystery_template",
)

HEADING = (
    '<text id="division_name">D</text>'
    '<text id="phase_description">P</text>'
    '<text id="round_number">1</text>'
    '<text id="race_name">R</text>'
)


def _svg(body: str) -> etree._Element:
    return etree.fromstring(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        f"{body}</svg>".encode()
    )


def _p2(sessions: int, *, extra: str = "") -> etree._Element:
    blocks = "".join(
        f'<g id="session_{n}_group">'
        f'<text id="session_{n}_name">S</text>'
        f'<text id="session_{n}_slot_type">Mixed</text>'
        f"</g>"
        for n in range(1, sessions + 1)
    )
    return _svg(HEADING + blocks + extra)


def _p3(sessions: int, slots: int, *, slots_for_first: int | None = None) -> etree._Element:
    blocks = ""
    for n in range(1, sessions + 1):
        count = slots_for_first if (n == 1 and slots_for_first is not None) else slots
        cells = "".join(
            f'<g id="session_{n}_slot_{m}_group">'
            f'<text id="session_{n}_slot_{m}_label">Clear</text>'
            f"</g>"
            for m in range(1, count + 1)
        )
        blocks += f'<g id="session_{n}_group"><text id="session_{n}_name">S</text>{cells}</g>'
    return _svg(HEADING + blocks)


# ── 1. The six catalogues exist and are separately addressable ────────────


def test_all_six_weather_catalogues_are_populated():
    for key in WEATHER_KEYS:
        assert not catalogue_for(key).is_empty, key
    assert set(ASPECT_TEMPLATES["weather"]) == set(WEATHER_KEYS)


def test_every_phase_catalogue_carries_the_heading_fields():
    """Four mandatory headings on each of the three phases (FR-003)."""
    for key in WEATHER_KEYS:
        if key == "weather_mystery_template":
            continue
        mandatory = catalogue_for(key).mandatory
        assert {"division_name", "phase_description", "round_number", "race_name"} <= mandatory


def test_the_grand_prix_name_is_mandatory_and_the_circuit_name_optional():
    """2026-09-01 — the classification was the reverse, and was wrong that way round.

    A forecast was obliged to name the tarmac and free to omit the race. The grand prix is
    what identifies a round to a league, and a circuit hosting two of them in a season
    identifies neither on its own. The check-in call and the results sheets already ranked
    the two this way.

    A league's weather template that names only the circuit is refused from here, when the
    file is named and again at season review.
    """
    for key in WEATHER_KEYS:
        if key == "weather_mystery_template":
            continue
        cat = catalogue_for(key)
        assert "race_name" in cat.mandatory, key
        assert "track_name" not in cat.mandatory, key
        assert "track_name" in cat.optional, key
        assert "track_name_group" in cat.optional, key
        # XIV.2 — a group may wrap a field of either classification.
        assert "race_name_group" in cat.optional, key


def test_rain_probability_is_mandatory_on_phase_one_alone():
    """FR-004 — phase 1's subject *is* the likelihood; the later phases carry it forward."""
    assert "rain_probability" in catalogue_for("weather_p1_template").mandatory
    for key in ("weather_p2_template", "weather_p2_sprint_template",
                "weather_p3_template", "weather_p3_sprint_template"):
        cat = catalogue_for(key)
        assert "rain_probability" not in cat.mandatory, key
        assert "rain_probability" in cat.optional, key


def test_phase_one_declares_no_session():
    """FR-005 — the phase 1 template holds no field beyond the headings."""
    cat = catalogue_for("weather_p1_template")
    assert cat.rows is None
    assert cat.columns is None


def test_the_mystery_notice_declares_four_fields_and_nothing_else():
    """FR-006 — it says a forecast is not coming, and has nothing else to say."""
    cat = catalogue_for("weather_mystery_template")
    assert cat.mandatory == {"division_name", "round_number"}
    assert cat.optional == {
        "season_number",
        "season_number_group",
        "division_tier",
        "division_tier_group",
    }
    assert cat.rows is None and cat.columns is None
    assert cat.assets == {}
    # No track, no grand prix, no country, no rain likelihood, no session, no slot.
    everything = cat.mandatory | cat.optional
    for absent in (
        "track_name",
        "race_name",
        "country_name",
        "track_image",
        "rain_probability",
        "phase_description",
    ):
        assert absent not in everything, absent


def test_the_weather_icon_class_is_named_for_every_icon_field():
    """FR-028 — both icon families resolve in the configured weather icon directory."""
    p2 = catalogue_for("weather_p2_template")
    assert p2.rows.assets == {"slot_type_icon": "weather"}
    p3 = catalogue_for("weather_p3_template")
    assert p3.rows.assets == {"slot_type_icon": "weather"}
    assert p3.rows.nested.assets == {"icon": "weather"}


def test_the_session_type_is_optional_on_phase_three():
    """Phase 3's subject is the sequence; a template need not restate phase 2's draw."""
    assert "slot_type" in catalogue_for("weather_p2_template").rows.mandatory_fields
    assert "slot_type" not in catalogue_for("weather_p3_template").rows.mandatory_fields
    assert "slot_type" in catalogue_for("weather_p3_template").rows.fields


# ── 2. The floors, derived and not written ────────────────────────────────


def test_the_floors_are_the_greatest_the_served_formats_demand():
    """FR-015 — derived from the weather module's own constants, never as literals."""
    sprint_types = SESSIONS_BY_FORMAT[RoundFormat.SPRINT]
    plain_types = (
        SESSIONS_BY_FORMAT[RoundFormat.NORMAL] + SESSIONS_BY_FORMAT[RoundFormat.ENDURANCE]
    )

    assert catalogue_for("weather_p2_sprint_template").rows.minimum == len(sprint_types)
    assert catalogue_for("weather_p2_template").rows.minimum == len(
        SESSIONS_BY_FORMAT[RoundFormat.NORMAL]
    )
    assert catalogue_for("weather_p3_sprint_template").rows.nested.minimum == max(
        MAX_SLOTS[t] for t in sprint_types
    )
    assert catalogue_for("weather_p3_template").rows.nested.minimum == max(
        MAX_SLOTS[t] for t in plain_types
    )


def test_the_sprint_slot_floor_is_three_not_two():
    """The wip-spec said two; the Long Feature Race allows three (author, 2026-08-13)."""
    assert MAX_SLOTS[RoundFormat and SESSIONS_BY_FORMAT[RoundFormat.SPRINT][-1]] == 3
    assert catalogue_for("weather_p3_sprint_template").rows.nested.minimum == 3
    assert catalogue_for("weather_p3_template").rows.nested.minimum == 4


def test_a_template_below_the_session_floor_is_refused():
    """FR-016 — and the message names the count declared and the count required."""
    cat = catalogue_for("weather_p2_sprint_template")
    with pytest.raises(CapacityError) as exc:
        cat.capacity(_p2(3))
    message = str(exc.value)
    assert "3" in message and "4" in message
    assert "session" in message


def test_a_template_below_the_slot_floor_is_refused():
    cat = catalogue_for("weather_p3_sprint_template")
    with pytest.raises(CapacityError) as exc:
        cat.capacity(_p3(4, 2))
    message = str(exc.value)
    assert "slot" in message
    assert "2" in message and "3" in message


def test_the_slot_floor_binds_every_session_not_merely_the_first():
    """A template generous in its first session and short in its second is still refused."""
    cat = catalogue_for("weather_p3_template")
    with pytest.raises(CapacityError):
        cat.capacity(_p3(2, 2, slots_for_first=4))


def test_over_declaring_is_accepted_and_never_a_divergence():
    """FR-017 — the floor is a lower bound and never an upper one."""
    cat = catalogue_for("weather_p2_sprint_template")
    assert cat.capacity(_p2(6)) == 6

    p3 = catalogue_for("weather_p3_sprint_template")
    assert p3.capacity(_p3(5, 5)) == 5


def test_a_gap_in_the_session_numbering_is_refused():
    """FR-018 — XIV.11 requires contiguity from 1."""
    body = HEADING + (
        '<g id="session_1_group"><text id="session_1_name">S</text>'
        '<text id="session_1_slot_type">Mixed</text></g>'
        '<g id="session_3_group"><text id="session_3_name">S</text>'
        '<text id="session_3_slot_type">Mixed</text></g>'
    )
    with pytest.raises(CapacityError) as exc:
        catalogue_for("weather_p2_template").capacity(_svg(body))
    assert "gap" in str(exc.value)


def test_a_gap_in_the_slot_numbering_is_refused():
    cells = "".join(
        f'<g id="session_1_slot_{m}_group">'
        f'<text id="session_1_slot_{m}_label">Clear</text></g>'
        for m in (1, 2, 4, 5)
    )
    body = HEADING + (
        f'<g id="session_1_group"><text id="session_1_name">S</text>{cells}</g>'
        '<g id="session_2_group"><text id="session_2_name">S</text></g>'
    )
    with pytest.raises(CapacityError) as exc:
        catalogue_for("weather_p3_template").capacity(_svg(body))
    assert "gap" in str(exc.value)


def test_the_slot_floor_leaves_every_other_image_type_alone():
    """The new strictness is confined to a nest that declares a floor.

    Every catalogue written before 042 leaves ``minimum`` unset, so a nest of theirs keeps
    exactly the behaviour it had — this feature does not quietly tighten the attendance
    grid or the lineup's seats.
    """
    for key, cat in CATALOGUES.items():
        if key.startswith("weather_"):
            continue
        if cat.rows is not None:
            assert cat.rows.minimum is None, key
            if cat.rows.nested is not None:
                assert cat.rows.nested.minimum is None, key


# ── 3. The two meanings of `slot` (FR-009) ────────────────────────────────


def test_the_session_type_field_cannot_be_counted_as_a_slot():
    """`session_1_slot_type` is a field of the session, not a member of its slots.

    The two are told apart by this catalogue and never by parsing an id (XIV.11, v4.7.0).
    This holds mechanically: the nest matches `_slot_(\\d+)` and `type` is not a number.
    """
    nested = catalogue_for("weather_p3_template").rows.nested
    declared = {
        "session_1_slot_type",
        "session_1_slot_type_icon",
        *(
            f"session_1_slot_{m}_{suffix}"
            for m in range(1, 5)
            for suffix in ("group", "label")
        ),
    }
    # Four slots are declared, not six: the two `slot_type` ids are fields of the session.
    assert nested.declared_capacity("session_1", declared) == 4


def test_the_session_type_field_does_count_as_a_field_of_its_session():
    rows = catalogue_for("weather_p2_template").rows
    root = _p2(2)
    assert rows.declared_capacity(root) == 2
    assert "session_1_slot_type" in rows.all_field_ids(root)


def test_the_two_slot_ids_canonicalise_differently():
    assert _canonical("session_1_slot_type") == "session_#_slot_type"
    assert _canonical("session_1_slot_1_label") == "session_#_slot_#_label"
    assert _canonical("session_1_slot_type") != _canonical("session_1_slot_1_label")


# ── 4. Siblings (FR-002) ──────────────────────────────────────────────────


def test_each_weather_type_is_a_sibling_of_the_other_five():
    """XIV.3 has named "the six forecasts" since it was written — no code changed here."""
    for key in WEATHER_KEYS:
        assert set(sibling_keys(key)) == set(WEATHER_KEYS) - {key}, key


def test_a_phase_two_template_declaring_a_slot_field_is_the_wrong_file():
    """The named instance of FR-002: the fields of a slot on a phase 2 template."""
    declared = FieldIndex(
        _p2(2, extra='<text id="session_1_slot_1_label">Clear</text>')
    ).declared()
    foreign = sibling_fields_declared("weather_p2_sprint_template", declared)
    assert "session_1_slot_1_label" in foreign


def test_a_phase_two_template_declaring_a_summary_is_the_wrong_file():
    declared = FieldIndex(
        _p2(2, extra='<text id="session_1_summary">clear, then wet</text>')
    ).declared()
    assert "session_1_summary" in sibling_fields_declared("weather_p2_template", declared)


def test_a_sound_phase_two_template_carries_no_foreign_field():
    declared = FieldIndex(_p2(4)).declared()
    assert sibling_fields_declared("weather_p2_sprint_template", declared) == []


def test_a_sound_phase_three_template_carries_no_foreign_field():
    declared = FieldIndex(_p3(2, 4)).declared()
    assert sibling_fields_declared("weather_p3_template", declared) == []


def test_a_weather_template_is_not_a_sibling_of_another_module_s_type():
    for key in WEATHER_KEYS:
        siblings = set(sibling_keys(key))
        assert "calendar_template" not in siblings
        assert "attendance_template" not in siblings
        assert "results_race_template" not in siblings


# ── 5. The shipped defaults satisfy their own catalogues ──────────────────


@pytest.mark.parametrize("key", WEATHER_KEYS)
def test_the_shipped_template_satisfies_its_catalogue(key):
    """Every default that ships must pass the check a league's own file would face."""
    from models.image_constants import TEMPLATE_COLUMNS

    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "resources", "defaults", "templates",
        TEMPLATE_COLUMNS[key],
    )
    root = etree.parse(path).getroot()
    catalogue = catalogue_for(key)
    mandatory = catalogue.all_mandatory_ids(root)
    index = FieldIndex(root)
    missing = sorted(name for name in mandatory if index.resolve(name) is None)
    assert missing == [], f"{key} is missing {missing}"
    assert sibling_fields_declared(key, index.declared()) == []
