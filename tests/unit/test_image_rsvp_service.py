"""Resolution and projection of the check-in call graphic (041, US1).

Covers FR-009, FR-021–FR-030 and FR-040.

**The negative test in this file is the important one.** The check-in graphic is the module's
first *static* type (XIV.17): generated once, riding a message that is edited in place on every
button press. What makes that safe is that it draws nothing a press can change — no driver, no
team, no RSVP status, no attendance point, no roster. Nothing in the module can detect a breach
of that; the result would be a stale picture under a live message, reporting nothing.
"""
from __future__ import annotations

import inspect
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import image_rsvp_service  # noqa: E402
from services.image_rsvp_service import (  # noqa: E402
    MYSTERY_LITERAL,
    MYSTERY_RACE_NAME,
    RsvpDataError,
    build_fill_spec,
    format_label,
    resolve_drawing,
    session_names,
)

SVG_NS = "http://www.w3.org/2000/svg"
_START = datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc)


def _svg(ids):
    root = ET.Element(f"{{{SVG_NS}}}svg", {"width": "1200", "height": "675"})
    for name in ids:
        tag = "image" if name.endswith("_image") else "text"
        ET.SubElement(root, f"{{{SVG_NS}}}{tag}", {"id": name})
    return root


def _call_svg(sessions=4, extras=()):
    ids = [
        "division_name",
        "round_number",
        "race_name",
        "round_format",
        "round_date",
        "round_time",
    ]
    for n in range(1, sessions + 1):
        ids += [f"session_{n}_group", f"session_{n}_name"]
    ids += list(extras)
    return _svg(ids)


def _drawing(**kwargs):
    base = dict(
        division_name="Division 1",
        round_number=1,
        round_format="NORMAL",
        scheduled_at=_START,
        track_name="Silverstone Circuit",
        race_name="British Grand Prix",
        country_name="United Kingdom",
    )
    base.update(kwargs)
    return resolve_drawing(**base)


# ── Sessions ──────────────────────────────────────────────────────────────


def test_a_sprint_round_names_four_sessions_in_the_order_they_are_run():
    assert session_names("SPRINT") == (
        "Sprint Qualifying",
        "Sprint Race",
        "Feature Qualifying",
        "Feature Race",
    )


@pytest.mark.parametrize("fmt", ["NORMAL", "ENDURANCE", "MYSTERY"])
def test_every_other_format_names_two(fmt):
    assert session_names(fmt) == ("Qualifying", "Race")


def test_a_session_name_carries_no_qualifier_of_its_length():
    """A mystery round's short qualifying and long race are named as any other's (FR-024)."""
    for name in session_names("MYSTERY"):
        assert "short" not in name.lower()
        assert "long" not in name.lower()


def test_the_sessions_are_projected_in_order():
    spec = build_fill_spec(_drawing(round_format="SPRINT"), _call_svg(sessions=4))
    assert spec.text["session_1_name"] == "Sprint Qualifying"
    assert spec.text["session_4_name"] == "Feature Race"


def test_sessions_declared_beyond_the_round_leave_by_their_group():
    spec = build_fill_spec(_drawing(round_format="NORMAL"), _call_svg(sessions=4))
    assert spec.text["session_2_name"] == "Race"
    assert "session_3_group" in spec.remove
    assert "session_4_group" in spec.remove
    assert "session_3_name" in spec.off_canvas


def test_a_template_declaring_no_session_names_none_and_is_not_faulty():
    """The session list is an optional unit (FR-004) — and this is not an overflow."""
    spec = build_fill_spec(_drawing(round_format="SPRINT"), _call_svg(sessions=0))
    assert not any(name.startswith("session_") for name in spec.text)
    assert spec.row_count == 0


def test_a_template_declaring_too_few_sessions_still_overflows():
    """Declaring *some* and too few is an ordinary capacity fault (FR-040)."""
    spec = build_fill_spec(_drawing(round_format="SPRINT"), _call_svg(sessions=2))
    assert spec.row_count == 4


