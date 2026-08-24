"""Unit tests for projecting a results drawing onto a template — T017.

Covers :func:`build_fill_spec`: what is filled, what is emptied quietly, what leaves the
canvas, and the single recolour. The values themselves are settled in
``test_image_results_service.py`` and in ``test_results_formatter.py``.
"""
from __future__ import annotations

import os
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_results_service import (
    QUALIFYING_TEMPLATE_KEY,
    RACE_TEMPLATE_KEY,
    FastestLapBlock,
    ResultsDataError,
    ResultsDrawing,
    ResultsEntry,
    build_fill_spec,
)

SVG_NS = "http://www.w3.org/2000/svg"

_WHOLE_GRAPHIC = (
    "division_name",
    "round_number",
    "race_name",
    "session_name",
    "result_status",
    "season_number",
    "division_tier",
)

_QUALIFYING_ROW = (
    "group",
    "position",
    "driver_name",
    "driver_flag",
    "team_name",
    "team_image",
    "postrace_penalty",
    "appeal_penalty",
    "points",
    "tyre",
    "best_lap",
    "gap",
)

_RACE_ROW = (
    "group",
    "position",
    "driver_name",
    "driver_flag",
    "team_name",
    "team_image",
    "postrace_penalty",
    "appeal_penalty",
    "points",
    "time",
    "fastest_lap",
    "ingame_penalty",
)


def _template(rows: int, suffixes, *extra: str) -> etree._Element:
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "1600")
    root.set("height", "2400")

    def add(name: str) -> None:
        child = etree.SubElement(root, f"{{{SVG_NS}}}text")
        child.set("id", name)

    for name in _WHOLE_GRAPHIC:
        add(name)
    for name in extra:
        add(name)
    for index in range(1, rows + 1):
        for suffix in suffixes:
            add(f"row_{index}_{suffix}")
    return root


def _entry(ordinal: int, **overrides) -> ResultsEntry:
    base = dict(
        ordinal=ordinal,
        driver_name=f"Driver {ordinal}",
        team_name=f"Team {ordinal}",
        points="25",
        postrace_penalty="—",
        appeal_penalty="—",
        nationality="British",
        tyre="Soft",
        best_lap="1:23.456",
        gap="+1.000",
        time="1:23:45.678",
        fastest_lap="1:23.456",
        ingame_penalty="—",
    )
    base.update(overrides)
    return ResultsEntry(**base)


def _drawing(template_key: str, entries, **overrides) -> ResultsDrawing:
    base = dict(
        template_key=template_key,
        division_name="Division One",
        round_number="3",
        race_name="British Grand Prix",
        session_name="Qualifying",
        result_status_label="Final Results",
        penalty_phase_closed=True,
        appeal_phase_closed=True,
        division_tier="1",
        season_number="4",
        entries=list(entries),
    )
    base.update(overrides)
    return ResultsDrawing(**base)


# ── Whole-graphic fields ──────────────────────────────────────────────────


def test_the_whole_graphic_fields_are_filled():
    root = _template(2, _QUALIFYING_ROW)
    spec = build_fill_spec(_drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1)]), root)
    assert spec.text["division_name"] == "Division One"
    assert spec.text["round_number"] == "3"
    assert spec.text["race_name"] == "British Grand Prix"
    assert spec.text["session_name"] == "Qualifying"
    assert spec.text["result_status"] == "Final Results"
    assert spec.text["season_number"] == "4"
    assert spec.text["division_tier"] == "1"


def test_a_field_the_template_does_not_declare_is_not_addressed():
    root = _template(1, _QUALIFYING_ROW)
    for child in list(root):
        if child.get("id") == "season_number":
            root.remove(child)
    spec = build_fill_spec(_drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1)]), root)
    assert "season_number" not in spec.text
    assert "season_number" not in spec.empty


# ── The row collection ────────────────────────────────────────────────────


def test_the_position_is_filled_from_the_rows_own_ordinal():
    root = _template(3, _QUALIFYING_ROW)
    spec = build_fill_spec(
        _drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1), _entry(2)]), root
    )
    assert spec.text["row_1_position"] == "1"
    assert spec.text["row_2_position"] == "2"


