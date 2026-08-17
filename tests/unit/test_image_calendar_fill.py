"""Unit tests for calendar resolution and projection — T010.

Covers:
  1. Resolution of a normal round from the tracks registry.
  2. A mystery round drawn and marked as such, never emptied.
  3. A normal round's format field emptied rather than dashed.
  4. A track name matching no record — fatal (research R7).
  5. A division holding no round — fatal.
  6. Projection: text, image data, crop, row_count, off_canvas.
  7. Rounds beside the final one removed by group; rounds below it left to the cut.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_calendar_service import (
    MYSTERY_COUNTRY,
    MYSTERY_DATUM,
    MYSTERY_RACE_NAME,
    MYSTERY_TRACK_NAME,
    CalendarDataError,
    build_fill_spec,
    resolve_drawing,
)

SVG_NS = "http://www.w3.org/2000/svg"

SILVERSTONE = NS(
    name="Silverstone Circuit", gp_name="British Grand Prix", country="United Kingdom"
)
TRACKS = {"Silverstone Circuit": SILVERSTONE}

SUFFIXES = (
    "number",
    "country_name",
    "race_name",
    "track_name",
    "format",
    "date",
    "time",
    "vertical_crop_point",
)


def _round(number: int, fmt: str = "NORMAL", track: str | None = "Silverstone Circuit"):
    return NS(
        round_number=number,
        format=fmt,
        track_name=None if fmt == "MYSTERY" else track,
        scheduled_at=datetime(2026, 6, 4 + 7 * (number - 1), 20, 0, tzinfo=timezone.utc),
    )


def _template(count: int, *, height: float = 400.0, group: bool = False):
    """A template declaring *count* rounds stacked down the canvas."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "600")
    root.set("height", str(height))
    node = etree.SubElement(root, f"{{{SVG_NS}}}text")
    node.set("id", "division_name")
    step = height / count
    for index in range(1, count + 1):
        container = root
        if group:
            container = etree.SubElement(root, f"{{{SVG_NS}}}g")
            container.set("id", f"round_{index}_group")
        for suffix in SUFFIXES:
            child = etree.SubElement(container, f"{{{SVG_NS}}}text")
            child.set("id", f"round_{index}_{suffix}")
            # Crop point at the foot of its round; every other field above it.
            child.set("y", str(step * index if suffix == "vertical_crop_point" else step * (index - 1) + 1))
    return root


def _draw(rounds):
    return resolve_drawing(
        division_name="Elite",
        division_tier=1,
        season_number=3,
        rounds=rounds,
        tracks=TRACKS,
    )


# ── Resolution ────────────────────────────────────────────────────────────


def test_normal_round_reads_country_and_gp_name_from_the_track():
    entry = _draw([_round(1)]).rounds[0]
    assert entry.country_name == "United Kingdom"
    assert entry.race_name == "British Grand Prix"
    assert entry.track_name == "Silverstone Circuit"
    assert entry.image_datum == "Silverstone Circuit"


def test_normal_round_empties_its_format_label():
    """A template author decides by the chrome whether an ordinary round is marked."""
    assert _draw([_round(1)]).rounds[0].format_label == ""


@pytest.mark.parametrize(
    "fmt,label", [("SPRINT", "Sprint"), ("ENDURANCE", "Endurance"), ("MYSTERY", "Mystery")]
)
def test_other_formats_are_labelled(fmt, label):
    assert _draw([_round(1, fmt)]).rounds[0].format_label == label


def test_mystery_round_is_drawn_and_marked_never_emptied():
    entry = _draw([_round(1, "MYSTERY")]).rounds[0]
    assert entry.country_name == MYSTERY_COUNTRY
    assert entry.race_name == MYSTERY_RACE_NAME
    assert entry.track_name == MYSTERY_TRACK_NAME
    assert entry.image_datum == MYSTERY_DATUM


def test_date_and_time_carry_the_configured_zone_abbreviation():
    entry = _draw([_round(1)]).rounds[0]
    assert entry.date_text == "Thu 04 Jun 2026"
    assert entry.time_text.endswith("UTC")


