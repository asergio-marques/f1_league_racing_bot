#!/usr/bin/env python3
"""Re-lay the season grid of the two standings templates, and draw its highlight chips.

Supersedes ``add_standings_cell_highlights.py``, which inserted the chips as recoloured
``<rect>``s into the grid as it then stood. Both things it did are done here, because the
grid's geometry is now changing underneath them:

* **The columns are widened.** A result cell may hold an outcome literal with another raised
  beside it — ``DSQ`` over ``DSQ`` — which measures 45.6px in the font CI and the Raspberry Pi
  actually resolve. The columns were 32px on the drivers grid and 24px on the constructors
  one, so the widest pair overran its neighbour. They are now 54px on both.
* **The chips are ``<image>`` slots**, not rects. A chip is artwork of the closed-set
  ``marker`` class, so a league may draw the mark it wants; the fastest lap is a
  corner triangle rather than a wash over the cell precisely because a file can be one and a
  stylesheet colour cannot.

Two rules govern how this works, and both matter more than they look.

**It edits the file as text, never as an lxml tree.** Reserialising would rewrite attribute
quoting and namespace prefixes across every line of a 3,400-line file, and the diff would be
unreviewable. Matching and rewriting lines leaves everything else byte-identical.

**It computes the grid rather than reading it.** Its predecessor did the opposite — it derived
each chip from the ``x`` and ``y`` of the cell beside it, precisely so it could not drift from
a layout it was not changing. That reasoning inverts here: *every* x in the grid is moving, so
there is nothing to read it from, and the constants below are the source. What keeps them
honest is `test_image_standings_geometry.py`, which asserts the shipped files against the same
numbers from the other side.

Idempotent: run it twice and the second run finds the geometry already correct and rewrites
the same bytes. Usage::

    python3 tools/relayout_standings_grid.py            # both shipped templates
    python3 tools/relayout_standings_grid.py FILE ...   # named files
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "resources" / "defaults" / "templates"

# ── The grid, in six numbers ──────────────────────────────────────────────

#: The page margin. The heading rules, the footer rule and the zebra bands all run from here
#: to the grid's right edge, so widening the grid widens them too.
LEFT_MARGIN = 48

#: Where the season grid begins. Everything to the left of this — the classification block,
#: its positions, names, points and gaps — is not this script's business and does not move.
GRID_LEFT = 360

#: One session column. 54 clears the 45.6px widest pair with 8.4px to spare, which is the
#: headroom a font narrower or wider than DejaVu needs.
COLUMN = 54

#: Two columns and the gutter that separates one round from the next. 2px, the gutter the
#: templates already used (66 = 2x32 + 2).
GUTTER = 2
PITCH = COLUMN * 2 + GUTTER

#: Rounds every standings template declares.
ROUNDS = 12

#: The margin between the grid's right edge and the canvas edge, as the files already set it.
RIGHT_MARGIN = 48

GRID_RIGHT = GRID_LEFT + PITCH * ROUNDS
CANVAS_WIDTH = GRID_RIGHT + RIGHT_MARGIN

#: Chip width, inset from the column so neighbouring chips keep a gutter between them.
CHIP_WIDTH = 52

#: How far above the cell's baseline a chip begins. The same on both files: the raised
#: qualifying figure reaches about 11px above the baseline and the descenders of the result
#: about 3px below, so a chip from 15 above covers the pair whatever its height.
CHIP_TOP_OFFSET = 15


def column_centre(round_ordinal: int, feature: bool) -> float:
    """The x a session column's text is centred on."""
    base = GRID_LEFT + PITCH * (round_ordinal - 1) + GUTTER // 2 + COLUMN / 2
    return base + (COLUMN if feature else 0)


def round_centre(round_ordinal: int) -> float:
    """The midpoint of a round's two columns — where its number and flag sit."""
    return (column_centre(round_ordinal, False) + column_centre(round_ordinal, True)) / 2


def divider_x(round_ordinal: int) -> int:
    """The rule drawn down the left edge of a round."""
    return GRID_LEFT + PITCH * (round_ordinal - 1)


