"""The season grid of the two shipped standings templates, as geometry.

`tools/relayout_standings_grid.py` computes that grid from a handful of constants rather than
reading it out of the files, because every x in it moved at once and there was nothing left to
read it from. This file is the other side of that: it asserts the shipped files against the
same numbers, so the script and the templates cannot drift apart unnoticed.

**Nothing here consults a font.** The suite runs on three materially different hosts and they
resolve different faces; an assertion on rendered text would pass where its author sat and fail
everywhere else. The one number a font did produce — the width of the widest pair a cell may be
asked to carry — is recorded as a constant below and checked as pure geometry. What the raster
actually does with it is pinned by the marked test in `test_image_standings_post.py`.
"""
from __future__ import annotations

import os
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

SVG_NS = "http://www.w3.org/2000/svg"
_TEMPLATE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources", "defaults", "templates"
)

DRIVERS = "standings_drivers_template"
CONSTRUCTORS = "standings_constructors_template"

#: The widest pair a result cell may be asked to carry: an outcome literal at 11px with
#: another raised beside it at 8px — `DSQ` over `DSQ`. Measured at 45.6px against DejaVu Sans,
#: which is what CI's runners and the Raspberry Pi resolve when Inter is absent, and which is
#: the wider of the two. Rounded down to 46 and frozen here: a column narrower than this clips,
#: and the clipping is invisible in the markup because SVG text simply overruns its column.
WIDEST_PAIR = 46

#: What `tools/relayout_standings_grid.py` lays out.
COLUMN = 54
GUTTER = 2
PITCH = COLUMN * 2 + GUTTER
GRID_LEFT = 360
ROUNDS = 12
GRID_RIGHT = GRID_LEFT + PITCH * ROUNDS
CANVAS_WIDTH = GRID_RIGHT + 48


def _root(key: str):
    return etree.parse(os.path.join(_TEMPLATE_DIR, f"{key}.svg")).getroot()


def _cells(root):
    """Every result cell's `<text>`, keyed by the id of the result inside it."""
    found = {}
    for node in root.iter(f"{{{SVG_NS}}}text"):
        for span in node:
            span_id = span.get("id") or ""
            if span_id.endswith("_result"):
                found[span_id] = node
    return found


def _first_cell_id(key: str, round_ordinal: int, session: str) -> str:
    """The id of one race cell of round *z* on the first row.

    The constructors grid nests a car between the round and the session, so the two files
    do not name the same cell the same way; every test below asks for a cell through here.
    """
    car = "" if key == DRIVERS else "_driver_1"
    return f"row_1_round_{round_ordinal}{car}_{session}_race_result"


# ── The canvas and the columns ────────────────────────────────────────────


@pytest.mark.parametrize("key", [DRIVERS, CONSTRUCTORS])
def test_the_canvas_is_wide_enough_for_the_grid_it_draws(key):
    root = _root(key)
    assert int(float(root.get("width"))) == CANVAS_WIDTH
    assert root.get("viewBox").split()[2] == str(CANVAS_WIDTH)


@pytest.mark.parametrize("key", [DRIVERS, CONSTRUCTORS])
def test_a_session_column_clears_the_widest_pair_a_cell_may_carry(key):
    """The defect this re-layout exists to fix, stated as the number that fixes it."""
    root = _root(key)
    cells = _cells(root)
    sprint_x = float(cells[_first_cell_id(key, 1, "sprint")].get("x"))
    feature_x = float(cells[_first_cell_id(key, 1, "feature")].get("x"))
    spacing = feature_x - sprint_x
    assert spacing == COLUMN
    assert spacing >= WIDEST_PAIR, (
        f"a column of {spacing}px cannot hold the {WIDEST_PAIR}px widest pair; "
        f"`DSQ` beside a raised `DSQ` would overrun into the next round"
    )


