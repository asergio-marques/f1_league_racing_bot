"""Unit tests for lineup projection onto a template — T002, T015.

Covers:
  1. Keyed and nested ids built through `normalise`.
  2. An unoccupied seat: name emptied, flag and image removed.
  3. `reserve_group` removed whole when the division fields no reserve driver.
  4. Reserve slots beyond the division's drivers treated as unoccupied seats.
  5. Reserve drivers beyond the template's slots — fatal, naming them.
  6. Asset data carrying the right class for team, flag and portrait.
  7. The binding attached to the FillSpec.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_lineup_service import (
    LineupDataError,
    build_fill_spec,
    resolve_drawing,
    suppressed_flag_fields,
)

SVG_NS = "http://www.w3.org/2000/svg"


def _template(teams: dict[str, int], reserve_slots: int = 4, *, reserve_group: bool = True):
    """Two teams of two seats plus four reserve slots, by default (T002)."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "800")
    root.set("height", "600")

    def node(parent, field_id: str, tag: str = "text"):
        child = etree.SubElement(parent, f"{{{SVG_NS}}}{tag}")
        child.set("id", field_id)
        return child

    node(root, "division_name")
    node(root, "season_number")
    node(root, "division_tier")
    for key, seats in teams.items():
        node(root, f"team_{key}_name")
        node(root, f"team_{key}_image", "image")
        for seat in range(1, seats + 1):
            node(root, f"team_{key}_driver_{seat}_name")
            node(root, f"team_{key}_driver_{seat}_flag", "image")
            node(root, f"team_{key}_driver_{seat}_image", "image")

    container = node(root, "reserve_group", "g") if reserve_group else root
    node(container, "reserve_name")
    node(container, "reserve_image", "image")
    for slot in range(1, reserve_slots + 1):
        node(container, f"reserve_driver_{slot}_name")
        node(container, f"reserve_driver_{slot}_flag", "image")
        node(container, f"reserve_driver_{slot}_image", "image")
    return root


def _seat(number: int, uid: str | None = None, **extra):
    if uid is None:
        return NS(seat_number=number, discord_user_id=None)
    values = dict(
        discord_user_id=uid,
        server_display_name=f"Driver {uid}",
        discord_username=None,
        test_display_name=None,
        nationality="British",
    )
    values.update(extra)
    return NS(seat_number=number, **values)


def _team(name: str, seats, is_reserve: bool = False):
    return NS(name=name, is_reserve=is_reserve, seats=seats)


TWO_TEAMS = {"red_bull": 2, "force_india_b": 2}


def _drawing(reserve_seats=(), **kwargs):
    return resolve_drawing(
        division_name="Elite",
        division_tier=1,
        season_number=3,
        teams=[
            _team("Red Bull", [_seat(1, "a"), _seat(2, "b")]),
            _team("Force India (B)", [_seat(1, "c"), _seat(2, "d")]),
            _team("Reserve", list(reserve_seats), True),
        ],
        **kwargs,
    )


# ── Keyed and nested ids ──────────────────────────────────────────────────


def test_keyed_ids_are_built_through_normalise():
    spec = build_fill_spec(_drawing(), _template(TWO_TEAMS))
    assert spec.text["team_red_bull_name"] == "Red Bull"
    assert spec.text["team_force_india_b_name"] == "Force India (B)"


def test_nested_seat_ids_carry_the_driver_name():
    spec = build_fill_spec(_drawing(), _template(TWO_TEAMS))
    assert spec.text["team_red_bull_driver_1_name"] == "Driver a"
    assert spec.text["team_force_india_b_driver_2_name"] == "Driver d"


def test_whole_graphic_fields_are_filled():
    spec = build_fill_spec(_drawing(), _template(TWO_TEAMS))
    assert spec.text["division_name"] == "Elite"
    assert spec.text["division_tier"] == "1"
    assert spec.text["season_number"] == "3"


def test_a_field_the_template_does_not_declare_is_not_filled():
    root = _template(TWO_TEAMS)
    for node in root.findall(f".//{{{SVG_NS}}}text[@id='season_number']"):
        node.getparent().remove(node)
    spec = build_fill_spec(_drawing(), root)
    assert "season_number" not in spec.text


# ── Unoccupied seats ──────────────────────────────────────────────────────


def test_an_unoccupied_seat_empties_its_name_and_removes_its_images():
    drawing = resolve_drawing(
        division_name="Elite",
        teams=[_team("Red Bull", [_seat(1, "a"), _seat(2)]), _team("Reserve", [], True)],
    )
    spec = build_fill_spec(drawing, _template({"red_bull": 2}))
    # `empty_quietly`, not `empty`: the value is *determined* to be empty, so it raises no
    # notice and does not offend the mandatory classification (XIV.3).
    assert "team_red_bull_driver_2_name" in spec.empty_quietly
    assert "team_red_bull_driver_2_name" not in spec.empty
    assert "team_red_bull_driver_2_flag" in spec.remove
    assert "team_red_bull_driver_2_image" in spec.remove


