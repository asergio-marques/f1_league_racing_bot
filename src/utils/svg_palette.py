"""Per-tier colour slots: what a template declares, and how a palette is injected.

Pure: no database, no Discord, no rasteriser — an lxml tree in, an lxml tree changed in
place. That is what lets the whole mechanism be tested without a bot.

A league differentiates its tiers by marking elements of its **own** template with a class
naming a colour slot, then setting a colour against that slot per division. Nothing the bot
ships declares a slot, so the feature draws nothing until a league asks for it — the same
shape the division logo takes, and for the same reason.

Three prefixes, one per property, because one slot may need to paint different things in
different places::

    <rect class="accent colour-fill-accent"/>       fill
    <path class="colour-stroke-accent"/>            stroke
    <stop class="colour-stop-accent"/>              stop-color

The remainder of the class token is the slot id, so the parse is unambiguous even though
slot ids are the league's to invent: the prefix fixes where the name begins, and the token
boundary fixes where it ends.

**Why a stylesheet and not CSS custom properties.** The obvious design — declare
``--accent`` on the root and write ``fill:var(--accent)`` — is unusable here. Inkscape 1.4
parses CSS with libcroco, which rejects a custom-property declaration outright ("while
parsing declaration: next property is malformed") and then **discards the entire
stylesheet**, taking every unrelated rule in the template with it; ``var()`` renders black
even where a fallback is given. Verified against the rasteriser before this was written, and
pinned by the rasteriser-marked test. So the palette is injected as an ordinary stylesheet
whose rules win on document order.

**Why gradient stops are not in that stylesheet.** Also verified: Inkscape applies a class
rule to a ``<rect>``, a ``<text>``, a ``<tspan>``, a ``<g>``'s children and a ``stroke``, but
**not** to a ``<stop>``'s ``stop-color`` — the stop silently keeps the colour it was authored
with. An inline ``style`` on the stop does work, so that is what a stop gets.

Precedence, weakest first: the template's own rules and presentation attributes, then the
injected palette, then anything written into an element's inline ``style``. The last of those
is where :mod:`utils.svg_fill` writes a data-driven recolour, so the fastest-lap colour and
the standings highlight ink still win over a league's palette — they say something about the
*data*, and a tier's identity must not overwrite them.
"""
from __future__ import annotations

import re

from lxml import etree

from utils.svg_document import SVG_NS, merge_style

#: What a slot id may be made of. Deliberately narrow: a slot is interpolated into a CSS
#: selector, so anything outside this set could close the rule and inject a stylesheet of
#: the league's choosing into every graphic the server posts.
SLOT_PATTERN = "[a-z0-9_-]{1,64}"

FILL_PREFIX = "colour-fill-"
STROKE_PREFIX = "colour-stroke-"
STOP_PREFIX = "colour-stop-"

#: ``fill`` and ``stroke`` are painted by the injected stylesheet; ``stop`` is painted
#: inline. Keyed by the kind as it appears in the class token.
_PROPERTY_FOR_KIND = {"fill": "fill", "stroke": "stroke", "stop": "stop-color"}

_TOKEN_RE = re.compile(rf"colour-(fill|stroke|stop)-({SLOT_PATTERN})\Z")
_SLOT_RE = re.compile(rf"{SLOT_PATTERN}\Z")


class InvalidSlot(ValueError):
    """A slot id that may not be written into a stylesheet."""


def normalise_slot(value: str | None) -> str:
    """Return *value* as a canonical slot id, or raise :class:`InvalidSlot`.

    Lower-cased on the way in so that `Accent` and `accent` are one slot rather than two —
    a league setting the same colour twice under two spellings and seeing one of them
    ignored is a fault nobody would think to look for.

    This is the **only** gate between a league's typing and a CSS selector, and both the
    command and the service call it. See :data:`SLOT_PATTERN`.
    """
    candidate = (value or "").strip().lower()
    if not _SLOT_RE.match(candidate):
        raise InvalidSlot(
            f"`{value}` is not a usable colour slot. A slot is 1 to 64 characters of "
            f"lower-case letters, digits, `-` or `_` — for example `accent`."
        )
    return candidate


def _tokens(element: etree._Element):
    """Every (kind, slot) this element's ``class`` declares.

    The attribute is split on whitespace and each token matched whole, rather than the
    pattern being searched for inside it. A slot id may itself contain `-`, so a substring
    search would happily find `accent` inside a class named `colour-fill-accent-dim` and
    paint the wrong thing.
    """
    for token in (element.get("class") or "").split():
        matched = _TOKEN_RE.match(token)
        if matched is not None:
            yield matched.group(1), matched.group(2)


def colour_slots(root: etree._Element) -> frozenset[str]:
    """Every colour slot *root* demands, whatever property it demands it for.

    This is what validation measures a league's configuration against: a template declaring
    no slots demands nothing and can never be blocked, and a template declaring one is
    incomplete until every division has a colour for it.

    **The whole tree is walked, ``<defs>`` included** — unlike
    :class:`~utils.svg_document.FieldIndex`, which skips it. A gradient's stops live in
    ``<defs>``, so a scan that skipped it would miss slots that are genuinely demanded and
    report a template complete when it is not.
    """
    return frozenset(
        slot for element in root.iter() if isinstance(element.tag, str)
        for _kind, slot in _tokens(element)
    )


def declared_pairs(root: etree._Element) -> frozenset[tuple[str, str]]:
    """Every (kind, slot) the template declares. The precise form of :func:`colour_slots`."""
    return frozenset(
        pair for element in root.iter() if isinstance(element.tag, str)
        for pair in _tokens(element)
    )


def apply_palette(root: etree._Element, palette: dict[str, str]) -> None:
    """Paint *palette* into *root*, in place.

    *palette* maps a slot id to a colour. A slot the template does not declare is ignored,
    and a slot the template declares but the palette does not carry is left alone — it keeps
    whatever the template drew it in, which is what makes an unconfigured league's graphics
    identical to how they looked before the feature existed.

    Nothing is written at all when the two sets do not meet, so a template with no slots is
    returned untouched rather than gaining an empty stylesheet.
    """
    if not palette:
        return

    pairs = sorted(pair for pair in declared_pairs(root) if pair[1] in palette)
    if not pairs:
        return

    # Stops first, and separately: a class rule does not reach a `<stop>` in Inkscape, so
    # each one is painted through its inline style instead. Merged rather than assigned, so
    # a stop carrying other declarations keeps them.
    stop_slots = {slot for kind, slot in pairs if kind == "stop"}
    if stop_slots:
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            for kind, slot in _tokens(element):
                if kind == "stop" and slot in stop_slots:
                    merge_style(element, {"stop-color": palette[slot]})

    rules = [
        f".colour-{kind}-{slot} {{ {_PROPERTY_FOR_KIND[kind]}:{palette[slot]} }}"
        for kind, slot in pairs
        if kind != "stop"
    ]
    if not rules:
        return

    # Appended to `<defs>`, and last, so the block sits after whatever the template
    # declares and wins on document order — the cascade is the whole mechanism. It goes in
    # `<defs>` rather than beside the artwork because the crop operations walk the drawing
    # for geometry, and a bare `<style>` among the drawn elements is one more thing for them
    # to reason about; `<defs>` is already understood to hold nothing that is drawn.
    defs = root.find(f"{{{SVG_NS}}}defs")
    if defs is None:
        defs = etree.SubElement(root, f"{{{SVG_NS}}}defs")
    style = etree.SubElement(defs, f"{{{SVG_NS}}}style")
    style.text = "\n" + "\n".join(f"      {rule}" for rule in rules) + "\n    "
