"""Font resolution and measurement — T056.

A missing first-choice family is a **notice**, never a problem (Constitution XIV.4): a
host without a template's preferred face still renders.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.font_metrics import (  # noqa: E402
    ResolvedFont,
    font_index,
    measure,
    parse_font_family,
    resolve_family,
)


@pytest.fixture(scope="module")
def index():
    return font_index()


# ── Family list parsing ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "declaration,expected",
    [
        ("Inter", ["Inter"]),
        ("Inter, Arial, sans-serif", ["Inter", "Arial", "sans-serif"]),
        ("'Inter Tight', Arial", ["Inter Tight", "Arial"]),
        ('"Inter Tight" , Arial', ["Inter Tight", "Arial"]),
        (None, []),
        ("", []),
    ],
)
def test_family_lists_are_parsed_in_order(declaration, expected):
    assert parse_font_family(declaration) == expected


# ── Resolution and substitution ───────────────────────────────────────────


def test_an_installed_family_resolves_without_substitution(index):
    installed = next(iter(index))
    resolved = resolve_family(installed)
    assert resolved.substituted is False
    assert resolved.path is not None


def test_a_missing_first_choice_falls_through_to_the_next(index):
    """The face a renderer would actually land on, not the one that was asked for."""
    installed = next(name for name in index if " " not in name)
    resolved = resolve_family(f"NoSuchFaceAnywhere, {installed}")

    assert resolved.requested == "NoSuchFaceAnywhere"
    assert resolved.family.lower() == installed.lower()
    assert resolved.substituted is True


def test_a_wholly_absent_family_still_resolves_to_something(index):
    """Resolution never fails; it substitutes and says so."""
    resolved = resolve_family("NoSuchFaceAnywhere")
    assert resolved.requested == "NoSuchFaceAnywhere"
    assert resolved.substituted is True


def test_generic_family_resolves_and_counts_as_substitution():
    resolved = resolve_family("sans-serif")
    assert resolved.substituted is True


def test_index_is_built_once_per_process():
    """Rebuilding per render would cost more than the rasterisation."""
    first = font_index()
    second = font_index()
    assert first is second


def test_index_is_non_empty_on_a_real_host(index):
    assert len(index) > 0


# ── Measurement ───────────────────────────────────────────────────────────


def test_empty_string_measures_zero(index):
    assert measure("", resolve_family(next(iter(index))), 20) == 0.0


def test_measurement_scales_linearly_with_size(index):
    resolved = resolve_family(next(iter(index)))
    at_10 = measure("Verstappen", resolved, 10)
    at_20 = measure("Verstappen", resolved, 20)
    assert at_20 == pytest.approx(at_10 * 2, rel=1e-6)


def test_a_longer_string_is_wider(index):
    resolved = resolve_family(next(iter(index)))
    assert measure("Verstappen", resolved, 20) > measure("Ver", resolved, 20)


def test_measurement_is_proportional_not_monospaced(index):
    """A proportional face must not measure 'iii' and 'WWW' the same."""
    resolved = resolve_family("Arial")
    if resolved.path is None:
        pytest.skip("Arial not installed on this host")
    assert measure("WWW", resolved, 20) > measure("iii", resolved, 20)


def test_a_line_declared_to_fit_does_fit(index):
    """The property the wrap depends on: measurement must be tight enough to trust."""
    resolved = resolve_family("Arial")
    if resolved.path is None:
        pytest.skip("Arial not installed on this host")

    text = "The stewards reviewed the incident"
    width = measure(text, resolved, 20)

    # Adding any character must exceed the measured width.
    assert measure(text + "x", resolved, 20) > width
    # And the prefix must be strictly narrower.
    assert measure(text[:-1], resolved, 20) < width


def test_measurement_falls_back_rather_than_raising():
    """An unresolvable face still yields an estimate — a wrap must never crash."""
    unresolvable = ResolvedFont(None, None, "NoSuchFace", substituted=True)
    width = measure("Verstappen", unresolvable, 20)
    assert width > 0


def test_unknown_glyphs_do_not_raise(index):
    resolved = resolve_family(next(iter(index)))
    assert measure("中文\U0001f600", resolved, 20) >= 0