def test_a_gap_in_the_session_numbering_is_fatal():
    root = _svg(
        [
            "division_name", "round_number", "race_name", "round_format",
            "round_date", "round_time", "session_1_name", "session_3_name",
        ]
    )
    with pytest.raises(RsvpDataError):
        build_fill_spec(_drawing(), root)


# ── The format label and the heading ──────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [("NORMAL", "Normal"), ("SPRINT", "Sprint"), ("ENDURANCE", "Endurance"),
     ("MYSTERY", "Mystery"), ("RoundFormat.SPRINT", "Sprint")],
)
def test_the_format_label_is_the_text_the_embed_carries(raw, expected):
    assert format_label(raw) == expected


def test_the_heading_fields_are_filled():
    spec = build_fill_spec(_drawing(season_number=4, division_tier=1), _call_svg())
    assert spec.text["division_name"] == "Division 1"
    assert spec.text["round_number"] == "1"
    assert spec.text["race_name"] == "British Grand Prix"


# ── The mystery round ─────────────────────────────────────────────────────


def test_a_mystery_round_fills_its_fields_rather_than_emptying_them():
    """No mandatory field is emptied for want of a track (FR-029)."""
    drawing = _drawing(
        round_format="MYSTERY", track_name=None, race_name=None, country_name=None
    )
    spec = build_fill_spec(drawing, _call_svg(extras=("track_name", "country_name")))

    assert spec.text["race_name"] == MYSTERY_RACE_NAME
    assert spec.text["track_name"] == MYSTERY_LITERAL
    assert spec.text["country_name"] == MYSTERY_LITERAL
    assert spec.empty == []


def test_a_mystery_rounds_image_resolves_from_the_mystery_datum():
    drawing = _drawing(round_format="MYSTERY", track_name=None, race_name=None)
    spec = build_fill_spec(drawing, _call_svg(extras=("track_image",)))
    assert spec.image_data["track_image"] == ("track", MYSTERY_LITERAL)


def test_a_track_image_resolves_from_the_track():
    spec = build_fill_spec(_drawing(), _call_svg(extras=("track_image",)))
    assert spec.image_data["track_image"] == ("track", "Silverstone Circuit")


def test_an_optional_field_with_a_group_leaves_whole_rather_than_standing_empty():
    """So a round carrying no country leaves no label naming what is not there."""
    drawing = _drawing(country_name=None)
    spec = build_fill_spec(
        drawing, _call_svg(extras=("country_name", "country_name_group"))
    )
    assert "country_name_group" in spec.remove
    assert spec.empty == []


# ── Date, time and the deadline ───────────────────────────────────────────


def test_the_time_carries_the_configured_zones_abbreviation():
    """A picture cannot carry a per-reader timestamp, so it says which zone it is in."""
    spec = build_fill_spec(_drawing(time_zone="UTC"), _call_svg())
    assert "UTC" in spec.text["round_time"]


def test_the_date_and_time_are_rendered_in_the_configured_zone():
    utc = build_fill_spec(_drawing(time_zone="UTC"), _call_svg()).text
    tokyo = build_fill_spec(_drawing(time_zone="Asia/Tokyo"), _call_svg()).text
    assert utc["round_time"] != tokyo["round_time"]


def test_the_deadline_is_drawn_when_supplied():
    deadline = datetime(2026, 5, 17, 8, 30, tzinfo=timezone.utc)
    spec = build_fill_spec(
        _drawing(deadline_at=deadline, time_zone="UTC"),
        _call_svg(extras=("deadline_date", "deadline_time")),
    )
    assert "08:30" in spec.text["deadline_time"]


def test_a_deadline_at_the_rounds_own_start_is_drawn_as_that_moment():
    """A configuration of 0 hours (FR-026)."""
    spec = build_fill_spec(
        _drawing(deadline_at=_START, time_zone="UTC"),
        _call_svg(extras=("deadline_date", "deadline_time")),
    )
    assert spec.text["deadline_time"] == spec.text["round_time"]


# ── The static declaration ────────────────────────────────────────────────


