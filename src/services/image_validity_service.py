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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from models.image_catalogues import (
    CapacityError,
    catalogue_for,
    sibling_fields_declared,
)
from models.image_constants import (
    ASPECT_SOURCE_MODULE,
    ASPECT_TEMPLATES,
    ASPECTS,
    ASSET_DIRECTORIES,
    TEMPLATE_COLUMNS,
    TEMPLATE_LABELS,
)
from models.image_module import (
    PROBLEM_EXTENSION,
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
    """Layer 3 — every wrapped field the template declares can actually be laid out.

    A field is *wrapped* where it carries ``shape-inside`` naming a rectangle. Three defects
    make one unlayable, and all three are read off the **template alone** — no division, no
    round, no classification (Constitution XIV.5, v4.8.0):

    * the ``shape-inside`` names a rectangle the template does not declare;
    * no ``line-height`` resolves upon the field, so the lines its box admits cannot be
      counted — and no default may be substituted, a leading silently deciding how much of a
      league's prose is drawn;
    * the rectangle declares no usable width and height, so there is no room to lay out in.

    Being structural, each is complete at every one of XIV.9's three moments and refuses at
    each with that moment's severity. The same three are enforced again in the fill pipeline,
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
            _descend,
            _font_size,
            _line_height_ratio,
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
            shape_id = _shape_inside_id(style)
            if shape_id is None:
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
                    f"so the number of lines its box admits cannot be worked out",
                )

            if length(rect.get("width")) is None or length(rect.get("height")) is None:
                return LayerResult(
                    False,
                    f"wrapped field `{field_id}` names shape-inside `{shape_id}`, "
                    f"which declares no usable width and height to lay the text out in",
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

    return Problem(
        kind=_problem_kind_for(report),
        detail=report.reason or f"{label} is not usable.",
        template_key=template_key,
    )


def _problem_kind_for(report: ValidityReport) -> str:
    """Map a failing layer onto a problem kind, keeping the classes distinguishable."""
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
                kind=_problem_kind_for(report),
                detail=report.reason or "not usable.",
                template_key=template_key,
            )
        )
    return problems


def describe(problem: Problem) -> str:
    """One line naming the individual template and its own reason (FR-008)."""
    label = TEMPLATE_LABELS.get(problem.template_key or "", problem.template_key or "")
    return f"**{label}**: {problem.detail}" if label else problem.detail


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
    return reports


def _template_directory_problem(config: ImageConfig, root: Path | None) -> str | None:
    try:
        directory = resolve_within_project_root(config.template_directory, root=root)
    except (PathContainmentError, ValueError) as exc:
        return str(exc)
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
    is invalid, its source module is disabled, or the rasteriser is absent. Every blocking
    reason names the individual template or the specific module — never the group.
    """
    disabled_modules = disabled_source_modules or set()
    statuses: list[AspectStatus] = []

    for aspect in ASPECTS:
        keys = ASPECT_TEMPLATES[aspect]
        reports = [template_reports[key] for key in keys if key in template_reports]
        enabled = toggles.get(aspect, False)

        if not enabled:
            statuses.append(AspectStatus(aspect, STATE_DISABLED, reports))
            continue

        blocking: list[str] = []

        for report in reports:
            if not report.valid:
                blocking.append(
                    f"{TEMPLATE_LABELS[report.template_key]}: {report.reason}"
                )

        source_module = ASPECT_SOURCE_MODULE[aspect]
        if source_module and source_module in disabled_modules:
            blocking.append(
                f"the {source_module} module is disabled, so this aspect produces nothing"
            )

        if not converter_available:
            blocking.append("the SVG-to-PNG converter is not installed on this host")

        statuses.append(
            AspectStatus(
                aspect,
                STATE_ENABLED_INVALID if blocking else STATE_ENABLED,
                reports,
                blocking,
            )
        )

    return statuses


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