# ── The reserve block ─────────────────────────────────────────────────────


def test_no_reserve_driver_removes_the_group_whole():
    spec = build_fill_spec(_drawing(), _template(TWO_TEAMS))
    assert "reserve_group" in spec.remove
    # The group takes everything with it; no field is removed one by one beside it.
    assert not any(name.startswith("reserve_driver_") for name in spec.remove)


def test_without_a_group_every_reserve_field_is_removed_one_by_one():
    spec = build_fill_spec(_drawing(), _template(TWO_TEAMS, reserve_group=False))
    assert "reserve_driver_1_name" in spec.remove
    assert "reserve_name" in spec.remove


def test_reserve_slots_beyond_the_drivers_are_treated_as_unoccupied_seats():
    spec = build_fill_spec(
        _drawing(reserve_seats=[_seat(1, "r1"), _seat(2, "r2")]),
        _template(TWO_TEAMS, reserve_slots=4),
    )
    assert spec.text["reserve_driver_1_name"] == "Driver r1"
    assert spec.text["reserve_driver_2_name"] == "Driver r2"
    assert "reserve_driver_3_name" in spec.empty_quietly
    assert "reserve_driver_4_flag" in spec.remove


def test_reserve_drivers_beyond_the_slots_are_fatal_and_name_them():
    reserve = [_seat(n, f"r{n}") for n in range(1, 4)]
    with pytest.raises(LineupDataError) as exc:
        build_fill_spec(_drawing(reserve_seats=reserve), _template(TWO_TEAMS, reserve_slots=2))
    message = str(exc.value)
    assert "3 reserve drivers" in message
    assert "2 reserve slots" in message
    assert "Driver r3" in message


def test_reserve_drivers_are_drawn_by_seat_order_not_joining_order():
    reserve = [_seat(3, "late"), _seat(1, "early")]
    spec = build_fill_spec(_drawing(reserve_seats=reserve), _template(TWO_TEAMS))
    assert spec.text["reserve_driver_1_name"] == "Driver early"
    assert spec.text["reserve_driver_2_name"] == "Driver late"


# ── Assets ────────────────────────────────────────────────────────────────


def test_asset_data_carries_the_right_class_for_each_field():
    spec = build_fill_spec(
        _drawing(reserve_seats=[_seat(1, "r1")]), _template(TWO_TEAMS)
    )
    assert spec.image_data["team_red_bull_image"] == ("team", "Red Bull")
    assert spec.image_data["team_red_bull_driver_1_flag"] == ("flag", "British")
    assert spec.image_data["team_red_bull_driver_1_image"] == ("driver", "a")
    assert spec.image_data["reserve_image"] == ("team", "Reserve")


def test_a_driver_with_no_nationality_has_the_flag_field_removed():
    drawing = resolve_drawing(
        division_name="Elite",
        teams=[
            _team("Red Bull", [_seat(1, "a", nationality=None)]),
            _team("Reserve", [], True),
        ],
    )
    spec = build_fill_spec(drawing, _template({"red_bull": 1}))
    assert "team_red_bull_driver_1_flag" in spec.remove
    assert "team_red_bull_driver_1_flag" not in spec.image_data


# ── Notice suppression ────────────────────────────────────────────────────


def test_no_flag_field_is_suppressed_while_the_league_collects_nationality():
    drawing = resolve_drawing(
        division_name="Elite",
        teams=[
            _team("Red Bull", [_seat(1, "a", nationality=None)]),
            _team("Reserve", [], True),
        ],
    )
    assert suppressed_flag_fields(drawing) == set()


def test_flag_fields_are_suppressed_when_collection_is_switched_off():
    drawing = resolve_drawing(
        division_name="Elite",
        teams=[
            _team("Red Bull", [_seat(1, "a", nationality=None)]),
            _team("Reserve", [_seat(1, "r", nationality=None)], True),
        ],
        nationality_collected=False,
    )
    assert suppressed_flag_fields(drawing) == {
        "team_red_bull_driver_1_flag",
        "reserve_driver_1_flag",
    }


# ── The binding ───────────────────────────────────────────────────────────


def test_the_fill_spec_carries_the_binding():
    spec = build_fill_spec(_drawing(), _template(TWO_TEAMS))
    assert spec.binding is not None
    assert spec.binding.team_keys == ("red_bull", "force_india_b")
    assert spec.binding.seats == {"red_bull": 2, "force_india_b": 2}


def test_the_image_type_is_the_lineup():
    spec = build_fill_spec(_drawing(), _template(TWO_TEAMS))
    assert spec.image_type == "lineup_template"