def test_rounds_are_ordered_by_number_however_supplied():
    drawing = _draw([_round(3), _round(1), _round(2)])
    assert [r.ordinal for r in drawing.rounds] == [1, 2, 3]


# ── Fatal data ────────────────────────────────────────────────────────────


def test_track_name_matching_no_record_is_fatal():
    """Rounds hold a track name, not an id; a renamed track cannot be resolved (R7)."""
    with pytest.raises(CalendarDataError, match="matches no track"):
        _draw([_round(1, track="Circuit Gone Missing")])


def test_division_holding_no_round_is_fatal():
    with pytest.raises(CalendarDataError, match="no round at all"):
        _draw([])


# ── Projection ────────────────────────────────────────────────────────────


def test_spec_fills_the_rounds_it_draws():
    spec = build_fill_spec(_draw([_round(1), _round(2)]), _template(2))
    assert spec.text["division_name"] == "Elite"
    assert spec.text["round_1_race_name"] == "British Grand Prix"
    assert spec.text["round_2_number"] == "2"
    assert spec.row_count == 2


def test_spec_routes_the_round_image_through_the_track_asset_class():
    spec = build_fill_spec(_draw([_round(1)]), _template(1))
    # The template above declares no image field, so add one and re-project.
    root = _template(1)
    node = etree.SubElement(root, f"{{{SVG_NS}}}image")
    node.set("id", "round_1_image")
    spec = build_fill_spec(_draw([_round(1)]), root)
    assert spec.image_data["round_1_image"] == ("track", "Silverstone Circuit")


def test_spec_crops_at_the_final_drawn_round():
    spec = build_fill_spec(_draw([_round(1)]), _template(3))
    assert spec.crop == "round_1_vertical_crop_point"


def test_crop_is_final_only_when_the_division_fills_the_template():
    full = build_fill_spec(_draw([_round(1), _round(2)]), _template(2))
    short = build_fill_spec(_draw([_round(1)]), _template(2))
    assert full.crop_is_final is True
    assert short.crop_is_final is False


def test_rounds_beyond_the_division_are_never_treated_as_unresolved():
    """XIV.3 — a field taken off the canvas by the crop is not unresolved."""
    spec = build_fill_spec(_draw([_round(1)]), _template(3))
    assert "round_2_number" in spec.off_canvas
    assert "round_3_date" in spec.off_canvas
    assert "round_1_number" not in spec.off_canvas


def test_a_round_below_the_cut_is_left_for_the_crop_to_remove():
    """Stacked rounds all fall below the cut, so nothing is removed by group."""
    spec = build_fill_spec(_draw([_round(1)]), _template(3, group=True))
    assert spec.remove == []