def test_rows_beyond_the_entries_leave_by_their_group():
    root = _template(4, _QUALIFYING_ROW)
    spec = build_fill_spec(
        _drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1), _entry(2)]), root
    )
    assert "row_3_group" in spec.remove
    assert "row_4_group" in spec.remove
    assert "row_2_group" not in spec.remove


def test_every_field_of_an_undrawn_row_is_off_the_canvas():
    """A field a group removal took off the canvas is not a field left unfilled (XIV.3)."""
    root = _template(3, _QUALIFYING_ROW)
    spec = build_fill_spec(_drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1)]), root)
    assert "row_3_points" in spec.off_canvas
    assert "row_3_driver_name" in spec.off_canvas
    assert "row_1_points" not in spec.off_canvas


def test_the_entry_count_is_reported_for_the_capacity_check():
    root = _template(2, _QUALIFYING_ROW)
    drawing = _drawing(QUALIFYING_TEMPLATE_KEY, [_entry(n) for n in range(1, 6)])
    spec = build_fill_spec(drawing, root)
    assert spec.row_count == 5  # more than the two the template declares — a problem


def test_a_template_declaring_no_row_is_fatal():
    root = _template(0, _QUALIFYING_ROW)
    with pytest.raises(ResultsDataError, match="declares no `row` at all"):
        build_fill_spec(_drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1)]), root)


def test_a_gap_in_the_row_numbering_is_fatal():
    root = _template(2, _QUALIFYING_ROW)
    child = etree.SubElement(root, f"{{{SVG_NS}}}text")
    child.set("id", "row_4_points")
    with pytest.raises(ResultsDataError, match="has a gap"):
        build_fill_spec(_drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1)]), root)


# ── Determined-empty cells ────────────────────────────────────────────────


def test_a_cell_that_does_not_apply_is_emptied_quietly():
    """Determined to be nothing, so no notice and no mandatory field offended (XIV.3)."""
    root = _template(1, _QUALIFYING_ROW)
    spec = build_fill_spec(
        _drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1, gap=None)]), root
    )
    assert "row_1_gap" in spec.empty_quietly
    assert "row_1_gap" not in spec.empty


def test_an_open_phase_empties_its_sanction_cells_quietly():
    root = _template(2, _QUALIFYING_ROW)
    drawing = _drawing(
        QUALIFYING_TEMPLATE_KEY,
        [_entry(1, postrace_penalty=None, appeal_penalty=None)],
        penalty_phase_closed=False,
        appeal_phase_closed=False,
    )
    spec = build_fill_spec(drawing, root)
    assert "row_1_postrace_penalty" in spec.empty_quietly
    assert "row_1_appeal_penalty" in spec.empty_quietly


# ── Column groups ─────────────────────────────────────────────────────────


def test_an_open_phase_removes_its_column_group():
    root = _template(1, _QUALIFYING_ROW, "postrace_penalty_group", "appeal_penalty_group")
    drawing = _drawing(
        QUALIFYING_TEMPLATE_KEY,
        [_entry(1)],
        penalty_phase_closed=False,
        appeal_phase_closed=False,
    )
    spec = build_fill_spec(drawing, root)
    assert "postrace_penalty_group" in spec.remove
    assert "appeal_penalty_group" in spec.remove


def test_a_closed_penalty_phase_keeps_its_heading_and_the_open_appeal_loses_its_own():
    root = _template(1, _QUALIFYING_ROW, "postrace_penalty_group", "appeal_penalty_group")
    drawing = _drawing(
        QUALIFYING_TEMPLATE_KEY,
        [_entry(1)],
        penalty_phase_closed=True,
        appeal_phase_closed=False,
    )
    spec = build_fill_spec(drawing, root)
    assert "postrace_penalty_group" not in spec.remove
    assert "appeal_penalty_group" in spec.remove


def test_a_template_declaring_no_column_group_carries_its_heading_over_an_empty_column():
    """Meant, and not a fault — the heading is chrome the template draws."""
    root = _template(1, _QUALIFYING_ROW)
    drawing = _drawing(
        QUALIFYING_TEMPLATE_KEY, [_entry(1)], penalty_phase_closed=False
    )
    spec = build_fill_spec(drawing, root)
    assert spec.remove == []


