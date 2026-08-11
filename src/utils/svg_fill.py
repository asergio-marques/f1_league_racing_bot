"""The six fill operations of Constitution Principle XIV.2, and no others.

    Text fill | Image fill | Recolour | Group removal | Vertical crop | Text wrap

``fill()`` **raises nothing** for a data disagreement — it reports, and the caller
decides. That is what lets a caller distinguish a render that failed from one that
merely degraded (XIV.4).

Pure: no database, no Discord, no subprocess.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from models.image_constants import (
    NOTICE_FONT_SUBSTITUTED,
    NOTICE_INLINE_SIZE_TRUNCATED,
    NOTICE_WRAP_TRUNCATED,
)
from models.image_module import FillResult, RenderNotice
from utils.font_metrics import ResolvedFont, measure, resolve_family
from utils.svg_document import (
    SVG_NS,
    XLINK_NS,
    canvas_of,
    computed_style,
    declarations,
    index_by_id,
    length,
    stylesheet,
)

log = logging.getLogger(__name__)

ELLIPSIS = "…"

#: Half a pixel at a time, per XIV.5.
_SIZE_STEP = 0.5

#: Fallback leading when a template declares no line-height.
_DEFAULT_LINE_HEIGHT_RATIO = 1.2

_SHAPE_INSIDE_RE = re.compile(r"url\(\s*#([^)\s]+)\s*\)")


@dataclass
class FillSpec:
    """What to fill a template with. One per render."""

    root: etree._Element
    image_type: str = "unknown"

    text: dict[str, str] = field(default_factory=dict)
    images: dict[str, str] = field(default_factory=dict)
    recolour: dict[str, str] = field(default_factory=dict)
    remove: list[str] = field(default_factory=list)
    crop: str | None = None

    #: The image type's field catalogue, when known. Supplying it lets `fill` report a
    #: template field the data left unfilled; without it only unknown fields are caught.
    #: Layer 2 of the validity contract will make this shared rather than per-caller.
    expected_fields: set[str] | None = None


def fill(spec: FillSpec) -> FillResult:
    """Apply the six operations and report what did not resolve."""
    root = spec.root
    notices: list[RenderNotice] = []
    unresolved: list[str] = []

    rules = stylesheet(root)

    # ── 1. Group removal ──────────────────────────────────────────────────
    # First, so that ids inside a removed subtree are gone before anything else looks
    # for them: a field removed with its group is not a field left unfilled.
    index = index_by_id(root)
    removed_ids: set[str] = set()
    for group_id in spec.remove:
        element = index.get(group_id)
        if element is None:
            unresolved.append(f"unknown group `{group_id}` (template declares no such id)")
            continue
        for descendant in element.iter():
            if descendant.get("id"):
                removed_ids.add(descendant.get("id"))
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)

    index = index_by_id(root)

    # ── 2. Vertical crop ──────────────────────────────────────────────────
    # Made in the SVG rather than asked of the rasteriser's export area, so it does not
    # depend on which way up a rasteriser counts.
    crop_y: float | None = None
    if spec.crop is not None:
        node = index.get(spec.crop)
        if node is None:
            unresolved.append(
                f"unknown crop point `{spec.crop}` (template declares no such id)"
            )
        else:
            crop_y = _element_y(node)
            if crop_y is None:
                unresolved.append(f"crop point `{spec.crop}` declares no y")
            else:
                _crop_to(root, crop_y)

    canvas = canvas_of(root)

    # ── 3. Recolour ───────────────────────────────────────────────────────
    # Merged into the element's *inline* style, never written as a presentation
    # attribute (which would lose to the template's own stylesheet) and never as a
    # `style` replacement (which would discard declarations the template set).
    #
    # A recolour does NOT consume the field: a coloured field still has to be filled,
    # which is what keeps the unresolved check honest.
    for field_id, colour in spec.recolour.items():
        element = index.get(field_id)
        if element is None:
            if field_id not in removed_ids:
                unresolved.append(
                    f"unknown field `{field_id}` (template declares no such id)"
                )
            continue
        _restyle(element, {"fill": colour})

    consumed: set[str] = set()

    # ── 4. Image fill ─────────────────────────────────────────────────────
    for field_id, href in spec.images.items():
        element = index.get(field_id)
        if element is None:
            if field_id not in removed_ids:
                unresolved.append(
                    f"unknown image field `{field_id}` (template declares no such id)"
                )
            continue
        element.set(f"{{{XLINK_NS}}}href", str(href))
        element.set("href", str(href))
        consumed.add(field_id)

    # ── 5 & 6. Text fill, with wrap and inline-size bounds ────────────────
    for field_id, value in spec.text.items():
        element = index.get(field_id)
        if element is None:
            if field_id not in removed_ids:
                unresolved.append(
                    f"unknown field `{field_id}` (template declares no such id)"
                )
            continue

        style = computed_style(element, rules)
        resolved, substitution = _resolve_font(style, field_id, spec.image_type)
        if substitution is not None:
            notices.append(substitution)

        shape_id = _shape_inside_id(style)
        if shape_id is not None:
            rect = index.get(shape_id)
            if rect is None:
                unresolved.append(
                    f"field `{field_id}` names shape-inside `{shape_id}`, "
                    f"which the template does not declare"
                )
                continue
            notice = _lay_out(element, rect, str(value), style, resolved, spec.image_type, field_id)
            if notice is not None:
                notices.append(notice)
            consumed.add(field_id)
            consumed.add(shape_id)  # the rectangle is consumed as an addressed field
            continue

        notice = _set_text(element, str(value), style, resolved, spec.image_type, field_id)
        if notice is not None:
            notices.append(notice)
        consumed.add(field_id)

    # ── Unresolved fields (XIV.3) ─────────────────────────────────────────
    # A field the crop took off the canvas is not a field left unfilled, so only ids
    # above the cut are reported.
    if spec.expected_fields is not None:
        index = index_by_id(root)
        for field_id in sorted(spec.expected_fields - consumed - removed_ids):
            element = index.get(field_id)
            if element is None:
                continue
            if crop_y is not None and _is_below(element, crop_y):
                continue
            unresolved.append(f"field `{field_id}` was not filled")

    return FillResult(
        svg=etree.tostring(root.getroottree(), xml_declaration=True, encoding="utf-8"),
        canvas=canvas,
        unresolved=unresolved,
        notices=notices,
    )


# ── Operation helpers ─────────────────────────────────────────────────────


def _restyle(element: etree._Element, updates: dict[str, str | None]) -> None:
    """Merge *updates* into the element's inline ``style`` (XIV.2).

    Merged, not replaced: overwriting `style` would discard whatever else the template
    declared on the same element. A value of ``None`` drops the declaration.

    Note that dropping an inline declaration does not undo one the template's own
    stylesheet makes — inline is the strongest source, so a property that must be
    cancelled is written with an explicit neutral value rather than removed.
    """
    current = declarations(element.get("style"))
    for name, value in updates.items():
        if value is None:
            current.pop(name, None)
        else:
            current[name] = value
    if current:
        element.set("style", ";".join(f"{n}:{v}" for n, v in current.items()))
    elif element.get("style") is not None:
        del element.attrib["style"]


def _element_y(element: etree._Element) -> float | None:
    """The element's own y, or the smallest y among its descendants."""
    own = length(element.get("y"))
    if own is not None:
        return own
    candidates = [
        length(descendant.get("y"))
        for descendant in element.iter()
        if descendant.get("y") is not None
    ]
    candidates = [value for value in candidates if value is not None]
    return min(candidates) if candidates else None


