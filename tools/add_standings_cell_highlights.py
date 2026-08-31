#!/usr/bin/env python3
"""Add the highlight chips to the two shipped standings templates.

A one-off, kept as the record of what was done to those files — the same reason
``tools/gen_season_cog.py`` is kept. Re-running it is safe: a cell already carrying a
background is skipped, so the script is idempotent and can be pointed at a template a league
has since edited.

Two rules govern how it works, and both matter more than they look.

**It edits the file as text, never as an lxml tree.** Reserialising through ``etree.tostring``
would rewrite attribute quoting and namespace prefixes across every line of a 3,400-line file
and produce a wholly-changed diff that nobody can review. Matching and inserting lines leaves
everything else byte-identical, so the diff is exactly the insertion.

**It derives every chip from the ``x`` and ``y`` of the cell's own text element**, never from
a formula over the row and round ordinals. The pitches are documented in the templates and are
easy to restate here, but a restatement is a copy that goes stale: commit 8b2cc60 already
re-laid these columns once, and a generator computing its own geometry would have silently
drawn the chips where the columns used to be.

Usage::

    python3 tools/add_standings_cell_highlights.py            # both shipped templates
    python3 tools/add_standings_cell_highlights.py FILE ...   # named files
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "resources" / "defaults" / "templates"

#: Chip geometry per template, as an inset from the cell's own text anchor. The width is a
#: little under the column pitch so neighbouring chips keep a gutter between them, and the
#: height a little under the row band so the bands do not merge into a solid block.
#:
#: ``dy`` is measured up from the text baseline. The raised qualifying glyph sits about 11px
#: above it and the descenders of the result reach about 3px below, so a chip from
#: ``baseline - 15`` down covers both.
GEOMETRY = {
    "standings_drivers_template.svg": {"width": 30, "height": 20, "dy": 15},
    "standings_constructors_template.svg": {"width": 22, "height": 18, "dy": 15},
}

#: Only the two race cells are given chips. The raised qualifying glyph shares one auto-laid
#: text chunk with the race result beside it, so it has no fixed position of its own and no
#: chip could be made to line up behind it — see the catalogue's note on the two qualifying
#: background fields, which a re-laid template may still declare.
RACE_SESSIONS = ("sprint_race", "feature_race")

#: Matches a cell's text element and the id of the race result inside it. The race result is
#: the first tspan of the pair, so its id names the session the chip belongs to.
CELL_RE = re.compile(
    r'^(?P<indent>\s*)<text class="cell" x="(?P<x>[-\d.]+)" y="(?P<y>[-\d.]+)"'
    r'[^>]*><tspan id="(?P<id>[^"]+)_(?P<session>sprint_race|feature_race)_result"'
)


def chips_for(stem: str, session: str, x: float, y: float, geometry: dict) -> list[str]:
    """The two rects standing beneath one race cell, background first.

    Both are authored transparent and are **never removed**. The render recolours the ones a
    highlight reaches and leaves the rest alone, so an unhighlighted cell costs nothing —
    where removal would put a thousand ids into the fill spec and walk a subtree for each.
    """
    left = round(x - geometry["width"] / 2, 3)
    top = round(y - geometry["dy"], 3)
    box = (
        f'x="{left:g}" y="{top:g}" '
        f'width="{geometry["width"]}" height="{geometry["height"]}" fill="none"'
    )
    return [
        f'<rect id="{stem}_{session}_{layer}" '
        f'inkscape:label="{stem}_{session}_{layer}" {box}/>'
        for layer in ("background", "fastest_lap")
    ]


def rewrite(path: pathlib.Path) -> int:
    geometry = GEOMETRY[path.name]
    lines = path.read_text(encoding="utf-8").split("\n")
    declared = set(re.findall(r'id="([^"]+)"', "\n".join(lines)))

    out: list[str] = []
    added = 0
    for line in lines:
        match = CELL_RE.match(line)
        if match is not None:
            stem, session = match["id"], match["session"]
            # Idempotence: a cell already carrying its background is left exactly as it is.
            if f"{stem}_{session}_background" not in declared:
                out.extend(
                    f"{match['indent']}{chip}"
                    for chip in chips_for(
                        stem, session, float(match["x"]), float(match["y"]), geometry
                    )
                )
                added += 2
        out.append(line)

    if added:
        path.write_text("\n".join(out), encoding="utf-8")
    return added


def main(argv: list[str]) -> int:
    targets = (
        [pathlib.Path(name) for name in argv]
        if argv
        else [TEMPLATES / name for name in GEOMETRY]
    )
    for path in targets:
        if path.name not in GEOMETRY:
            print(f"{path}: no chip geometry is defined for this template", file=sys.stderr)
            return 1
        added = rewrite(path)
        print(f"{path.name}: {added} rects added" if added else f"{path.name}: already done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