# ── Assets ────────────────────────────────────────────────────────────────


def test_the_team_image_is_resolved_from_the_team_name():
    root = _template(1, _QUALIFYING_ROW)
    spec = build_fill_spec(_drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1)]), root)
    assert spec.image_data["row_1_team_image"] == ("team", "Team 1")


def test_a_recorded_nationality_resolves_a_flag():
    root = _template(1, _QUALIFYING_ROW)
    spec = build_fill_spec(_drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1)]), root)
    assert spec.image_data["row_1_driver_flag"] == ("flag", "United Kingdom")


def test_an_absent_nationality_the_league_collects_is_an_ordinary_emptied_field():
    root = _template(1, _QUALIFYING_ROW)
    spec = build_fill_spec(
        _drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1, nationality=None)]), root
    )
    assert "row_1_driver_flag" in spec.empty
    assert "row_1_driver_flag" not in spec.image_data


def test_nationality_switched_off_at_its_source_raises_nothing():
    root = _template(1, _QUALIFYING_ROW)
    drawing = _drawing(
        QUALIFYING_TEMPLATE_KEY,
        [_entry(1, nationality=None)],
        nationality_collected=False,
    )
    spec = build_fill_spec(drawing, root)
    assert "row_1_driver_flag" in spec.empty_quietly
    assert "row_1_driver_flag" not in spec.empty


def test_the_switch_beats_a_nationality_the_driver_already_stated():
    """The switch is read before the value, so no driver keeps a flag the others lost."""
    root = _template(1, _QUALIFYING_ROW)
    drawing = _drawing(
        QUALIFYING_TEMPLATE_KEY,
        [_entry(1, nationality="British")],
        nationality_collected=False,
    )
    spec = build_fill_spec(drawing, root)
    assert "row_1_driver_flag" not in spec.image_data
    assert "row_1_driver_flag" in spec.empty_quietly


def test_a_tyre_is_always_offered_to_the_resolver():
    """An absent compound draws the class fallback, which the catalogue declares."""
    root = _template(1, _QUALIFYING_ROW)
    spec = build_fill_spec(
        _drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1, tyre=None)]), root
    )
    assert spec.image_data["row_1_tyre"] == ("tyre", "")


def test_the_catalogue_travels_with_the_spec():
    root = _template(1, _RACE_ROW)
    spec = build_fill_spec(_drawing(RACE_TEMPLATE_KEY, [_entry(1)]), root)
    assert spec.catalogue is not None
    assert spec.image_type == RACE_TEMPLATE_KEY


# ── The recolour and the fastest-lap block ────────────────────────────────


def test_exactly_the_holder_of_the_bonus_is_recoloured():
    root = _template(3, _RACE_ROW)
    drawing = _drawing(
        RACE_TEMPLATE_KEY,
        [_entry(1), _entry(2, holds_fastest_lap=True), _entry(3)],
        fastest_lap_colour="#A020F0",
        fastest_lap=FastestLapBlock(driver_name="Driver 2", lap_time="1:21.000"),
    )
    spec = build_fill_spec(drawing, root)
    assert spec.recolour == {"row_2_fastest_lap": "#A020F0"}


def test_a_recoloured_field_is_still_filled():
    """A recolour does not consume the field (XIV.2)."""
    root = _template(2, _RACE_ROW)
    drawing = _drawing(
        RACE_TEMPLATE_KEY,
        [_entry(1, holds_fastest_lap=True)],
        fastest_lap_colour="#A020F0",
        fastest_lap=FastestLapBlock(driver_name="Driver 1", lap_time="1:23.456"),
    )
    spec = build_fill_spec(drawing, root)
    assert spec.text["row_1_fastest_lap"] == "1:23.456"
    assert "row_1_fastest_lap" in spec.recolour


def test_no_row_is_recoloured_where_the_session_conferred_no_bonus():
    root = _template(2, _RACE_ROW)
    drawing = _drawing(
        RACE_TEMPLATE_KEY, [_entry(1), _entry(2)], fastest_lap_colour="#A020F0"
    )
    spec = build_fill_spec(drawing, root)
    assert spec.recolour == {}