def test_a_round_beside_the_final_one_leaves_by_its_group():
    """Two rounds abreast: round 2 stands above round 1's crop point, so the cut
    cannot reach it and its group must go instead."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "600")
    root.set("height", "200")
    etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", "division_name")
    for index in (1, 2):
        group = etree.SubElement(root, f"{{{SVG_NS}}}g")
        group.set("id", f"round_{index}_group")
        for suffix in SUFFIXES:
            child = etree.SubElement(group, f"{{{SVG_NS}}}text")
            child.set("id", f"round_{index}_{suffix}")
            child.set("y", "200" if suffix == "vertical_crop_point" else "10")

    spec = build_fill_spec(_draw([_round(1)]), root)
    assert spec.remove == ["round_2_group"]


def test_a_round_beside_the_final_one_leaves_field_by_field_without_a_group():
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "600")
    root.set("height", "200")
    etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", "division_name")
    for index in (1, 2):
        for suffix in SUFFIXES:
            child = etree.SubElement(root, f"{{{SVG_NS}}}text")
            child.set("id", f"round_{index}_{suffix}")
            child.set("y", "200" if suffix == "vertical_crop_point" else "10")

    spec = build_fill_spec(_draw([_round(1)]), root)
    assert "round_2_number" in spec.remove
    assert "round_1_number" not in spec.remove


def test_the_preview_supplies_the_track_directory():
    """Regression: `/images test calendar` must resolve its round images.

    `build_fill_spec` routes a round image through the `track` asset class, and a class
    with no configured directory is an unresolved field — fatal. The preview reads no live
    configuration (FR-036), so it must supply the packaged directory itself or every round
    image on a real template reports its class unconfigured and no preview is drawn.

    Found by rendering the shipped template rather than a fixture: the fixtures used in
    these tests declare no `round_<x>_image`, so nothing here exercised the path.
    """
    from services.image_sample_data import build_spec

    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "600")
    root.set("height", "400")
    etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", "division_name")
    for suffix in SUFFIXES:
        node = etree.SubElement(root, f"{{{SVG_NS}}}text")
        node.set("id", f"round_1_{suffix}")
        node.set("y", "400" if suffix == "vertical_crop_point" else "10")
    etree.SubElement(root, f"{{{SVG_NS}}}image").set("id", "round_1_image")

    spec = build_spec("calendar_template", root)
    assert "track" in spec.asset_directories, (
        "the calendar preview supplied no track directory, so every round image would "
        "report its asset class unconfigured"
    )
    assert spec.image_data["round_1_image"][0] == "track"


def test_template_declaring_no_round_is_fatal_at_projection():
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "600")
    root.set("height", "200")
    etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", "division_name")
    with pytest.raises(CalendarDataError, match="declares no `round`"):
        build_fill_spec(_draw([_round(1)]), root)


# --------------------------------------------------------------------------
# 044 — the two round imagery classes
# --------------------------------------------------------------------------

def _spec_for_ids(image_ids, *, country="United Kingdom",
                  track="Silverstone Circuit", mystery=False):
    """A one-round calendar whose round declares *image_ids* and nothing else extra."""
    root = _template(1)
    for image_id in image_ids:
        node = etree.SubElement(root, f"{{{SVG_NS}}}image")
        node.set("id", image_id)
        node.set("y", "1")
    tracks = {track: NS(name=track, gp_name="A Grand Prix", country=country)}
    drawing = resolve_drawing(
        division_name="Elite",
        division_tier=1,
        season_number=3,
        rounds=[_round(1, "MYSTERY" if mystery else "NORMAL", track)],
        tracks=tracks,
    )
    return build_fill_spec(drawing, root)


def test_a_round_declaring_both_draws_both():
    spec = _spec_for_ids(["round_1_flag", "round_1_image"])
    assert spec.image_data["round_1_flag"] == ("flag", "United Kingdom")
    assert spec.image_data["round_1_image"] == ("track", "Silverstone Circuit")


def test_a_round_declaring_only_the_flag_draws_only_the_flag():
    spec = _spec_for_ids(["round_1_flag"], country="Brazil")
    assert spec.image_data["round_1_flag"] == ("flag", "Brazil")
    assert "round_1_image" not in spec.image_data


def test_a_round_declaring_only_the_map_draws_only_the_map():
    spec = _spec_for_ids(["round_1_image"])
    assert spec.image_data["round_1_image"] == ("track", "Silverstone Circuit")
    assert "round_1_flag" not in spec.image_data


def test_a_round_declaring_neither_draws_neither_and_raises_nothing():
    spec = _spec_for_ids([])
    assert "round_1_flag" not in spec.image_data
    assert "round_1_image" not in spec.image_data


def test_a_mystery_round_resolves_each_class_from_its_own_directory():
    """The concealed round takes the ``Mystery`` literal in both classes.

    Each resolves its own directory's ``mystery.svg``; neither is emptied and
    neither raises. The track directory's file already ships; the flag
    directory's is added by this increment.
    """
    spec = _spec_for_ids(["round_1_flag", "round_1_image"], mystery=True)
    assert spec.image_data["round_1_flag"] == ("flag", "Mystery")
    assert spec.image_data["round_1_image"] == ("track", "Mystery")