#: Chip height per template, and the old canvas width each is being widened from. The heights
#: differ because the row bands do — 32px on the drivers grid, 20px per car line on the
#: constructors one — and they now may: the class is exempt from the one-aspect rule and its
#: slots stretch (Constitution XIV.6, v7.4.0).
TEMPLATES_SPEC = {
    "standings_drivers_template.svg": {"chip_height": 22, "old_right": 1152},
    "standings_constructors_template.svg": {"chip_height": 18, "old_right": 1080},
}

#: Every slot hangs off a race cell, the qualifying mark included. The raised qualifying figure
#: shares one auto-laid text chunk with the race result and has no position of its own, so its
#: mark is drawn in a corner of the race cell's box rather than behind the figure.

CELL_RE = re.compile(
    r'^(?P<indent>\s*)<text class="cell" x="(?P<x>[-\d.]+)" y="(?P<y>[-\d.]+)"'
    r'(?P<rest>[^>]*)><tspan id="(?P<id>[^"]+)_(?P<session>sprint|feature)_'
    r'(?P<kind>race|qualifying)_result"'
)
CHIP_RE = re.compile(
    r'^\s*<(?:rect|image) id="[^"]*_(?:background|fastest_lap|qualifying_mark)"'
)


def _num(value: float) -> str:
    return f"{value:g}"


def chips_for(stem: str, session: str, centre: float, top: float, height: int) -> list[str]:
    """The three slots standing beneath one race cell, all sharing one box.

    Sharing the box is deliberate: where a mark sits is the **artwork's** business, not the
    template's. The plate fills its box, the fastest lap draws in the top-left corner of its
    own, and the qualifying mark in the top-right of its own. Giving each a corner-sized slot
    instead would freeze that arrangement into 3,600 elements a league could not restyle.

    The qualifying mark hangs off the *qualifying* session's stem though it is drawn over the
    race cell, because it marks the qualifying result. It is the corner nearest the raised
    qualifying figure, which is why that corner and not another.

    Both are authored with **no href**. An ``<image>`` carrying none draws nothing and is
    passed over by the unreachable-link check, so a cell that earns no highlight costs the
    render nothing at all — where removing the slot instead would put a thousand ids into
    every fill spec and walk a subtree for each.

    ``preserveAspectRatio="none"`` is what makes the asset stretch to the box rather than be
    letterboxed inside it, and is the reason this class carries no fixed aspect.
    """
    left = _num(centre - CHIP_WIDTH / 2)
    box = (
        f'x="{left}" y="{_num(top)}" width="{CHIP_WIDTH}" height="{height}" '
        f'preserveAspectRatio="none"'
    )
    family = session.rsplit("_", 1)[0]
    return [
        f'<image id="{name}" inkscape:label="{name}" {box}/>'
        for name in (
            f"{stem}_{session}_background",
            f"{stem}_{session}_fastest_lap",
            f"{stem}_{family}_qualifying_mark",
        )
    ]


