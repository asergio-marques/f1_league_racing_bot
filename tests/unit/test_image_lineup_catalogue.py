"""Unit tests for the lineup catalogue's three collection shapes — T012.

Covers:
  1. Binding-free enumeration yields only the team-independent ids.
  2. Bound enumeration adds team and seat ids.
  3. `reserve_driver_<y>_name` is mandatory for y=1 and optional beyond it.
  4. divergent_members reports in both directions, naming the team or seat.
  5. Reserve capacity is counted from the template; a gap is fatal.
  6. Asset classes resolve for keyed, nested and singleton fields alike.
  7. LineupBinding's invariants.
  8. The calendar is untouched by any of it.
"""
from __future__ import annotations

import os
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import (
    CALENDAR_CATALOGUE,
    LINEUP_CATALOGUE,
    CapacityError,
    LineupBinding,
    catalogue_for,
    declared_capacities,
)

SVG_NS = "http://www.w3.org/2000/svg"


def _template(teams: dict[str, int], reserve_slots: int = 3, *, division: bool = True):
    """A lineup template declaring *teams* (key → seats) and *reserve_slots* slots."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "800")
    root.set("height", "600")

    def node(field_id: str) -> None:
        child = etree.SubElement(root, f"{{{SVG_NS}}}text")
        child.set("id", field_id)

    if division:
        node("division_name")
    for key, seats in teams.items():
        node(f"team_{key}_name")
        node(f"team_{key}_image")
        for seat in range(1, seats + 1):
            node(f"team_{key}_driver_{seat}_name")
            node(f"team_{key}_driver_{seat}_flag")
            node(f"team_{key}_driver_{seat}_image")
    node("reserve_group")
    node("reserve_name")
    for slot in range(1, reserve_slots + 1):
        node(f"reserve_driver_{slot}_name")
        node(f"reserve_driver_{slot}_flag")
        node(f"reserve_driver_{slot}_image")
    return root


TEAMS = {"red_bull": 2, "force_india_b": 1}
BINDING = LineupBinding(team_keys=("red_bull", "force_india_b"), seats=TEAMS)


# ── Enumeration ───────────────────────────────────────────────────────────


def test_binding_free_enumeration_yields_only_team_independent_ids():
    """The correct answer at a moment holding no division — not a degraded one."""
    root = _template(TEAMS)
    ids = LINEUP_CATALOGUE.all_mandatory_ids(root)
    assert ids == {"division_name", "reserve_group", "reserve_driver_1_name"}


def test_bound_enumeration_adds_team_and_seat_ids():
    root = _template(TEAMS)
    ids = LINEUP_CATALOGUE.all_mandatory_ids(root, BINDING)
    assert "team_red_bull_name" in ids
    assert "team_red_bull_driver_2_name" in ids
    assert "team_force_india_b_driver_1_name" in ids
    # Optional fields never appear among the mandatory.
    assert "team_red_bull_image" not in ids
    assert "team_red_bull_driver_1_flag" not in ids


def test_a_team_with_no_seats_contributes_no_seat_ids():
    binding = LineupBinding(team_keys=("red_bull",), seats={"red_bull": 0})
    ids = LINEUP_CATALOGUE.all_mandatory_ids(_template({"red_bull": 0}), binding)
    assert "team_red_bull_name" in ids
    assert not any(name.startswith("team_red_bull_driver_") for name in ids)


def test_reserve_seat_one_is_mandatory_and_the_rest_are_not():
    """XIV.3, v4.3.0 — a classification varying by member, declared by a rule."""
    ids = LINEUP_CATALOGUE.all_mandatory_ids(_template(TEAMS, reserve_slots=4))
    assert "reserve_driver_1_name" in ids
    assert "reserve_driver_2_name" not in ids
    assert "reserve_driver_4_name" not in ids


def test_reserve_seat_one_is_required_even_when_the_template_declares_no_slot():
    """A template declaring reserve_group but no slot must still be rejected."""
    root = _template(TEAMS, reserve_slots=0)
    assert "reserve_driver_1_name" in LINEUP_CATALOGUE.all_mandatory_ids(root)


def test_all_known_ids_covers_optional_team_and_seat_fields():
    known = LINEUP_CATALOGUE.all_known_ids(_template(TEAMS), BINDING)
    assert {"team_red_bull_image", "team_red_bull_group"} <= known
    assert "team_force_india_b_driver_1_flag" in known
    assert {"season_number", "division_tier"} <= known


# ── Divergence, in both directions ────────────────────────────────────────


def test_no_divergence_when_template_and_binding_agree():
    assert LINEUP_CATALOGUE.divergent_members(_template(TEAMS), BINDING) == []


def test_template_declares_a_team_the_division_does_not_field():
    root = _template({**TEAMS, "mercedes": 2})
    problems = LINEUP_CATALOGUE.divergent_members(root, BINDING)
    assert len(problems) == 1
    assert "mercedes" in problems[0]


def test_division_fields_a_team_the_template_does_not_declare():
    root = _template({"red_bull": 2})
    problems = LINEUP_CATALOGUE.divergent_members(root, BINDING)
    assert len(problems) == 1
    assert "force_india_b" in problems[0]


def test_template_declares_a_seat_beyond_the_teams_configured_count():
    root = _template({"red_bull": 3, "force_india_b": 1})
    problems = LINEUP_CATALOGUE.divergent_members(root, BINDING)
    assert any("team_red_bull_driver_3_name" in p for p in problems)


def test_division_holds_a_seat_the_template_does_not_declare():
    root = _template({"red_bull": 1, "force_india_b": 1})
    problems = LINEUP_CATALOGUE.divergent_members(root, BINDING)
    assert any("team_red_bull_driver_2_name" in p for p in problems)


def test_no_binding_means_no_divergence_reported():
    """A moment holding no division cannot find a divergence."""
    assert LINEUP_CATALOGUE.divergent_members(_template({"mercedes": 2}), None) == []


def test_a_key_holding_underscores_is_read_back_correctly():
    """`force_india_b` must not be read as `force` with suffix `india_b`."""
    keys = LINEUP_CATALOGUE.keyed.declared_keys(
        {"team_force_india_b_name", "team_force_india_b_driver_1_flag"}
    )
    assert keys == {"force_india_b"}


# ── Reserve capacity ──────────────────────────────────────────────────────


def test_reserve_capacity_is_counted_from_the_template():
    assert LINEUP_CATALOGUE.capacity(_template(TEAMS, reserve_slots=5)) == 5


def test_reserve_capacity_is_unknown_without_a_template():
    assert LINEUP_CATALOGUE.capacity(None) is None


def test_a_gap_in_the_reserve_numbering_is_fatal():
    root = _template(TEAMS, reserve_slots=0)
    for slot in (1, 3):
        node = etree.SubElement(root, f"{{{SVG_NS}}}text")
        node.set("id", f"reserve_driver_{slot}_name")
    with pytest.raises(CapacityError) as exc:
        LINEUP_CATALOGUE.capacity(root)
    assert "gap" in str(exc.value)


# ── Asset classes ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field_id,expected",
    [
        ("team_red_bull_image", "team"),
        ("team_force_india_b_driver_1_flag", "flag"),
        ("team_red_bull_driver_2_image", "driver"),
        ("reserve_image", "team"),
        ("reserve_driver_1_flag", "flag"),
        ("reserve_driver_3_image", "driver"),
        ("division_name", None),
        ("team_red_bull_name", None),
    ],
)
def test_asset_class_resolution(field_id, expected):
    assert LINEUP_CATALOGUE.asset_class_for(field_id) == expected


# ── The binding's invariants ──────────────────────────────────────────────


def test_duplicate_keys_cannot_form_a_binding():
    with pytest.raises(ValueError, match="same key"):
        LineupBinding(team_keys=("red_bull", "red_bull"))


def test_the_reserved_key_cannot_be_a_team():
    with pytest.raises(ValueError, match="reserved"):
        LineupBinding(team_keys=("reserve",))


def test_seats_for_an_unbound_team_are_refused():
    with pytest.raises(ValueError, match="not in the binding"):
        LineupBinding(team_keys=("red_bull",), seats={"mercedes": 2})


def test_an_empty_binding_is_not_the_same_as_no_binding():
    """An empty binding means a division fielding no team; None means no division."""
    root = _template(TEAMS)
    assert LINEUP_CATALOGUE.divergent_members(root, LineupBinding()) != []
    assert LINEUP_CATALOGUE.divergent_members(root, None) == []


# ── The calendar is untouched ─────────────────────────────────────────────


def test_the_lineup_is_registered_and_not_empty():
    assert catalogue_for("lineup_template") is LINEUP_CATALOGUE
    assert not LINEUP_CATALOGUE.is_empty


def test_the_calendar_declares_no_keyed_or_singleton_collection():
    assert CALENDAR_CATALOGUE.keyed is None
    assert CALENDAR_CATALOGUE.singleton is None


def test_declared_capacities_still_excludes_the_lineup():
    """T013 — the placement guard counts seated drivers and must not see reserve slots."""
    assert "lineup_template" not in declared_capacities()


def test_the_last_unspecified_type_was_specified_at_043():
    # The six weather types were specified at 042 and the verdict at 043, which was the
    # last: every template column now carries a catalogue.
    assert not catalogue_for("verdicts_template").is_empty