def _is_below(element: etree._Element, crop_y: float) -> bool:
    y = _element_y(element)
    return y is not None and y >= crop_y


def _crop_to(root: etree._Element, crop_y: float) -> None:
    """Rewrite the root's height and viewBox to *crop_y* (XIV.2).

    Both are rewritten: leaving the viewBox alone would scale the drawing into the
    shorter canvas instead of cutting it.
    """
    root.set("height", str(int(round(crop_y))))
    view_box = root.get("viewBox")
    if view_box:
        parts = re.split(r"[\s,]+", view_box.strip())
        if len(parts) == 4:
            root.set("viewBox", f"{parts[0]} {parts[1]} {parts[2]} {int(round(crop_y))}")


def _resolve_font(
    style: dict[str, str], field_id: str, image_type: str
) -> tuple[ResolvedFont, RenderNotice | None]:
    resolved = resolve_family(style.get("font-family"))
    if not resolved.substituted:
        return resolved, None

    detail = (
        f"`{resolved.requested}` is not installed on this host; "
        f"`{resolved.family or 'a default'}` was used instead."
    )
    return resolved, RenderNotice(
        image_type=image_type,
        notice_kind=NOTICE_FONT_SUBSTITUTED,
        detail=detail,
        field_id=field_id,
    )


def _font_size(style: dict[str, str]) -> float:
    return length(style.get("font-size")) or 16.0


