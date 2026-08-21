"""The verdict field catalogue — T010, T012.

Written against specs/043-verdicts-image-generation/contracts/verdicts-catalogue.md and
Constitution XIV.2, XIV.3, XIV.10, XIV.11 and XIV.13.

The verdict is the module's simplest catalogue: one template serving three kinds of
verdict, told apart by the text on two fields, and declaring **no collection at all**. Only
the weather mystery notice had reached that before it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import (  # noqa: E402
    CATALOGUES,
    catalogue_for,
    sibling_fields_declared,
)
from utils.svg_document import FieldIndex, parse_svg_bytes  # noqa: E402

KEY = "verdicts_template"

TEMPLATE = (
    Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates" / "verdicts_template.svg"
)

EXPECTED_MANDATORY = {
    "division_name",
    "round_number",
    "session_name",
    "verdict_stage",
    "driver_name",
    "penalty",
    "description",
    "justification",
}

EXPECTED_OPTIONAL = {
    "season_number",
    "season_number_group",
    "division_tier",
    "division_tier_group",
    "race_name",
    "team_name",
    "team_name_group",
    "session_name_group",
    "driver_flag",
    "team_image",
}


def _catalogue():
    return catalogue_for(KEY)


# ── The shape ─────────────────────────────────────────────────────────────


def test_the_catalogue_is_registered_and_not_empty():
    assert KEY in CATALOGUES
    assert not _catalogue().is_empty, "an empty catalogue would be skipped by Layer 2"


def test_mandatory_fields_are_exactly_the_eight_declared():
    assert set(_catalogue().mandatory) == EXPECTED_MANDATORY


def test_optional_fields_are_exactly_those_declared():
    assert set(_catalogue().optional) == EXPECTED_OPTIONAL


def test_session_name_is_mandatory_though_it_may_be_drawn_empty():
    """An attendance sanction determines it to be nothing, which XIV.3 holds is determined.

    The template must still declare it, so the classification stays mandatory. Reading the
    emptying as an unresolved mandatory field is the mistake this pins against.
    """
    assert "session_name" in _catalogue().mandatory
    assert "session_name" not in _catalogue().optional


def test_the_two_image_fields_name_their_asset_classes():
    assert _catalogue().assets == {"driver_flag": "flag", "team_image": "team"}


def test_no_collection_of_any_kind_is_declared():
    """The second catalogue of the module to declare none, after the mystery notice."""
    catalogue = _catalogue()
    assert catalogue.rows is None
    assert catalogue.columns is None
    assert catalogue.singleton is None


def test_every_id_is_flat_and_carries_no_discriminator():
    """A discriminator in a verdicts id means a template authored against another type."""
    for field_id in set(_catalogue().mandatory) | set(_catalogue().optional):
        parts = field_id.split("_")
        assert not any(part.isdigit() for part in parts), field_id


def test_the_catalogue_declares_nothing_it_was_told_not_to_draw():
    """No track image, country, date, session result, points, lifecycle or steward."""
    declared = set(_catalogue().mandatory) | set(_catalogue().optional)
    for forbidden in (
        "track_image",
        "track_name",
        "country_name",
        "round_date",
        "result_status",
        "points",
        "steward_name",
    ):
        assert forbidden not in declared


# ── The shipped template answers to it ────────────────────────────────────


def _shipped_root():
    if not TEMPLATE.is_file():
        pytest.skip("the packaged verdicts template is not present")
    return parse_svg_bytes(TEMPLATE.read_bytes())


def test_the_shipped_template_declares_every_mandatory_field():
    """The packaged template and the catalogue must not drift apart."""
    index = FieldIndex(_shipped_root())
    missing = sorted(
        name for name in _catalogue().all_mandatory_ids() if index.resolve(name) is None
    )
    assert missing == []


def test_the_shipped_template_declares_no_sibling_field():
    index = FieldIndex(_shipped_root())
    assert sibling_fields_declared(KEY, index.declared()) == []


def test_the_shipped_template_declares_two_wrapped_fields_with_leading():
    """description and justification wrap; a wrapped field with no leading is fatal."""
    from utils.svg_document import computed_style, stylesheet  # noqa: PLC0415

    root = _shipped_root()
    rules = stylesheet(root)
    index = FieldIndex(root)

    wrapped = []
    for field_id in set(_catalogue().mandatory) | set(_catalogue().optional):
        element = index.resolve(field_id)
        if element is None:
            continue
        style = computed_style(element, rules)
        if "shape-inside" in style:
            wrapped.append(field_id)
            assert style.get("line-height"), f"{field_id} wraps but declares no leading"

    assert set(wrapped) == {"description", "justification"}