def test_the_utility_performs_no_time_arithmetic_of_its_own():
    """XIV.7: the deadline arrives finished from ``derive_checkin_deadline``."""
    source = inspect.getsource(image_rsvp_service.resolve_drawing)
    assert "timedelta" not in source
    assert "hours" not in source.replace("deadline_hours", "")


def test_the_drawing_carries_nothing_a_button_press_can_change():
    """The substance of the static declaration (XIV.17)."""
    drawing = _drawing(round_format="SPRINT")
    fields = {f.name for f in drawing.__dataclass_fields__.values()}
    for mutable in ("driver", "team", "rsvp", "status", "points", "roster", "reserve"):
        assert not any(mutable in name for name in fields), mutable


def test_no_per_driver_datum_can_reach_the_utility_at_all():
    """resolve_drawing takes no roster, no statuses and no attendance record."""
    parameters = set(inspect.signature(resolve_drawing).parameters)
    for mutable in ("driver", "team", "rsvp", "status", "points", "roster", "reserve"):
        assert not any(mutable in name for name in parameters), mutable


# --------------------------------------------------------------------------
# 044 — the check-in graphic pictures the round either way, or both
# --------------------------------------------------------------------------

def test_the_catalogue_declares_both_imagery_classes():
    """The check-in graphic is one of the two types that may draw a circuit map."""
    from models.image_catalogues import RSVP_CATALOGUE

    assert RSVP_CATALOGUE.assets == {"track_flag": "flag", "track_image": "track"}
    for field_id in ("track_flag", "track_flag_group", "track_image", "track_image_group"):
        assert field_id in RSVP_CATALOGUE.optional, f"{field_id} must be optional"


def _rsvp_root(image_ids):
    """A check-in template declaring the mandatory text plus *image_ids*."""
    from lxml import etree

    ns = "http://www.w3.org/2000/svg"
    root = etree.Element(f"{{{ns}}}svg")
    root.set("width", "600")
    root.set("height", "400")
    for text_id in ("division_name", "round_number", "race_name", "round_format",
                    "round_date", "round_time"):
        node = etree.SubElement(root, f"{{{ns}}}text")
        node.set("id", text_id)
        node.text = "x"
    for image_id in image_ids:
        node = etree.SubElement(root, f"{{{ns}}}image")
        node.set("id", image_id)
    return root


def _rsvp_spec(image_ids, **kw):
    drawing = resolve_drawing(
        division_name="Test Division",
        round_number=1,
        round_format=kw.pop("round_format", "NORMAL"),
        scheduled_at=datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc),
        track_name=kw.pop("track_name", "Silverstone Circuit"),
        country_name=kw.pop("country_name", "United Kingdom"),
        race_name="British Grand Prix",
        **kw,
    )
    return build_fill_spec(drawing, _rsvp_root(image_ids))


def test_a_template_declaring_both_draws_both():
    spec = _rsvp_spec(["track_flag", "track_image"])
    assert spec.image_data["track_flag"] == ("flag", "United Kingdom")
    assert spec.image_data["track_image"] == ("track", "Silverstone Circuit")


def test_a_template_declaring_only_one_draws_only_that_one():
    spec = _rsvp_spec(["track_flag"])
    assert spec.image_data["track_flag"] == ("flag", "United Kingdom")
    assert "track_image" not in spec.image_data

    spec = _rsvp_spec(["track_image"])
    assert spec.image_data["track_image"] == ("track", "Silverstone Circuit")
    assert "track_flag" not in spec.image_data


def test_a_template_declaring_neither_still_produces_the_graphic():
    spec = _rsvp_spec([])
    assert "track_flag" not in spec.image_data
    assert "track_image" not in spec.image_data


def test_a_mystery_round_draws_each_class_from_its_own_mystery_file():
    spec = _rsvp_spec(["track_flag", "track_image"], round_format="MYSTERY")
    assert spec.image_data["track_flag"] == ("flag", "Mystery")
    assert spec.image_data["track_image"] == ("track", "Mystery")
