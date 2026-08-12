"""Field resolution: identifier first, layer label as fallback (Constitution XIV.2).

Pure tests — no database, no Discord, no rasteriser.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from utils.svg_document import FieldIndex, canvas_of, parse_svg_bytes  # noqa: E402

SVG_OPEN = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
    'width="400" height="300">'
)


def build(body: str):
    return parse_svg_bytes((SVG_OPEN + body + "</svg>").encode("utf-8"))


def layer(label: str, node_id: str, inner: str = "") -> str:
    return (
        f'<g inkscape:groupmode="layer" inkscape:label="{label}" id="{node_id}">'
        f"{inner}</g>"
    )


# ── Resolution precedence (FR-018, FR-019, FR-020) ────────────────────────


def test_resolves_by_identifier():
    index = FieldIndex(build('<text id="driver_name">x</text>'))
    element = index.resolve("driver_name")
    assert element is not None
    assert element.get("id") == "driver_name"


def test_resolves_by_layer_label_when_no_such_identifier():
    """FR-019 — the manager set the label; the editor generated the id."""
    index = FieldIndex(build(layer("driver_name", "g4821")))
    element = index.resolve("driver_name")
    assert element is not None
    assert element.get("id") == "g4821"


def test_identifier_wins_when_both_exist():
    """FR-020 — two different nodes, and the identifier is normative."""
    index = FieldIndex(
        build('<text id="driver_name">by id</text>' + layer("driver_name", "g99"))
    )
    element = index.resolve("driver_name")
    assert element is not None
    assert element.get("id") == "driver_name"
    assert element.text == "by id"


def test_unresolvable_name_returns_none():
    index = FieldIndex(build('<text id="points">3</text>'))
    assert index.resolve("driver_name") is None


def test_label_on_a_non_layer_group_is_not_an_address():
    """Inkscape writes labels on ordinary objects without the manager choosing them.

    Indexing those would let a field name collide with a shape nobody meant to address.
    """
    index = FieldIndex(
        build('<g inkscape:label="driver_name" id="g1"><rect id="r1"/></g>')
    )
    assert index.resolve("driver_name") is None
    assert index.resolve("g1") is not None  # still reachable by its identifier


def test_label_on_a_non_group_element_is_not_an_address():
    index = FieldIndex(build('<rect inkscape:label="driver_name" id="r2"/>'))
    assert index.resolve("driver_name") is None


def test_declared_unions_identifiers_and_layer_labels():
    """What `/images test` checks sample data against (T011b)."""
    index = FieldIndex(build('<text id="points">3</text>' + layer("driver_name", "g7")))
    assert index.declared() == {"points", "driver_name", "g7"}


def test_contains_follows_resolve():
    index = FieldIndex(build(layer("driver_name", "g7")))
    assert "driver_name" in index
    assert "missing" not in index


def test_first_of_duplicate_identifiers_wins():
    """A malformed template with a repeated id resolves deterministically."""
    index = FieldIndex(build('<text id="dup">first</text><text id="dup">second</text>'))
    assert index.resolve("dup").text == "first"


# ── Removable groups (FR-022 … FR-026) ────────────────────────────────────


def test_group_for_finds_the_wrapper_by_identifier():
    index = FieldIndex(
        build('<g id="sanctions_group"><text id="sanctions">x</text></g>')
    )
    group = index.group_for("sanctions")
    assert group is not None
    assert group.get("id") == "sanctions_group"


def test_group_for_finds_the_wrapper_by_layer_label():
    """A group is addressed on the same terms as any other field."""
    index = FieldIndex(build(layer("sanctions_group", "g55", '<text id="sanctions"/>')))
    group = index.group_for("sanctions")
    assert group is not None
    assert group.get("id") == "g55"


def test_group_for_returns_none_when_no_wrapper_declared():
    index = FieldIndex(build('<text id="sanctions">x</text>'))
    assert index.group_for("sanctions") is None


def test_group_wrapping_a_field_the_catalogue_does_not_name_is_still_found():
    """FR-025 — a template may declare groups beyond those a catalogue names."""
    index = FieldIndex(
        build('<g id="league_motto_group"><text id="league_motto">x</text></g>')
    )
    assert index.group_for("league_motto") is not None


def test_nested_groups_are_both_addressable():
    index = FieldIndex(
        build(
            '<g id="outer_group"><g id="inner_group">'
            '<text id="inner">x</text></g></g>'
        )
    )
    assert index.group_for("outer") is not None
    assert index.group_for("inner") is not None


def test_removing_a_group_does_not_change_the_canvas():
    """FR-026 — a block that may vanish belongs where a gap is survivable."""
    root = build('<g id="sanctions_group"><text id="sanctions">x</text></g>')
    before = canvas_of(root)

    index = FieldIndex(root)
    group = index.group_for("sanctions")
    group.getparent().remove(group)

    assert canvas_of(root) == before


def test_index_must_be_rebuilt_after_a_removal():
    """Documents the contract: a stale index still holds the detached element."""
    root = build('<g id="sanctions_group"><text id="sanctions">x</text></g>')
    index = FieldIndex(root)
    group = index.group_for("sanctions")
    group.getparent().remove(group)

    assert FieldIndex(root).resolve("sanctions") is None


@pytest.mark.parametrize("name", ["", "   ", "row_1_"])
def test_odd_names_resolve_to_nothing_rather_than_raising(name):
    index = FieldIndex(build('<text id="points">3</text>'))
    assert index.resolve(name) is None


# ══════════════════════════════════════════════════════════════════════════
# T043 / T044 / T045 / T046 — group removal, emptying, and the canvas
# ══════════════════════════════════════════════════════════════════════════

from models.image_catalogues import FieldCatalogue, RowSpec  # noqa: E402
from utils.svg_fill import FillSpec, fill  # noqa: E402
from utils.svg_document import parse_svg_bytes as _parse  # noqa: E402

FLAG_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"/>'


def render(body: str, **spec_kwargs):
    root = build(body)
    result = fill(FillSpec(root=root, image_type="t", **spec_kwargs))
    return result, _parse(result.svg)


def test_emptying_a_field_with_a_group_removes_the_whole_block():
    """FR-023 — the label leaves with the value it introduces."""
    result, out = render(
        '<g id="sanctions_group">'
        '<text id="sanctions_label">Sanctions</text>'
        '<text id="sanctions">x</text></g>',
        empty=["sanctions"],
    )
    index = FieldIndex(out)

    assert index.resolve("sanctions_group") is None
    assert index.resolve("sanctions_label") is None
    assert index.resolve("sanctions") is None
    assert result.unresolved == []


def test_emptying_a_field_without_a_group_strands_its_label():
    """FR-024 — the contrast the group convention exists to let an author avoid."""
    result, out = render(
        '<text id="sanctions_label">Sanctions</text><text id="sanctions">x</text>',
        empty=["sanctions"],
    )
    index = FieldIndex(out)

    assert index.resolve("sanctions_label") is not None   # stranded, by design
    assert index.resolve("sanctions") is not None
    assert not (index.resolve("sanctions").text or "")    # emptied, not removed


def test_emptying_raises_a_notice_naming_the_field():
    result, _ = render('<text id="sanctions">x</text>', empty=["sanctions"])

    assert len(result.notices) == 1
    assert result.notices[0].notice_kind == "OPTIONAL_FIELD_EMPTIED"
    assert result.notices[0].field_id == "sanctions"


def test_a_group_addressed_by_layer_label_is_removed_too():
    _, out = render(
        layer("sanctions_group", "g70", '<text id="sanctions">x</text>'),
        empty=["sanctions"],
    )
    assert FieldIndex(out).resolve("sanctions") is None


def test_removing_a_group_leaves_the_canvas_alone():
    """FR-026 — stated on the rendered output, not just the tree."""
    before, out_before = render('<g id="a_group"><text id="a">x</text></g>')
    after, out_after = render(
        '<g id="a_group"><text id="a">x</text></g>', empty=["a"]
    )
    assert before.canvas == after.canvas == (400, 300)
    assert canvas_of(out_before) == canvas_of(out_after)


def test_nested_groups_leave_together():
    _, out = render(
        '<g id="outer_group"><g id="inner_group">'
        '<text id="inner">x</text></g><text id="outer">y</text></g>',
        empty=["outer"],
    )
    index = FieldIndex(out)
    assert index.resolve("inner") is None
    assert index.resolve("outer") is None


def test_a_field_taken_off_by_its_group_is_not_unresolved():
    """Constitution XIV.3 — removal is not a failure to fill."""
    result, _ = render(
        '<g id="a_group"><text id="a">x</text></g>',
        empty=["a"],
        expected_fields={"a"},
    )
    assert result.unresolved == []


# ── T048: which bound a field gets is decided by what it declares ─────────


def test_inline_size_alone_truncates():
    result, out = render(
        '<text id="name" style="inline-size:40px;font-size:12px">x</text>',
        text={"name": "Bartholomew Fotheringay-Pemberton III"},
    )
    kinds = {n.notice_kind for n in result.notices}
    assert "INLINE_SIZE_TRUNCATED" in kinds
    assert FieldIndex(out).resolve("name").text.endswith("…")


def test_shape_inside_without_inline_size_wraps(tmp_path):
    """Spec A-002 — a shape-inside is meaningless except as a wrap instruction."""
    result, out = render(
        '<rect id="box" x="0" y="0" width="120" height="200"/>'
        '<text id="prose" style="shape-inside:url(#box);font-size:10px">x</text>',
        text={"prose": "one two three four five six seven eight nine ten eleven"},
    )
    tspans = list(FieldIndex(out).resolve("prose"))
    assert len(tspans) > 1, "a shape-inside field must wrap, not run on one line"


def test_declaring_neither_gives_one_unbounded_line():
    _, out = render(
        '<text id="plain">x</text>',
        text={"plain": "a very long value that nothing bounds at all whatsoever"},
    )
    element = FieldIndex(out).resolve("plain")
    assert len(list(element)) == 0
    assert element.text.startswith("a very long")
    assert "…" not in element.text


# ── T047: row ids are built, never concatenated by a utility ──────────────


def test_row_ids_come_from_the_rowspec():
    rows = RowSpec(capacity=3, fields=frozenset({"position", "driver_name"}))
    assert rows.field_id(2, "driver_name") == "row_2_driver_name"
    assert rows.group_id(2) == "row_2_group"
    assert "row_10_position" not in rows.all_field_ids()  # capacity is 3


def _rows(count: int) -> str:
    return "".join(
        f'<g id="row_{i}_group"><text id="row_{i}_position">{i}</text>'
        f'<text id="row_{i}_driver_name">d</text></g>'
        for i in range(1, count + 1)
    )


def test_emptying_a_row_takes_the_whole_row_with_it():
    """`row_3_group` wraps the row, so the row is addressed as `row_3`."""
    _, out = render(_rows(3), empty=["row_3"])
    index = FieldIndex(out)

    assert index.resolve("row_3_position") is None
    assert index.resolve("row_3_driver_name") is None
    assert index.resolve("row_2_driver_name") is not None


def test_emptying_one_field_of_a_row_leaves_the_rest_of_the_row():
    """A field is not its row: `row_3_position` has no `_group` of its own here."""
    _, out = render(_rows(3), empty=["row_3_position"])
    index = FieldIndex(out)

    assert index.resolve("row_3_driver_name") is not None
    assert not (index.resolve("row_3_position").text or "")


def test_unused_rows_are_removed_by_their_groups():
    """FR-022 — fewer data than slots: the spare rows leave (Constitution XIV.12)."""
    rows = RowSpec(capacity=5, fields=frozenset({"position", "driver_name"}))
    _, out = render(_rows(5), empty=[rows.row_id(i) for i in (4, 5)])
    index = FieldIndex(out)

    assert index.resolve("row_3_position") is not None
    assert index.resolve("row_4_position") is None
    assert index.resolve("row_5_position") is None
