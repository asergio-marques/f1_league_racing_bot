"""Projecting a verdict onto its template — T017.

Written against specs/043-verdicts-image-generation/contracts/verdicts-catalogue.md and
Constitution XIV.3 and XIV.13.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_verdict_service import (  # noqa: E402
    VerdictDrawing,
    VerdictKind,
    build_fill_spec,
)
from utils.svg_document import parse_svg_bytes  # noqa: E402

FULL_TEMPLATE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" '
    b'xmlns:xlink="http://www.w3.org/1999/xlink" width="1200" height="675">'
    b'<g id="season_number_group"><text id="season_number">1</text></g>'
    b'<text id="division_name">D</text>'
    b'<g id="division_tier_group"><text id="division_tier">1</text></g>'
    b'<text id="round_number">1</text>'
    b'<text id="race_name">R</text>'
    b'<g id="session_name_group"><text id="session_name">S</text></g>'
    b'<text id="verdict_stage">V</text>'
    b'<text id="driver_name">N</text>'
    b'<image id="driver_flag" xlink:href="x.svg"/>'
    b'<g id="team_name_group">'
    b'<text id="team_name">T</text>'
    b'<image id="team_image" xlink:href="x.svg"/>'
    b"</g>"
    b'<text id="penalty">P</text>'
    b'<text id="description">De</text>'
    b'<text id="justification">J</text>'
    b"</svg>"
)


def _root():
    return parse_svg_bytes(FULL_TEMPLATE)


def _penalty(**overrides) -> VerdictDrawing:
    values = dict(
        kind=VerdictKind.PENALTY,
        season_number=3,
        division_name="Pro Division",
        division_tier=1,
        round_number=7,
        race_name="British Grand Prix",
        session_name="Feature Race",
        driver_name="Ada Lovelace",
        driver_nationality="British",
        team_name="Red Bull",
        penalty="5 seconds added",
        description="Contact at turn four.",
        justification="Video evidence reviewed.",
    )
    values.update(overrides)
    return VerdictDrawing(**values)


def _sanction(**overrides) -> VerdictDrawing:
    values = dict(
        kind=VerdictKind.ATTENDANCE_SANCTION,
        division_name="Pro Division",
        round_number=7,
        session_name=None,
        team_name=None,
        driver_name="Ada Lovelace",
        driver_nationality="British",
        penalty="Sacked",
        description="Sacked due to accumulation of attendance points.",
        justification="Ada Lovelace has reached the 12 attendance point limit.",
    )
    values.update(overrides)
    return VerdictDrawing(**values)


# ── A penalty: every field placed ─────────────────────────────────────────


def test_a_penalty_places_every_text_field():
    spec = build_fill_spec(_penalty(), _root())

    assert spec.text["division_name"] == "Pro Division"
    assert spec.text["round_number"] == "7"
    assert spec.text["race_name"] == "British Grand Prix"
    assert spec.text["session_name"] == "Feature Race"
    assert spec.text["verdict_stage"] == "Post-Race Penalty"
    assert spec.text["driver_name"] == "Ada Lovelace"
    assert spec.text["team_name"] == "Red Bull"
    assert spec.text["penalty"] == "5 seconds added"
    assert spec.text["description"] == "Contact at turn four."
    assert spec.text["justification"] == "Video evidence reviewed."
    assert spec.text["season_number"] == "3"
    assert spec.text["division_tier"] == "1"


def test_the_image_fields_carry_their_datum_and_class_not_a_path():
    spec = build_fill_spec(_penalty(), _root())
    assert spec.image_data["driver_flag"] == ("flag", "United Kingdom")
    assert spec.image_data["team_image"] == ("team", "Red Bull")


def test_the_template_key_and_catalogue_are_carried_on_the_spec():
    spec = build_fill_spec(_penalty(), _root())
    assert spec.image_type == "verdicts_template"
    assert spec.catalogue is not None


def test_a_penalty_removes_nothing_and_empties_nothing():
    spec = build_fill_spec(_penalty(), _root())
    assert spec.remove == []
    assert spec.empty == []
    assert spec.empty_quietly == []


def test_no_collection_is_counted():
    """The verdict declares none, so there is no row count to compare (XIV.12)."""
    assert build_fill_spec(_penalty(), _root()).row_count is None


# ── An attendance sanction: the session and the team come off ─────────────


def test_a_sanction_empties_the_session_quietly_and_raises_no_notice():
    """XIV.3's determined-empty: the value *is* nothing, and nothing is wrong."""
    spec = build_fill_spec(_sanction(), _root())

    assert "session_name" in spec.empty_quietly
    assert "session_name" not in spec.empty
    assert "session_name" not in spec.text


