"""Validity of a calendar template at the three moments — T019.

Constitution XIV.9: validity is evaluated when the template is named, at season review,
and immediately before a render, and all three read **one and the same evaluation**. These
tests exercise that evaluation directly, which is what makes the three agree by
construction rather than by coincidence.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import CATALOGUES, catalogue_for
from services.image_validity_service import (
    LAYER_BOUNDS,
    LAYER_CATALOGUE,
    LAYER_RESOLUTION,
    CatalogueLayer,
    TemplateContext,
    evaluate_template,
)

HEADER = '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
SUFFIXES = ("number", "country_name", "race_name", "date", "vertical_crop_point")


def _config():
    from models.image_constants import ASSET_DIRECTORIES, TEMPLATE_COLUMNS
    from models.image_module import ImageConfig

    return ImageConfig(
        server_id=1,
        module_enabled=True,
        template_directory="templates",
        # Every asset directory, pointed at the **packaged** folder rather than the
        # league one the column actually defaults to. `resources/defaults/` is tracked
        # and identical on every host; `resources/league/` is gitignored and holds
        # whatever the machine running this happens to carry.
        #
        # Derived rather than listed, so an asset class added later arrives here on its
        # own. Listing them is what made the eighth class break five fixtures at once.
        **{
            column: packaged
            for column, (_cmd, _league, packaged) in ASSET_DIRECTORIES.items()
        },
        use_pfp=False,
        pfp_prerender=True,
        pfp_daily=False,
        pfp_daily_time="03:00",
        time_zone="UTC",
        time_format="24H",
        date_format="DDD_DD_MON_YYYY",
        fastest_lap_colour="#A020F0",
        **dict(TEMPLATE_COLUMNS),
    )


def _round_markup(index: int, *, omit: str | None = None) -> str:
    out = []
    for suffix in SUFFIXES:
        if suffix == omit:
            continue
        out.append(f'<text id="round_{index}_{suffix}">x</text>')
    return "".join(out)


def _template(*, rounds=(1,), division_name=True, omit=None) -> bytes:
    body = '<text id="division_name">d</text>' if division_name else ""
    for index in rounds:
        body += _round_markup(index, omit=omit if index == rounds[-1] else None)
    return (HEADER + body + "</svg>").encode()


@pytest.fixture()
def named(tmp_path):
    """Write a calendar template and evaluate it exactly as the module does."""

    def _write(markup: bytes):
        directory = tmp_path / "templates"
        directory.mkdir(exist_ok=True)
        path = directory / "calendar_template.svg"
        path.write_bytes(markup)
        ctx = TemplateContext(
            config=_config(),
            template_key="calendar_template",
            root=tmp_path,
        )
        return evaluate_template(ctx)

    return _write


# ── The layer applies at all ──────────────────────────────────────────────


def test_layer_two_applies_to_the_calendar_now_it_has_a_catalogue():
    assert CatalogueLayer().applies_to("calendar_template")
    assert not catalogue_for("calendar_template").is_empty


def test_layer_two_still_skips_a_type_with_no_catalogue():
    """XIV.9.4 — a type checked shallowly must not read as fully valid.

    Every one of the fifteen catalogues is populated as of 043, so the condition is staged
    rather than found: what it proves must still hold for whichever type is specified next.
    """
    from models.image_catalogues import FieldCatalogue

    saved = dict(CATALOGUES)
    try:
        CATALOGUES["verdicts_template"] = FieldCatalogue()
        assert not CatalogueLayer().applies_to("verdicts_template")
        assert CATALOGUES["verdicts_template"].is_empty
    finally:
        CATALOGUES.clear()
        CATALOGUES.update(saved)


# ── A sound template ──────────────────────────────────────────────────────


def test_a_sound_template_passes_to_layer_two(named):
    report = named(_template(rounds=(1, 2, 3)))
    assert report.valid, report.reason
    assert report.depth_checked == LAYER_BOUNDS


def test_a_single_round_template_is_sound(named):
    """Round count is not judged when the template is named — no division is in view."""
    report = named(_template(rounds=(1,)))
    assert report.valid, report.reason


# ── The faults, each named in its own terms ───────────────────────────────


def test_a_missing_whole_graphic_field_is_named(named):
    report = named(_template(rounds=(1,), division_name=False))
    assert not report.valid
    assert report.failed_layer == LAYER_CATALOGUE
    assert "division_name" in report.reason


def test_a_missing_round_field_is_named(named):
    report = named(_template(rounds=(1, 2), omit="date"))
    assert not report.valid
    assert "round_2_date" in report.reason


def test_a_missing_crop_point_is_named(named):
    report = named(_template(rounds=(1,), omit="vertical_crop_point"))
    assert not report.valid
    assert "round_1_vertical_crop_point" in report.reason


def test_a_template_declaring_no_round_is_rejected(named):
    report = named((HEADER + '<text id="division_name">d</text></svg>').encode())
    assert not report.valid
    assert "declares no `round`" in report.reason


def test_a_gap_in_the_round_numbering_is_rejected(named):
    report = named(_template(rounds=(1, 2, 4)))
    assert not report.valid
    assert "gap" in report.reason
    assert "3" in report.reason


def test_an_uncountable_template_fails_layer_two_not_layer_one(named):
    """The file resolves and parses; it is the collection that cannot be counted."""
    report = named(_template(rounds=(1, 3)))
    assert report.failed_layer == LAYER_CATALOGUE
    assert report.depth_checked >= LAYER_RESOLUTION
