"""Unit tests for hex parsing and WCAG contrast — T044, T045.

FR-025 fixes the accepted form exactly: a `#` followed by six hex digits, either case.
FR-026 names 4.5:1, the WCAG AA threshold for normal-size text.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.colour import (  # noqa: E402
    CONTRAST_AA_NORMAL,
    InvalidColour,
    coerce_css_colour,
    contrast_ratio,
    is_valid_hex,
    meets_aa_normal,
    normalise_hex,
    parse_hex,
    relative_luminance,
)


# ── T044: the accepted form ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "A020F0",      # no hash
        "#A020F",      # five digits
        "#A020F0A",    # seven digits
        "#A020F0AB",   # eight digits (alpha form)
        "#ABC",        # three-digit shorthand
        "#GGGGGG",     # not hexadecimal
        "#12345Z",     # one bad digit
        "purple",      # a name
        "rgb(160,32,240)",
        "",
        "   ",
    ],
)
def test_rejected_forms(value):
    with pytest.raises(InvalidColour):
        parse_hex(value)
    assert not is_valid_hex(value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("#A020F0", (160, 32, 240)),
        ("#a020f0", (160, 32, 240)),
        ("#A020f0", (160, 32, 240)),
        ("#000000", (0, 0, 0)),
        ("#FFFFFF", (255, 255, 255)),
        ("  #A020F0  ", (160, 32, 240)),
    ],
)
def test_accepted_forms_in_either_case(value, expected):
    assert parse_hex(value) == expected
    assert is_valid_hex(value)


def test_none_is_rejected():
    with pytest.raises(InvalidColour):
        parse_hex(None)


def test_error_states_the_required_form():
    with pytest.raises(InvalidColour) as excinfo:
        parse_hex("#ABC")
    message = str(excinfo.value)
    assert "six" in message
    assert "#A020F0" in message


def test_normalise_is_canonical_uppercase():
    assert normalise_hex("#a020f0") == "#A020F0"
    assert normalise_hex("#A020F0") == "#A020F0"


def test_default_fastest_lap_colour_is_valid():
    """The packaged default must survive its own validation."""
    assert is_valid_hex("#A020F0")


# ── T045: the WCAG formula ────────────────────────────────────────────────


def test_luminance_endpoints():
    assert relative_luminance((0, 0, 0)) == pytest.approx(0.0)
    assert relative_luminance((255, 255, 255)) == pytest.approx(1.0)


def test_black_on_white_is_the_maximum_ratio():
    """21:1 is the highest ratio the formula can produce."""
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)


def test_a_colour_against_itself_is_one():
    assert contrast_ratio("#A020F0", "#A020F0") == pytest.approx(1.0, abs=0.001)


def test_ratio_is_symmetric():
    """Argument order must not matter."""
    forward = contrast_ratio("#A020F0", "#FFFFFF")
    backward = contrast_ratio("#FFFFFF", "#A020F0")
    assert forward == pytest.approx(backward)


@pytest.mark.parametrize(
    "foreground,background,expected",
    [
        # Published WCAG reference pairs.
        ("#FFFFFF", "#000000", 21.0),
        ("#777777", "#FFFFFF", 4.48),
        ("#767676", "#FFFFFF", 4.54),
        ("#0000FF", "#FFFFFF", 8.59),
        ("#FF0000", "#FFFFFF", 4.0),
    ],
)
def test_known_pairs_match_published_values(foreground, background, expected):
    assert contrast_ratio(foreground, background) == pytest.approx(expected, abs=0.02)


def test_the_threshold_classifies_correctly_either_side():
    """#777777 on white is just under 4.5; #767676 is just over."""
    just_under = contrast_ratio("#777777", "#FFFFFF")
    just_over = contrast_ratio("#767676", "#FFFFFF")

    assert just_under < CONTRAST_AA_NORMAL
    assert just_over >= CONTRAST_AA_NORMAL
    assert not meets_aa_normal(just_under)
    assert meets_aa_normal(just_over)


def test_exact_threshold_counts_as_meeting_it():
    assert meets_aa_normal(4.5)
    assert not meets_aa_normal(4.49)


def test_default_purple_on_a_dark_plate_is_low_contrast():
    """The packaged default is a legibility warning on a dark plate — and is kept anyway."""
    ratio = contrast_ratio("#A020F0", "#1A1A1A")
    assert not meets_aa_normal(ratio)


# ── Reading a template's declared background ──────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("#A020F0", "#A020F0"),
        ("#a020f0", "#A020F0"),
        ("#ABC", "#AABBCC"),
        ("white", "#FFFFFF"),
        ("BLACK", "#000000"),
        ("grey", "#808080"),
        ("rgb(160, 32, 240)", "#A020F0"),
        ("rgba(160, 32, 240, 0.5)", "#A020F0"),
    ],
)
def test_css_colours_are_coerced(value, expected):
    assert coerce_css_colour(value) == expected


@pytest.mark.parametrize(
    "value",
    ["url(#gradient)", "currentColor", "none", "transparent", "", None, "hsl(280,80%,50%)"],
)
def test_unrecognised_css_colour_returns_none(value):
    """None is a real outcome: FR-027 requires it be reported, not guessed."""
    assert coerce_css_colour(value) is None
