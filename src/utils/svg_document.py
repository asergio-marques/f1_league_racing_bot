"""SVG loading, canvas reading and style resolution.

Pure: no database, no Discord. That is what makes the engine unit-testable without a bot.

Constitution XIV.1 makes the template's declared ``width``/``height`` the authoritative
canvas — the renderer reads it from the root and assumes no fixed canvas for any image
type.
"""
from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
NSMAP = {"svg": SVG_NS, "xlink": XLINK_NS}

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


def load_svg(path: Path) -> etree._Element:
    """Parse *path* and return its root element.

    Raises :class:`SvgParseError` for anything that is not well-formed SVG. A missing
    file is the caller's to detect — the validity layer distinguishes "not found" from
    "does not parse", and conflating them here would lose that distinction.
    """
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=False)
    try:
        tree = etree.parse(str(path), parser)
    except etree.XMLSyntaxError as exc:
        raise SvgParseError(str(exc).split("\n")[0]) from exc
    except OSError as exc:  # unreadable, a directory, a permission problem
        raise SvgParseError(str(exc)) from exc

    root = tree.getroot()
    if etree.QName(root).localname != "svg":
        raise SvgParseError(
            f"root element is <{etree.QName(root).localname}>, not <svg>"
        )
    return root


def parse_svg_bytes(data: bytes) -> etree._Element:
    """Parse SVG held in memory. Used by tests and by the fill pipeline."""
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=False)
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise SvgParseError(str(exc).split("\n")[0]) from exc


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


def index_by_id(root: etree._Element) -> dict[str, etree._Element]:
    """Map every ``@id`` in the document to its element.

    The only contract between a template and the code that fills it (Constitution XIV.2).
    """
    return {
        element.get("id"): element
        for element in root.iter()
        if element.get("id")
    }


# ── Style resolution ──────────────────────────────────────────────────────
#
# Needed twice: by the fastest-lap contrast check (FR-026a) to read the background a
# template draws behind that field, and by the recolour operation, which XIV.2 requires
# be merged into inline style precisely *because* a presentation attribute loses to the
# template's own stylesheet. Same resolution, so it lives here once.

_DECLARATION_RE = re.compile(r"([\w-]+)\s*:\s*([^;]+)")
_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")


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
    """
    rules: dict[str, dict[str, str]] = {}
    for style_element in root.iter(f"{{{SVG_NS}}}style"):
        css = style_element.text or ""
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
