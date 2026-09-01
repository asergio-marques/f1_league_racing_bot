"""A packaged file drawn into a slot it was not authored for says so — 2026-09-01.

Since the shape a class carries became the league's to choose, the artwork the bot ships
cannot follow it: `resources/defaults/` is drawn at one shape per class and answers for every
datum a league has not drawn itself. A league that re-shapes its flag slots to 2:1 therefore
keeps getting our 3:2 flags for the countries it has not drawn, stretched, and nothing else in
the module would mention it.

The notice is deliberately quiet in the ordinary case. Almost every league draws its templates
at the shape we ship, and a notice on every render for a fallback that fits perfectly would be
noise they would learn to ignore.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import (
    FALLBACK_ASSET_NAME,
    NOTICE_ASSET_FALLBACK_USED,
    NOTICE_PACKAGED_ASSET_OFF_SHAPE,
    PACKAGED_ASSET_ASPECTS,
    packaged_directory_for,
)
from utils.svg_document import parse_svg_bytes
from utils.svg_fill import FillSpec, fill

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"/>'


@pytest.fixture()
def league_flags(tmp_path):
    """A league's own flag directory, holding neither the country nor a fallback."""
    directory = tmp_path / "league_flags"
    directory.mkdir()
    return directory


@pytest.fixture()
def packaged_flags(tmp_path, monkeypatch):
    """The packaged flag directory, carrying its `fallback.svg` as the module ships one."""
    import utils.paths as paths_module

    root = tmp_path / "project"
    directory = root / packaged_directory_for("flag")
    directory.mkdir(parents=True)
    (directory / FALLBACK_ASSET_NAME).write_bytes(SVG)
    monkeypatch.setattr(paths_module, "PROJECT_ROOT", root, raising=False)
    return directory


def _render(directory, *, width, height, stretches=False, datum="Nonesuchland"):
    """Fill one calendar flag slot of the given shape, and return the notices raised."""
    stretch = ' preserveAspectRatio="none"' if stretches else ""
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400">'
        f'<image id="round_1_flag" width="{width}" height="{height}"{stretch}/>'
        "</svg>"
    ).encode("utf-8")
    result = fill(
        FillSpec(
            root=parse_svg_bytes(doc),
            image_type="calendar_template",
            image_data={"round_1_flag": ("flag", datum)},
            asset_directories={"flag": directory},
        )
    )
    return result.notices


def _kinds(notices):
    return {notice.notice_kind for notice in notices}


# ── it fires where a packaged file is genuinely the wrong shape ───────────


def test_a_packaged_flag_in_a_re_shaped_slot_is_reported(league_flags, packaged_flags):
    notices = _render(league_flags, width=120, height=60)

    off_shape = [n for n in notices if n.notice_kind == NOTICE_PACKAGED_ASSET_OFF_SHAPE]
    assert len(off_shape) == 1
    assert off_shape[0].field_id == "round_1_flag"
    assert "3:2" in off_shape[0].detail          # what we ship
    assert "2:1" in off_shape[0].detail          # what the slot is
    assert "stretched" in off_shape[0].detail


def test_it_is_raised_beside_the_fallback_notice_rather_than_instead_of_it(
    league_flags, packaged_flags
):
    """Two different facts: that a fallback was drawn, and that it does not fit."""
    notices = _render(league_flags, width=120, height=60)
    assert _kinds(notices) == {NOTICE_ASSET_FALLBACK_USED, NOTICE_PACKAGED_ASSET_OFF_SHAPE}


# ── and stays silent everywhere else ─────────────────────────────────────


def test_a_slot_at_the_shape_we_ship_says_nothing(league_flags, packaged_flags):
    """Which is every league that has re-shaped nothing, and so nearly all of them."""
    notices = _render(league_flags, width=120, height=80)
    assert NOTICE_PACKAGED_ASSET_OFF_SHAPE not in _kinds(notices)
    assert NOTICE_ASSET_FALLBACK_USED in _kinds(notices)


def test_the_leagues_own_file_is_never_reported(league_flags, packaged_flags):
    """Their own artwork is drawn at their own shape, which their templates agree with."""
    from utils.asset_resolver import filename_for

    (league_flags / filename_for("Nonesuchland")).write_bytes(SVG)
    notices = _render(league_flags, width=120, height=60)
    assert notices == []


def test_a_stretching_slot_is_never_reported(league_flags, packaged_flags):
    """It fills its box whatever shape either is, so nothing is letterboxed."""
    notices = _render(league_flags, width=120, height=60, stretches=True)
    assert NOTICE_PACKAGED_ASSET_OFF_SHAPE not in _kinds(notices)


def test_a_slot_declaring_no_usable_dimensions_is_not_guessed_at(
    league_flags, packaged_flags
):
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400">'
        '<image id="round_1_flag"/></svg>'
    ).encode("utf-8")
    result = fill(
        FillSpec(
            root=parse_svg_bytes(doc),
            image_type="calendar_template",
            image_data={"round_1_flag": ("flag", "Nonesuchland")},
            asset_directories={"flag": league_flags},
        )
    )
    assert NOTICE_PACKAGED_ASSET_OFF_SHAPE not in _kinds(result.notices)


def test_the_shape_it_compares_against_is_the_packaged_table():
    """Pinned so the notice cannot quietly start reporting against something else."""
    assert PACKAGED_ASSET_ASPECTS["flag"] == pytest.approx(1.5)