def test_the_block_is_filled_where_the_bonus_was_conferred():
    root = _template(
        1, _RACE_ROW, "fastest_lap_group", "fastest_lap_driver_name", "fastest_lap_time"
    )
    drawing = _drawing(
        RACE_TEMPLATE_KEY,
        [_entry(1)],
        fastest_lap=FastestLapBlock(driver_name="Driver 9", lap_time="1:21.000"),
    )
    spec = build_fill_spec(drawing, root)
    assert spec.text["fastest_lap_driver_name"] == "Driver 9"
    assert spec.text["fastest_lap_time"] == "1:21.000"
    assert "fastest_lap_group" not in spec.remove


def test_the_block_group_leaves_whole_where_no_bonus_was_conferred():
    root = _template(
        1, _RACE_ROW, "fastest_lap_group", "fastest_lap_driver_name", "fastest_lap_time"
    )
    spec = build_fill_spec(_drawing(RACE_TEMPLATE_KEY, [_entry(1)]), root)
    assert "fastest_lap_group" in spec.remove
    assert "fastest_lap_driver_name" in spec.off_canvas
    assert "fastest_lap_time" in spec.off_canvas


def test_without_a_block_group_the_block_fields_are_emptied_instead():
    root = _template(1, _RACE_ROW, "fastest_lap_driver_name", "fastest_lap_time")
    spec = build_fill_spec(_drawing(RACE_TEMPLATE_KEY, [_entry(1)]), root)
    assert "fastest_lap_driver_name" in spec.empty_quietly
    assert "fastest_lap_time" in spec.empty_quietly


def test_a_qualifying_graphic_addresses_no_block_and_no_race_column():
    root = _template(1, _QUALIFYING_ROW)
    spec = build_fill_spec(_drawing(QUALIFYING_TEMPLATE_KEY, [_entry(1)]), root)
    assert "fastest_lap_driver_name" not in spec.text
    assert "row_1_time" not in spec.text
    assert spec.text["row_1_best_lap"] == "1:23.456"


# ---------------------------------------------------------------------------
# The fabricated session `/images test results` draws — T018
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from tests.support.image_sample_data import build_results_drawing  # noqa: E402
from utils.svg_document import load_svg  # noqa: E402


def _teams(count: int = 3):
    return [
        SimpleNamespace(name=name, max_seats=2, is_reserve=False)
        for name in ("Red Bull", "Ferrari", "Mercedes")[:count]
    ] + [SimpleNamespace(name="Reserve", max_seats=0, is_reserve=True)]


def test_the_sample_draws_one_entry_fewer_than_the_rows_declared():
    """So the rendering of an unused row can be judged."""
    root = _template(8, _QUALIFYING_ROW)
    drawing = build_results_drawing(root, QUALIFYING_TEMPLATE_KEY, _teams())
    assert drawing.entry_count == 7


def test_a_single_row_template_draws_one_entry_and_leaves_none_unused():
    root = _template(1, _QUALIFYING_ROW)
    drawing = build_results_drawing(root, QUALIFYING_TEMPLATE_KEY, _teams())
    assert drawing.entry_count == 1


def test_the_sample_is_drawn_for_the_fabricated_division_at_the_final_stage():
    root = _template(5, _QUALIFYING_ROW)
    drawing = build_results_drawing(root, QUALIFYING_TEMPLATE_KEY, _teams())
    assert drawing.division_name == "Test Division"
    assert drawing.division_tier == "1"
    assert drawing.season_number == "1"
    assert drawing.round_number == "1"
    assert drawing.result_status_label == "Final Results"
    assert drawing.penalty_phase_closed and drawing.appeal_phase_closed


def test_the_sample_draws_the_leagues_own_teams():
    root = _template(4, _QUALIFYING_ROW)
    drawing = build_results_drawing(root, QUALIFYING_TEMPLATE_KEY, _teams())
    drawn = {entry.team_name for entry in drawing.entries}
    assert drawn <= {"Red Bull", "Ferrari", "Mercedes"}
    assert "Reserve" not in drawn


