"""SVG loading, canvas reading and style resolution.

Pure: no database, no Discord. That is what makes the engine unit-testable without a bot.

Constitution XIV.1 makes the template's declared ``width``/``height`` the authoritative
canvas — the renderer reads it from the root and assumes no fixed canvas for any image
type.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from lxml import etree

log = logging.getLogger(__name__)

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
#: Templates are authored in Inkscape, whose layer label is the fallback address for a
#: field (Constitution XIV.2): a league manager sets the label and never sees the
#: identifier the editor generated.
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
NSMAP = {"svg": SVG_NS, "xlink": XLINK_NS, "inkscape": INKSCAPE_NS}

#: A CSS length: leading number, optional unit. Templates declare px or bare numbers;
#: physical units are converted at the CSS 96dpi reference.
_LENGTH_RE = re.compile(r"^\s*(-?[0-9]*\.?[0-9]+)\s*([a-z%]*)\s*$", re.IGNORECASE)

_UNIT_TO_PX = {
    "": 1.0,
    "px": 1.0,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
}


class SvgError(Exception):
    """Base for template-level problems (Constitution XIV.4 problems, never notices)."""


class SvgParseError(SvgError):
    """The file exists but is not well-formed SVG."""


class SvgNoCanvasError(SvgError):
    """The file parses but its root declares no usable width/height."""


#: Substrings of lxml's message → the fault named in the module's own words.
#:
#: FR-046 forbids showing a user the parser's own text. These are the classes a
#: hand-authored file actually hits; anything else falls through to the generic line.
_FAULT_SIGNATURES: tuple[tuple[str, str], ...] = (
    (
        "double hyphen within comment",
        "a comment contains a double hyphen (`--`), which XML does not allow inside one",
    ),
    ("comment not terminated", "a comment is left unclosed"),
    ("opening and ending tag mismatch", "an opening tag and its closing tag do not match"),
    ("premature end of data", "a tag is left unclosed"),
    ("expected '>'", "a tag is malformed and never closed"),
    ("undeclared entity", "an undefined entity is referenced (write `&amp;` for a literal `&`)"),
    ("entityref", "a stray `&` is used where `&amp;` was meant"),
    ("encoding", "the encoding declaration is not one the parser accepts"),
    ("attributes construct error", "an attribute is malformed"),
    ("space required after the public identifier", "the doctype declaration is malformed"),
    ("extra content at the end of the document", "content follows the root element"),
)


def _name_parse_fault(exc: etree.XMLSyntaxError) -> str:
    """Describe *exc* in the module's own words, never the parser's (FR-046).

    The raw text goes to the application log so an operator can still reach it; a league
    manager gets a sentence they can act on.
    """
    raw = str(exc)
    log.debug("SVG parse failure (raw parser text): %s", raw)

    lowered = raw.lower()
    line = getattr(exc, "lineno", 0) or 0
    where = f" at line {line}" if line else ""

    for signature, description in _FAULT_SIGNATURES:
        if signature in lowered:
            return f"{description}{where}"

    return f"the file is not well-formed XML{where}"


def load_svg(path: Path | str) -> etree._Element:
    """Parse *path* and return its root element.

    Raises :class:`SvgParseError` for anything that is not well-formed SVG. A missing
    file is the caller's to detect — the validity layer distinguishes "not found" from
    "does not parse", and conflating them here would lose that distinction.

    A `str` is accepted as readily as a `Path`; several callers hold the template path as
    one and the annotation used to say otherwise.
    """
    # A directory is named as unreadable here rather than left to lxml, because which
    # fault lxml reports for one depends on the libxml2 it was linked against. libxml2
    # 2.9 (what Debian's `python3-lxml` uses) opens the directory, reads nothing, and
    # raises XMLSyntaxError "Document is empty" — so the OSError branch below never runs
    # and a directory gets reported as malformed XML instead. Newer libxml2, including
    # the copy bundled in pip's manylinux wheels, raises an I/O error as expected. Deciding
    # it here keeps the fault the same on every host.
    if Path(path).is_dir():
        log.debug("SVG could not be read: %s is a directory", path)
        raise SvgParseError("the file could not be read")

    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=False)
    try:
        tree = etree.parse(str(path), parser)
    except etree.XMLSyntaxError as exc:
        raise SvgParseError(_name_parse_fault(exc)) from exc
    except OSError as exc:  # unreadable, a permission problem
        log.debug("SVG could not be read: %s", exc)
        raise SvgParseError("the file could not be read") from exc

    root = tree.getroot()
    if etree.QName(root).localname != "svg":
        raise SvgParseError(
            f"the root element is <{etree.QName(root).localname}>, not <svg>"
        )
    return root


def parse_svg_bytes(data: bytes) -> etree._Element:
    """Parse SVG held in memory. Used by tests and by the fill pipeline."""
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=False)
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise SvgParseError(_name_parse_fault(exc)) from exc


def length(value: str | None) -> float | None:
    """Convert a CSS length to pixels. Returns None for percentages and nonsense."""
    if value is None:
        return None
    match = _LENGTH_RE.match(value)
    if match is None:
        return None
    magnitude, unit = match.group(1), match.group(2).lower()
    factor = _UNIT_TO_PX.get(unit)
    if factor is None:  # '%' and anything unrecognised
        return None
    return float(magnitude) * factor


def canvas_of(root: etree._Element) -> tuple[int, int]:
    """Return the template's declared canvas in pixels (Constitution XIV.1).

    Falls back to the ``viewBox`` when width/height are absent or given as percentages,
    because a template sized entirely by viewBox still declares a canvas. Raises
    :class:`SvgNoCanvasError` when neither yields a usable size.
    """
    width = length(root.get("width"))
    height = length(root.get("height"))

    if width is None or height is None:
        view_box = root.get("viewBox")
        if view_box:
            parts = re.split(r"[\s,]+", view_box.strip())
            if len(parts) == 4:
                try:
                    width = width if width is not None else float(parts[2])
                    height = height if height is not None else float(parts[3])
                except ValueError:
                    pass

    if width is None or height is None or width <= 0 or height <= 0:
        raise SvgNoCanvasError(
            "root declares no usable width and height (and no viewBox to fall back on)"
        )

    return int(round(width)), int(round(height))


#: Suffix marking a removable group (Constitution XIV.2): the chrome around a field
#: leaves the graphic together with the value it introduces.
GROUP_SUFFIX = "_group"


class FieldIndex:
    """Resolve a field *name* to an element (Constitution XIV.2).

    Two addresses, in order of authority:

    1. a node whose ``@id`` is the name — normative;
    2. a **layer** whose ``inkscape:label`` is the name — the fallback.

    Only layers are indexed by label. Inkscape writes ``inkscape:label`` on ordinary
    objects too, without the manager choosing them, and sweeping those in would let a
    field name collide with a shape nobody meant to address.

    **Nothing inside ``<defs>`` is indexed.** A gradient, filter, marker or clip path is
    referenced by ``url(#…)`` and therefore *must* carry an identifier, but it is a paint
    the template mixes rather than a place the render puts a value — it is never a field. It
    has to be excluded rather than merely ignored, because ``declared()`` is what the
    catalogue checks a template against: an indexed gradient would be reported as an id the
    catalogue cannot name, and the standings templates would be refused for owning the very
    gradients their highlight chips are painted with.

    Rebuild after any structural change to the tree; a removal invalidates the index
    exactly as it did the dict this class replaces.
    """

    __slots__ = ("by_id", "by_label")

    def __init__(self, root: etree._Element) -> None:
        self.by_id: dict[str, etree._Element] = {}
        self.by_label: dict[str, etree._Element] = {}

        label_attr = f"{{{INKSCAPE_NS}}}label"
        groupmode_attr = f"{{{INKSCAPE_NS}}}groupmode"

        # Held in a set rather than compared by identity value: keeping the proxies alive is
        # what makes lxml's element identity stable for the length of the walk.
        definitions: set[etree._Element] = set()
        for defs in root.iter(f"{{{SVG_NS}}}defs"):
            definitions.update(defs.iter())

        for element in root.iter():
            if element in definitions:
                continue

            element_id = element.get("id")
            if element_id:
                self.by_id.setdefault(element_id, element)

            if element.get(groupmode_attr) != "layer":
                continue
            label = element.get(label_attr)
            if label:
                self.by_label.setdefault(label, element)

    def resolve(self, name: str) -> etree._Element | None:
        """The element addressed by *name*, or None. The identifier wins (FR-020)."""
        found = self.by_id.get(name)
        if found is not None:
            return found
        return self.by_label.get(name)

    def group_for(self, name: str) -> etree._Element | None:
        """The removable group wrapping *name*, if the template declares one."""
        return self.resolve(f"{name}{GROUP_SUFFIX}")

    def declared(self) -> set[str]:
        """Every name this template can be addressed by — ids and layer labels alike."""
        return set(self.by_id) | set(self.by_label)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.resolve(name) is not None


# ── Style resolution ──────────────────────────────────────────────────────
#
# Needed twice: by the fastest-lap contrast check (FR-026a) to read the background a
# template draws behind that field, and by the recolour operation, which XIV.2 requires
# be merged into inline style precisely *because* a presentation attribute loses to the
# template's own stylesheet. Same resolution, so it lives here once.

_DECLARATION_RE = re.compile(r"([\w-]+)\s*:\s*([^;]+)")
_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")

#: CSS comments, stripped before rules are parsed. See ``stylesheet`` for why this is not
#: cosmetic: a comma inside a comment silently disables the rule that follows it.
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def declarations(text: str | None) -> dict[str, str]:
    """Parse a CSS declaration block into a dict."""
    if not text:
        return {}
    return {
        name.strip().lower(): value.strip()
        for name, value in _DECLARATION_RE.findall(text)
    }


def stylesheet(root: etree._Element) -> dict[str, dict[str, str]]:
    """Collect the template's own ``<style>`` rules, keyed by selector.

    Only the simple selectors a template realistically uses are indexed: ``#id``,
    ``.class`` and bare element names. A selector list is split and each part indexed.

    **Comments are stripped first, and must be.** A selector group is split on commas, so a
    `/* ... */` comment containing one — which any prose sentence eventually does — splits into
    two false selectors and takes the rule *following* it with it, leaving that rule matching
    nothing at all. The shipped results, lineup, attendance and verdict templates all document
    `.dname` in a comment above it, and every one of those bounds was silently inert until this
    was fixed.
    """
    rules: dict[str, dict[str, str]] = {}
    for style_element in root.iter(f"{{{SVG_NS}}}style"):
        css = _COMMENT_RE.sub(" ", style_element.text or "")
        for selector_group, block in _RULE_RE.findall(css):
            parsed = declarations(block)
            if not parsed:
                continue
            for selector in selector_group.split(","):
                key = selector.strip()
                if key:
                    rules.setdefault(key, {}).update(parsed)
    return rules


def computed_style(
    element: etree._Element, rules: dict[str, dict[str, str]] | None = None
) -> dict[str, str]:
    """Resolve an element's effective declarations, weakest source first.

    Order: presentation attributes, then matching stylesheet rules, then inline
    ``style``. Inline wins, which is why XIV.2 requires a recolour be written there.
    """
    resolved: dict[str, str] = {}

    for name, value in element.attrib.items():
        if name in _PRESENTATION_ATTRIBUTES:
            resolved[name] = value

    if rules:
        tag = etree.QName(element).localname
        if tag in rules:
            resolved.update(rules[tag])
        for class_name in (element.get("class") or "").split():
            if f".{class_name}" in rules:
                resolved.update(rules[f".{class_name}"])
        element_id = element.get("id")
        if element_id and f"#{element_id}" in rules:
            resolved.update(rules[f"#{element_id}"])

    resolved.update(declarations(element.get("style")))
    return resolved


_PRESENTATION_ATTRIBUTES = frozenset(
    {
        "fill",
        "stroke",
        "stroke-width",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-anchor",
        "opacity",
        "fill-opacity",
        "letter-spacing",
        "font-variant-numeric",
    }
)
