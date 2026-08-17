"""Degradations reach staff and never a channel drivers read (041, US5).

Covers FR-056 and FR-031's three states, and quickstart § 7.

The distinction this file exists to hold is XIV.4's: a **configured** absence has not degraded
and raises nothing. A league that switched nationality collection off has already been told, by
itself; reporting it back once per driver on every render would bury the notices that mean
something under the one that cannot.
"""
from __future__ import annotations

import inspect
import os
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import attendance_service, image_attendance_post, image_rsvp_post  # noqa: E402
from services.image_attendance_service import (  # noqa: E402
    DriverRecord,
    RoundHeading,
    build_fill_spec,
    resolve_drawing,
)

SVG_NS = "http://www.w3.org/2000/svg"


def _svg(ids):
    root = ET.Element(f"{{{SVG_NS}}}svg", {"width": "1200", "height": "675"})
    for name in ids:
        tag = "image" if name.endswith(("_image", "_flag")) else "text"
        ET.SubElement(root, f"{{{SVG_NS}}}{tag}", {"id": name})
    return root


def _sheet(rows=1, rounds=0):
    ids = ["division_name", "round_number"]
    for r in range(1, rows + 1):
        ids += [
            f"row_{r}_group", f"row_{r}_driver_name", f"row_{r}_points",
            f"row_{r}_team_name", f"row_{r}_team_image", f"row_{r}_driver_flag",
        ]
    for z in range(1, rounds + 1):
        ids += [f"round_{z}_group", f"round_{z}_number", f"round_{z}_flag"]
    return _svg(ids)


def _drawing(**kwargs):
    base = dict(
        division_name="Division 1",
        round_number=3,
        records=[DriverRecord(key=1, total=4)],
        display_names={1: "Ayrton"},
    )
    base.update(kwargs)
    return resolve_drawing(**base)


# ── The flag, in its three states (FR-031) ────────────────────────────────


def test_a_recorded_nationality_draws_its_flag_and_reports_nothing():
    spec = build_fill_spec(_drawing(nationalities={1: "British"}), _sheet())
    assert spec.image_data["row_1_driver_flag"] == ("flag", "United Kingdom")
    assert spec.empty == []


def test_a_driver_who_stated_none_is_an_ordinary_emptied_optional_and_reports():
    """The league collects nationality; this driver simply did not give one."""
    spec = build_fill_spec(
        _drawing(nationalities={1: None}, nationality_collected=True), _sheet()
    )
    assert "row_1_driver_flag" in spec.remove
    assert "row_1_driver_flag" in spec.empty


def test_collection_switched_off_at_its_source_reports_nothing_at_all():
    """XIV.4: nothing has degraded — the graphic draws what the league configured."""
    spec = build_fill_spec(
        _drawing(nationalities={1: None}, nationality_collected=False), _sheet()
    )
    assert "row_1_driver_flag" in spec.remove
    assert spec.empty == []


def test_a_sheet_with_no_flags_at_all_is_a_legitimate_outcome():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=[DriverRecord(key=k, total=0) for k in (1, 2, 3)],
        display_names={1: "A", 2: "B", 3: "C"},
        nationalities={1: None, 2: None, 3: None},
        nationality_collected=False,
    )
    spec = build_fill_spec(drawing, _sheet(rows=3))
    assert spec.empty == []
    assert not any(name.endswith("_driver_flag") for name in spec.text)


# ── The assets that fall back (FR-056) ────────────────────────────────────


def test_a_team_image_is_resolved_by_the_team_name_so_a_miss_can_report_it():
    spec = build_fill_spec(_drawing(team_names={1: "Backmarker GP"}), _sheet())
    assert spec.image_data["row_1_team_image"] == ("team", "Backmarker GP")


def test_a_driver_with_no_team_has_the_badge_removed_rather_than_left_stale():
    """An image field has nothing to empty: emptying one leaves the shipped picture."""
    spec = build_fill_spec(_drawing(team_names={}), _sheet())
    assert "row_1_team_image" in spec.remove


def test_a_round_image_is_resolved_by_its_track_naming_the_round_in_any_report():
    drawing = _drawing(rounds=[RoundHeading(1, "1", "Nowhere Circuit", "Nowhereland")])
    spec = build_fill_spec(drawing, _sheet(rounds=1))
    assert spec.image_data["round_1_flag"] == ("flag", "Nowhereland")


def test_a_round_with_no_track_has_its_image_removed():
    drawing = _drawing(rounds=[RoundHeading(1, "1", None)])
    spec = build_fill_spec(drawing, _sheet(rounds=1))
    assert "round_1_flag" in spec.remove


# ── Where a report goes (FR-056, XIV.4) ───────────────────────────────────


def test_both_hooks_report_through_the_shared_logging_helper():
    """One reporting path, so a divergence between the types is impossible."""
    for module in (image_attendance_post, image_rsvp_post):
        source = inspect.getsource(module)
        assert "image_results_post" in source
        assert "report_notices" in source


def test_neither_hook_can_post_into_a_channel_a_driver_reads():
    """A notice must never reach an attendance or RSVP channel (FR-056)."""
    for module in (image_attendance_post, image_rsvp_post):
        source = inspect.getsource(module)
        assert "channel.send" not in source
        assert "attendance_channel" not in source
        assert "rsvp_channel" not in source


def test_the_sheet_reports_its_notices_before_it_returns_an_attachment():
    """A degradation must be reported even on the path that succeeds."""
    source = inspect.getsource(attendance_service._sheet_attachment)
    assert source.index("report_notices(") < source.index("return discord.File")


def test_the_sheet_report_names_the_division_and_the_round():
    source = inspect.getsource(attendance_service._sheet_attachment)
    assert "division_name" in source and "round_number" in source
    assert "attendance after round" in source


def test_the_call_report_names_the_division_and_the_round():
    source = inspect.getsource(image_rsvp_post.try_attach)
    assert "check-in for round" in source


# ── The configured absence is read from the league's own switch ───────────


def test_the_live_sheet_path_reads_the_nationality_switch():
    """Without this the suppression is unreachable in production (FR-031)."""
    source = inspect.getsource(attendance_service._sheet_attachment)
    assert "_nationality_collected" in source
    assert "nationality_collected=" in source


def test_the_live_sheet_path_resolves_names_through_the_shared_convention():
    source = inspect.getsource(attendance_service._sheet_attachment)
    assert "_driver_names" in source
    assert "_nationalities" in source


def test_the_live_sheet_path_draws_the_seat_held_at_generation():
    source = inspect.getsource(attendance_service._sheet_attachment)
    assert "_seat_team_names" in source


@pytest.mark.asyncio
async def test_the_seat_lookup_survives_an_unreadable_database():
    """A nameless team is drawn empty; it is never a reason to fail a sheet."""
    result = await attendance_service._seat_team_names("no-such.db", 7, [1, 2])
    assert result == {}


@pytest.mark.asyncio
async def test_the_seat_lookup_is_empty_for_no_drivers():
    assert await attendance_service._seat_team_names("no-such.db", 7, []) == {}