def _line_height_ratio(style: dict[str, str], size: float) -> float:
    """Resolve ``line-height`` to a multiple of the font size.

    CSS gives it two meanings: a bare number is a *ratio* (`1.3`), while a value with a
    unit is an absolute length (`26px`). They must not be conflated — reading `1.3` as
    1.3px collapses the leading to nothing, and every line then "fits".
    """
    raw = (style.get("line-height") or "").strip()
    if not raw:
        return _DEFAULT_LINE_HEIGHT_RATIO

    try:
        return float(raw)  # unitless: already a ratio
    except ValueError:
        pass

    absolute = length(raw)
    if absolute is not None and size > 0:
        return absolute / size
    return _DEFAULT_LINE_HEIGHT_RATIO


def _set_text(
    element: etree._Element,
    value: str,
    style: dict[str, str],
    resolved: ResolvedFont,
    image_type: str,
    field_id: str,
) -> RenderNotice | None:
    """Fill a single-line field, honouring any declared ``inline-size`` (XIV.5).

    ``inline-size`` is the only bound the module places on a Discord display name, which
    is of no length a league controls.
    """
    limit = length(style.get("inline-size"))
    size = _font_size(style)
    notice: RenderNotice | None = None

    if limit is not None and measure(value, resolved, size) > limit:
        value = _truncate_to_width(value, resolved, size, limit)
        notice = RenderNotice(
            image_type=image_type,
            notice_kind=NOTICE_INLINE_SIZE_TRUNCATED,
            detail=f"`{field_id}` was cut to the {limit:g}px it was given.",
            field_id=field_id,
        )
        # The text now fits by construction; cancel the bound explicitly rather than
        # deleting it, so a stylesheet rule declaring it cannot come back.
        _restyle(element, {"inline-size": "auto"})

    _clear_children(element)
    element.text = value
    return notice


def _truncate_to_width(
    value: str, resolved: ResolvedFont, size: float, limit: float
) -> str:
    """Cut at a word boundary and end with an ellipsis (XIV.5)."""
    if measure(ELLIPSIS, resolved, size) > limit:
        return ELLIPSIS

    words = value.split()
    kept: list[str] = []
    for word in words:
        candidate = " ".join(kept + [word]) + ELLIPSIS
        if measure(candidate, resolved, size) > limit:
            break
        kept.append(word)

    if kept:
        return " ".join(kept) + ELLIPSIS

    # A single word wider than the whole box: cut mid-word rather than emit nothing.
    trimmed = value
    while trimmed and measure(trimmed + ELLIPSIS, resolved, size) > limit:
        trimmed = trimmed[:-1]
    return (trimmed + ELLIPSIS) if trimmed else ELLIPSIS


def root_of(element: etree._Element) -> etree._Element:
    return element.getroottree().getroot()


def _remove_shape_inside(element: etree._Element, root: etree._Element) -> None:
    """Strip ``shape-inside`` from everything that applies it to *element*.

    Removed rather than overridden: Inkscape honours no cancelling value — `none` leaves
    the text in flowed mode just as a real shape does.

    The inline declaration is the case that matters, because that is what Inkscape itself
    writes when a designer draws a flowed text box. An ``#id`` rule is also handled since
    it targets exactly one element. A ``shape-inside`` applied through a shared class or
    element selector is not stripped: it would flow every element sharing that selector
    into the same rectangle, which is not a thing a template can usefully declare.
    """
    _restyle(element, {"shape-inside": None})

    element_id = element.get("id")
    if not element_id:
        return

    for style_element in root.iter(f"{{{SVG_NS}}}style"):
        css = style_element.text or ""
        if "shape-inside" not in css or f"#{element_id}" not in css:
            continue
        style_element.text = _strip_property_from_id_rule(css, element_id, "shape-inside")


