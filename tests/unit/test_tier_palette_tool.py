"""Deriving a tier's palette: the colour maths, and the tool that prints it.

Two rules are under test and both were settled by rendering rather than by argument, so
they are pinned here against being "simplified" back to the versions that looked reasonable
and were wrong:

* the hue is **set** to the accent's, never rotated by the offset the drawing happened to
  have — rotating the shipped greys by the 81 degrees that takes cyan to violet lands them
  at hue 339, which is red;
* the chroma is **scaled down**, because greys keeping their original chroma read as
  neutral in blue and tinted in violet.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from utils.colour import from_lch, restate_in_hue, to_lch  # noqa: E402
from utils.svg_document import parse_svg_bytes  # noqa: E402

import tier_palette  # noqa: E402

#: The shipped league palette, as the drawings declare it.
PALETTE = {
    "page": "#12161B", "rail": "#0A0D11", "band": "#171C22", "rule": "#242C35",
    "rule_dim": "#1A2028", "ink": "#F4F7FA", "ink_2": "#C9D3DE", "ink_3": "#8493A2",
    "ink_4": "#5C6874", "accent": "#3DD6F5",
}
VIOLET = "#A78BFA"


def _svg(rules: str) -> object:
    """A drawing declaring *rules*, with one element carrying every class they name.

    The element matters: a rule nothing uses is not a slot the bot would demand either,
    so a fixture without one would be testing a state that cannot occur.
    """
    import re as _re

    classes = sorted(set(_re.findall(r"\.([a-z0-9_-]+)\s*\{", rules)))
    # One element per class, not one carrying them all: an element with two colour classes
    # has a single computed fill, so both slots would read the same colour and the fixture
    # would assert a state no real drawing produces.
    body = "".join(f'<rect class="{name}"/>' for name in classes)
    return parse_svg_bytes(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        f"<defs><style>{rules}</style></defs>{body}</svg>".encode()
    )


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


# ── Reading the palette out of a drawing ──────────────────────────────────

def _inline_svg(body: str) -> object:
    return parse_svg_bytes(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">{body}</svg>'.encode()
    )


def test_the_palette_is_read_where_the_colour_sits_on_the_element():
    """The other authoring form the guides offer, and the one that used to find nothing.

    A drawing may give a slot its default inline rather than as a stylesheet rule. Read
    only from the stylesheet, such a drawing yielded an empty palette — so the tool said
    "no colour slots" about a drawing the bot was simultaneously demanding colours for.
    """
    root = _inline_svg(
        '<rect fill="#3DD6F5" class="colour-fill-accent"/>'
        '<text fill="#F4F7FA" class="colour-fill-ink">x</text>'
    )
    assert tier_palette.base_palette(root) == {"accent": "#3DD6F5", "ink": "#F4F7FA"}


def test_the_tool_finds_exactly_the_slots_the_bot_demands():
    """The property that makes the two agree, whichever way a drawing is authored.

    `colour_slots` is what the shortfall check measures a league against; anything it finds
    and this does not is a colour the bot blocks on and the tool cannot help with.
    """
    from utils.svg_palette import colour_slots

    for root in (
        _inline_svg('<rect fill="#3DD6F5" class="colour-fill-accent"/>'),
        _svg(".colour-fill-accent { fill:#3DD6F5 }"),
        _inline_svg(
            '<defs><style>.colour-fill-ink { fill:#F4F7FA }</style></defs>'
            '<rect fill="#3DD6F5" class="colour-fill-accent"/>'
            '<text class="colour-fill-ink">x</text>'
        ),
    ):
        found = set(tier_palette.base_palette(root))
        assert found == colour_slots(root), f"tool {found} vs bot {colour_slots(root)}"


def test_a_stroke_slot_is_read_from_the_element_too():
    root = _inline_svg('<path stroke="#242C35" class="colour-stroke-rule"/>')
    assert tier_palette.base_palette(root) == {"rule": "#242C35"}


def test_a_colour_written_some_other_css_way_is_understood():
    """A hand-authored drawing may say `rgb(...)` or a colour name."""
    root = _inline_svg('<rect fill="rgb(61, 214, 245)" class="colour-fill-accent"/>')
    assert tier_palette.base_palette(root) == {"accent": "#3DD6F5"}


def test_the_first_element_of_a_slot_settles_it():
    """Deterministic, so two runs of one drawing print the same palette."""
    root = _inline_svg(
        '<rect fill="#111111" class="colour-fill-accent"/>'
        '<rect fill="#222222" class="colour-fill-accent"/>'
    )
    assert tier_palette.base_palette(root) == {"accent": "#111111"}


def test_the_palette_is_read_from_the_drawings_own_rules():
    root = _svg(".colour-fill-ink { fill:#F4F7FA } .colour-fill-page { fill:#12161B }")
    assert tier_palette.base_palette(root) == {"ink": "#F4F7FA", "page": "#12161B"}


def test_a_stroke_slot_is_read_too():
    root = _svg(".colour-stroke-rule { stroke:#242C35 }")
    assert tier_palette.base_palette(root) == {"rule": "#242C35"}


def test_fill_wins_where_a_slot_declares_both():
    root = _svg(".colour-stroke-rule { stroke:#111111 } .colour-fill-rule { fill:#242C35 }")
    assert tier_palette.base_palette(root) == {"rule": "#242C35"}


def test_rules_that_are_not_slots_are_ignored():
    root = _svg(".headline { fill:#F4F7FA } .colour-background-x { fill:#000000 }")
    assert tier_palette.base_palette(root) == {}


def test_a_drawing_with_no_slots_yields_nothing():
    assert tier_palette.base_palette(_svg(".headline { font-size:42px }")) == {}


# ── Deriving and printing ─────────────────────────────────────────────────

def test_the_accent_itself_is_never_restated():
    derived = tier_palette.derive(PALETTE, VIOLET, 0.35)
    assert derived["accent"] == VIOLET


def test_every_slot_survives_the_derivation():
    assert set(tier_palette.derive(PALETTE, VIOLET, 0.35)) == set(PALETTE)


def test_the_commands_name_the_division_and_every_slot():
    derived = tier_palette.derive(PALETTE, VIOLET, 0.35)
    lines = tier_palette.commands("Division 2", derived)
    assert len(lines) == len(PALETTE)
    assert all(line.startswith("/images config per-tier-set-colour ") for line in lines)
    assert all("division:Division 2" in line for line in lines)
    for slot, colour in derived.items():
        assert any(f"slot:{slot} colour:{colour}" in line for line in lines)


def test_the_slots_come_out_in_a_settled_order():
    """Two runs of the same input print the same thing, so the output can be diffed."""
    a = tier_palette.commands("D", tier_palette.derive(PALETTE, VIOLET, 0.35))
    b = tier_palette.commands("D", tier_palette.derive(dict(reversed(list(PALETTE.items()))), VIOLET, 0.35))
    assert a == b


# ── The command line ──────────────────────────────────────────────────────

def test_a_malformed_accent_is_refused(capsys):
    assert tier_palette.main(["--division", "D", "--accent", "purple"]) == 2
    assert "not a valid colour" in capsys.readouterr().err


def test_a_negative_chroma_is_refused(capsys):
    assert tier_palette.main(
        ["--division", "D", "--accent", "#A78BFA", "--chroma", "-1"]
    ) == 2
    assert "cannot be negative" in capsys.readouterr().err


def test_a_missing_drawing_is_refused(capsys):
    assert tier_palette.main(
        ["--division", "D", "--accent", "#A78BFA", "--template", "no/such.svg"]
    ) == 2
    assert "no such drawing" in capsys.readouterr().err


def test_a_drawing_with_no_slots_says_so(tmp_path, capsys):
    drawing = tmp_path / "plain.svg"
    drawing.write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect/></svg>'
    )
    assert tier_palette.main(
        ["--division", "D", "--accent", "#A78BFA", "--template", str(drawing)]
    ) == 1
    assert "declares no colour slots" in capsys.readouterr().err


def test_it_prints_the_commands_for_a_slotted_drawing(tmp_path, capsys):
    drawing = tmp_path / "slotted.svg"
    drawing.write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><defs><style>'
        b".colour-fill-accent { fill:#3DD6F5 } .colour-fill-ink { fill:#F4F7FA }"
        b'</style></defs><rect class="colour-fill-accent"/>'
        b'<text class="colour-fill-ink">x</text></svg>'
    )
    assert tier_palette.main(
        ["--division", "Division 2", "--accent", "#A78BFA", "--template", str(drawing)]
    ) == 0
    out = capsys.readouterr().out
    assert "slot:accent colour:#A78BFA" in out
    assert "slot:ink colour:" in out


def test_it_writes_nothing(tmp_path):
    """A tool that only prints cannot damage a configuration by being run."""
    drawing = tmp_path / "slotted.svg"
    drawing.write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><defs><style>'
        b".colour-fill-accent { fill:#3DD6F5 }</style></defs>"
        b'<rect class="colour-fill-accent"/></svg>'
    )
    before = drawing.read_bytes()
    tier_palette.main(
        ["--division", "D", "--accent", "#A78BFA", "--template", str(drawing)]
    )
    assert drawing.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["slotted.svg"]
