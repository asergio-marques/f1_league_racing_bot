"""Restating a colour in another hue: the LCH conversion in `leaguebot.image.utils.colour`.

Two rules are under test and both were settled by rendering rather than by argument, so they
are pinned here against being "simplified" back to the versions that looked reasonable and were
wrong:

* the hue is **set** to the target's, never rotated by the offset the colour happened to have —
  rotating the shipped greys by the 81 degrees that takes cyan to violet lands them at hue 339,
  which is red;
* the chroma is **scaled down**, because greys keeping their original chroma read as neutral in
  blue and tinted in violet.

These moved here from the tests of `tools/tier_palette.py`, the script that uses them, when
scripts in `tools/` stopped being unit-tested (2026-09-16). The functions are the bot's own, in
`src/`, and stay under test.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

from leaguebot.image.utils.colour import from_lch, restate_in_hue, to_lch  # noqa: E402

#: The shipped league palette, as the drawings declare it.
PALETTE = {
    "page": "#12161B", "rail": "#0A0D11", "band": "#171C22", "rule": "#242C35",
    "rule_dim": "#1A2028", "ink": "#F4F7FA", "ink_2": "#C9D3DE", "ink_3": "#8493A2",
    "ink_4": "#5C6874", "accent": "#3DD6F5",
}
VIOLET = "#A78BFA"


# ── The colour maths ──────────────────────────────────────────────────────

@pytest.mark.parametrize("colour", sorted(PALETTE.values()))
def test_the_conversion_round_trips(colour):
    assert from_lch(*to_lch(colour)) == colour


def test_the_shipped_accent_measures_as_expected():
    lightness, chroma, hue = to_lch("#3DD6F5")
    assert round(lightness) == 79
    assert round(chroma) == 39
    assert round(hue) == 222


def test_the_shipped_greys_are_near_neutral():
    """Between 2 and 10 — which is what makes them read as grey rather than as blue."""
    for slot in ("rail", "page", "band", "rule", "rule_dim", "ink", "ink_2", "ink_3", "ink_4"):
        assert 1.0 < to_lch(PALETTE[slot])[1] < 11.0


def test_a_pure_grey_has_no_chroma():
    assert to_lch("#808080")[1] == pytest.approx(0.0, abs=0.01)


def test_restating_keeps_the_lightness():
    """Lightness is what keeps text readable; the derivation must never move it."""
    for colour in PALETTE.values():
        before = to_lch(colour)[0]
        after = to_lch(restate_in_hue(colour, to_lch(VIOLET)[2], 0.35))[0]
        assert after == pytest.approx(before, abs=0.4)


def test_restating_sets_the_hue_rather_than_turning_it():
    """The whole point: a restated colour sits at the accent's hue, wherever it began.

    Asserted as a distance in the a*/b* plane rather than as an angle, because hue angle is
    not a fair measure at low chroma: a near-grey has a tiny a*/b*, so rounding it to 8-bit
    sRGB swings the angle a long way while barely moving the colour. The shipped `#0A0D11`
    lands 4.5 degrees off at chroma 2.3 and 0.3 degrees off at chroma 6.7 — the same error,
    differently reported. Half a unit is below what 8-bit sRGB can represent at all.
    """
    target = to_lch(VIOLET)[2]
    for colour in sorted(PALETTE.values()):
        chroma = to_lch(colour)[1]
        got = to_lch(restate_in_hue(colour, target, 1.0))[2]
        chord = 2 * chroma * math.sin(math.radians(abs(got - target)) / 2)
        assert chord < 0.5, f"{colour} landed {chord:.2f} away in a*/b*"


def test_the_hue_is_exact_where_there_is_chroma_enough_to_carry_one():
    """The readable form of the claim above, on the colours that can express it."""
    target = to_lch(VIOLET)[2]
    carriers = [c for c in PALETTE.values() if to_lch(c)[1] >= 5]
    assert len(carriers) >= 5
    for colour in carriers:
        assert to_lch(restate_in_hue(colour, target, 1.0))[2] == pytest.approx(target, abs=1.6)


def test_restating_scales_the_chroma():
    before = to_lch("#8493A2")[1]
    after = to_lch(restate_in_hue("#8493A2", to_lch(VIOLET)[2], 0.35))[1]
    assert after == pytest.approx(before * 0.35, abs=0.5)


def test_a_scale_of_zero_gives_a_true_grey():
    """`--chroma 0` must be exactly neutral, not nearly — it is the safe-for-any-hue option."""
    grey = restate_in_hue("#8493A2", to_lch(VIOLET)[2], 0.0)
    assert grey[1:3] == grey[3:5] == grey[5:7]


def test_the_offset_rotation_that_was_rejected_really_does_land_in_red():
    """Evidence for the rule, so nobody reinstates the offset thinking it equivalent."""
    accent_shift = (to_lch(VIOLET)[2] - to_lch("#3DD6F5")[2]) % 360
    rotated = (to_lch("#8493A2")[2] + accent_shift) % 360
    assert 320 < rotated < 355                      # the red-pink arc
    red, _, blue = (int(from_lch(*(to_lch("#8493A2")[:2]), rotated)[i:i+2], 16) for i in (1, 3, 5))
    assert red > blue                               # warm, which the greys must never be
