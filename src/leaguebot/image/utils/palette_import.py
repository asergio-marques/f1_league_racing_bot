"""Reading a per-tier palette a human supplied — pasted lines, or an XML payload.

Two entry points for the same job, because a league setting one tier and a league setting
five want different things: `parse_palette_lines` reads the `slot colour` block pasted into
the bulk modal, and `parse_palette_xml` reads a document covering many divisions at once.

Pure: no database, no Discord. Both validate through the same gates the single-colour
command uses — `normalise_slot`, because a slot reaches a CSS selector, and `normalise_hex`,
because a colour reaches a stylesheet.

**The division is the unit of atomicity** (decided 2026-09-08). A block naming an unknown
division, or carrying one bad slot or colour, is rejected whole and reported; the other
blocks still import. A division is never half-applied — a tier drawn in four of its ten
colours is worse than a tier not yet configured, because it looks deliberate.

Malformed XML is different in kind and fails outright: nothing can be salvaged from a
document that does not parse, so there are no blocks to accept or reject.
"""
from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from utils.colour import InvalidColour, normalise_hex
from utils.svg_palette import InvalidSlot, normalise_slot

__all__ = [
    "PaletteBlock",
    "PaletteXmlError",
    "parse_palette_lines",
    "parse_palette_xml",
]

#: Entities off and the network unreachable: this parses a file a league uploaded.
#: Same settings as `utils.xml_import`, and for the same reason.
_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)

#: What may sit between a slot and its colour in a pasted line. A league pastes what the
#: tool printed, but types what it likes.
_SEPARATORS = str.maketrans({"=": " ", ":": " ", ",": " ", "\t": " "})


@dataclass(frozen=True)
class PaletteBlock:
    """One division's colours, as supplied and already validated."""

    division: str
    colours: dict[str, str]


def parse_palette_lines(text: str) -> tuple[dict[str, str], list[str]]:
    """Read `slot colour` lines into a palette, with a problem per unusable line.

    Blank lines are skipped, and so is any line whose first character is `#`, so the
    tool's own annotated output can be pasted whole without stripping the commentary from
    it first.

    **A `#` is only a comment at the start of a line**, never part-way through one: every
    colour begins with one, so treating it as a trailing comment marker would silently
    discard the value on every single line.

    Returns ``(colours, problems)``. A caller applies the palette only when *problems* is
    empty — the atomicity rule above is the caller's to enforce, not this function's, so
    that it can report every fault at once rather than stopping at the first.
    """
    colours: dict[str, str] = {}
    problems: list[str] = []

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.translate(_SEPARATORS).split()
        if len(parts) != 2:
            problems.append(
                f"line {number}: expected a slot and a colour, got {raw.strip()!r}."
            )
            continue

        raw_slot, raw_colour = parts
        try:
            slot = normalise_slot(raw_slot)
        except InvalidSlot as exc:
            problems.append(f"line {number}: {exc}")
            continue
        try:
            colour = normalise_hex(raw_colour)
        except InvalidColour as exc:
            problems.append(f"line {number}: {exc}")
            continue

        if slot in colours and colours[slot] != colour:
            problems.append(
                f"line {number}: `{slot}` is given twice, as {colours[slot]} and {colour}."
            )
            continue
        colours[slot] = colour

    if not colours and not problems:
        problems.append("Nothing to set — the text held no `slot colour` lines.")
    return colours, problems


def parse_palette_xml(xml_text: str) -> tuple[list[PaletteBlock], list[str]]:
    """Read a many-division payload into accepted blocks and rejected ones.

    The shape, which the tool writes and a person can hand-edit::

        <palettes>
          <division name="Division 1">
            <colour slot="accent">#3DD6F5</colour>
            <colour slot="ink">#F4F7FA</colour>
          </division>
        </palettes>

    Returns ``(blocks, problems)``. Every block in *blocks* is safe to apply; every entry in
    *problems* names a division that was not. Both may be non-empty at once, which is the
    point: one mistyped tier does not cost a league the other four.

    Raises :class:`PaletteXmlError` only where the document itself cannot be read.
    """
    try:
        root = etree.fromstring(xml_text.encode(), parser=_XML_PARSER)
    except etree.XMLSyntaxError as exc:
        raise PaletteXmlError([f"XML syntax error: {exc}"]) from exc

    divisions = root.findall("division")
    if not divisions:
        raise PaletteXmlError(
            ["No <division> block found. The document holds nothing to import."]
        )

    blocks: list[PaletteBlock] = []
    problems: list[str] = []
    seen: set[str] = set()

    for index, element in enumerate(divisions, start=1):
        name = (element.get("name") or "").strip()
        label = f"`{name}`" if name else f"the {_ordinal(index)} <division>"

        if not name:
            problems.append(f"{label} has no `name`, so there is no tier to set.")
            continue
        if name.casefold() in seen:
            problems.append(f"{label} appears more than once; only the first was read.")
            continue
        seen.add(name.casefold())

        colours, faults = _colours_of(element)
        if faults:
            problems.append(f"{label}: {faults[0]}" if len(faults) == 1
                            else f"{label}: " + "; ".join(faults))
            continue
        if not colours:
            problems.append(f"{label} names no colours.")
            continue
        blocks.append(PaletteBlock(division=name, colours=colours))

    return blocks, problems


class PaletteXmlError(Exception):
    """The document could not be read at all. Carries one message per fault."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _colours_of(element) -> tuple[dict[str, str], list[str]]:
    """Every `<colour slot="…">` under one division, or the reasons it is unusable."""
    colours: dict[str, str] = {}
    faults: list[str] = []

    for child in element.findall("colour"):
        try:
            slot = normalise_slot(child.get("slot"))
        except InvalidSlot as exc:
            faults.append(str(exc))
            continue
        try:
            colour = normalise_hex((child.text or "").strip())
        except InvalidColour as exc:
            faults.append(f"`{slot}` — {exc}")
            continue
        if slot in colours and colours[slot] != colour:
            faults.append(f"`{slot}` is given twice, as {colours[slot]} and {colour}.")
            continue
        colours[slot] = colour

    return colours, faults


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        return f"{number}th"
    return f"{number}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th') }"
