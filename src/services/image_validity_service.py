"""ImageValidityService — layered template validity (Constitution Principle XIV.9).

What makes a template valid is deliberately not settled in this increment. It is defined
incrementally, one image type at a time, as each type's field catalogue is written. This
module builds the surface those later definitions plug into.

Adding a layer is **one class and one list entry**. If a later session finds itself
editing a cog, a command signature, ``ValidityReport`` or the report renderer in order to
add a layer, the stable-surface invariant has been broken and this design has failed.

Four invariants bind the growth:

1. Stable surface   — a new layer changes no command, no state, no report shape.
2. Specific attribution — every layer names the individual template, never the group.
3. Declared depth   — a report states which layers were applied.
4. No silent pass   — a type checked shallowly is not presented as fully valid.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from models.image_catalogues import (
    CapacityError,
    catalogue_for,
    sibling_fields_declared,
)
from models.image_constants import (
    ASPECT_LABELS,
    ASPECT_SOURCE_MODULE,
    ASPECT_TEMPLATES,
    ASPECTS,
    ASSET_ASPECT_TOLERANCE,
    ASSET_DIRECTORIES,
    RATIO_CONSISTENT_ASSET_CLASSES,
    STRETCHABLE_ASSET_CLASSES,
    TEMPLATE_COLUMNS,
    TEMPLATE_COMMAND_NAMES,
    TEMPLATE_LABELS,
)
from models.image_module import (
    PROBLEM_EXTENSION,
    PROBLEM_ASPECT_DISAGREEMENT,
    PROBLEM_MISSING_MANDATORY_FIELD,
    PROBLEM_NOT_FOUND,
    PROBLEM_NOT_SVG,
    STATE_DISABLED,
    STATE_ENABLED,
    STATE_ENABLED_INVALID,
    AspectStatus,
    DirectoryReport,
    ImageConfig,
    Problem,
    ValidityReport,
)
from utils.paths import PathContainmentError, resolve_within_project_root
from utils.svg_document import (
    FieldIndex,
    SvgNoCanvasError,
    SvgParseError,
    canvas_of,
    load_svg,
)

log = logging.getLogger(__name__)

_SVG_NS = "http://www.w3.org/2000/svg"

#: Layer numbers. Only LAYER_RESOLUTION is implemented in this increment; the rest are
#: reserved so their numbering is settled before their definitions arrive.
LAYER_RESOLUTION = 1
LAYER_CATALOGUE = 2       # reserved — needs the image type's field catalogue
LAYER_BOUNDS = 3          # reserved — needs the catalogue plus which fields are unbounded
LAYER_TRIAL_RENDER = 4    # reserved — needs sample data per type

LAYER_NAMES = {
    LAYER_RESOLUTION: "Resolution",
    LAYER_CATALOGUE: "Catalogue conformance",
    LAYER_BOUNDS: "Bounds declaration",
    LAYER_TRIAL_RENDER: "Trial render",
}


@dataclass
class TemplateContext:
    """Everything a layer is given about one template."""

    config: ImageConfig
    template_key: str
    root: Path | None = None          # project root override, for tests

    #: Parsed trees shared between layers **within one evaluation** (research R5).
    #:
    #: Layer 1 parses to check well-formedness and the canvas; Layer 2 needs the same
    #: tree to look for mandatory fields. Without sharing, a season review reads fifteen
    #: files twice. It is deliberately not memoised across evaluations: a manager edits a
    #: template and re-runs the check expecting to see the change.
    parsed: dict[Path, object] = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return getattr(self.config, self.template_key)

    def resolve(self) -> Path:
        directory = resolve_within_project_root(
            self.config.template_directory, root=self.root
        )
        return directory / self.filename

    def tree(self, path: Path):
        """The parsed root for *path*, parsing at most once per evaluation.

        Raises :class:`SvgParseError` exactly as ``load_svg`` does; a failure is not
        cached, because nothing downstream runs after Layer 1 rejects the file.
        """
        cached = self.parsed.get(path)
        if cached is None:
            cached = load_svg(path)
            self.parsed[path] = cached
        return cached


@dataclass
class LayerResult:
    passed: bool
    reason: str | None = None


@runtime_checkable
class ValidityLayer(Protocol):
    number: int
    name: str

    def applies_to(self, template_key: str) -> bool: ...

    def check(self, ctx: TemplateContext) -> LayerResult: ...


class ResolutionLayer:
    """Layer 1 — the only mandatory layer, applying to all fifteen templates.

    Three checks, whose failures must be mutually distinguishable: the file resolves
    inside the configured directory, it parses as well-formed SVG, and its root declares
    a canvas (Constitution XIV.1).
    """

    number = LAYER_RESOLUTION
    name = LAYER_NAMES[LAYER_RESOLUTION]

    def applies_to(self, template_key: str) -> bool:
        return True

    def check(self, ctx: TemplateContext) -> LayerResult:
        path = ctx.resolve()

        if not path.exists():
            return LayerResult(False, f"file not found: {path}")
        if not path.is_file():
            return LayerResult(False, f"not a file: {path}")

        try:
            root = ctx.tree(path)
        except SvgParseError as exc:
            return LayerResult(False, f"not a valid SVG file — {exc}")

        try:
            canvas_of(root)
        except SvgNoCanvasError as exc:
            return LayerResult(False, f"declares no canvas: {exc}")

        return LayerResult(True)


#: The only two image types that may declare a field of the ``track`` class — the two on
#: which the round is the graphic's subject and a circuit outline has room to read
#: (Constitution XIV.13). Everywhere else a round is a column heading and draws its flag.
MAP_BEARING_TEMPLATES = frozenset({"calendar_template", "rsvp_template"})


def _slot_aspect(node) -> float | None:
    """The declared width ÷ height of an image slot, or None where it declares neither.

    A slot with no usable dimensions is already a fault of its own elsewhere; this
    defers to that rather than dividing by zero or inventing a shape for it.
    """
    try:
        width = float(node.get("width"))
        height = float(node.get("height"))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _class_slots(root, template_key: str) -> dict[str, list[tuple[str, float, bool]]]:
    """Every image slot of *root* grouped by asset class.

    One walk of the tree serving both shape checks, so a template is not iterated twice to
    ask two questions about the same elements. Each entry is the field id, the ratio it
    declares, and whether it declares that it stretches.

    A slot with no usable dimensions is dropped here unless it stretches, rather than
    defended against downstream: an unmeasurable slot is already a fault of its own in
    Layer 3, and inventing a shape for it would report the wrong one.
    """
    catalogue = catalogue_for(template_key)
    if catalogue.is_empty:
        return {}

    slots: dict[str, list[tuple[str, float, bool]]] = {}
    for node in root.iter(f"{{{_SVG_NS}}}image"):
        field_id = node.get("id")
        if not field_id:
            continue
        asset_class = catalogue.asset_class_for(field_id)
        if asset_class is None:
            continue
        found = _slot_aspect(node)
        stretches = node.get("preserveAspectRatio") == "none"
        if found is None and not stretches:
            continue
        slots.setdefault(asset_class, []).append(
            (field_id, found if found is not None else 0.0, stretches)
        )
    return slots


def stretch_faults_of(root, template_key: str) -> list[str]:
    """Every slot declaring it stretches whose class is not allowed to (XIV.6).

    Only `marker` may: it draws the standings and attendance marks, which fill the room
    their cell gives them. Every other class is authored to a shape, and a slot saying
    otherwise squashes whatever is drawn into it.

    **This is checked in its own right, and must be.** It was formerly enforced only as a
    side effect of the shape comparison -- the exemption was refused, and the slot then
    failed the comparison if its shape happened also to be wrong. Now that the shape is
    taken from the template rather than from a table, that side effect is gone: a lineup
    whose portrait slots *all* stretch agrees with itself perfectly, is passed over by
    :func:`class_aspect_faults_of`, and would draw every face in the league squashed with
    nothing whatever said. So the declaration is the fault, whatever the shape beside it.
    """
    faults: list[str] = []
    for asset_class, slots in sorted(_class_slots(root, template_key).items()):
        if asset_class in STRETCHABLE_ASSET_CLASSES:
            continue
        for field_id, _ratio, stretches in sorted(slots):
            if stretches:
                faults.append(
                    f"`{field_id}` draws the {asset_class} class and declares that it "
                    f"stretches, which only a marker slot may do. Whatever is drawn into "
                    f"it would be squashed to the shape of the box, and no artwork could "
                    f"correct it."
                )
    return faults


def class_aspect_faults_of(root, template_key: str) -> list[str]:
    """Every image slot whose shape disagrees with the others of its class (XIV.6).

    One class carries one shape *on the template that draws it*, because a league authors
    one file per datum and the generator never pads: that one file is letterboxed wherever a
    slot disagrees with the rest, and no artwork the league could supply would answer it.

    **Which** shape is the league's own business, and this function names none. It reads the
    reference off the template in hand -- the ratio the most slots of the class declare --
    and reports the others against it. A template drawing every flag at 2:1 is drawing them
    correctly; one drawing twenty-three at 2:1 and the twenty-fourth square is not. Until
    2026-09-01 the reference was a table in `image_constants`, which refused the first of
    those as readily as the second.

    The mode, rather than a pairwise clustering of the ratios found. Chained near-misses --
    1.00, 1.005, 1.01 and on -- are each within tolerance of their neighbour while the ends
    are nowhere near each other, so clustering would let a set drift arbitrarily far apart
    with every comparison passing. Ties are broken by sorted field id, so the reference does
    not depend on document order, which differs between authoring tools.

    A stretching slot is passed over: it fills its box whatever shape either is, so there is
    nothing for it to be letterboxed against. Only `marker` may say so, which
    :func:`stretch_faults_of` has already established by the time this runs.

    The comparison is **relative and tolerant**: template geometry is authored in Inkscape
    and carries floating-point values, so an exact test would reject every template a human
    drew. The tolerance still catches a square slot among 3:2 ones, which is a 50% error.
    """
    faults: list[str] = []
    for asset_class, slots in sorted(_class_slots(root, template_key).items()):
        if asset_class not in RATIO_CONSISTENT_ASSET_CLASSES:
            continue

        measured = sorted(
            (field_id, ratio) for field_id, ratio, stretches in slots if not stretches
        )
        if len(measured) < 2:
            continue  # a single slot has nothing to disagree with

        reference, majority = _modal_ratio(measured)
        for field_id, ratio in measured:
            if abs(ratio - reference) / reference <= ASSET_ASPECT_TOLERANCE:
                continue
            faults.append(
                f"`{field_id}` draws the {asset_class} class at {_ratio_text(ratio)}, but "
                f"`{majority}` and the other {asset_class} slots on this template are "
                f"{_ratio_text(reference)}. One file is drawn into all of them, so it would "
                f"be letterboxed in one shape or the other; the generator never pads."
            )
    return faults


def _modal_ratio(measured: list[tuple[str, float]]) -> tuple[float, str]:
    """The ratio the most slots declare, and a field id declaring it.

    Ties go to whichever group holds the first field id in sorted order, so the reference is
    the same on every host and under every authoring tool's element ordering.
    """
    tally: dict[float, list[str]] = {}
    for field_id, ratio in measured:
        tally.setdefault(round(ratio, 4), []).append(field_id)
    reference = min(tally, key=lambda key: (-len(tally[key]), tally[key][0]))
    return reference, tally[reference][0]


def class_aspect_of(root, template_key: str, asset_class: str) -> float | None:
    """The shape *asset_class* is drawn at on this template, or None where it draws none.

    The same reference :func:`class_aspect_faults_of` reports against, exposed for the one
    caller that wants the shape itself rather than a verdict on it: the portrait service,
    which wraps a Discord avatar to the shape the league's own lineup template declares.
    """
    slots = _class_slots(root, template_key).get(asset_class, [])
    measured = sorted(
        (field_id, ratio) for field_id, ratio, stretches in slots if not stretches
    )
    if not measured:
        return None
    return _modal_ratio(measured)[0]


#: Marks a Layer 2 failure as a *shape* fault rather than a missing field, so
#: :func:`_problem_kind_for` can tell them apart without a new field on `ValidityReport`.
#: The same trick Layer 1 already uses for "declares no canvas". Stripped before a league
#: ever sees the sentence -- they are shown `plain_reason` regardless, and the log wants the
#: precise text, not a marker.
ASPECT_FAULT_PREFIX = "aspect: "


#: The ids the track class is addressed by. Matched **by shape** rather than through the
#: catalogue, because a type that may not draw a map no longer declares these fields at
#: all — ``asset_class_for`` would return None and the trespass would pass unnoticed. A
#: league converting an old template is the case this catches: the slot is left behind,
#: the id still reads ``_image``, and nothing else would say so.
_MAP_FIELD_IDS = re.compile(r"^(track_image|round_\d+_image)$")


def map_bearing_faults_of(root, template_key: str) -> list[str]:
    """Every track-class field declared by a type that may not draw one (XIV.13)."""
    if template_key in MAP_BEARING_TEMPLATES:
        return []

    catalogue = catalogue_for(template_key)
    if catalogue.is_empty:
        return []

    faults: list[str] = []
    for node in root.iter(f"{{{_SVG_NS}}}image"):
        field_id = node.get("id") or ""
        is_map = catalogue.asset_class_for(field_id) == "track" or _MAP_FIELD_IDS.match(
            field_id
        )
        if is_map:
            faults.append(
                f"`{field_id}` declares a circuit map, which only the calendar and the "
                f"check-in graphic may draw. A round is a column heading here, and draws "
                f"its country flag."
            )
    return faults


def _ratio_text(value: float) -> str:
    """A ratio a template author can act on: ``3:2`` rather than ``1.5``."""
    for width, height in ((1, 1), (3, 2), (4, 3), (16, 9), (2, 1)):
        if abs(value - width / height) < 0.005:
            return f"{width}:{height}"
    return f"{value:.3f}:1"


class CatalogueLayer:
    """Layer 2 — the template carries every field its image type declares mandatory.

    ``applies_to`` returns **False** for an image type whose catalogue is empty, so the
    layer is *skipped* rather than passed. That distinction is what satisfies Constitution
    XIV.9's "no silent pass": ``evaluate_template`` records depth only for layers that
    actually ran, so a type with no generation specification still reports depth 1 and
    ``depth_summary`` keeps saying the catalogue check was not applied.

    Passing trivially instead would report depth 2 for a template nothing was checked
    against — precisely the claim XIV.9.4 forbids.
    """

    number = LAYER_CATALOGUE
    name = LAYER_NAMES[LAYER_CATALOGUE]

    def applies_to(self, template_key: str) -> bool:
        return not catalogue_for(template_key).is_empty

    def check(self, ctx: TemplateContext) -> LayerResult:
        catalogue = catalogue_for(ctx.template_key)
        path = ctx.resolve()

        try:
            root = ctx.tree(path)
        except SvgParseError as exc:  # Layer 1 already rejected this; belt and braces
            return LayerResult(False, f"not a valid SVG file — {exc}")

        # Where the capacity is derived from the template — the calendar, whose rounds a
        # league draws to suit its own season — the collection must be countable before
        # its mandatory ids can be enumerated at all. An uncountable one (no member, or a
        # gap in the numbering) is itself the failure, and is reported in its own terms.
        try:
            mandatory = catalogue.all_mandatory_ids(root)
        except CapacityError as exc:
            return LayerResult(False, str(exc))

        index = FieldIndex(root)

        # A **sibling's** field is the wrong file in this slot (XIV.3, v4.4.0). Where an
        # aspect is drawn by more than one image type — qualifying and race results — a
        # template carrying the other's row field would draw one session's columns under
        # another's headings. Checked before the missing-field report, because that is the
        # more useful thing to be told: a race template named as the qualifying one is
        # missing most of its fields *and* carrying foreign ones, and only the second says
        # what actually happened.
        foreign = sibling_fields_declared(ctx.template_key, index.declared())
        if foreign:
            shown = ", ".join(f"`{name}`" for name in foreign[:8])
            if len(foreign) > 8:
                shown += f", and {len(foreign) - 8} more"

            # Name the file the manager has actually supplied. The sibling relation now spans
            # a whole source module (XIV.3, v4.6.0), so a fixed phrase would tell an
            # attendance manager their sheet belongs to "the other kind of results template".
            from models.image_catalogues import sibling_owners
            from models.image_constants import TEMPLATE_LABELS

            owners = sibling_owners(ctx.template_key, foreign)
            if owners:
                named = " or ".join(
                    f"**{TEMPLATE_LABELS.get(key, key)}**" for key in owners
                )
                whose = f"to the {named} template"
            else:
                whose = "to another image type"

            return LayerResult(
                False,
                f"it declares {shown}, which "
                f"{'belongs' if len(foreign) == 1 else 'belong'} {whose}. "
                f"This looks like the wrong file for this slot.",
            )

        # A track-class field on a type that may not draw one (XIV.13, 044). Checked
        # before the aspect, because a slot that should not exist at all is a more useful
        # thing to be told than that its shape is wrong.
        trespass = map_bearing_faults_of(root, ctx.template_key)
        if trespass:
            return LayerResult(False, "; ".join(trespass))

        # A slot claiming to stretch whose class may not (XIV.6). Checked before the shapes
        # are compared, and not merely for the better message: a stretching slot is passed
        # over by the comparison, so a template whose portrait slots all stretch would agree
        # with itself and pass. The declaration has to be the fault in its own right.
        stretching = stretch_faults_of(root, ctx.template_key)
        if stretching:
            return LayerResult(False, ASPECT_FAULT_PREFIX + "; ".join(stretching))

        # Every slot of a class at the shape its siblings on this template use (XIV.6, 044,
        # relaxed 2026-09-01). Which shape is the league's to choose; that they agree is not.
        # One file is drawn into all of them and the generator never pads, so a slot out of
        # step letterboxes that file with no artwork able to correct it.
        faults = class_aspect_faults_of(root, ctx.template_key)
        if faults:
            return LayerResult(False, ASPECT_FAULT_PREFIX + "; ".join(faults))

        missing = sorted(name for name in mandatory if index.resolve(name) is None)
        if not missing:
            return LayerResult(True)

        # Name every one. A count tells a manager nothing about what to draw.
        shown = ", ".join(f"`{name}`" for name in missing[:8])
        if len(missing) > 8:
            shown += f", and {len(missing) - 8} more"
        return LayerResult(
            False,
            f"missing {len(missing)} mandatory "
            f"{'field' if len(missing) == 1 else 'fields'}: {shown}",
        )


class BoundsLayer:
    """Layer 3 — every bounded field the template declares can actually be laid out.

    A field is *bounded* where it declares a box: a ``shape-inside`` naming a rectangle, or a
    ``max-lines`` giving a budget in CSS. The defects that make one unlayable are read off the
    **template alone** — no division, no round, no classification (Constitution XIV.5, v7.0.0):

    * a ``max-lines`` that is not a positive whole number, which is no budget at all;
    * the ``shape-inside`` names a rectangle the template does not declare;
    * no ``line-height`` resolves upon a field that needs one, so the leading between its lines
      cannot be worked out — and no default may be substituted, a leading silently deciding how
      much of a league's prose is drawn;
    * no usable width to wrap against. Height joins it only where the field declares no
      ``max-lines``, since a declared budget stands in for the height that would otherwise fix
      it.

    Being structural, each is complete at every one of XIV.9's three moments and refuses at
    each with that moment's severity. The same defects are enforced again in the fill pipeline,
    which is where a template configured before this layer existed is still caught; this layer
    is what moves the telling forward to the moment the file is named.

    ``applies_to`` follows Layer 2: a type with no catalogue is *skipped*, so a template whose
    fields nothing claims is not reported as having been checked to a depth it was not.
    """

    number = LAYER_BOUNDS
    name = LAYER_NAMES[LAYER_BOUNDS]

    def applies_to(self, template_key: str) -> bool:
        return not catalogue_for(template_key).is_empty

    def check(self, ctx: TemplateContext) -> LayerResult:
        from utils.svg_document import computed_style, length, stylesheet
        from utils.svg_fill import (
            _MAX_LINES_INVALID,
            _descend,
            _font_size,
            _line_height_ratio,
            _max_lines,
            _shape_inside_id,
        )

        path = ctx.resolve()
        try:
            root = ctx.tree(path)
        except SvgParseError as exc:  # Layer 1 already rejected this; belt and braces
            return LayerResult(False, f"not a valid SVG file — {exc}")

        catalogue = catalogue_for(ctx.template_key)
        index = FieldIndex(root)
        rules = stylesheet(root)

        try:
            declared = catalogue.all_mandatory_ids(root) | set(catalogue.optional)
        except CapacityError:
            # Layer 2 reports an uncountable collection in its own terms; this layer has
            # nothing to add and must not report the same fault twice.
            return LayerResult(True)

        for field_id in sorted(declared):
            element = index.resolve(field_id)
            if element is None:
                continue

            # A field addressed by layer label resolves to the layer; the wrapped text is
            # inside it. The fill pipeline's own descent is reused so the two cannot
            # disagree about which element carries a field's style.
            target = _descend(element, "text")
            if target is None:
                continue

            style = computed_style(target, rules)

            budget = _max_lines(style)
            if budget == _MAX_LINES_INVALID:
                return LayerResult(
                    False,
                    f"field `{field_id}` declares a `max-lines` of "
                    f"`{style.get('max-lines', '').strip()}`, which is not a positive whole "
                    f"number of lines",
                )

            shape_id = _shape_inside_id(style)
            if shape_id is None:
                # A box declared in CSS needs a width to wrap against and a leading to space
                # its lines by; a single-line field needs neither and is bounded already.
                if budget is not None and budget > 1:
                    if length(style.get("inline-size")) is None:
                        return LayerResult(
                            False,
                            f"field `{field_id}` declares a `max-lines` of {budget} but no "
                            f"`inline-size` giving the width to wrap it against",
                        )
                    if _line_height_ratio(style, _font_size(style)) is None:
                        return LayerResult(
                            False,
                            f"field `{field_id}` declares a `max-lines` of {budget} but has "
                            f"no `line-height` resolving upon it, so the leading between its "
                            f"lines cannot be worked out",
                        )
                continue

            rect = index.resolve(shape_id)
            if rect is None:
                return LayerResult(
                    False,
                    f"wrapped field `{field_id}` names shape-inside `{shape_id}`, "
                    f"which the template does not declare",
                )

            if _line_height_ratio(style, _font_size(style)) is None:
                return LayerResult(
                    False,
                    f"wrapped field `{field_id}` has no `line-height` resolving upon it, "
                    f"so the leading between its lines cannot be worked out",
                )

            if length(rect.get("width")) is None:
                return LayerResult(
                    False,
                    f"wrapped field `{field_id}` names shape-inside `{shape_id}`, "
                    f"which declares no usable width to lay the text out in",
                )

            # Height fixes the budget only where the field declares no `max-lines` of its own.
            if budget is None and length(rect.get("height")) is None:
                return LayerResult(
                    False,
                    f"wrapped field `{field_id}` names shape-inside `{shape_id}`, "
                    f"which declares no usable height, and declares no `max-lines` to "
                    f"stand in for it",
                )

        return LayerResult(True)


#: The ordered registry. A later session adds a layer by appending one entry here.
LAYERS: list[ValidityLayer] = [ResolutionLayer(), CatalogueLayer(), BoundsLayer()]


def evaluate_template(ctx: TemplateContext, layers: list[ValidityLayer] | None = None) -> ValidityReport:
    """Run the applicable layers in order, stopping at the first failure.

    ``depth_checked`` records the highest layer actually applied, so a template checked
    only to Layer 1 is never presented as though it had passed a deeper check.
    """
    active = LAYERS if layers is None else layers

    try:
        resolved_path: Path | None = ctx.resolve()
    except (PathContainmentError, ValueError) as exc:
        return ValidityReport(
            template_key=ctx.template_key,
            resolved_path=None,
            valid=False,
            depth_checked=0,
            failed_layer=LAYER_RESOLUTION,
            reason=str(exc),
        )

    depth = 0
    for layer in sorted(active, key=lambda item: item.number):
        if not layer.applies_to(ctx.template_key):
            continue
        result = layer.check(ctx)
        depth = layer.number
        if not result.passed:
            return ValidityReport(
                template_key=ctx.template_key,
                resolved_path=resolved_path,
                valid=False,
                depth_checked=depth,
                failed_layer=layer.number,
                reason=result.reason,
            )

    return ValidityReport(
        template_key=ctx.template_key,
        resolved_path=resolved_path,
        valid=True,
        depth_checked=depth,
    )


# ── The ordered check sequence (FR-001 … FR-004) ──────────────────────────
#
# One function, serving the configuration command and the season gate alike. Two
# verification paths that could disagree about whether a template is usable is precisely
# what contracts/verification.md forbids.

#: Checked before any filesystem access — a name is cheap to reject (FR-001).
SVG_EXTENSION = ".svg"


def check_filename(filename: str) -> Problem | None:
    """FR-001 — the name must end `.svg`, case-insensitively.

    Case-insensitive because a manager types what their file manager shows them, and the
    host filesystem is what ultimately resolves the name. Whether the file is *really*
    SVG is settled by the parse, not by its name.
    """
    candidate = (filename or "").strip()
    if not candidate:
        return Problem(kind=PROBLEM_EXTENSION, detail="a filename is required.")
    if not candidate.lower().endswith(SVG_EXTENSION):
        return Problem(
            kind=PROBLEM_EXTENSION,
            detail=f"`{candidate}` does not end in `.svg`. Templates are SVG files.",
        )
    return None


def check_template(
    config: ImageConfig,
    template_key: str,
    *,
    root: Path | None = None,
    check_extension: bool = True,
) -> Problem | None:
    """Run FR-001 … FR-004 in order, cheapest first. None means usable.

    Order matters: no filesystem access until the name is plausible, no parse until the
    file is there, no field search until it parses.

    *check_extension* is False at season approval, where a stored filename was already
    validated when it was stored (FR-001 cannot fail there).
    """
    label = TEMPLATE_LABELS.get(template_key, template_key)
    filename = getattr(config, template_key, "")

    if check_extension:
        problem = check_filename(filename)
        if problem is not None:
            return dataclasses.replace(problem, template_key=template_key)

    ctx = TemplateContext(config=config, template_key=template_key, root=root)
    report = evaluate_template(ctx)
    if report.valid:
        return None

    # Deliberately the precise reason, not `plain_reason`. This is the answer to a
    # manager naming a file: they are looking at that one template, in the moment they
    # can fix it, and "which field" or "which path" is what they need. The season review
    # and `/images config view` survey fifteen at once and speak plainly instead.
    detail = report.reason or f"{label} is not usable."
    return Problem(
        kind=_problem_kind_for(report),
        # The kind has been read off the marker by now, so it has done its work and would
        # only be noise in front of the sentence a manager reads.
        detail=detail.removeprefix(ASPECT_FAULT_PREFIX),
        template_key=template_key,
    )


def _problem_kind_for(report: ValidityReport) -> str:
    """Map a failing layer onto a problem kind, keeping the classes distinguishable."""
    if (report.reason or "").startswith(ASPECT_FAULT_PREFIX):
        return PROBLEM_ASPECT_DISAGREEMENT
    if report.failed_layer == LAYER_CATALOGUE:
        return PROBLEM_MISSING_MANDATORY_FIELD
    reason = (report.reason or "").lower()
    if reason.startswith("not a valid svg file") or "root element" in reason:
        return PROBLEM_NOT_SVG
    if "declares no canvas" in reason:
        return PROBLEM_NOT_SVG
    return PROBLEM_NOT_FOUND


def check_all_templates(
    config: ImageConfig, *, root: Path | None = None
) -> list[Problem]:
    """FR-007 — every template, each with its own problem. Never a group, never a count.

    Serves both `/season review`, which reports these, and `/season approve`, which
    blocks on them, from one evaluation so the two surfaces cannot disagree (FR-008a).
    """
    reports = evaluate_all_templates(config, root=root)
    problems: list[Problem] = []
    for template_key, report in reports.items():
        if report.valid:
            continue
        problems.append(
            Problem(
                # `detail` is what a league reads, so it is the plain sentence and the
                # command that addresses it. `kind` is unchanged, and the engineering
                # text is in the log.
                kind=_problem_kind_for(report),
                detail=f"{plain_reason(report)}. {plain_remedy(report)}",
                template_key=template_key,
            )
        )
    return problems


def describe(problem: Problem) -> str:
    """One line naming the individual template and its own reason (FR-008)."""
    label = TEMPLATE_LABELS.get(problem.template_key or "", problem.template_key or "")
    return f"**{label}**: {problem.detail}" if label else problem.detail


#: What a league is told when a drawing cannot be used. Their words, not ours: no field
#: id, no layer number, no path, no jargon. The exact fault is written to the log
#: instead, so a manager reads a sentence they can act on and an operator still has the
#: detail to work from.
PLAIN_DIRECTORY_MISSING = "the folder your drawings live in can't be found"
PLAIN_UNBOUNDED_FIELD = (
    "one of its text boxes has no size set, so the bot can't fit text into it"
)
PLAIN_NOT_A_DRAWING = "this file isn't a drawing the bot can read"
PLAIN_MISSING_FIELD = "the drawing is missing something the bot has to fill in"
PLAIN_ASPECT_DISAGREEMENT = (
    "its picture boxes for one kind of picture aren't all the same shape as each other"
)
PLAIN_FILE_MISSING = "the drawing file can't be found where the bot was told to look"
PLAIN_FILE_OUTSIDE = "this file is outside the bot's own folder, so it can't be read"
PLAIN_UNUSABLE = "this drawing can't be used"


def plain_reason(report: ValidityReport) -> str:
    """The sentence a league manager reads when a template is not usable.

    Returns the sentence alone. Every caller already prefixes the template's label, and
    naming the individual template (FR-032) is therefore their job rather than this one's.

    The bounds layer is tested before :func:`_problem_kind_for`, which has no branch of
    its own for it and would otherwise report an unbounded text box as a missing file.
    """
    reason = (report.reason or "").lower()
    if "template directory" in reason:
        return PLAIN_DIRECTORY_MISSING
    if report.failed_layer == LAYER_BOUNDS:
        return PLAIN_UNBOUNDED_FIELD
    if "outside the project root" in reason:
        return PLAIN_FILE_OUTSIDE

    kind = _problem_kind_for(report)
    if kind == PROBLEM_NOT_SVG:
        return PLAIN_NOT_A_DRAWING
    if kind == PROBLEM_ASPECT_DISAGREEMENT:
        return PLAIN_ASPECT_DISAGREEMENT
    if kind == PROBLEM_MISSING_MANDATORY_FIELD:
        return PLAIN_MISSING_FIELD
    if kind == PROBLEM_NOT_FOUND:
        return PLAIN_FILE_MISSING
    return PLAIN_UNUSABLE


PLAIN_FOLDER_MISSING = "this folder can't be found"
PLAIN_NOT_A_FOLDER = "this is a file, not a folder"
PLAIN_FOLDER_OUTSIDE = "this folder is outside the bot's own folder, so it won't be read"
PLAIN_FOLDER_UNSET = "no folder has been set for this"


def plain_directory_reason(report: DirectoryReport) -> str:
    """The same courtesy for the seven asset folders (FR-029)."""
    reason = (report.reason or "").lower()
    if "not a directory" in reason:
        return PLAIN_NOT_A_FOLDER
    if "directory not found" in reason:
        return PLAIN_FOLDER_MISSING
    if "cannot be empty" in reason:
        return PLAIN_FOLDER_UNSET
    return PLAIN_FOLDER_OUTSIDE


# ── What to do about it ───────────────────────────────────────────────────
#
# A report that says what is wrong and stops there leaves a manager exactly where they
# started: they can see the fault and not the way out of it. Every fault this module
# reports therefore names the command that addresses it, or says plainly that no command
# of theirs will.

#: Said of the rasteriser, which no league command installs. Naming a command a manager
#: cannot run would be worse than naming none.
PLAIN_REMEDY_ASK_OPERATOR = (
    "Ask whoever runs the bot to install it — no command of yours can."
)


def plain_remedy(report: ValidityReport) -> str:
    """The command that addresses this template's fault, as a whole sentence.

    Where the fault is inside the drawing rather than in what was configured, the remedy
    is to name the same file again: `/images template …` answers with the precise reason,
    which is the one surface that still does.
    """
    reason = (report.reason or "").lower()
    if "template directory" in reason:
        return "Point the bot at the right folder with `/images config template-directory`."

    command = TEMPLATE_COMMAND_NAMES.get(report.template_key, report.template_key)
    naming = f"`/images template {command}`"

    if report.failed_layer == LAYER_BOUNDS:
        return f"Run {naming} on the same file and the reply names the text box at fault."
    if "outside the project root" in reason:
        return f"The file has to sit inside the bot's own folder. Name one there with {naming}."

    kind = _problem_kind_for(report)
    if kind == PROBLEM_NOT_SVG:
        return f"Save it again as a plain SVG, or name a different file with {naming}."
    if kind == PROBLEM_ASPECT_DISAGREEMENT:
        return f"Run {naming} on the same file and the reply names the box at fault."
    if kind == PROBLEM_MISSING_MANDATORY_FIELD:
        return f"Run {naming} on the same file and the reply names what is missing."
    if kind == PROBLEM_NOT_FOUND:
        return f"Put the file in that folder, or name a different one with {naming}."
    return f"Run {naming} on the same file and the reply says what is wrong."


def plain_directory_remedy(report: DirectoryReport) -> str:
    """The one command that sets this asset folder."""
    command = ASSET_DIRECTORIES[report.directory_key][0]
    return f"Point the bot at the right folder with `/images config {command}`."


def plain_template_line(report: ValidityReport) -> str:
    """The whole of what a league is told about one unusable template.

    Its own label, what is wrong with it, and what to do — in that order, on one line, so
    a manager scanning eight aspects reads a row rather than a paragraph.
    """
    label = TEMPLATE_LABELS.get(report.template_key, report.template_key)
    return f"{label}: {plain_reason(report)}. {plain_remedy(report)}"


def evaluate_all_templates(
    config: ImageConfig,
    *,
    root: Path | None = None,
    layers: list[ValidityLayer] | None = None,
) -> dict[str, ValidityReport]:
    """Evaluate all fifteen templates.

    When the template directory itself does not resolve, every template is reported
    invalid against that one shared reason rather than producing fifteen near-identical
    file-not-found lines. Each template still receives its own report, so the caller's
    rendering is unchanged.
    """
    directory_problem = _template_directory_problem(config, root)

    reports: dict[str, ValidityReport] = {}
    for template_key in TEMPLATE_COLUMNS:
        if directory_problem is not None:
            reports[template_key] = ValidityReport(
                template_key=template_key,
                resolved_path=None,
                valid=False,
                depth_checked=0,
                failed_layer=LAYER_RESOLUTION,
                reason=directory_problem,
            )
        else:
            reports[template_key] = evaluate_template(
                TemplateContext(config=config, template_key=template_key, root=root),
                layers=layers,
            )

    # The precise fault is written here and nowhere else. A league is shown
    # `plain_reason` — a sentence they can act on, with no field id, layer number or
    # path in it — so the engineering text has to survive somewhere, and this is where.
    # One shared directory fault is logged once, not fifteen times, for the same reason
    # the reports themselves share it.
    if directory_problem is not None:
        log.info("image validity: no template is usable: %s", directory_problem)
    else:
        for report in reports.values():
            if not report.valid:
                log.info(
                    "image validity: %s invalid at layer %s: %s",
                    report.template_key,
                    LAYER_NAMES.get(report.failed_layer or 0, report.failed_layer),
                    report.reason,
                )

    return reports


def _template_directory_problem(config: ImageConfig, root: Path | None) -> str | None:
    try:
        directory = resolve_within_project_root(config.template_directory, root=root)
    except (PathContainmentError, ValueError) as exc:
        # Named, so a reader of the log — and :func:`plain_reason` — can tell a bad
        # template directory from a bad individual template file.
        return f"template directory: {exc}"
    if not directory.exists():
        return f"template directory not found: {directory}"
    if not directory.is_dir():
        return f"template directory is not a directory: {directory}"
    return None


def evaluate_directories(
    config: ImageConfig, *, root: Path | None = None
) -> dict[str, DirectoryReport]:
    """Validity of the seven asset directories, on the same terms (FR-029)."""
    reports: dict[str, DirectoryReport] = {}
    for column in ASSET_DIRECTORIES:
        value = getattr(config, column)
        try:
            resolved = resolve_within_project_root(value, root=root)
        except (PathContainmentError, ValueError) as exc:
            reports[column] = DirectoryReport(column, None, False, str(exc))
            continue
        if not resolved.exists():
            reports[column] = DirectoryReport(
                column, resolved, False, f"directory not found: {resolved}"
            )
        elif not resolved.is_dir():
            reports[column] = DirectoryReport(
                column, resolved, False, f"not a directory: {resolved}"
            )
        else:
            reports[column] = DirectoryReport(column, resolved, True)

    for report in reports.values():
        if not report.valid:
            log.info(
                "image validity: asset folder %s unusable: %s",
                report.directory_key,
                report.reason,
            )

    return reports


def build_aspect_statuses(
    toggles: dict[str, bool],
    template_reports: dict[str, ValidityReport],
    *,
    disabled_source_modules: set[str] | None = None,
    converter_available: bool = True,
) -> list[AspectStatus]:
    """Roll the per-template reports up into the three states of FR-031.

    An aspect is ENABLED_INVALID when its toggle is on **and** any of: a backing template
    is invalid, its source module is disabled, or the rasteriser is absent. Every reason
    names the individual template or the specific module — never the group.

    A disabled aspect explains itself too. A red cross and a name told a manager nothing:
    they could see neither why the aspect was off nor what it would run into were they to
    switch it on, though both were already known here. The two are computed from one
    helper, so what the disabled row promises is exactly what the enabled row would say.
    """
    disabled_modules = disabled_source_modules or set()
    statuses: list[AspectStatus] = []

    for aspect in ASPECTS:
        keys = ASPECT_TEMPLATES[aspect]
        reports = [template_reports[key] for key in keys if key in template_reports]
        enabled = toggles.get(aspect, False)
        problems = _aspect_problems(
            aspect, reports, disabled_modules, converter_available
        )

        if not enabled:
            # The lead-in is said once rather than prefixed onto each problem: weather
            # alone can carry six, and six repetitions of the same five words is what a
            # manager stops reading.
            reasons = [plain_aspect_off(aspect)]
            if problems:
                reasons.append(PLAIN_WOULD_NEED_FIXING)
                reasons += problems
            statuses.append(
                AspectStatus(aspect, STATE_DISABLED, reports, disabled_reasons=reasons)
            )
            continue

        statuses.append(
            AspectStatus(
                aspect,
                STATE_ENABLED_INVALID if problems else STATE_ENABLED,
                reports,
                problems,
            )
        )

    return statuses


def templates_of_enabled_aspects(toggles: dict[str, bool]) -> set[str]:
    """The templates that can actually be drawn, given which aspects are switched on.

    An aspect that is off posts as text and reaches no template at all, so a template
    backing only off aspects cannot stop anything happening. Read from
    ``ASPECT_TEMPLATES``, so an aspect added later is covered without editing this.
    """
    wanted: set[str] = set()
    for aspect in ASPECTS:
        if toggles.get(aspect, False):
            wanted.update(ASPECT_TEMPLATES[aspect])
    return wanted


def blocking_template_problems(
    config: ImageConfig,
    toggles: dict[str, bool],
    *,
    root: Path | None = None,
) -> list[Problem]:
    """The template faults that must stop a season, given what is switched on.

    One rule, applied by `/season review` and `/season approve` alike so the two cannot
    disagree: **a broken template blocks only where the aspect drawing it is on.** A
    league that never switched verdicts on is not stopped by a verdicts template it has
    no use for, and used to be — `check_all_templates` surveys all fifteen regardless,
    which is right for the question "does this folder hold every drawing?" and wrong for
    "may this season run?".

    A fault under a *disabled* aspect is not silently dropped. It is reported as a
    warning by the surfaces that report, because a manager switching that aspect on
    later wants to have known; it simply withholds no approval in the meantime.
    """
    wanted = templates_of_enabled_aspects(toggles)
    return [
        problem
        for problem in check_all_templates(config, root=root)
        if problem.template_key in wanted
    ]


#: What "off" means to a league: the posting still happens, in text rather than as a
#: picture. The same words `/images config toggle` uses when an aspect is switched off.
PLAIN_ASPECT_OFF = "switched off — this is posted as text, not as a picture"

#: Said once above the problems that await, so a manager who is about to switch an aspect
#: on learns of them here rather than after the season is approved.
PLAIN_WOULD_NEED_FIXING = "switch it on and these would need fixing first:"

#: The rasteriser by what it does, not by its name. A manager reading this has no way to
#: act on "Inkscape" or "SVG-to-PNG converter"; the operator line elsewhere names it.
PLAIN_NO_RASTERISER = (
    "the program the bot uses to turn drawings into pictures isn't installed"
)


def plain_aspect_off(aspect: str) -> str:
    """Why the cross, and the one command that removes it.

    The aspect is named by the label the report already prints, which is also the name of
    its choice in the toggle's dropdown, so a manager can copy what they read.
    """
    return (
        f"{PLAIN_ASPECT_OFF}. "
        f"Switch it on with `/images config toggle aspect:{ASPECT_LABELS[aspect]}`"
    )


def _plain_module_off(module: str) -> str:
    return (
        f"the {module} module is switched off, so there'd be nothing to draw. "
        f"Switch it on with `/module enable module_name:{module}`"
    )


def _aspect_problems(
    aspect: str,
    reports: list[ValidityReport],
    disabled_modules: set[str],
    converter_available: bool,
) -> list[str]:
    """Everything standing between this aspect and a picture, in a league's own words.

    Read by both branches of :func:`build_aspect_statuses`, so a disabled aspect can
    never name a different set from the one it would actually meet on being enabled.
    Each problem carries its own remedy: they are addressed by different commands, and a
    manager reading six lines needs to know which of them applies to which.
    """
    problems = [
        plain_template_line(report) for report in reports if not report.valid
    ]

    source_module = ASPECT_SOURCE_MODULE[aspect]
    if source_module and source_module in disabled_modules:
        problems.append(_plain_module_off(source_module))

    if not converter_available:
        problems.append(f"{PLAIN_NO_RASTERISER}. {PLAIN_REMEDY_ASK_OPERATOR}")

    return problems


class ImageValidityService:
    """Async facade the cogs use. The evaluation itself is pure and synchronous."""

    def __init__(self, config_service, module_service) -> None:
        self._config_service = config_service
        self._module_service = module_service

    async def template_reports(self, server_id: int) -> dict[str, ValidityReport]:
        config = await self._config_service.get_config(server_id)
        if config is None:
            return {}
        return evaluate_all_templates(config)

    async def directory_reports(self, server_id: int) -> dict[str, DirectoryReport]:
        config = await self._config_service.get_config(server_id)
        if config is None:
            return {}
        return evaluate_directories(config)

    async def disabled_source_modules(self, server_id: int) -> set[str]:
        disabled: set[str] = set()
        if not await self._module_service.is_results_enabled(server_id):
            disabled.add("results")
        if not await self._module_service.is_attendance_enabled(server_id):
            disabled.add("attendance")
        if not await self._module_service.is_weather_enabled(server_id):
            disabled.add("weather")
        return disabled

    async def aspect_statuses(self, server_id: int) -> list[AspectStatus]:
        from services.image_render_service import converter_available

        reports = await self.template_reports(server_id)
        toggles = await self._config_service.get_toggles(server_id)
        return build_aspect_statuses(
            toggles,
            reports,
            disabled_source_modules=await self.disabled_source_modules(server_id),
            converter_available=converter_available(),
        )

    @staticmethod
    def depth_summary(reports: dict[str, ValidityReport]) -> str:
        """State the depth templates were actually checked to (XIV.9, invariants 3 & 4).

        Read from the reports, not from the layer registry. A layer that is registered but
        skipped — Layer 2 against an image type whose catalogue is empty — has checked
        nothing, and saying otherwise would present a template as verified more deeply
        than it was. The registry is the *available* depth; the reports are the *applied*
        one, and only the latter may be claimed.
        """
        if not reports:
            return "No templates were checked."

        applied = max((report.depth_checked for report in reports.values()), default=0)
        shallowest = min((report.depth_checked for report in reports.values()), default=0)

        def names_through(depth: int) -> str:
            return ", ".join(
                LAYER_NAMES[number] for number in sorted(LAYER_NAMES) if number <= depth
            )

        if applied == shallowest:
            text = f"Checked to layer {applied} ({names_through(applied)})."
        else:
            # Mixed: some types have catalogues and were checked deeper than others.
            text = (
                f"Checked to layer {shallowest} ({names_through(shallowest)}) for every "
                f"template, and to layer {applied} where a field catalogue exists."
            )

        pending = [
            f"{number} {LAYER_NAMES[number]}"
            for number in sorted(LAYER_NAMES)
            if number > applied
        ]
        if pending:
            text += f" Not yet checked: {', '.join(pending)}."
        return text