def test_the_qualifying_sample_exhibits_its_enumerated_cases():
    root = _template(10, _QUALIFYING_ROW)
    drawing = build_results_drawing(root, QUALIFYING_TEMPLATE_KEY, _teams())
    entries = drawing.entries
    assert entries[0].gap is None                       # the reference entry
    assert entries[1].gap == "+0.400"                   # under a second
    assert entries[2].gap.startswith("+1:")             # over a minute
    assert entries[3].tyre is None                      # no tyre recorded
    assert entries[4].best_lap == "DNS"                 # set no time
    assert entries[5].postrace_penalty == "DSQ"         # penalty phase
    assert entries[6].appeal_penalty == "DSQ"           # appeal phase
    assert entries[7].postrace_penalty == "—"           # sanctioned by neither
    assert entries[7].appeal_penalty == "—"
    assert entries[8].points == "0"                     # conferred no points


def test_the_race_sample_exhibits_its_enumerated_cases():
    root = _template(11, _RACE_ROW)
    drawing = build_results_drawing(root, RACE_TEMPLATE_KEY, _teams())
    entries = drawing.entries
    assert entries[0].time.startswith("1:02:")          # more than an hour
    assert entries[1].time == "+0.400"                  # under a second
    assert entries[2].time.startswith("+1:36")          # over a minute
    assert entries[3].time == "+1 Lap"
    assert entries[4].time == "+3 Laps"
    assert entries[5].time == "DNF"
    assert entries[6].time == "DNS"
    assert entries[7].time == "DSQ"
    assert entries[2].ingame_penalty == "+5s"           # a whole number of seconds
    assert entries[3].ingame_penalty == "+0.750s"       # a fraction below one
    assert entries[0].ingame_penalty == "—"             # none applied
    assert entries[4].postrace_penalty == "+5.500s"     # a penalty-phase time penalty
    assert entries[7].postrace_penalty == "DSQ"
    assert entries[8].appeal_penalty == "DSQ"           # disqualified again on appeal
    assert entries[8].postrace_penalty == "+5s"         # its penalty phase stands below
    assert entries[9].points == "0"


def test_the_bonus_is_held_by_the_entry_that_did_not_finish():
    """Not by the first-placed one — the fabricated configuration sets no position limit."""
    root = _template(11, _RACE_ROW)
    drawing = build_results_drawing(root, RACE_TEMPLATE_KEY, _teams())
    assert drawing.entries[5].holds_fastest_lap
    assert drawing.entries[5].time == "DNF"
    assert not drawing.entries[0].holds_fastest_lap
    assert drawing.fastest_lap is not None


def test_a_driver_who_stated_no_nationality_is_among_the_sample():
    root = _template(6, _QUALIFYING_ROW)
    drawing = build_results_drawing(root, QUALIFYING_TEMPLATE_KEY, _teams())
    assert "Other" in {entry.nationality for entry in drawing.entries}


def test_a_server_with_no_team_beyond_the_reserve_is_rejected():
    root = _template(5, _QUALIFYING_ROW)
    reserve_only = [SimpleNamespace(name="Reserve", max_seats=0, is_reserve=True)]
    with pytest.raises(ResultsDataError, match="no team beyond the reserve team"):
        build_results_drawing(root, QUALIFYING_TEMPLATE_KEY, reserve_only)


# ── Against the templates that actually ship ──────────────────────────────


@pytest.mark.parametrize(
    "filename,key",
    [
        ("results_qualifying_template.svg", QUALIFYING_TEMPLATE_KEY),
        ("results_race_template.svg", RACE_TEMPLATE_KEY),
    ],
)
def test_the_shipped_template_fills_from_the_sample_with_no_unresolved_field(filename, key):
    """The end-to-end fill, short of rasterising: every mandatory field resolved."""
    from pathlib import Path

    from utils.svg_fill import fill

    path = (
        Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates" / filename
    )
    root = load_svg(path)
    spec = build_results_drawing(root, key, _teams())
    directories = {
        name: Path(__file__).resolve().parents[2] / "resources" / "defaults" / folder
        for name, folder in (("team", "teams"), ("flag", "flags"), ("tyre", "tyres"))
    }
    from services.image_results_service import build_fill_spec as project

    result = fill(project(spec, root, asset_directories=directories))
    assert result.unresolved == []
