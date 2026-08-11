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

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from models.image_constants import (
    ASPECT_SOURCE_MODULE,
    ASPECT_TEMPLATES,
    ASPECTS,
    ASSET_DIRECTORIES,
    TEMPLATE_COLUMNS,
    TEMPLATE_LABELS,
)
from models.image_module import (
    STATE_DISABLED,
    STATE_ENABLED,
    STATE_ENABLED_INVALID,
    AspectStatus,
    DirectoryReport,
    ImageConfig,
    ValidityReport,
)
from utils.paths import PathContainmentError, resolve_within_project_root
from utils.svg_document import SvgNoCanvasError, SvgParseError, canvas_of, load_svg

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

    @property
    def filename(self) -> str:
        return getattr(self.config, self.template_key)

    def resolve(self) -> Path:
        directory = resolve_within_project_root(
            self.config.template_directory, root=self.root
        )
        return directory / self.filename


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
            root = load_svg(path)
        except SvgParseError as exc:
            return LayerResult(False, f"not well-formed SVG: {exc}")

        try:
            canvas_of(root)
        except SvgNoCanvasError as exc:
            return LayerResult(False, f"declares no canvas: {exc}")

        return LayerResult(True)


#: The ordered registry. A later session adds a layer by appending one entry here.
LAYERS: list[ValidityLayer] = [ResolutionLayer()]


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
        """State the depth templates were checked to (invariant 3, FR-028b)."""
        implemented = max((layer.number for layer in LAYERS), default=0)
        names = ", ".join(
            LAYER_NAMES[layer.number] for layer in sorted(LAYERS, key=lambda i: i.number)
        )
        reserved = [n for n in sorted(LAYER_NAMES) if n > implemented]
        text = f"Checked to layer {implemented} ({names})."
        if reserved:
            pending = ", ".join(f"{n} {LAYER_NAMES[n]}" for n in reserved)
            text += f" Not yet checked: {pending}."
        return text