def test_a_sanction_empties_the_team_name_and_removes_the_team_image():
    spec = build_fill_spec(_sanction(), _root())

    assert "team_name" in spec.empty_quietly
    assert "team_image" in spec.remove
    assert "team_image" not in spec.image_data


def test_a_sanction_still_draws_the_stage_and_the_driver():
    spec = build_fill_spec(_sanction(), _root())
    assert spec.text["verdict_stage"] == "Attendance Sanction"
    assert spec.text["driver_name"] == "Ada Lovelace"


def test_the_sanction_label_is_never_written_into_the_session_field():
    """It stands on the stage alone; writing it twice says it under two headings."""
    spec = build_fill_spec(_sanction(), _root())
    assert spec.text.get("session_name") != "Attendance Sanction"


# ── Values that do not apply are emptied, never dashed ────────────────────


def test_an_absent_tier_is_emptied_rather_than_dashed():
    spec = build_fill_spec(_penalty(division_tier=None), _root())
    assert "division_tier" in spec.empty
    assert spec.text.get("division_tier") in (None, "")


def test_an_absent_season_number_is_emptied():
    spec = build_fill_spec(_penalty(season_number=None), _root())
    assert "season_number" in spec.empty


def test_a_mystery_round_reads_mystery_gp():
    spec = build_fill_spec(_penalty(race_name="Mystery GP"), _root())
    assert spec.text["race_name"] == "Mystery GP"


def test_an_absent_nationality_removes_the_flag():
    spec = build_fill_spec(_penalty(driver_nationality=None), _root())
    assert "driver_flag" in spec.remove
    assert "driver_flag" not in spec.image_data


def test_a_league_that_collects_no_nationality_removes_the_flag_quietly():
    """XIV.4's configured absence: nothing has degraded, so nothing is reported."""
    from services.image_verdict_service import suppressed_flag_fields  # noqa: PLC0415

    drawing = _penalty(driver_nationality=None, nationality_collected=False)
    spec = build_fill_spec(drawing, _root())

    assert "driver_flag" in spec.remove
    assert suppressed_flag_fields(drawing) == {"driver_flag"}


def test_the_switch_beats_a_nationality_the_driver_already_stated():
    """The switch is read before the value, so no driver keeps a flag the others lost."""
    from services.image_verdict_service import suppressed_flag_fields  # noqa: PLC0415

    drawing = _penalty(driver_nationality="British", nationality_collected=False)
    spec = build_fill_spec(drawing, _root())

    assert "driver_flag" not in spec.image_data
    assert "driver_flag" in spec.remove
    assert suppressed_flag_fields(drawing) == {"driver_flag"}


def test_a_driver_who_stated_none_removes_the_flag_loudly():
    from services.image_verdict_service import suppressed_flag_fields  # noqa: PLC0415

    drawing = _penalty(driver_nationality=None, nationality_collected=True)
    spec = build_fill_spec(drawing, _root())

    assert "driver_flag" in spec.remove
    assert suppressed_flag_fields(drawing) == set()


def test_a_sanction_names_no_team_so_its_image_removal_is_quiet():
    """Nothing degraded: this kind of verdict has no team, and the graphic says so."""
    from services.image_verdict_service import suppressed_team_fields  # noqa: PLC0415

    drawing = _sanction()
    spec = build_fill_spec(drawing, _root())

    assert "team_image" in spec.remove
    assert suppressed_team_fields(drawing) == {"team_image"}


# ── A template declaring less than the full set ───────────────────────────


def test_a_field_the_template_does_not_declare_is_not_placed():
    """An optional field absent from the template is not a failure (XIV.3)."""
    lean = parse_svg_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        b'<text id="division_name">D</text>'
        b'<text id="round_number">1</text>'
        b'<text id="session_name">S</text>'
        b'<text id="verdict_stage">V</text>'
        b'<text id="driver_name">N</text>'
        b'<text id="penalty">P</text>'
        b'<text id="description">De</text>'
        b'<text id="justification">J</text>'
        b"</svg>"
    )
    spec = build_fill_spec(_penalty(), lean)

    assert "season_number" not in spec.text
    assert "team_name" not in spec.text
    assert "driver_flag" not in spec.image_data
    assert spec.text["division_name"] == "Pro Division"
