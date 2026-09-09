"""Reading a palette a human supplied: the pasted block, and the XML payload.

The rule these encode is that **the division is the unit of atomicity** — a block naming an
unknown tier, or carrying one bad colour, is rejected whole while its neighbours import. A
tier drawn in four of its ten colours looks deliberate, which is worse than a tier not yet
configured looking unconfigured.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from utils.palette_import import (  # noqa: E402
    PaletteXmlError,
    parse_palette_lines,
    parse_palette_xml,
)


# ── The pasted block ──────────────────────────────────────────────────────

def test_a_plain_block_is_read():
    colours, problems = parse_palette_lines("accent #A78BFA\nink #F4F7FA")
    assert colours == {"accent": "#A78BFA", "ink": "#F4F7FA"}
    assert problems == []


@pytest.mark.parametrize("line", [
    "accent #A78BFA", "accent=#A78BFA", "accent:#A78BFA",
    "accent,#A78BFA", "accent\t#A78BFA", "   accent    #A78BFA   ",
])
def test_the_separator_is_whatever_a_person_typed(line):
    assert parse_palette_lines(line)[0] == {"accent": "#A78BFA"}


def test_a_hash_is_only_a_comment_at_the_start_of_a_line():
    """Every colour begins with one, so a trailing-comment rule would eat every value."""
    colours, problems = parse_palette_lines(
        "# accent #3DD6F5 was the old one\naccent #A78BFA\n"
    )
    assert colours == {"accent": "#A78BFA"}
    assert problems == []


def test_the_tools_own_output_pastes_whole():
    """Its table lines start with `#`, so a league need not strip them first."""
    colours, problems = parse_palette_lines(
        "# resources/league/templates/x.svg  ·  accent #A78BFA  ·  chroma x0.35\n"
        "# 2 slots for Division 2\n"
        "#   accent     #A78BFA   L*  64.6  C* 62.3\n"
        "\n"
        "accent #A78BFA\n"
        "ink #F7F6F8\n"
    )
    assert colours == {"accent": "#A78BFA", "ink": "#F7F6F8"}
    assert problems == []


def test_the_colour_is_canonicalised():
    assert parse_palette_lines("accent #a78bfa")[0] == {"accent": "#A78BFA"}


def test_the_slot_is_lower_cased():
    assert parse_palette_lines("Accent #A78BFA")[0] == {"accent": "#A78BFA"}


def test_a_bad_colour_names_its_line():
    colours, problems = parse_palette_lines("accent #A78BFA\nink purple")
    assert colours == {"accent": "#A78BFA"}
    assert len(problems) == 1 and "line 2" in problems[0]


def test_a_line_that_is_not_a_pair_is_reported():
    assert "line 1" in parse_palette_lines("nonsense")[1][0]


def test_a_slot_that_could_escape_a_selector_is_refused():
    assert parse_palette_lines("a{}b #A78BFA")[1]


def test_a_slot_given_twice_with_two_colours_is_reported():
    colours, problems = parse_palette_lines("accent #A78BFA\naccent #4ADE80")
    assert colours == {"accent": "#A78BFA"}
    assert len(problems) == 1 and "twice" in problems[0]


def test_a_slot_given_twice_with_one_colour_is_fine():
    assert parse_palette_lines("accent #A78BFA\naccent #A78BFA") == (
        {"accent": "#A78BFA"}, []
    )


def test_an_empty_block_says_so_rather_than_succeeding_silently():
    colours, problems = parse_palette_lines("\n# only a comment\n")
    assert colours == {}
    assert problems and "Nothing to set" in problems[0]


def test_every_fault_is_reported_not_just_the_first():
    """A league fixing them one submission at a time is a league the check is failing."""
    assert len(parse_palette_lines("bad one\nalso bad\nthird bad")[1]) == 3


# ── The XML payload ───────────────────────────────────────────────────────

GOOD = """
<palettes>
  <division name="Division 1">
    <colour slot="accent">#3DD6F5</colour>
    <colour slot="ink">#F4F7FA</colour>
  </division>
  <division name="Division 2">
    <colour slot="accent">#A78BFA</colour>
  </division>
</palettes>
"""


def test_a_good_document_yields_a_block_per_division():
    blocks, problems = parse_palette_xml(GOOD)
    assert problems == []
    assert [b.division for b in blocks] == ["Division 1", "Division 2"]
    assert blocks[0].colours == {"accent": "#3DD6F5", "ink": "#F4F7FA"}


def test_a_bad_block_is_rejected_and_the_others_still_import():
    """The decision this file exists for: one mistyped tier does not cost the rest."""
    blocks, problems = parse_palette_xml(
        '<palettes>'
        '<division name="Good"><colour slot="accent">#A78BFA</colour></division>'
        '<division name="Bad"><colour slot="accent">purple</colour></division>'
        '</palettes>'
    )
    assert [b.division for b in blocks] == ["Good"]
    assert len(problems) == 1 and "Bad" in problems[0]


def test_a_block_is_never_half_applied():
    """One bad colour rejects the whole division, not merely that colour."""
    blocks, problems = parse_palette_xml(
        '<palettes><division name="D">'
        '<colour slot="accent">#A78BFA</colour>'
        '<colour slot="ink">nonsense</colour>'
        '</division></palettes>'
    )
    assert blocks == []
    assert problems


def test_a_division_without_a_name_is_rejected_and_located():
    blocks, problems = parse_palette_xml(
        '<palettes><division><colour slot="accent">#A78BFA</colour></division></palettes>'
    )
    assert blocks == []
    assert "1st <division>" in problems[0]


def test_a_division_named_twice_keeps_the_first():
    blocks, problems = parse_palette_xml(
        '<palettes>'
        '<division name="D"><colour slot="accent">#111111</colour></division>'
        '<division name="d"><colour slot="accent">#222222</colour></division>'
        '</palettes>'
    )
    assert len(blocks) == 1 and blocks[0].colours == {"accent": "#111111"}
    assert "more than once" in problems[0]


def test_a_division_with_no_colours_is_rejected():
    blocks, problems = parse_palette_xml('<palettes><division name="D"/></palettes>')
    assert blocks == []
    assert "names no colours" in problems[0]


def test_malformed_xml_fails_outright():
    """Nothing can be salvaged from a document that does not parse."""
    with pytest.raises(PaletteXmlError):
        parse_palette_xml("<palettes><division name='D'>")


def test_a_document_with_no_divisions_fails_outright():
    with pytest.raises(PaletteXmlError):
        parse_palette_xml("<palettes/>")


def test_an_entity_is_not_resolved():
    """A league uploads this file; it must not be able to read the host's filesystem."""
    payload = (
        '<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<palettes><division name="D">'
        '<colour slot="accent">&xxe;</colour></division></palettes>'
    )
    try:
        blocks, problems = parse_palette_xml(payload)
    except PaletteXmlError:
        return                      # refused outright, which is also correct
    assert blocks == []             # never accepted with the entity expanded
    assert problems