def _strip_property_from_id_rule(css: str, element_id: str, prop: str) -> str:
    """Remove *prop* from any rule whose selector list contains ``#element_id``."""

    def _rewrite(match: re.Match[str]) -> str:
        selectors, block = match.group(1), match.group(2)
        if f"#{element_id}" not in {s.strip() for s in selectors.split(",")}:
            return match.group(0)
        kept = [
            declaration
            for declaration in block.split(";")
            if declaration.strip() and not declaration.strip().lower().startswith(prop)
        ]
        return f"{selectors}{{{';'.join(kept)}}}"

    return re.sub(r"([^{}]+)\{([^{}]*)\}", _rewrite, css)


def _shape_inside_id(style: dict[str, str]) -> str | None:
    raw = style.get("shape-inside")
    if not raw:
        return None
    match = _SHAPE_INSIDE_RE.search(raw)
    return match.group(1) if match else None


def _lay_out(
    element: etree._Element,
    rect: etree._Element,
    value: str,
    style: dict[str, str],
    resolved: ResolvedFont,
    image_type: str,
    field_id: str,
) -> RenderNotice | None:
    """Wrap *value* against *rect*, descending by half a pixel until it fits (XIV.5).

    At the floor of **half** the template-declared size, the text is cut at a word
    boundary and ended with an ellipsis. Line height scales with the reduced size and the
    admissible line count is recomputed at the reduced leading — which is what makes the
    floor buy substantially more room than the same line count set smaller.
    """
    box_width = length(rect.get("width"))
    box_height = length(rect.get("height"))
    box_x = length(rect.get("x")) or 0.0
    box_y = length(rect.get("y")) or 0.0

    declared_size = _font_size(style)
    ratio = _line_height_ratio(style, declared_size)
    floor_size = declared_size / 2.0

    if box_width is None or box_height is None:
        _write_lines(element, [value], box_x, box_y, declared_size, declared_size * ratio)
        return None

    size = declared_size
    truncated = False
    lines: list[str] = []

    while True:
        leading = size * ratio
        admissible = max(1, int(box_height // leading))
        lines = _wrap(value, resolved, size, box_width)

        if len(lines) <= admissible:
            break

        if size - _SIZE_STEP < floor_size:
            # At the floor: cut at a word boundary and ellipsise the last kept line.
            lines = lines[:admissible]
            if lines:
                lines[-1] = _ellipsise_line(lines[-1], resolved, size, box_width)
            truncated = True
            break

        size -= _SIZE_STEP

    # The reduced size is written inline, and shape-inside is *removed* — not set to
    # `none`. Inkscape treats a `<text>` carrying any shape-inside declaration, `none`
    # included, as SVG2 flowed text and ignores the per-tspan positions, collapsing the
    # whole field to the top edge of the canvas. The declaration has to go entirely.
    _restyle(element, {"font-size": f"{size:g}px"})
    _remove_shape_inside(element, root_of(element))
    _write_lines(element, lines, box_x, box_y, size, size * ratio)

    if truncated:
        return RenderNotice(
            image_type=image_type,
            notice_kind=NOTICE_WRAP_TRUNCATED,
            detail=(
                f"`{field_id}` reached the {floor_size:g}px floor and was cut to the "
                f"room its box gives."
            ),
            field_id=field_id,
        )
    return None


def _wrap(value: str, resolved: ResolvedFont, size: float, width: float) -> list[str]:
    """Break *value* into lines no wider than *width*, at word boundaries."""
    lines: list[str] = []
    for paragraph in value.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if measure(candidate, resolved, size) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _ellipsise_line(
    line: str, resolved: ResolvedFont, size: float, width: float
) -> str:
    if measure(line + ELLIPSIS, resolved, size) <= width:
        return line + ELLIPSIS
    return _truncate_to_width(line, resolved, size, width)


def _write_lines(
    element: etree._Element,
    lines: list[str],
    x: float,
    y: float,
    size: float,
    leading: float,
) -> None:
    """Replace the element's content with one ``<tspan>`` per line at an absolute y."""
    _clear_children(element)
    element.text = None
    element.set("x", f"{x:g}")
    element.set("y", f"{y + size:g}")

    for number, line in enumerate(lines):
        tspan = etree.SubElement(element, f"{{{SVG_NS}}}tspan")
        tspan.set("x", f"{x:g}")
        tspan.set("y", f"{y + size + number * leading:g}")
        tspan.text = line


def _clear_children(element: etree._Element) -> None:
    for child in list(element):
        element.remove(child)
    element.text = None
