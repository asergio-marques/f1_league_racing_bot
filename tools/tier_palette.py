"""Work out a tier's whole palette from one accent colour.

A league that slots its whole palette must set ten colours per division, and choosing them
by eye goes wrong in ways that are easy to miss — see `docs/how-to/configuring-the-image-module.md`,
"Working out a tier's palette". This applies the two rules that document states:

  * every colour takes the accent's **hue** — not an offset from it, because a fixed angle
    does not mean the same thing at different points of the wheel;
  * every colour keeps its own **lightness**, and its **chroma** is scaled down (a third by
    default), because greys holding their original chroma read as neutral in blue and as
    tinted in violet.

**The base palette is read out of the league's own drawing, never held here.** A template
that declares `.colour-fill-ink { fill:#F4F7FA }` has stated its palette; this reads that
back with the same parser the bot uses. So the tool needs no copy of anything, works with
whatever slots a league invented and whatever colours it drew them in, and cannot drift from
the drawings it is describing. That also settles where it could otherwise have got them:
`resources/league/` is gitignored, so a tracked tool must not depend on it existing.

It changes nothing. It prints commands.

    python3 tools/tier_palette.py --division "Division 2" --accent "#A78BFA"
    python3 tools/tier_palette.py --division "Division 3" --accent "#4ADE80" --chroma 0
    python3 tools/tier_palette.py --division "Division 2" --accent "#A78BFA" \\
        --template resources/league/templates/results_race_template.svg
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.colour import InvalidColour, normalise_hex, restate_in_hue, to_lch  # noqa: E402
from utils.svg_document import load_svg, stylesheet  # noqa: E402

#: Where the drawings live unless the caller names one. The league tier, because the
#: packaged templates declare no slots and so state no palette.
DEFAULT_TEMPLATE_DIR = Path("resources/league/templates")

#: `.colour-fill-<slot>` and `.colour-stroke-<slot>`, as `svg_palette` spells them.
_RULE = re.compile(r"^\.colour-(fill|stroke)-([a-z0-9_-]{1,64})$")

#: How much of its colourfulness a non-accent keeps. See the module docstring.
DEFAULT_CHROMA = 0.35


def base_palette(root) -> dict[str, str]:
    """The colour each slot is drawn in, read off the drawing's own stylesheet.

    A slot declared for both fill and stroke is one slot and one colour; the fill wins where
    the two disagree, that being the form nearly every element uses.
    """
    palette: dict[str, str] = {}
    for selector, block in stylesheet(root).items():
        matched = _RULE.match(selector.strip())
        if matched is None:
            continue
        kind, slot = matched.groups()
        colour = block.get("fill" if kind == "fill" else "stroke")
        if colour and (kind == "fill" or slot not in palette):
            palette[slot] = colour.strip()
    return palette


def derive(palette: dict[str, str], accent: str, chroma: float) -> dict[str, str]:
    """Every slot restated for a tier whose accent is *accent*.

    The accent slot is the accent itself, untouched: it is the colour the league chose, and
    scaling *its* chroma would be answering a question nobody asked.
    """
    hue = to_lch(accent)[2]
    return {
        slot: accent if slot == "accent" else restate_in_hue(colour, hue, chroma)
        for slot, colour in sorted(palette.items())
    }


def commands(division: str, palette: dict[str, str]) -> list[str]:
    """The slash commands that set *palette* for *division*, ready to paste."""
    return [
        f"/images config per-tier-set-colour division:{division} "
        f"slot:{slot} colour:{colour}"
        for slot, colour in palette.items()
    ]


def _find_template(named: str | None) -> Path:
    if named:
        return Path(named)
    candidates = sorted(DEFAULT_TEMPLATE_DIR.glob("*.svg"))
    if not candidates:
        raise SystemExit(
            f"No drawings found in {DEFAULT_TEMPLATE_DIR}. Name one with --template."
        )
    # Sorted, not whatever the filesystem yields first, so two runs agree.
    return candidates[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive a division's colour palette from one accent colour.",
        epilog="Prints the commands. Run the ones you want.",
    )
    parser.add_argument("--division", required=True, help="The division's name.")
    parser.add_argument("--accent", required=True, help="Its accent, as #RRGGBB.")
    parser.add_argument(
        "--chroma",
        type=float,
        default=DEFAULT_CHROMA,
        help=f"How much colourfulness the greys keep (default {DEFAULT_CHROMA}; "
             f"0 leaves them perfectly neutral).",
    )
    parser.add_argument("--template", help="Read the palette from this drawing.")
    args = parser.parse_args(argv)

    try:
        accent = normalise_hex(args.accent)
    except InvalidColour as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.chroma < 0:
        print("error: --chroma cannot be negative.", file=sys.stderr)
        return 2

    template = _find_template(args.template)
    if not template.exists():
        print(f"error: no such drawing: {template}", file=sys.stderr)
        return 2

    palette = base_palette(load_svg(template))
    if not palette:
        print(
            f"{template} declares no colour slots, so there is no palette to work from.\n"
            f"Mark an element with a class such as `colour-fill-accent` first — see\n"
            f"docs/how-to/configuring-the-image-module.md.",
            file=sys.stderr,
        )
        return 1

    derived = derive(palette, accent, args.chroma)

    print(f"# {template}  ·  accent {accent}  ·  chroma x{args.chroma:g}")
    print(f"# {len(derived)} slots for {args.division}\n")
    for slot, colour in derived.items():
        lightness, chroma_value, _ = to_lch(colour)
        print(f"#   {slot:<10} {colour}   L* {lightness:5.1f}  C* {chroma_value:4.1f}")
    print()
    for line in commands(args.division, derived):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