@pytest.mark.parametrize("key", [DRIVERS, CONSTRUCTORS])
def test_the_rounds_are_evenly_pitched_and_end_at_the_right_margin(key):
    root = _root(key)
    cells = _cells(root)
    firsts = [
        float(cells[_first_cell_id(key, z, "sprint")].get("x"))
        for z in range(1, ROUNDS + 1)
    ]
    assert [round(b - a) for a, b in zip(firsts, firsts[1:])] == [PITCH] * (ROUNDS - 1)
    # The last feature column, its outer half, and the gutter that follows it, reach the
    # grid's right edge exactly — which is what makes the canvas width fall out of the pitch.
    last_feature = float(cells[_first_cell_id(key, ROUNDS, "feature")].get("x"))
    assert last_feature + COLUMN / 2 + GUTTER / 2 == GRID_RIGHT


@pytest.mark.parametrize("key", [DRIVERS, CONSTRUCTORS])
def test_the_classification_block_did_not_move(key):
    """Widening the grid is not licence to disturb what stands to the left of it."""
    root = _root(key)
    index = {n.get("id"): n for n in root.iter() if n.get("id")}
    assert float(index["row_1_team_name"].get("x")) < GRID_LEFT
    assert float(index["row_1_points"].get("x")) < GRID_LEFT


@pytest.mark.parametrize("key", [DRIVERS, CONSTRUCTORS])
def test_nothing_is_left_stranded_beyond_the_grid(key):
    """Every element that followed the old right margin followed it to the new one."""
    root = _root(key)
    stranded = []
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        for attr in ("x", "x1", "x2"):
            value = node.get(attr)
            if value is None:
                continue
            try:
                edge = float(value)
            except ValueError:
                continue
            width = node.get("width")
            if width and attr == "x":
                try:
                    edge += float(width)
                except ValueError:
                    pass
            if edge > CANVAS_WIDTH:
                stranded.append(f"{node.get('id') or node.tag}@{attr}={value}")
    assert stranded == []


# ── The highlight chips ───────────────────────────────────────────────────


@pytest.mark.parametrize("key", [DRIVERS, CONSTRUCTORS])
def test_every_chip_is_a_stretching_image_beneath_its_cell(key):
    """Three things must hold for a chip to do its job.

    It is an `<image>` — the chip is artwork now, not a colour. It stretches rather than
    fitting, which is the whole reason its class carries no fixed aspect. And it is authored
    **before** the text so it paints underneath rather than over it.

    Containment is asserted on the text's **anchor point alone** and never on any measured
    extent of the text, for the reason in this module's docstring.
    """
    root = _root(key)
    chips = [
        node
        for node in root.iter()
        if (node.get("id") or "").startswith("row_")
        and (node.get("id") or "").endswith(("_background", "_fastest_lap"))
    ]
    assert chips, "the shipped template declares no highlight chip at all"

    for chip in sorted(chips, key=lambda n: n.get("id")):
        chip_id = chip.get("id")
        assert etree.QName(chip).localname == "image", f"{chip_id} is not an <image>"
        assert chip.get("preserveAspectRatio") == "none", (
            f"{chip_id} would letterbox its asset instead of stretching it"
        )
        assert not chip.get("href") and not chip.get(
            "{http://www.w3.org/1999/xlink}href"
        ), f"{chip_id} ships an href; an unhighlighted cell must draw nothing"

        stem = chip_id.rsplit("_background", 1)[0].rsplit("_fastest_lap", 1)[0]
        siblings = list(chip.getparent())
        cell = next(
            (
                node
                for node in siblings
                if any((s.get("id") or "") == f"{stem}_result" for s in node.iter())
            ),
            None,
        )
        assert cell is not None, f"{chip_id} does not stand beside the cell it highlights"
        assert siblings.index(chip) < siblings.index(cell), (
            f"{chip_id} is authored after its text and would paint over it"
        )

        x, y = float(chip.get("x")), float(chip.get("y"))
        w, h = float(chip.get("width")), float(chip.get("height"))
        anchor_x, anchor_y = float(cell.get("x")), float(cell.get("y"))
        assert x <= anchor_x <= x + w, f"{chip_id} does not span its cell's anchor"
        assert y <= anchor_y <= y + h, f"{chip_id} does not cover its cell's baseline"