def rewrite(path: pathlib.Path) -> tuple[int, int]:
    spec = TEMPLATES_SPEC[path.name]
    height, old_right = spec["chip_height"], spec["old_right"]
    text = path.read_text(encoding="utf-8")

    # 1. The canvas, and everything anchored to the old right margin: three horizontal
    #    rules per file, plus `result_status` and the footer caption, both end-anchored.
    text = re.sub(
        r'(<svg[^>]*?)width="\d+" height="(\d+)" viewBox="0 0 \d+ (\d+)"',
        lambda m: f'{m.group(1)}width="{CANVAS_WIDTH}" height="{m.group(2)}" '
        f'viewBox="0 0 {CANVAS_WIDTH} {m.group(3)}"',
        text,
        count=1,
    )
    # The bands that span the picture, which widen with it: the canvas ground and the red rule
    # beneath the heading run the full width from x=0; the zebra bands run from the page margin
    # to the grid's right edge. Leaving these behind was the one thing the first widening
    # missed, and it is invisible in the markup — the picture simply ends early.
    #
    # Matched by a `<rect x="` with no attribute before it, which is what separates a band from
    # a **crop point**: that also sits at x=0, but carries an `id` first and is a zero-width
    # mark whose width must never be touched.
    text = re.sub(
        r'<rect x="0" (y="[-\d.]+" )width="\d+"',
        lambda m: f'<rect x="0" {m.group(1)}width="{CANVAS_WIDTH}"',
        text,
    )
    text = re.sub(
        rf'<rect x="{LEFT_MARGIN}" (y="[-\d.]+" )width="\d+"',
        lambda m: f'<rect x="{LEFT_MARGIN}" {m.group(1)}width="{GRID_RIGHT - LEFT_MARGIN}"',
        text,
    )
    text = re.sub(rf'\b(x2?)="{old_right}"', lambda m: f'{m.group(1)}="{GRID_RIGHT}"', text)

    # 2. The round headings: the divider, the number, the flag, and the S/F column heads.
    for z in range(1, ROUNDS + 1):
        sprint, feature = column_centre(z, False), column_centre(z, True)
        centre = round_centre(z)
        text = re.sub(
            rf'(<text id="round_{z}_number"[^>]*?) x="[-\d.]+"',
            lambda m, c=centre: f'{m.group(1)} x="{_num(c)}"',
            text,
            count=1,
        )
        text = re.sub(
            rf'(<image id="round_{z}_flag"[^>]*?) x="[-\d.]+"',
            lambda m, c=centre: f'{m.group(1)} x="{_num(c - 10)}"',
            text,
            count=1,
        )
        # The divider and the two column heads live inside round_<z>_group and carry no id,
        # so they are rewritten within the span of that group alone.
        start = text.index(f'<g id="round_{z}_group"')
        end = text.index("</g>", start)
        block = text[start:end]
        block = re.sub(
            r'(<line )x1="[-\d.]+"( y1="[-\d.]+" )x2="[-\d.]+"',
            lambda m, d=divider_x(z): f'{m.group(1)}x1="{d}"{m.group(2)}x2="{d}"',
            block,
            count=1,
        )
        heads = iter((sprint, feature))
        block = re.sub(
            r'(<text class="colhead" )x="[-\d.]+"',
            lambda m: f'{m.group(1)}x="{_num(next(heads))}"',
            block,
        )
        text = text[:start] + block + text[end:]

    # 3. Every cell: move its text to the new column centre, and stand its chips beneath it.
    lines = text.split("\n")
    out: list[str] = []
    cells = chips = 0
    for line in lines:
        if CHIP_RE.match(line):
            continue  # rebuilt below; idempotence comes from dropping the old pair first
        match = CELL_RE.match(line)
        if match is None:
            out.append(line)
            continue

        stem, session, kind = match["id"], match["session"], match["kind"]
        z = int(re.search(r"_round_(\d+)", stem).group(1))
        centre = column_centre(z, feature=session == "feature")
        baseline = float(match["y"])
        cells += 1

        if kind == "race":
            top = baseline - CHIP_TOP_OFFSET
            out.extend(
                f"{match['indent']}{chip}"
                # `sprint_race`, not `sprint`: the catalogue names the field for the session
                # it belongs to, and a qualifying cell of the same round is a different id.
                for chip in chips_for(stem, f"{session}_{kind}", centre, top, height)
            )
            chips += 3

        out.append(
            re.sub(r'(<text class="cell" )x="[-\d.]+"', rf'\g<1>x="{_num(centre)}"', line, count=1)
        )

    path.write_text("\n".join(out), encoding="utf-8")
    return cells, chips


def main(argv: list[str]) -> int:
    targets = (
        [pathlib.Path(name) for name in argv]
        if argv
        else [TEMPLATES / name for name in TEMPLATES_SPEC]
    )
    for path in targets:
        if path.name not in TEMPLATES_SPEC:
            print(f"{path}: no grid geometry is defined for this template", file=sys.stderr)
            return 1
        cells, chips = rewrite(path)
        print(f"{path.name}: {cells} cells re-laid, {chips} chips drawn")
    print(f"canvas now {CANVAS_WIDTH} wide; columns {COLUMN}, round pitch {PITCH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
