"""The six fill operations of Constitution Principle XIV.2, and no others.

    Text fill | Image fill | Recolour | Group removal | Vertical crop | Text fit

``fill()`` **raises nothing** for a data disagreement — it reports, and the caller
decides. That is what lets a caller distinguish a render that failed from one that
merely degraded (XIV.4).

Pure: no database, no Discord, no subprocess.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from models.image_constants import (
    NOTICE_ASSET_FALLBACK_USED,
    NOTICE_CROP_POINT_OFF_CANVAS,
    NOTICE_FIELD_REDUCED,
    NOTICE_FONT_SUBSTITUTED,
    NOTICE_OPTIONAL_FIELD_EMPTIED,
    is_closed_set_datum,
)
from models.image_module import FillResult, RenderNotice
from utils.asset_resolver import ASSET_EXTENSION, normalise, resolve_asset
from utils.font_metrics import ResolvedFont, measure, resolve_family
from utils.svg_document import (
    SVG_NS,
    XLINK_NS,
    FieldIndex,
    canvas_of,
    computed_style,
    declarations,
    length,
    stylesheet,
)

log = logging.getLogger(__name__)

#: Half a pixel at a time, per XIV.5.
_SIZE_STEP = 0.5

#: The reduction stops here and nowhere else (XIV.5, v7.0.0). The legibility floor of half the
#: declared size raises a notice but does not halt the descent, so something has to end it for a
#: box of no usable width, which no reduction could ever satisfy. One pixel is far below any size
#: a template would declare, so a field that reaches it is already reported and already wrong.
_MIN_SIZE = 1.0

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

    #: True where ``crop`` names the crop point of the **last member the template
    #: declares**, not merely the last the data fills. Only then is the crop point
    #: expected to stand at the declared canvas height, so only then can it be off it
    #: (FR-026). A division drawn shorter than its template crops higher by design.
    crop_is_final: bool = False

    #: Fields whose value could not be determined. Each is emptied, or its `_group`
    #: removed where the template declares one (FR-013, FR-023).
    empty: list[str] = field(default_factory=list)

    #: Fields the render deliberately takes off the canvas — by a group removal or by
    #: falling below the vertical crop. Constitution XIV.3: "A field taken off the canvas
    #: by a group removal or a vertical crop is **not** unresolved." Without this the
    #: pre-render check would demand values for members the graphic never draws, and a
    #: division smaller than its template could never render.
    off_canvas: set[str] = field(default_factory=set)

    #: Rows of data this render has for the image type's repeating collection. Compared
    #: against the catalogue's declared capacity before anything is drawn (FR-028).
    row_count: int | None = None

    #: field name -> (asset class, the datum it depicts). Resolved through the slug rule
    #: and the class's directory rather than by a path the caller built (FR-042).
    image_data: dict[str, tuple[str, str]] = field(default_factory=dict)

    #: asset class -> the configured directory it resolves in.
    asset_directories: dict[str, Path] = field(default_factory=dict)

    #: Asset class -> why its **configured** directory could not be resolved. An
    #: absent class is otherwise indistinguishable from one never configured, and a
    #: league told it never set a directory it did in fact set has nothing to act on.
    asset_directory_faults: dict[str, str] = field(default_factory=dict)

    #: The image type's field catalogue, when known.
    #:
    #: Mandatory and optional classify the **fields of the template**: whether the
    #: template must declare the field, and whether its value must be determinable.
    #: Asset resolution is a separate matter and does not consult it — see the asset
    #: fill below.
    catalogue: object | None = None

    #: Fields the data determines to be **empty**, as against ``empty`` above, which means
    #: a value that could not be determined. A lineup seat that is configured but
    #: unoccupied is the case: the template's layout is fixed, so the seat is drawn with
    #: its name cleared rather than omitted, and there is nothing wrong. These raise no
    #: notice and are not read as unresolved, even where the field is mandatory — XIV.3
    #: makes a mandatory field fatal when its value *cannot be determined*, and this one
    #: was determined.
    empty_quietly: list[str] = field(default_factory=list)

    #: The image type's field catalogue, when known. Supplying it lets `fill` report a
    #: template field the data left unfilled; without it only unknown fields are caught.
    expected_fields: set[str] | None = None


def _packaged_directory(asset_class: str) -> Path | None:
    """The module's own directory for *asset_class*, as the second fallback tier.

    Resolved against the project root, since the configured default is stored relative to
    it. None where the class is unknown or the directory is not there — a missing packaged
    directory simply leaves the tier empty and the miss falls through to fatal, which is
    what a single-tier resolution would have done anyway.
    """
    import utils.paths as paths  # read as an attribute: tests patch PROJECT_ROOT
    from models.image_constants import packaged_directory_for

    relative = packaged_directory_for(asset_class)
    if relative is None:
        return None
    directory = Path(paths.PROJECT_ROOT) / relative
    return directory if directory.is_dir() else None


def _mandatory_ids(spec: FillSpec) -> frozenset[str]:
    """This image type's mandatory field ids, read **before** anything is removed.

    Read once, at the top of ``fill``, and never again. A derived capacity is counted by
    scanning the template's member ordinals, and a group removal takes a whole member out
    of the tree — so asking again afterwards can see ``round_3`` missing between 2 and 4
    and read that as a gap. The catalogue is a fact about the *template as authored*, not
    about the tree part-way through being filled.
    """
    catalogue = spec.catalogue
    if catalogue is None:
        return frozenset()
    try:
        return frozenset(catalogue.all_mandatory_ids(spec.root))
    except Exception:  # noqa: BLE001 — an uncountable template is reported elsewhere
        return frozenset()


def fill(spec: FillSpec) -> FillResult:
    """Apply the six operations and report what did not resolve."""
    root = spec.root
    notices: list[RenderNotice] = []
    unresolved: list[str] = []

    # Before any removal: see the note on _mandatory_ids.
    mandatory_ids = _mandatory_ids(spec)
    has_catalogue = spec.catalogue is not None

    rules = stylesheet(root)

    # ── 1. Group removal ──────────────────────────────────────────────────
    # First, so that ids inside a removed subtree are gone before anything else looks
    # for them: a field removed with its group is not a field left unfilled.
    index = FieldIndex(root)
    removed_ids: set[str] = set()
    for group_id in spec.remove:
        element = index.resolve(group_id)
        if element is None:
            unresolved.append(f"unknown group `{group_id}` (template declares no such id)")
            continue
        _detach(element, removed_ids)

    index = FieldIndex(root)

    # ── 1b. Fields whose value could not be determined ────────────────────
    # Where the template wraps the field in `<field>_group`, the whole group leaves, so
    # the label, plate or separator introducing the value goes with it. Where it does
    # not, the field alone is emptied and the chrome around it is stranded — which is
    # the contrast the group convention exists to let an author avoid (FR-023, FR-024).
    for field_id in spec.empty:
        notice = _vacate(index, field_id, spec.image_type, removed_ids)
        if notice is not None:
            notices.append(notice)

    # Fields the data determined to be empty — a lineup seat nobody occupies. Vacated the
    # same way and reported not at all: nothing degraded, so there is nothing to notice.
    for field_id in spec.empty_quietly:
        _vacate(index, field_id, spec.image_type, removed_ids, notify=False)

    if spec.empty or spec.empty_quietly:
        index = FieldIndex(root)

    # ── 2. Vertical crop ──────────────────────────────────────────────────
    # Made in the SVG rather than asked of the rasteriser's export area, so it does not
    # depend on which way up a rasteriser counts.
    crop_y: float | None = None
    if spec.crop is not None:
        node = index.resolve(spec.crop)
        if node is None:
            unresolved.append(
                f"unknown crop point `{spec.crop}` (template declares no such id)"
            )
        else:
            crop_y = _element_y(node)
            if crop_y is None:
                unresolved.append(f"crop point `{spec.crop}` declares no y")
            else:
                # FR-026: a template is expected to put its **last** declared member's
                # crop point at the declared canvas height, so a division holding as many
                # members as the template declares is drawn whole. Where it does not, cut
                # there anyway and say so — the template still draws correctly for every
                # smaller division, so refusing it would be disproportionate.
                declared_height = length(root.get("height"))
                if (
                    spec.crop_is_final
                    and declared_height is not None
                    and abs(crop_y - declared_height) > 0.5
                ):
                    notices.append(
                        RenderNotice(
                            image_type=spec.image_type,
                            notice_kind=NOTICE_CROP_POINT_OFF_CANVAS,
                            field_id=spec.crop,
                            detail=(
                                f"the last declared member's crop point sits at y={crop_y:g} "
                                f"but the template declares a height of {declared_height:g}. "
                                f"A full-size division is drawn to the crop point, not to "
                                f"the canvas."
                            ),
                        )
                    )
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
        element = index.resolve(field_id)
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
        element = index.resolve(field_id)
        if element is None:
            if field_id not in removed_ids:
                unresolved.append(
                    f"unknown image field `{field_id}` (template declares no such id)"
                )
            continue
        target = _descend(element, "image")
        if target is None:
            unresolved.append(
                f"image field `{field_id}` resolves to a layer holding no single "
                f"<image> element"
            )
            continue
        _set_href(target, href)
        consumed.add(field_id)

    # ── 4b. Image fill by asset class and datum ───────────────────────────
    # The datum is resolved through the slug rule inside the class's configured
    # directory (FR-042). **Four** outcomes since v6.0.0, and the field's
    # mandatory/optional classification bears on none of them:
    #
    #   file found in the configured directory     → drawn
    #   absent, configured directory has fallback  → fallback drawn, notice raised
    #   absent, packaged directory has fallback    → fallback drawn, the same notice
    #   absent, neither tier has one               → fatal; the generation is abandoned
    #
    # For a datum that is the module's **own vocabulary** rather than a value the league
    # chose, the third row is itself refined (Constitution XIV.13, v6.1.0): the packaged
    # directory is searched for the datum's own file before its `fallback.svg`, whether or
    # not the league has pointed the class at a directory of its own, because the league
    # never chose that vocabulary and cannot be incomplete against it. `is_closed_set_datum`
    # is the single question asked — a whole class qualifies where every datum it can be
    # handed is the module's (marker, weather), and an individual slug qualifies where the
    # class is otherwise the league's own (`mystery`, `other` in flag and track). Still the
    # same notice kind — only which file gets drawn, and what the notice says, differ.
    #
    # This is the module's **only** call to `resolve_asset`, which is what makes the
    # packaged tier reach every asset class of every graphic at once (047 FR-044).
    for field_id, (asset_class, datum) in spec.image_data.items():
        element = index.resolve(field_id)
        if element is None:
            if field_id not in removed_ids:
                unresolved.append(
                    f"unknown image field `{field_id}` (template declares no such id)"
                )
            continue

        target = _descend(element, "image")
        if target is None:
            unresolved.append(
                f"image field `{field_id}` resolves to a layer holding no single "
                f"<image> element"
            )
            continue

        directory = spec.asset_directories.get(asset_class)
        if directory is None:
            # A class the league configured, whose directory was rejected, is not a class
            # it never configured. Saying so is the difference between a fault a manager
            # can fix and one they cannot account for.
            fault = spec.asset_directory_faults.get(asset_class)
            if fault:
                unresolved.append(
                    f"image field `{field_id}` draws asset class `{asset_class}`, "
                    f"whose configured directory was rejected — {fault}"
                )
            else:
                unresolved.append(
                    f"image field `{field_id}` names asset class `{asset_class}`, "
                    f"which is not configured"
                )
            continue

        # An **absent datum** on a field whose catalogue declares one (XIV.13, v4.4.0).
        # There is nothing to look an asset up by, and the class's fallback stands for the
        # absence itself rather than for a file that should have existed — so it is drawn,
        # and nothing whatever is reported. A tyre the submission of a session never
        # obliged is the case this exists for.
        absent_datum = not (datum or "").strip()
        depicts_absence = absent_datum and bool(
            getattr(spec.catalogue, "draws_fallback_when_absent", lambda _f: False)(
                field_id
            )
        )

        resolution = resolve_asset(
            Path(directory),
            datum,
            packaged=_packaged_directory(asset_class),
            closed_set=is_closed_set_datum(asset_class, normalise(datum)),
        )

        if resolution.found:
            _set_href(target, str(resolution.path))
            consumed.add(field_id)
            continue

        if resolution.used_fallback:
            _set_href(target, str(resolution.path))
            consumed.add(field_id)
            if not depicts_absence:
                # What was actually drawn, not what the tier was called. A closed-set hit
                # in the packaged directory drew the module's **own correct file** for the
                # datum — calling that "the fallback was drawn" would send a manager
                # looking for artwork they were never expected to supply. The notice kind
                # stays the same on both tiers, which is what XIV.13 requires; only the
                # sentence differs.
                detail = (
                    f"the configured `{asset_class}` directory has no image for "
                    f"“{datum}”; the bot's own "
                    f"`{resolution.slug}{ASSET_EXTENSION}` was drawn instead."
                    if resolution.drew_own_file
                    else (
                        f"no `{asset_class}` image is supplied for “{datum}”; the "
                        f"directory's fallback was drawn instead."
                    )
                )
                notices.append(
                    RenderNotice(
                        image_type=spec.image_type,
                        notice_kind=NOTICE_ASSET_FALLBACK_USED,
                        detail=detail,
                        field_id=field_id,
                    )
                )
            continue

        # The datum is absent, the field declares the fallback, and the class carries none:
        # the declaration is inert and the field simply leaves. An absent datum is never
        # fatal for want of a file — that severity belongs to a datum that *was* sought.
        if depicts_absence:
            _vacate(index, field_id, spec.image_type, removed_ids, notify=False)
            consumed.add(field_id)
            continue

        # The asset is missing and its class carries no fallback: fatal, and the
        # generation is abandoned.
        #
        # An asset class covers every datum a league can present it with, or it carries
        # a `fallback.svg` that does. Neither is a gap in the league's asset set, and
        # not something to draw around.
        unresolved.append(
            f"field `{field_id}` needs a `{asset_class}` image for “{datum}”, and "
            f"neither `{resolution.slug or datum}.svg` nor a `fallback.svg` is in that "
            f"directory"
        )

    # ── 5 & 6. Text fill, with wrap and inline-size bounds ────────────────
    for field_id, value in spec.text.items():
        element = index.resolve(field_id)
        if element is not None:
            # A field addressed by layer label resolves to the layer; descend to the
            # text it holds, or the fill would gut the layer and draw nothing.
            target = _descend(element, "text")
            if target is None:
                # The wip-spec: "Where the layer holds no such element, or more than one,
                # the field is not resolved and the error is that of a mandatory or
                # optional field as its catalogue declares it." An *optional* field is
                # therefore a notice, not a failure — a league that drew a decorative
                # layer where an optional value could go has not broken its template.
                if has_catalogue and field_id not in mandatory_ids:
                    notices.append(
                        RenderNotice(
                            image_type=spec.image_type,
                            notice_kind=NOTICE_OPTIONAL_FIELD_EMPTIED,
                            field_id=field_id,
                            detail=(
                                f"`{field_id}` resolves to a layer holding no single "
                                f"<text> element, so nothing was placed on it"
                            ),
                        )
                    )
                else:
                    unresolved.append(
                        f"field `{field_id}` resolves to a layer holding no single "
                        f"<text> element to fill"
                    )
                continue
            element = target
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

        # The structural defects of a bounded field (XIV.5, v7.0.0). Each is read off the
        # template alone, so each is knowable long before a render — the layer of Rule 9
        # checks the same ones against the same template.
        budget = _max_lines(style)
        if budget == _MAX_LINES_INVALID:
            unresolved.append(
                f"field `{field_id}` declares a `max-lines` of "
                f"`{style.get('max-lines', '').strip()}`, which is not a positive whole "
                f"number of lines"
            )
            continue

        shape_id = _shape_inside_id(style)
        if shape_id is not None:
            rect = index.resolve(shape_id)
            if rect is None:
                unresolved.append(
                    f"field `{field_id}` names shape-inside `{shape_id}`, "
                    f"which the template does not declare"
                )
                continue

            ratio = _line_height_ratio(style, _font_size(style))
            if ratio is None:
                unresolved.append(
                    f"wrapped field `{field_id}` has no `line-height` resolving upon it, "
                    f"so the leading between its lines cannot be worked out"
                )
                continue

            box_width = length(rect.get("width"))
            box_height = length(rect.get("height"))
            if box_width is None:
                unresolved.append(
                    f"wrapped field `{field_id}` names shape-inside `{shape_id}`, "
                    f"which declares no usable width to lay the text out in"
                )
                continue
            # Height fixes the budget only where the field declares no `max-lines` of its own.
            if budget is None and box_height is None:
                unresolved.append(
                    f"wrapped field `{field_id}` names shape-inside `{shape_id}`, "
                    f"which declares no usable height, and declares no `max-lines` to "
                    f"stand in for it"
                )
                continue

            notice = _lay_out(
                element,
                rect,
                str(value),
                style,
                resolved,
                spec.image_type,
                field_id,
                ratio=ratio,
                box_width=box_width,
                box_height=box_height or 0.0,
                budget=budget,
            )
            if notice is not None:
                notices.append(notice)
            consumed.add(field_id)
            consumed.add(shape_id)  # the rectangle is consumed as an addressed field
            continue

        # A box declared in CSS, or no box at all: `inline-size` wide, `max-lines` tall.
        limit = length(style.get("inline-size"))
        ratio: float | None = None
        if budget is not None and budget > 1:
            if limit is None:
                unresolved.append(
                    f"field `{field_id}` declares a `max-lines` of {budget} but no "
                    f"`inline-size` giving the width to wrap it against"
                )
                continue
            ratio = _line_height_ratio(style, _font_size(style))
            if ratio is None:
                unresolved.append(
                    f"field `{field_id}` declares a `max-lines` of {budget} but has no "
                    f"`line-height` resolving upon it, so the leading between its lines "
                    f"cannot be worked out"
                )
                continue

        notice = _set_text(
            element,
            str(value),
            style,
            resolved,
            spec.image_type,
            field_id,
            limit=limit,
            budget=budget or 1,
            ratio=ratio,
        )
        if notice is not None:
            notices.append(notice)
        consumed.add(field_id)

    # Every field has now been measured against its box, so no bound may survive into the
    # rasteriser, which would otherwise re-flow the lines this module has already set.
    _remove_inline_size(root)

    # ── Unresolved fields (XIV.3) ─────────────────────────────────────────
    # A field the crop took off the canvas is not a field left unfilled, so only ids
    # above the cut are reported.
    if spec.expected_fields is not None:
        index = FieldIndex(root)
        for field_id in sorted(spec.expected_fields - consumed - removed_ids):
            element = index.resolve(field_id)
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


def _descend(element: etree._Element, localname: str) -> etree._Element | None:
    """Resolve a field that is a *layer* down to the element an operation can act on.

    The layer-label fallback (Constitution XIV.2) addresses a field by the label a
    manager set on a layer, and a layer is a ``<g>``. A text fill cannot act on a group:
    setting text on it would put a bare text node inside the group, which draws nothing,
    and clearing its children would delete the very ``<text>`` the manager labelled.

    So an operation that needs a particular kind of element descends to it. Exactly one
    candidate must be present — a layer holding two ``<text>`` nodes does not say which
    is the field, and guessing would fill the wrong one.

    A text fill acts on a ``<text>`` **or a ``<tspan>``**, which is what XIV.2's operation
    table names, so a field labelled on a tspan is already the element to fill and needs no
    descent. Templates authored in a graphical editor carry these: a manager selects the
    styled run inside a line and labels that, not the line.
    """
    here = etree.QName(element).localname
    if here == localname or (localname == "text" and here == "tspan"):
        return element

    candidates = [
        node
        for node in element.iter(f"{{{SVG_NS}}}{localname}")
        if node is not element
    ]
    return candidates[0] if len(candidates) == 1 else None


#: Schemes an href may already carry, which must be passed through untouched.
_URI_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def _as_href(value: str) -> str:
    """Render a value usable as an SVG ``href``.

    An absolute filesystem path is **not** a URI, and on Windows it is not even close:
    ``C:\\assets\\british.svg`` has a one-letter "scheme" and backslash separators. The
    rasteriser cannot resolve it and silently draws a broken-image icon in its place —
    a defect invisible in the SVG and obvious only in the PNG (Constitution XIV.14).

    An absolute path is therefore converted to a ``file://`` URI. Anything already
    carrying a scheme, and any relative reference, is left alone: a template may legally
    point at a file beside itself.
    """
    text = str(value)
    if _URI_SCHEME_RE.match(text) and not re.match(r"^[a-zA-Z]:[\\/]", text):
        return text  # data:, file:, http: … already a URI

    candidate = Path(text)
    if candidate.is_absolute():
        return candidate.as_uri()
    return text


def _set_href(element: etree._Element, href: str) -> None:
    """Point an image element at a file. Both spellings, for either SVG version."""
    value = _as_href(href)
    element.set(f"{{{XLINK_NS}}}href", value)
    element.set("href", value)


def _detach(element: etree._Element, removed_ids: set[str]) -> None:
    """Remove *element* and record every id that leaves with it.

    Ids inside a removed subtree must be known, because a field the removal took off the
    canvas is not a field left unfilled (Constitution XIV.3).
    """
    for descendant in element.iter():
        descendant_id = descendant.get("id")
        if descendant_id:
            removed_ids.add(descendant_id)
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _vacate(
    index,
    field_id: str,
    image_type: str,
    removed_ids: set[str],
    *,
    notify: bool = True,
) -> RenderNotice | None:
    """Take a field off the graphic: its `_group` if declared, else the field itself.

    The group carries the static chrome that introduces the value — a label, a plate, a
    separator — so removing the group is what stops a heading being left pointing at
    nothing (FR-023). Without one, only the field can be emptied, and it is the
    template author's business to have wrapped it (FR-024).

    The canvas is never resized either way (FR-026).
    """
    group = index.group_for(field_id)
    if group is not None:
        _detach(group, removed_ids)
    else:
        element = index.resolve(field_id)
        if element is None:
            return None
        if _descend(element, "image") is not None:
            # An **image** field has nothing to empty, so it is removed rather than cleared
            # (Constitution XIV.3, v4.4.0). Clearing one would leave the `<image>` pointing
            # at whatever href the template shipped, drawn as a stale picture or as the
            # broken-image mark Rule 6 exists to prevent.
            _detach(element, removed_ids)
        else:
            _clear_children(element)
            element.text = None
            removed_ids.add(field_id)

    if not notify:
        return None
    return RenderNotice(
        image_type=image_type,
        notice_kind=NOTICE_OPTIONAL_FIELD_EMPTIED,
        detail=(
            "its value could not be determined, so "
            + ("the block around it was removed." if group is not None else "it was emptied.")
        ),
        field_id=field_id,
    )


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


def _is_bold(style: dict[str, str]) -> bool:
    """True where the field declares a weight a bold face would be drawn for."""
    raw = (style.get("font-weight") or "").strip().lower()
    if raw in {"bold", "bolder"}:
        return True
    try:
        return int(raw) >= 600
    except ValueError:
        return False


def _is_italic(style: dict[str, str]) -> bool:
    return (style.get("font-style") or "").strip().lower() in {"italic", "oblique"}


def _resolve_font(
    style: dict[str, str], field_id: str, image_type: str
) -> tuple[ResolvedFont, RenderNotice | None]:
    # Weight and style select among a family's faces, per XIV.5: a bold field measured
    # against the regular face would admit lines the canvas does not hold.
    resolved = resolve_family(
        style.get("font-family"), bold=_is_bold(style), italic=_is_italic(style)
    )
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


def _line_height_ratio(style: dict[str, str], size: float) -> float | None:
    """Resolve ``line-height`` to a multiple of the font size, or **None**.

    CSS gives it two meanings: a bare number is a *ratio* (`1.3`), while a value with a
    unit is an absolute length (`26px`). They must not be conflated — reading `1.3` as
    1.3px collapses the leading to nothing, and every line then "fits".

    Returning None where nothing resolves is deliberate and is the rule, not a defensive
    branch (XIV.5, v4.8.0). A substituted default would silently decide how many lines of
    a league's prose are drawn, which is the template's decision; the caller makes the
    absence a problem instead.
    """
    raw = (style.get("line-height") or "").strip()
    if not raw:
        return None

    try:
        return float(raw)  # unitless: already a ratio
    except ValueError:
        pass

    absolute = length(raw)
    if absolute is not None and size > 0:
        return absolute / size
    return None


#: Returned by ``_max_lines`` where a budget is declared but is not a positive whole number.
#: A structural defect under XIV.5, reported by the caller and never silently defaulted.
_MAX_LINES_INVALID = -1


def _max_lines(style: dict[str, str]) -> int | None:
    """The field's declared line budget, or None where it declares none (XIV.5).

    Where declared, this **is** the budget, whichever way the box was declared — it supersedes
    the count a rectangle's height would give. Half a line is not a budget and neither is none
    of one, so anything but a positive whole number is a defect rather than a value to round.
    """
    raw = (style.get("max-lines") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return _MAX_LINES_INVALID
    if value <= 0 or value != int(value):
        return _MAX_LINES_INVALID
    return int(value)


def _fit_lines(
    value: str,
    resolved: ResolvedFont,
    declared_size: float,
    width: float,
    budget_at: "Callable[[float], int]",
) -> tuple[list[str], float, bool]:
    """Break *value* into no more lines than its budget, reducing until it fits (XIV.5).

    Returns the lines, the size they are set at, and whether that size fell below the legibility
    floor of half *declared_size*.

    **The floor stops nothing** (v7.0.0). It marks the point at which a notice is owed, and the
    reduction carries on past it until the text fits, because the alternative — stopping and
    cutting — draws a value that is wrong rather than one that is small. Only ``_MIN_SIZE`` ends
    the descent, and only to keep a box of no usable width from looping for ever.

    *budget_at* is a function of the size because a rectangle's budget grows as the leading
    shrinks; a declared ``max-lines`` ignores its argument and is constant.

    Where even ``_MIN_SIZE`` will not bring the value within its budget — 400 characters in a
    120px box, which no font size reaches — the lines are returned **over budget** rather than
    trimmed to it. Trimming would be cutting, and cutting is exactly what v7.0.0 withdrew, so a
    field asked to hold the impossible draws it all and says so through the notice the caller
    raises. Only the caller can tell whether that reads as wrapping or as overlap, and neither
    is a thing the module can make good on the league's behalf.
    """
    size = declared_size
    while True:
        lines = _wrap(value, resolved, size, width)
        if len(lines) <= budget_at(size):
            break
        if size - _SIZE_STEP < _MIN_SIZE:
            break
        size -= _SIZE_STEP
    return lines, size, size < declared_size / 2.0


def _reduced_notice(
    image_type: str, field_id: str, size: float, declared_size: float
) -> RenderNotice:
    return RenderNotice(
        image_type=image_type,
        notice_kind=NOTICE_FIELD_REDUCED,
        detail=(
            f"`{field_id}` was set to {size:g}px to hold its value, below the "
            f"{declared_size / 2.0:g}px floor of the {declared_size:g}px it declares."
        ),
        field_id=field_id,
    )


def _set_text(
    element: etree._Element,
    value: str,
    style: dict[str, str],
    resolved: ResolvedFont,
    image_type: str,
    field_id: str,
    *,
    limit: float | None,
    budget: int,
    ratio: float | None,
) -> RenderNotice | None:
    """Fill a field whose box is declared in CSS, or which declares none at all (XIV.5).

    The box is ``inline-size`` wide and ``max-lines`` x ``line-height`` tall, and is centred on
    the field's declared ``y``. A field taking one line therefore lands exactly on the baseline
    the template drew — so bounding a field that never wraps moves nothing — and one taking two
    grows half a line either side of it.

    The element's own ``x`` is kept rather than rewritten, which is what preserves ``text-anchor``:
    the calendar's date and time are anchored at their right edge, and re-anchoring them to a box's
    left edge would push them across the card.
    """
    if limit is None:
        # No box at all: one unbounded line, measured against nothing (XIV.5).
        _clear_children(element)
        element.text = value
        return None

    declared_size = _font_size(style)
    lines, size, reduced = _fit_lines(
        value, resolved, declared_size, limit, lambda _size: budget
    )

    if size != declared_size:
        _restyle(element, {"font-size": f"{size:g}px"})

    # `inline-size` is not cancelled here. It is stripped from the whole document once every
    # field has been measured — see `_remove_inline_size`, which records why it cannot be done
    # field by field.
    if len(lines) <= 1:
        _clear_children(element)
        element.text = lines[0] if lines else ""
    else:
        leading = size * (ratio if ratio is not None else 1.0)
        baseline = _element_y(element)
        x = length(element.get("x"))
        _write_lines(
            element,
            lines,
            x=x,
            first_baseline=(baseline or 0.0) - (len(lines) - 1) * leading / 2.0,
            leading=leading,
        )

    return _reduced_notice(image_type, field_id, size, declared_size) if reduced else None


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


def _remove_inline_size(root: etree._Element) -> None:
    """Strip ``inline-size`` from the whole document once every field is laid out (XIV.5).

    Inkscape honours **no cancelling value**. `inline-size:auto` leaves a `<text>` in SVG2
    flowed mode exactly as a real length does, whereupon the rasteriser re-flows the very lines
    this module measured and placed: it concatenates the adjacent `<tspan>`s, losing the space
    between them, and re-breaks the result at its own idea of the box. "Enzo e" + "Dino
    Ferrari" comes back as "Enzo" / "eDino Ferrari". Verified against Inkscape 1.x, where
    `auto` rasterises byte-identically to the declared length; only removal works. This is the
    same trap `_remove_shape_inside` exists for, and it is sprung the same way.

    Done **once, at the end**, and not field by field, because the declaration usually lives on
    a *class* shared by many fields — the twelve rounds of a calendar all draw their circuit
    from one `.rsub` rule. Stripping it while the loop still had fields to measure would leave
    every later one unbounded, which is the defect this whole change exists to remove.
    """
    for element in root.iter():
        if "inline-size" in (element.get("style") or ""):
            _restyle(element, {"inline-size": None})

    for style_element in root.iter(f"{{{SVG_NS}}}style"):
        css = style_element.text or ""
        if "inline-size" in css:
            style_element.text = _strip_property(css, "inline-size")


def _strip_property(css: str, prop: str) -> str:
    """Remove *prop* from every rule of *css*, whatever its selector."""

    def _rewrite(match: re.Match[str]) -> str:
        selectors, block = match.group(1), match.group(2)
        kept = [
            declaration
            for declaration in block.split(";")
            if declaration.strip() and not declaration.strip().lower().startswith(prop)
        ]
        return f"{selectors}{{{';'.join(kept)}}}"

    return re.sub(r"([^{}]+)\{([^{}]*)\}", _rewrite, css)


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
    *,
    ratio: float,
    box_width: float,
    box_height: float,
    budget: int | None,
) -> RenderNotice | None:
    """Wrap *value* against *rect*, descending by half a pixel until it fits (XIV.5).

    The text is never cut. Below **half** the template-declared size a notice is raised, and the
    reduction carries on past that floor until the value fits (v7.0.0). Where the field declares
    no ``max-lines``, line height scales with the reduced size and the admissible line count is
    recomputed at the reduced leading — which is what makes reducing buy substantially more room
    than the same line count set smaller.

    *ratio*, *box_width* and *box_height* are resolved and validated by the caller: a
    field with no leading, or a rectangle with no usable width, is a **problem** and never
    reaches layout.

    Laid from the **top** of the rectangle rather than centred within it. These are the prose
    fields — a steward's description, a steward's justification — and prose floating in the
    middle of a box it does not fill reads as a mistake rather than as a design (XIV.5, v7.0.0).
    """
    box_x = length(rect.get("x")) or 0.0
    box_y = length(rect.get("y")) or 0.0

    declared_size = _font_size(style)

    if budget is None:
        def budget_at(size: float) -> int:
            # A rectangle's own budget grows as the leading shrinks, so a field set smaller holds
            # **more lines** rather than the same number more widely spaced (XIV.5). A declared
            # `max-lines` is constant instead, and says so by ignoring the size.
            return max(1, int(box_height // (size * ratio)))
    else:
        def budget_at(_size: float) -> int:
            return budget

    lines, size, reduced = _fit_lines(value, resolved, declared_size, box_width, budget_at)

    # The reduced size is written inline, and shape-inside is *removed* — not set to
    # `none`. Inkscape treats a `<text>` carrying any shape-inside declaration, `none`
    # included, as SVG2 flowed text and ignores the per-tspan positions, collapsing the
    # whole field to the top edge of the canvas. The declaration has to go entirely.
    _restyle(element, {"font-size": f"{size:g}px"})
    _remove_shape_inside(element, root_of(element))
    _write_lines(element, lines, x=box_x, first_baseline=box_y + size, leading=size * ratio)

    return _reduced_notice(image_type, field_id, size, declared_size) if reduced else None


def _split_word(word: str, resolved: ResolvedFont, size: float, width: float) -> list[str]:
    """Break a single word too wide for its box into pieces that fit (XIV.5).

    Scans forward rather than trimming from the end, so the cost is proportional to the
    word's length and not to its square. A character that will not fit on a line of its
    own still yields a piece: emitting nothing would drop the word silently.
    """
    pieces: list[str] = []
    remaining = word
    while remaining:
        if measure(remaining, resolved, size) <= width:
            pieces.append(remaining)
            break
        cut = 1
        while cut < len(remaining) and measure(remaining[: cut + 1], resolved, size) <= width:
            cut += 1
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    return pieces


def _wrap(value: str, resolved: ResolvedFont, size: float, width: float) -> list[str]:
    """Break *value* into lines no wider than *width*, at word boundaries.

    The author's own line breaks are honoured first and their blank lines kept, each
    counting against the field's budget as a line of text does. A word wider than the box
    is broken **within itself** rather than emitted as an over-wide line — a pasted URL in
    a steward's justification is the case that meets this.
    """
    lines: list[str] = []
    for paragraph in value.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue

        current = ""
        for word in words:
            if measure(word, resolved, size) > width:
                # Finish the line in progress, then break the word from a fresh one.
                if current:
                    lines.append(current)
                    current = ""
                pieces = _split_word(word, resolved, size, width)
                lines.extend(pieces[:-1])
                current = pieces[-1]
                continue

            candidate = word if not current else f"{current} {word}"
            if measure(candidate, resolved, size) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _write_lines(
    element: etree._Element,
    lines: list[str],
    *,
    x: float | None,
    first_baseline: float,
    leading: float,
) -> None:
    """Replace the element's content with one ``<tspan>`` per line at an absolute y.

    *x* is None where the element declares none of its own, in which case each line inherits the
    horizontal position and anchoring already in force rather than being pinned to a box's edge.
    Pinning is what the rectangle path wants and what the CSS-declared box path must not do: a
    field anchored at its right edge, as the calendar's date and time are, would be pushed across
    the card by an x taken from anywhere but itself.
    """
    _clear_children(element)
    element.text = None
    if x is not None:
        element.set("x", f"{x:g}")
    element.set("y", f"{first_baseline:g}")

    for number, line in enumerate(lines):
        tspan = etree.SubElement(element, f"{{{SVG_NS}}}tspan")
        if x is not None:
            tspan.set("x", f"{x:g}")
        tspan.set("y", f"{first_baseline + number * leading:g}")
        tspan.text = line


def _clear_children(element: etree._Element) -> None:
    for child in list(element):
        element.remove(child)
    element.text = None
