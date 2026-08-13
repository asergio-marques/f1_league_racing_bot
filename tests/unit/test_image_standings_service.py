"""Unit tests for the standings resolution utility — T021.

Covers `resolve_drawing` for both championships:
  1. The heading fields and the lifecycle label.
  2. The classification's composition, shared with the textual standings (FR-011, FR-012).
  3. Names, through the person and team conventions.
  4. The drivers graphic drawing the team seating the driver **now** (FR-020).
  5. The three derived columns arriving finished — the utility does no arithmetic (XIV.7).

The grid is US3's slice and is tested with it; these entries carry no cells.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot
from services.image_standings_service import (
    CONSTRUCTORS_TEMPLATE_KEY,
    DRIVERS_TEMPLATE_KEY,
    resolve_drawing,
)
from services.standings_service import MOVEMENT_GAINED, MOVEMENT_UNCHANGED, Movement


def _driver(user_id: int, position: int, points: int, *, participant: bool = True):
    return DriverStandingsSnapshot(
        id=0,
        round_id=1,
        division_id=1,
        driver_user_id=user_id,
        standing_position=position,
        total_points=points,
        finish_counts={},
        first_finish_rounds={},
        race_participant=participant,
    )


def _team(role_id: int, position: int, points: int):
    return TeamStandingsSnapshot(
        id=0,
        round_id=1,
        division_id=1,
        team_role_id=role_id,
        standing_position=position,
        total_points=points,
        finish_counts={},
        first_finish_rounds={},
    )


def _drivers_drawing(**overrides):
    values = dict(
        template_key=DRIVERS_TEMPLATE_KEY,
        division_name="Division 1",
        round_number=4,
        result_status="FINAL",
        snapshots=[_driver(1, 1, 50), _driver(2, 2, 30)],
        display_names={1: "Verstappen", 2: "Hamilton"},
        team_names={1: "Apex Racing", 2: "Meridian GP"},
        movements={1: None, 2: None},
    )
    values.update(overrides)
    return resolve_drawing(**values)


# ── 1. Headings and the lifecycle label ───────────────────────────────────


def test_the_heading_fields_are_carried_through():
    drawing = _drivers_drawing(season_number=3, division_tier=1, race_name="Belgian GP")
    assert drawing.division_name == "Division 1"
    assert drawing.round_number == "4"
    assert drawing.season_number == "3"
    assert drawing.division_tier == "1"
    assert drawing.race_name == "Belgian GP"


def test_the_lifecycle_label_is_drawn_on_the_graphic():
    """XIV.16 (v4.5.0): the split with message text is not exclusive."""
    assert _drivers_drawing(result_status="FINAL").result_status_label == "Final Results"
    assert (
        _drivers_drawing(result_status="PROVISIONAL").result_status_label
        == "Provisional Results"
    )


# ── 2. Composition, shared with the textual standings ─────────────────────


def test_every_non_reserve_driver_is_drawn_including_at_zero_points():
    drawing = _drivers_drawing(
        snapshots=[_driver(1, 1, 50), _driver(2, 2, 0)],
        display_names={1: "A", 2: "B"},
        team_names={1: "T", 2: "T"},
        movements={1: None, 2: None},
    )
    assert [e.ordinal for e in drawing.entries] == [1, 2]


def test_a_reserve_with_no_points_and_no_race_is_not_drawn():
    drawing = _drivers_drawing(
        snapshots=[_driver(1, 1, 50), _driver(9, 2, 0, participant=False)],
        display_names={1: "A", 9: "R"},
        team_names={1: "T", 9: "Reserve"},
        movements={1: None, 9: None},
        reserve_user_ids={9},
        show_reserves=True,
    )
    assert [e.ordinal for e in drawing.entries] == [1]


def test_a_reserve_who_raced_is_drawn_when_the_toggle_is_on():
    drawing = _drivers_drawing(
        snapshots=[_driver(1, 1, 50), _driver(9, 2, 0, participant=True)],
        display_names={1: "A", 9: "R"},
        team_names={1: "T", 9: "Reserve"},
        movements={1: None, 9: None},
        reserve_user_ids={9},
        show_reserves=True,
    )
    assert [e.ordinal for e in drawing.entries] == [1, 2]


def test_a_reserve_is_not_drawn_when_the_toggle_is_off():
    drawing = _drivers_drawing(
        snapshots=[_driver(1, 1, 50), _driver(9, 2, 20)],
        display_names={1: "A", 9: "R"},
        team_names={1: "T", 9: "Reserve"},
        movements={1: None, 9: None},
        reserve_user_ids={9},
        show_reserves=False,
    )
    assert [e.ordinal for e in drawing.entries] == [1]


def test_every_non_reserve_team_is_drawn_including_at_zero_points():
    drawing = resolve_drawing(
        template_key=CONSTRUCTORS_TEMPLATE_KEY,
        division_name="Division 1",
        round_number=4,
        result_status="FINAL",
        snapshots=[_team(10, 1, 60), _team(20, 2, 0)],
        display_names={10: "Apex Racing", 20: "Meridian GP"},
        team_names={10: "Apex Racing", 20: "Meridian GP"},
        movements={10: None, 20: None},
    )
    assert [e.ordinal for e in drawing.entries] == [1, 2]
    assert [e.team_name for e in drawing.entries] == ["Apex Racing", "Meridian GP"]


def test_entries_are_ordered_by_the_position_the_standings_recorded():
    """The countback is already applied there; the graphic reads it (XIV.12)."""
    drawing = _drivers_drawing(
        snapshots=[_driver(2, 2, 30), _driver(1, 1, 50)],
        display_names={1: "A", 2: "B"},
        team_names={1: "T", 2: "T"},
        movements={1: None, 2: None},
    )
    assert [e.ordinal for e in drawing.entries] == [1, 2]
    assert [e.driver_name for e in drawing.entries] == ["A", "B"]


# ── 3. Names ──────────────────────────────────────────────────────────────


def test_a_driver_name_comes_from_the_person_convention():
    drawing = _drivers_drawing()
    assert [e.driver_name for e in drawing.entries] == ["Verstappen", "Hamilton"]


def test_the_drivers_graphic_draws_the_team_seating_the_driver_now():
    """FR-020: never the team whose car they drove in any one round."""
    drawing = _drivers_drawing()
    assert [e.team_name for e in drawing.entries] == ["Apex Racing", "Meridian GP"]


def test_the_constructors_graphic_carries_no_driver_name():
    drawing = resolve_drawing(
        template_key=CONSTRUCTORS_TEMPLATE_KEY,
        division_name="D",
        round_number=1,
        result_status="FINAL",
        snapshots=[_team(10, 1, 60)],
        display_names={10: "Apex Racing"},
        team_names={10: "Apex Racing"},
        movements={10: None},
    )
    assert drawing.entries[0].driver_name is None
    assert drawing.entries[0].nationality is None


def test_a_nationality_is_carried_where_one_is_recorded():
    drawing = _drivers_drawing(nationalities={1: "dutch", 2: None})
    assert drawing.entries[0].nationality == "dutch"
    assert drawing.entries[1].nationality is None


# ── 4. The derived columns arrive finished ────────────────────────────────


def test_the_movement_record_is_carried_through_untouched():
    """XIV.7: the utility receives it and performs no arithmetic of its own."""
    movement = Movement(previous_position=3, change=2, direction=MOVEMENT_GAINED)
    drawing = _drivers_drawing(movements={1: movement, 2: None})
    assert drawing.entries[0].movement is movement
    assert drawing.entries[1].movement is None


def test_an_entry_with_no_movement_record_carries_none():
    drawing = _drivers_drawing(movements={1: None, 2: None})
    assert all(e.movement is None for e in drawing.entries)


def test_the_utility_holds_no_arithmetic_over_points_or_positions():
    """A reviewer's test for the derived-columns contract.

    The movement record is the *only* source of a gap or a change, so a drawing built with
    a deliberately wrong record must carry that wrong record rather than a recomputed one.
    """
    absurd = Movement(previous_position=99, change=97, direction=MOVEMENT_UNCHANGED)
    drawing = _drivers_drawing(movements={1: absurd, 2: None}, gaps={1: 999, 2: 0})
    assert drawing.entries[0].gap_to_leader == 999
    assert drawing.entries[0].movement.change == 97


# ── 5. Configured absence ─────────────────────────────────────────────────


def test_nationality_collection_switched_off_is_carried_on_the_drawing():
    drawing = _drivers_drawing(nationality_collected=False)
    assert drawing.nationality_collected is False


def test_the_points_are_the_recorded_totals_rendered_as_text():
    drawing = _drivers_drawing()
    assert [e.points for e in drawing.entries] == ["50", "30"]


# ── 6. The position drawn agrees with the textual standings ───────────────


def test_the_recorded_position_is_carried_not_the_drawing_order():
    """XIV.7: the graphic and the table may not disagree about a value both draw.

    A reserve who raced holds a standing position. With the reserves toggle off they are
    not drawn, so the recorded positions of those below them keep their gaps — exactly as
    `format_driver_standings` prints them.
    """
    drawing = _drivers_drawing(
        snapshots=[
            _driver(1, 1, 50),
            _driver(2, 2, 40),
            _driver(9, 3, 30, participant=True),  # reserve, filtered out
            _driver(4, 4, 20),
        ],
        display_names={1: "A", 2: "B", 9: "R", 4: "D"},
        team_names={1: "T", 2: "T", 9: "Reserve", 4: "T"},
        movements={1: None, 2: None, 9: None, 4: None},
        reserve_user_ids={9},
        show_reserves=False,
    )
    assert [e.ordinal for e in drawing.entries] == [1, 2, 3]
    assert [e.position for e in drawing.entries] == ["1", "2", "4"]


def test_the_position_matches_what_the_textual_standings_print():
    """The two renderings compared directly, on the same snapshots."""
    from utils.results_formatter import format_driver_standings

    snapshots = [
        _driver(1, 1, 50),
        _driver(2, 2, 40),
        _driver(9, 3, 30, participant=True),
        _driver(4, 4, 20),
    ]
    text = format_driver_standings(snapshots, {9}, False, driver_display={
        1: "A", 2: "B", 9: "R", 4: "D"
    })
    printed = [line.split(".", 1)[0] for line in text.splitlines()]

    drawing = _drivers_drawing(
        snapshots=snapshots,
        display_names={1: "A", 2: "B", 9: "R", 4: "D"},
        team_names={1: "T", 2: "T", 9: "Reserve", 4: "T"},
        movements={1: None, 2: None, 9: None, 4: None},
        reserve_user_ids={9},
        show_reserves=False,
    )
    assert [e.position for e in drawing.entries] == printed


def test_with_the_toggle_on_the_two_still_agree():
    from utils.results_formatter import format_driver_standings

    snapshots = [_driver(1, 1, 50), _driver(9, 2, 30, participant=True)]
    names = {1: "A", 9: "R"}
    text = format_driver_standings(snapshots, {9}, True, driver_display=names)
    printed = [line.split(".", 1)[0] for line in text.splitlines()]

    drawing = _drivers_drawing(
        snapshots=snapshots,
        display_names=names,
        team_names={1: "T", 9: "Reserve"},
        movements={1: None, 9: None},
        reserve_user_ids={9},
        show_reserves=True,
    )
    assert [e.position for e in drawing.entries] == printed == ["1", "2"]