@pytest.mark.parametrize("key", [DRIVERS, CONSTRUCTORS])
def test_only_the_race_cells_carry_chips(key):
    """The raised qualifying figure has no fixed position, so nothing can sit behind it."""
    root = _root(key)
    for node in root.iter():
        node_id = node.get("id") or ""
        if node_id.endswith(("_background", "_fastest_lap")) and node_id.startswith("row_"):
            assert "_qualifying_" not in node_id, node_id


def test_the_two_grids_may_differ_in_chip_shape():
    """The point of the aspect exemption, asserted so it cannot be quietly undone.

    A drivers cell sits in a 32px row band and a constructors cell in a 20px car line. One
    ratio cannot serve both, which is why `standings_highlight` names none and its slots
    stretch (Constitution XIV.6, v7.4.0).
    """
    from models.image_constants import ASSET_CLASS_ASPECTS

    assert "standings_highlight" not in ASSET_CLASS_ASPECTS

    shapes = set()
    for key in (DRIVERS, CONSTRUCTORS):
        root = _root(key)
        chip = next(
            n
            for n in root.iter()
            if (n.get("id") or "").endswith("_feature_race_background")
        )
        shapes.add(round(float(chip.get("width")) / float(chip.get("height")), 3))
    assert len(shapes) == 2, "the two grids happen to agree; the exemption is untested"


# ── What the geometry above is for ────────────────────────────────────────


@pytest.mark.rasteriser
def test_the_widest_cell_a_grid_can_carry_stays_inside_its_column(tmp_path):
    """Fill every cell with the widest pair there is, and look for ink on the dividers.

    The geometry tests above assert the columns are 54 wide. This asserts what that buys:
    that `DSQ` with a raised `DSQ` beside it does not reach the rule drawn between one round
    and the next. It is the whole point of the re-layout, and no assertion on the markup can
    reach it — SVG text simply overruns its column and says nothing.

    **No text is measured.** The check is that the divider column of pixels holds nothing
    bright, whatever the host's font did with the glyphs; a host whose font is wider than
    DejaVu fails here, which is the correct outcome rather than a flaky one.
    """
    from PIL import Image  # noqa: PLC0415

    from services.image_render_service import rasterise
    from services.image_standings_service import (
        DRIVERS_TEMPLATE_KEY,
        CellValue,
        RoundCells,
        RoundHeading,
        StandingsDrawing,
        StandingsEntry,
        build_fill_spec,
    )
    from utils.svg_document import canvas_of, load_svg
    from utils.svg_fill import fill

    widest = {
        suffix: CellValue(text="DSQ" if suffix.endswith("_race_result") else "DNS")
        for suffix in (
            "sprint_qualifying_result",
            "sprint_race_result",
            "feature_qualifying_result",
            "feature_race_result",
        )
    }
    headings = [RoundHeading(ordinal=z, number=str(z)) for z in range(1, ROUNDS + 1)]
    entries = [
        StandingsEntry(
            ordinal=i,
            position=str(i),
            team_name="Team",
            points="0",
            driver_name=f"Driver {i}",
            cells={h.ordinal: RoundCells(sessions=dict(widest)) for h in headings},
        )
        for i in range(1, 11)
    ]
    drawing = StandingsDrawing(
        template_key=DRIVERS_TEMPLATE_KEY,
        division_name="Alpha",
        round_number="12",
        result_status_label="Final Results",
        nationality_collected=False,
        entries=entries,
        rounds=headings,
    )

    doc = load_svg(os.path.join(_TEMPLATE_DIR, f"{DRIVERS_TEMPLATE_KEY}.svg"))
    root = doc.root if hasattr(doc, "root") else doc
    result = fill(build_fill_spec(drawing, root))
    png = rasterise(result.svg, tmp_path / "widest.png", result.canvas or canvas_of(root))

    image = Image.open(png).convert("L")
    _width, height = image.size
    top, bottom = 336, min(336 + 32 * len(entries), height)

    for z in range(1, ROUNDS + 1):
        x = GRID_LEFT + PITCH * (z - 1)
        brightest = max(image.getpixel((x, y)) for y in range(top, bottom))
        assert brightest < 100, (
            f"text reaches the divider between rounds {z - 1} and {z} "
            f"(brightest pixel {brightest}); the column is too narrow for `DSQ`+`DSQ`"
        )
