"""ImageRenderService — SVG-to-PNG rasterisation and render outcome assembly.

Constitution XIV.4 splits render outcomes in two, and ``RenderOutcome`` carries that split
explicitly: ``png_paths`` is empty whenever ``problem`` is set, so no caller can receive a
partial image or mistake a degraded render for a clean one.

The rasteriser is an external binary (Inkscape) that no package declaration installs. Its
absence is fatal to the module (FR-008) and is reported rather than raised.

The render writes nothing to Discord and holds no log writer: its notices go back on the outcome,
for a job to hand back as a line, or for the `/images test` preview to write through the router's
`post_log`, which it is handed (`preview_runner`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from dataclasses import dataclass, field as dataclass_field

from leaguebot.framework.utils.interaction_errors import describe_fault
from leaguebot.image.models.image_constants import NOTICE_TIER_COLOURS_NOT_APPLIED
from leaguebot.image.models.image_module import (
    PROBLEM_CONVERTER_ABSENT,
    PROBLEM_DRAWING_FAULT,
    PROBLEM_NOT_SVG,
    PROBLEM_RASTERISER,
    PROBLEM_UNKNOWN_IMAGE_TYPE,
    PROBLEM_UNRESOLVED_VALUE,
    DrawingRefused,
    PostingOrigin,
    Problem,
    RenderNotice,
    RenderOutcome,
)
from leaguebot.image.utils.svg_document import off_the_loop

#: What a caller should do with the result of ``render_for_posting``.
POST_IMAGE = "POST_IMAGE"
POST_TEXT_FALLBACK = "POST_TEXT_FALLBACK"
REJECT_COMMAND = "REJECT_COMMAND"


@dataclass
class PostingDecision:
    """The render's outcome, resolved against who asked for the posting (XIV.7).

    A caller reads ``action`` and does exactly one thing. It never has to re-derive the
    commanded/uncommanded rule for itself, which is what keeps the two behaviours from
    drifting apart across the call sites the image types will add.
    """

    action: str
    png_paths: list[Path] = dataclass_field(default_factory=list)
    problem: Problem | None = None
    notices: list[RenderNotice] = dataclass_field(default_factory=list)

    @property
    def posts_image(self) -> bool:
        return self.action == POST_IMAGE

    @property
    def falls_back_to_text(self) -> bool:
        return self.action == POST_TEXT_FALLBACK

    @property
    def rejects(self) -> bool:
        return self.action == REJECT_COMMAND

    def caller_message(self, label: str | None = None) -> str:
        """What to tell the person who ran the command (FR-030)."""
        if self.problem is None:
            return ""
        return f"❌ {self.problem.message(label)}"

log = logging.getLogger(__name__)

#: Environment variable naming the executable explicitly.
INKSCAPE_ENV_VAR = "INKSCAPE"

#: Human name used in every message about the missing binary.
CONVERTER_NAME = "Inkscape"

#: Conventional install locations, probed in order. PATH alone is not enough: a host can
#: carry the binary with a broken PATH entry, and reporting a fatal absence that is not
#: real would disable the whole module for no reason.
_WINDOWS_CANDIDATES = (
    r"C:\Program Files\Inkscape\bin\inkscape.exe",
    r"C:\Program Files\Inkscape\inkscape.exe",
    r"C:\Program Files (x86)\Inkscape\bin\inkscape.exe",
    r"C:\Program Files (x86)\Inkscape\inkscape.exe",
)

_POSIX_CANDIDATES = (
    "/usr/bin/inkscape",
    "/usr/local/bin/inkscape",
    "/snap/bin/inkscape",
    "/opt/homebrew/bin/inkscape",
    "/Applications/Inkscape.app/Contents/MacOS/inkscape",
)

#: Probed at enable, at every config view and at every season review — too often to pay a
#: filesystem walk each time, but short enough that installing the binary is noticed
#: without a bot restart.
_CACHE_TTL_SECONDS = 60.0

_cache: tuple[float, str | None] | None = None


# ── The league's own asset directories ────────────────────────────────────

#: The one (asset class, column) pair every image type resolves, whatever else it draws.
#: See :func:`resolve_configured_directories`.
DIVISION_LOGO_PAIR: tuple[str, str] = ("division_logo", "division_logo_directory")


def resolve_configured_directories(
    config,
    pairs: tuple[tuple[str, str], ...],
    *,
    image_type: str = "unknown",
) -> tuple[dict[str, Path], dict[str, str]]:
    """Resolve the directories *config* names for *pairs*, keeping the rejections.

    *pairs* is ``((asset_class, config_column), …)`` — each image type names the classes
    it draws and no others.

    Returns the resolved directories and, separately, the classes whose configured value
    could not be resolved, mapped to the reason. Every posting path resolved these inside
    a bare ``except`` that discarded the reason, after which the renderer reported the
    class as *not configured* — which is not what happened, and left a league with a
    fault it could not account for. The reason is now logged and carried through.

    A configured directory is only rejected for containment or emptiness; a directory that
    simply does not exist resolves and is returned, its assets falling back as XIV.13
    requires.

    **The division-logo directory is added to whatever *pairs* names** (2026-09-02). Every
    image type may draw one, because every catalogue admits the field and it is a league's own
    template that decides where -- so unlike the classes a caller lists, there is no graphic
    this one does not apply to. Adding it here rather than to eight tuples means a posting
    path added later cannot omit it and leave one aspect quietly unable to draw a logo, which
    is a failure this class in particular could not report: an unresolvable class is reported,
    but a league would have to notice the absence themselves.
    """
    from leaguebot.framework.utils.paths import resolve_within_project_root

    directories: dict[str, Path] = {}
    faults: dict[str, str] = {}

    if config is None:
        return directories, faults

    # Only where the configuration actually carries the column. A config object that does not
    # have it is not a league with an unset directory -- it is a caller who assembled a
    # partial config for some other purpose -- and manufacturing "Directory cannot be empty"
    # for a class they never asked about would report a fault that is not one. An attribute
    # that is present but unusable still faults, like every other class.
    _class, _column = DIVISION_LOGO_PAIR
    if DIVISION_LOGO_PAIR not in pairs and hasattr(config, _column):
        pairs = pairs + (DIVISION_LOGO_PAIR,)

    for asset_class, column in pairs:
        configured = getattr(config, column, None)
        try:
            directories[asset_class] = resolve_within_project_root(configured)
        except Exception as exc:  # noqa: BLE001 — the reason is the point
            reason = str(exc) or exc.__class__.__name__
            faults[asset_class] = reason
            log.warning(
                "%s: the configured %s directory %r was rejected — %s",
                image_type,
                asset_class,
                configured,
                reason, exc_info=True,
            )

    return directories, faults


def spec_builder_with_faults(build_fill_spec, drawing, directories, faults):
    """A spec builder that carries the directory rejections onto the ``FillSpec``.

    The renderer takes a callback rather than a spec, so this is where the two halves
    meet: the posting path knows which directories were rejected, and the filler is what
    reports an asset class it cannot resolve.
    """

    def _build(root):
        spec = build_fill_spec(drawing, root, asset_directories=directories)
        spec.asset_directory_faults = faults
        return spec

    return _build


def find_converter(*, use_cache: bool = True) -> str | None:
    """Return the rasteriser's path, or None when it cannot be found.

    Order: the ``INKSCAPE`` environment variable, then PATH, then the conventional
    install locations for the platform.
    """
    global _cache

    if use_cache and _cache is not None:
        cached_at, cached_value = _cache
        if time.monotonic() - cached_at < _CACHE_TTL_SECONDS:
            return cached_value

    found = _probe()
    _cache = (time.monotonic(), found)
    return found


def _probe() -> str | None:
    override = os.environ.get(INKSCAPE_ENV_VAR)
    if override:
        override_path = Path(override)
        if override_path.is_file():
            return str(override_path)
        log.warning(
            "%s is set to %r but no file is there; falling back to the usual locations.",
            INKSCAPE_ENV_VAR,
            override,
        )

    on_path = shutil.which("inkscape")
    if on_path:
        return on_path

    candidates = _WINDOWS_CANDIDATES if os.name == "nt" else _POSIX_CANDIDATES
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate

    return None


def converter_available(*, use_cache: bool = True) -> bool:
    return find_converter(use_cache=use_cache) is not None


def converter_absent_message() -> str:
    """The fatal, module-wide notice for a missing rasteriser (FR-008)."""
    return (
        f"⛔ **{CONVERTER_NAME} is not installed on the machine running the bot.**\n"
        f"Image generation is disabled entirely until it is. No package dependency "
        f"installs it — it is a separate program the host must carry.\n"
        f"If it is installed somewhere unusual, set the `{INKSCAPE_ENV_VAR}` environment "
        f"variable to the executable's full path."
    )


def reset_converter_cache() -> None:
    """Drop the memoised probe. Used by tests and after an operator installs the binary."""
    global _cache
    _cache = None


# ── The life of a rendered file ───────────────────────────────────────────

#: Names the per-render directory, and is the whole of how a render is recognised as the
#: bot's own afterwards. Every render gets its own directory under it so that the PNG can
#: keep the plain ``{image_type}.png`` name a league sees on an attachment.
_RENDER_DIR_PREFIX = "f1bot_render_"


def _is_render_artifact(path: Path) -> bool:
    """Is *path* a PNG this module rendered?

    The guard the discards are built on, and the reason they are safe to call on anything.
    A league's own artwork, a signup CSV, a template — none of them sit inside a directory
    named for a render, so none of them can be deleted by a mistaken call.
    """
    try:
        return path.parent.name.startswith(_RENDER_DIR_PREFIX)
    except (AttributeError, OSError):  # pragma: no cover - a path that cannot be read
        return False


def discard_render(*paths: Path | None) -> None:
    """Delete rendered PNGs and the directories holding them.

    Called once a posting attempt is over, **whatever its outcome**. A rendered file is
    read only between ``rasterise`` returning and the send completing; after that nothing
    will ever look at it again, because a transport failure re-posts the textual body and
    never the picture (XIV.8).

    Tolerant throughout. Tidying up is not the posting, and a failure to tidy up must
    never reach a league or abort a caller mid-flow.
    """
    for path in paths:
        if path is None or not _is_render_artifact(path):
            continue
        try:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except OSError as exc:
            # A directory holding something else, or a file another process still has
            # open. Neither is worth a word to anyone but a maintainer reading the log.
            log.debug("discard_render: could not remove %s: %s", path, exc)


def discard_attachment(*files) -> None:
    """Close each ``discord.File`` and delete the render behind it.

    Three posting helpers hand a ``discord.File`` across a module boundary and keep no
    path — the check-in call, the forecast and the attendance sheet. Recovering the path
    from the file object lets them keep their contracts rather than each growing a second
    return value for the sake of cleanup.

    A file wrapping a buffer rather than a path — the unassigned-drivers CSV is one — has
    no name to recover and is closed and left alone.
    """
    for file in files:
        if file is None:
            continue
        try:
            file.close()
        except Exception as exc:  # noqa: BLE001 - closing must not break a posting
            log.debug("discard_attachment: could not close a file: %s", exc, exc_info=True)
        name = getattr(getattr(file, "fp", None), "name", None)
        if isinstance(name, (str, os.PathLike)):
            discard_render(Path(name))


# ── Rasterisation ─────────────────────────────────────────────────────────

#: Discord's attachment ceiling for a non-boosted server. The canvas is declared by the
#: template (XIV.1) and so is not something this module controls, which is why exceeding
#: it is treated as a problem rather than left to fail at upload time.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

#: A render that has not finished by now is not going to.
_RASTERISE_TIMEOUT_SECONDS = 120


class RasterisationError(Exception):
    """The converter failed. A problem, never a notice: a fault in the bot, named by its kind and
    raised where the caller asks for it, unless it is a `ConverterAbsent` or a
    `RasterisationRefused`, which keep their words."""


class ConverterAbsent(RasterisationError):
    """The converter is not installed, found so by the conversion itself. It keeps its words and
    is the host's to put right."""


class RasterisationRefused(RasterisationError):
    """The converter wrote an image larger than Discord accepts. It keeps its words, and is the
    league's to put right by shrinking a canvas or artwork."""


def rasterise(svg: bytes, destination: Path, canvas: tuple[int, int]) -> Path:
    """Convert *svg* to a PNG at *destination*. Blocking — call it off the event loop.

    The width and height are passed explicitly so the export matches the canvas the
    template declared, rather than whatever the converter infers.
    """
    executable = find_converter()
    if executable is None:
        raise ConverterAbsent(f"{CONVERTER_NAME} is not installed on this host.")

    width, height = canvas
    source = destination.with_suffix(".svg")

    command = [
        executable,
        str(source),
        "--export-type=png",
        f"--export-filename={destination}",
        f"--export-width={width}",
        f"--export-height={height}",
    ]

    try:
        # Inside the try, so that a write failing part way through — a full disk, a tmpfs
        # with no room left — still reaches the unlink below rather than leaving half an
        # SVG behind.
        source.write_bytes(svg)

        completed = subprocess.run(  # noqa: S603 - executable is discovered, not user input
            command,
            capture_output=True,
            timeout=_RASTERISE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RasterisationError(
            f"{CONVERTER_NAME} did not finish within {_RASTERISE_TIMEOUT_SECONDS}s."
        ) from exc
    except OSError as exc:
        raise RasterisationError(f"{CONVERTER_NAME} could not be run: {exc}") from exc
    finally:
        source.unlink(missing_ok=True)

    # Each failure below is raised *after* a file may already exist, so each one clears up
    # after itself. The oversize case is the largest single file the bot can produce, and
    # is refused rather than uploaded — leaving it on the host would strand 25 MB, and
    # strand it again on every render that repeats the fault.
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        discard_render(destination)
        raise RasterisationError(
            f"{CONVERTER_NAME} exited {completed.returncode}"
            + (f": {detail.splitlines()[-1]}" if detail else "")
        )

    if not destination.exists():
        raise RasterisationError(f"{CONVERTER_NAME} produced no output file.")

    size = destination.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        discard_render(destination)
        raise RasterisationRefused(
            f"the rendered image is {size / 1024 / 1024:.1f} MB, above Discord's "
            f"{MAX_ATTACHMENT_BYTES // 1024 // 1024} MB attachment limit."
        )

    return destination


def _kind_for_layer(failed_layer: int | None) -> str:
    """The problem kind matching the validity layer that rejected a template.

    Keeps a render's problem distinguishable in the same terms the configuration command
    and the season gate use, rather than flattening every invalid template to one kind.
    """
    from leaguebot.image.models.image_module import (
        LAYER_CATALOGUE,
        PROBLEM_MISSING_MANDATORY_FIELD,
        PROBLEM_NOT_FOUND,
    )

    if failed_layer == LAYER_CATALOGUE:
        return PROBLEM_MISSING_MANDATORY_FIELD
    return PROBLEM_NOT_FOUND


def _removed_field_ids(index, removed) -> set[str]:
    """*removed*, plus every field name standing inside what it removes.

    A removable group is named for the field it wraps and takes its entire subtree off the
    graphic when the rules empty that field (Constitution XIV.2). Only the group's own id
    reaches ``spec.remove``; its children keep ids of their own and would otherwise still
    be demanded by the mandatory sweep — abandoning the render over a field that is no
    longer drawn, which XIV.2 says explicitly "is not a failure".

    Names are gathered the way :class:`FieldIndex` addresses them: every ``@id``, and an
    ``inkscape:label`` only where the node is a layer. Sweeping in the labels Inkscape
    writes on ordinary shapes would let an unrelated field escape the sweep.
    """
    from leaguebot.image.utils.svg_document import INKSCAPE_NS

    label_attr = f"{{{INKSCAPE_NS}}}label"
    groupmode_attr = f"{{{INKSCAPE_NS}}}groupmode"

    gone: set[str] = set(removed)
    for name in removed:
        node = index.resolve(name)
        if node is None:
            continue
        for element in node.iter():
            element_id = element.get("id")
            if element_id:
                gone.add(element_id)
            if element.get(groupmode_attr) == "layer":
                label = element.get(label_attr)
                if label:
                    gone.add(label)
    return gone


def _verify_against_data(root, spec, image_type: str) -> Problem | None:
    """Check the template against the values it is about to receive (FR-010 … FR-014).

    Three questions, in the order that makes the cheapest failure the first:

    1. Does the data have more rows than the template has slots? (FR-028)
    2. Is a mandatory field absent from the template? (FR-012)
    3. Is a mandatory field's value undeterminable? (FR-011)

    All three pass vacuously while the image type's catalogue is empty, which is every
    type in this increment. Populating one catalogue switches all three on for that type.
    """
    from leaguebot.image.models.image_catalogues import CapacityError, catalogue_for
    from leaguebot.image.models.image_module import (
        PROBLEM_CAPACITY_EXCEEDED,
        PROBLEM_MISSING_MANDATORY_FIELD,
    )
    from leaguebot.image.utils.svg_document import FieldIndex

    catalogue = catalogue_for(image_type)
    if catalogue.is_empty:
        return None

    # 0. Count the template's members. Where the capacity is derived (the calendar), an
    #    uncountable collection — none declared, or a gap — is itself the problem, and
    #    must be reported before anything is compared against it.
    try:
        capacity = catalogue.capacity(root)
    except CapacityError as exc:
        return Problem(
            kind=PROBLEM_MISSING_MANDATORY_FIELD,
            detail=str(exc),
            template_key=image_type,
        )

    # 1. Capacity. A graphic that drops a driver without saying so is worse than none.
    row_count = getattr(spec, "row_count", None)
    if capacity is not None and row_count is not None and row_count > capacity:
        return Problem(
            kind=PROBLEM_CAPACITY_EXCEEDED,
            detail=(
                f"there are {row_count} rows of data but the template provides "
                f"{capacity} slots. Enlarge the template, or the extra rows would be "
                f"silently dropped."
            ),
            template_key=image_type,
        )

    # A both-directions divergence check stood here until v6.0.0, for a capacity fixed by
    # the data. No collection is fixed that way any more: a template declaring more than
    # the data hold is correct and its surplus is removed, and a template declaring fewer
    # is caught by the capacity guard above.
    mandatory = catalogue.all_mandatory_ids(root)
    if not mandatory:
        return None

    # 2. Present in the template.
    index = FieldIndex(root)
    absent = sorted(name for name in mandatory if index.resolve(name) is None)
    if absent:
        return Problem(
            kind=PROBLEM_MISSING_MANDATORY_FIELD,
            detail=(
                f"the template declares no "
                + ", ".join(f"`{name}`" for name in absent[:8])
                + (f" and {len(absent) - 8} more" if len(absent) > 8 else "")
            ),
            template_key=image_type,
            field_id=absent[0],
        )

    # 3. Supplied by the data. A mandatory field the caller put in `empty` is a value it
    #    could not determine, which is exactly what FR-011 makes fatal.
    #
    #    Valueless fields are excluded: a calendar's crop point must be *present* but is
    #    geometry the crop reads, never text the render writes, so "its value could not
    #    be determined" cannot apply to it. Check 2 above still requires it to exist.
    supplied = set(spec.text) | set(spec.images) | set(spec.image_data)
    checkable = mandatory - catalogue.valueless_ids(root)
    # XIV.3: a field taken off the canvas by a group removal or a vertical crop is not
    # unresolved. A division holding fewer members than its template declares draws none
    # of the surplus, and must not be asked for their values.
    #
    # `spec.remove` names the nodes removed, and a removed **group** takes its whole
    # subtree with it (XIV.2) — but only the group's own id appears in that list. The
    # children have to be gathered from the tree, or a mandatory field wrapped in a
    # removed group is still demanded and the render is abandoned over a field that is no
    # longer on the graphic.
    checkable -= set(getattr(spec, "off_canvas", set()))
    checkable -= _removed_field_ids(index, spec.remove)
    # A value the data determined to be empty is determined. An unoccupied lineup seat is
    # drawn with its name cleared because the template's layout is fixed, and that is the
    # graphic being correct rather than a value going missing (XIV.3).
    checkable -= set(getattr(spec, "empty_quietly", ()))
    undetermined = sorted((checkable - supplied) | (checkable & set(spec.empty)))
    if undetermined:
        return Problem(
            kind=PROBLEM_UNRESOLVED_VALUE,
            detail=(
                "no value could be determined for "
                + ", ".join(f"`{name}`" for name in undetermined[:8])
                + (f" and {len(undetermined) - 8} more" if len(undetermined) > 8 else "")
            ),
            template_key=image_type,
            field_id=undetermined[0],
        )

    return None


def grouped_notice_lines(notices: list[RenderNotice]) -> list[str]:
    """Notices grouped by kind, identical culprits counted rather than repeated.

    A render degrades the same way many times over — twenty drivers in a standings image
    whose markers all come from one directory produce twenty identical notices — and a
    line apiece buries the one notice that differs among nineteen that do not.

    Returned without a heading so the log block and a command's own reply can each supply
    their own, and still be grouped identically. The notices themselves are untouched: this is
    presentation, and only presentation.
    """
    by_kind: dict[str, dict[tuple[str, str], list[str]]] = {}
    order: list[str] = []
    for notice in notices:
        kind = notice.notice_kind
        if kind not in by_kind:
            by_kind[kind] = {}
            order.append(kind)
        # The field id is not part of the key: it is the only thing that differs between
        # twenty otherwise identical marker notices, so keying on it would defeat the
        # grouping entirely. The ids are kept aside rather than discarded — a notice that
        # stands alone still names its field, which is what XIV.4 requires of it.
        key = (notice.image_type, notice.detail)
        by_kind[kind].setdefault(key, []).append(notice.field_id or "")

    lines: list[str] = []
    for kind in order:
        culprits = by_kind[kind]
        total = sum(len(ids) for ids in culprits.values())
        lines.append(f"  [{kind}] ×{total}" if total > 1 else f"  [{kind}]")
        for (image_type, detail), field_ids in culprits.items():
            # One occurrence names where it happened; many name how many times, because
            # a list of twenty ids is not something a manager reads or acts on.
            if len(field_ids) == 1:
                where = f" / `{field_ids[0]}`" if field_ids[0] else ""
                lines.append(f"    • {image_type}{where} — {detail}")
            else:
                lines.append(f"    • {image_type} — {detail} (×{len(field_ids)})")
    return lines


def _palette_notice(image_type: str, division_name: str) -> RenderNotice:
    """The notice that a tier's colours could not be applied and the graphic is drawn in the
    template's own."""
    return RenderNotice(
        image_type=image_type,
        notice_kind=NOTICE_TIER_COLOURS_NOT_APPLIED,
        detail=(
            f"the colours of {division_name} could not be applied, so the graphic is drawn in "
            "the template's own colours"
        ),
    )


@dataclass
class _Drawn:
    """What the render's worker thread hands back: plain data, holding nothing parsed. The
    serialised picture and its canvas, or the problem that stopped it, and the notices raised
    on the way."""

    problem: Problem | None = None
    svg: bytes | None = None
    canvas: tuple[int, int] | None = None
    notices: list[RenderNotice] = dataclass_field(default_factory=list)


def _drawing_problem(exc: Exception, image_type: str, refusal_words: str) -> Problem:
    """The problem a failed build or fill comes back as. A `DrawingRefused` is the league's to
    put right and keeps its own words (*refusal_words*); any other exception is a fault in the
    bot, named by its kind alone, its detail being in the log the caller has just written."""
    if isinstance(exc, DrawingRefused):
        return Problem(
            kind=PROBLEM_UNRESOLVED_VALUE, detail=refusal_words, template_key=image_type
        )
    return Problem(
        kind=PROBLEM_DRAWING_FAULT, detail=describe_fault(exc), template_key=image_type
    )


def _draw(
    path: Path,
    image_type: str,
    spec_builder,
    palette: dict[str, str],
    division_name: str | None,
    raise_faults: bool = False,
) -> _Drawn:
    """Parse the template, paint the tier's palette into it, build and verify the spec, and
    fill: the render's whole work on the tree, run in a worker thread through `off_the_loop`.

    The palette goes in before the builder, deliberately. Builders read the template's
    computed style — `_highlight_paints` pulls the standings highlight ink straight out of the
    stylesheet, and the fit engine resolves `fill` through `computed_style` — so injecting
    afterwards would leave them reading the unpalette'd file and a league unable to drive
    either through a slot.

    A template that cannot be read, a spec that cannot be built, data the verify finds unfit, a
    fill that fails and a field the fill cannot resolve come back as a problem, and a palette
    that cannot be applied and what the fill substituted as notices: the render's own fallback,
    which the posting paths fall back from to text. A refusal the league can put right is a
    problem in its own words; a fault in the bot is an internal problem naming only its kind. A
    fault the verify meets in counting the template's mandatory ids is not caught here: it
    leaves the worker and `off_the_loop` raises it out of the render, for the caller's place-5
    fallback.

    What the builder or the fill raises is a problem only when it is a `DrawingRefused`, an error
    the league caused and can put right. Any other exception is a fault in the bot, and a caller
    with no fallback to text (the preview) asks for it raised (*raise_faults*), so that it reaches
    its command's failure path and is not shown to a league in Python's words. The posting paths
    keep the default and fall back from an internal problem, whose detail is the kind of fault
    and never the exception's message.
    """
    from leaguebot.image.utils.svg_document import SvgError, load_svg
    from leaguebot.image.utils.svg_fill import fill
    from leaguebot.image.utils.svg_palette import apply_palette

    try:
        root = load_svg(path)
    except SvgError as exc:
        return _Drawn(
            problem=Problem(
                kind=PROBLEM_NOT_SVG,
                detail=f"not a valid SVG file — {exc}",
                template_key=image_type,
            )
        )

    notices: list[RenderNotice] = []
    if palette:
        try:
            apply_palette(root, palette)
        except Exception:  # noqa: BLE001 — place 5: the graphic is drawn in the template's colours
            log.exception("render: per-tier palette failed for %s / %s", image_type, division_name)
            notices.append(_palette_notice(image_type, division_name or ""))

    try:
        spec = spec_builder(root)
    except Exception as exc:  # noqa: BLE001 — place 5: the problem is what the caller falls back from, unless it has none
        if raise_faults and not isinstance(exc, DrawingRefused):
            raise
        log.exception("render: spec build failed for %s", image_type)
        return _Drawn(
            problem=_drawing_problem(
                exc, image_type, f"the data for this image could not be assembled — {exc}"
            ),
            notices=notices,
        )

    # ── Verification against the concrete data (FR-010) ───────────────
    # Layer 2 checked the template when it was configured. The data has moved since:
    # a division grew, a value stopped being determinable. This is the only moment
    # both are in hand.
    problem = _verify_against_data(root, spec, image_type)
    if problem is not None:
        return _Drawn(problem=problem, notices=notices)

    try:
        result = fill(spec)
    except Exception as exc:  # noqa: BLE001 — place 5: the problem is what the caller falls back from, unless it has none
        if raise_faults and not isinstance(exc, DrawingRefused):
            raise
        log.exception("render: fill failed for %s", image_type)
        return _Drawn(
            problem=_drawing_problem(
                exc, image_type, f"the template could not be filled — {exc}"
            ),
            notices=notices,
        )

    notices.extend(result.notices)
    # Constitution XIV.3: an unresolved field aborts the render. Notices raised
    # before the problem are still reported, so an operator sees the whole picture.
    if result.unresolved:
        return _Drawn(
            problem=Problem(
                kind=PROBLEM_UNRESOLVED_VALUE,
                detail="; ".join(result.unresolved),
                template_key=image_type,
            ),
            notices=notices,
        )
    return _Drawn(svg=result.svg, canvas=result.canvas, notices=notices)


class ImageRenderService:
    """Fill a template and rasterise it, reporting problems and notices separately."""

    def __init__(self, config_service, validity_service) -> None:
        self._config_service = config_service
        self._validity_service = validity_service

    async def tier_palette(self, division_name: str) -> dict[str, str]:
        """This tier's configured colours, or nothing while the feature is off (051).

        Reads nothing else, and nothing at all while the feature is off, which is what keeps
        it inert: a league that has not asked for per-tier colours pays not even a query for
        them. It is the read half of painting a tier into a template, which is done in a worker
        thread with what this answers (`apply_palette`), so that the read, which needs the
        loop, comes first.

        Public because it has two callers and must stay the one step both take. `render` paints
        a division's graphic with what it answers; the fastest-lap contrast check (#165) paints
        each division's copy of the race results template with it before reading the plate. A
        second way of resolving a tier's colours would let the figure a league is told drift
        from the colour it is drawn in, which is the defect #165 was.
        """
        config = await self._config_service.get_config()
        if config is None or not getattr(config, "per_tier_colour_enabled", False):
            return {}
        return await self._config_service.get_tier_palette(division_name)

    async def render(
        self,
        image_type: str,
        spec_builder,
        *,
        output_dir: Path | None = None,
        filename_stem: str | None = None,
        division_name: str | None = None,
        raise_faults: bool = False,
    ) -> RenderOutcome:
        """Render one template.

        *spec_builder* is called with the parsed template root and returns a
        :class:`FillSpec`. Keeping it a callback means the caller owns the data and this
        service owns the pipeline.

        *division_name* is the tier the graphic is being drawn for, and is what per-tier
        colours (051) are resolved against. Omitting it draws the template in the colours it
        was authored in, which is also what happens while the feature is off — so a posting
        path that does not pass it is not broken, merely uncoloured. That silence is why
        `test_every_render_call_site_names_the_division` exists.

        *filename_stem* names the PNG on disk, and through it the attachment a league
        receives — see :mod:`leaguebot.image.utils.image_naming`. It defaults to *image_type*, which is the
        template's own key and says nothing about what was drawn; a posting path that knows
        the season, division and round passes a stem built from them. The **directory** is
        what keeps concurrent renders apart and what ``discard_render`` recognises as the
        bot's own, so the stem is free to be anything a filesystem will take.

        *raise_faults* is for a caller with no fallback to text, which the preview is: an
        exception the data build or the fill raises that is not a `DrawingRefused` is raised out
        of the render, to the caller's command-failure path, instead of being returned as a
        problem. A refusal the league can put right still comes back as a problem. The posting
        paths leave it off and fall back from the problem as ever. The converter's own failures
        follow the same rule: a timeout, a failure to run, a non-zero exit and a missing output
        file are faults, raised or returned as an internal problem naming only their kind, while
        the converter's absence and an image larger than Discord accepts come back as problems
        in their own words either way.
        """
        if not converter_available():
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_CONVERTER_ABSENT,
                    detail=f"{CONVERTER_NAME} is not installed on this host.",
                    template_key=image_type,
                )
            )

        reports = await self._validity_service.template_reports()
        report = reports.get(image_type)
        if report is None:
            # No league can cause this: a caller asked for a type the module has no
            # column for. Recorded here, and reported to a user only in general terms.
            log.error("render: unknown image type %r requested", image_type)
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_UNKNOWN_IMAGE_TYPE,
                    detail=f"`{image_type}` is not a known image type.",
                    template_key=image_type,
                )
            )
        if not report.valid:
            return RenderOutcome(
                problem=Problem(
                    kind=_kind_for_layer(report.failed_layer),
                    detail=report.reason or "the template is not valid.",
                    template_key=image_type,
                )
            )

        # ── Per-tier colours (051): the read, which needs the loop ─────────
        # Never fatal. A palette that cannot be read is a graphic drawn in the template's own
        # colours, which is exactly what every league saw before this existed; refusing to
        # post over it would be a worse answer than posting the wrong shade of blue. It is
        # reported, though: a render notice says the tier's colours were not applied.
        notices: list[RenderNotice] = []
        palette: dict[str, str] = {}
        if division_name:
            try:
                palette = await self.tier_palette(division_name)
            except Exception:  # noqa: BLE001 — place 5: the graphic is drawn in the template's colours
                log.exception(
                    "render: per-tier palette failed for %s / %s", image_type, division_name
                )
                notices.append(_palette_notice(image_type, division_name))

        # The whole of the work on the tree, in one worker thread: parse, palette, spec,
        # verify, fill, serialise. Nothing parsed leaves it, so what comes back is the
        # serialised picture or a problem and the notices, plain data (`load_svg`).
        drawn = await off_the_loop(
            _draw, report.resolved_path, image_type, spec_builder, palette, division_name,
            raise_faults,
        )
        notices.extend(drawn.notices)
        if drawn.svg is None or drawn.canvas is None:
            return RenderOutcome(problem=drawn.problem, notices=notices)

        directory = output_dir or Path(tempfile.mkdtemp(prefix=_RENDER_DIR_PREFIX))
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{filename_stem or image_type}.png"

        try:
            # Blocking subprocess: it MUST NOT run on the event loop. On a single-process
            # asyncio bot that would stall the scheduler, the retry worker and every
            # in-flight interaction for its duration.
            png = await asyncio.to_thread(rasterise, drawn.svg, destination, drawn.canvas)
        except ConverterAbsent as exc:
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_CONVERTER_ABSENT,
                    detail=str(exc),
                    template_key=image_type,
                ),
                notices=notices,
            )
        except RasterisationRefused as exc:
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_RASTERISER,
                    detail=str(exc),
                    template_key=image_type,
                ),
                notices=notices,
            )
        except RasterisationError as exc:
            # The converter timed out, could not be run, exited with an error or wrote nothing:
            # a fault in the bot or its host, which no league can put right, so it is named by
            # its kind, the converter's own text going to the log alone.
            if raise_faults:
                raise
            log.error("render: conversion failed for %s: %s", image_type, exc, exc_info=True)
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_DRAWING_FAULT,
                    detail=describe_fault(exc),
                    template_key=image_type,
                ),
                notices=notices,
            )

        return RenderOutcome(png_paths=[png], notices=notices)

    @staticmethod
    def format_notices(notices: list[RenderNotice], *, subject: str | None = None) -> str:
        """The notice block, shared by the log channel and a command's own reply.

        Grouped by kind, and identical culprits counted rather than repeated — see
        :func:`grouped_notice_lines`.

        *subject* names what was being drawn, as the posting paths know it: "drivers
        standings — Season 3", "Race — Portugal". The image type alone names the template,
        which is not the same thing as naming the championship or the session a reader is
        looking for in a log of many.
        """
        heading = "Image render notices:"
        if subject:
            heading = f"Image render notices — {subject}:"
        return "\n".join([heading, *grouped_notice_lines(notices)])

    # ── Posting: the commanded / uncommanded split (Constitution XIV.7) ────

    async def render_for_posting(
        self,
        image_type: str,
        spec_builder,
        *,
        posting_origin: PostingOrigin,
        output_dir: Path | None = None,
        filename_stem: str | None = None,
        division_name: str | None = None,
    ) -> PostingDecision:
        """Render, and decide what the caller should do with a failure.

        ``posting_origin`` is **required** and is never inferred. The tempting inference —
        "is there an Interaction in scope?" — is wrong for a command that schedules later
        work, and wrong for the retry queue re-posting something a command originated.
        Making every call site state which it is means a new one cannot fall into the
        wrong behaviour by omission.

        The two behaviours are deliberately opposite on the same fault:

        * **COMMANDED** — reject. Post nothing anywhere; hand the fault back so the caller
          can tell the person who asked. They are the one person able to fix the template,
          and silently posting text would deny them the chance and hide the defect until
          it next fires unattended.
        * **SCHEDULED** — fall back to the traditional text output. There is nobody to
          tell, and the league still needs its information.

        The render's notices ride on the decision and are written by nobody here: the job that
        posts the picture writes them as its own lines.
        """
        if not isinstance(posting_origin, PostingOrigin):
            raise TypeError(
                "posting_origin must be a PostingOrigin; it is never inferred "
                "(Constitution XIV.7)."
            )

        outcome = await self.render(
            image_type,
            spec_builder,
            output_dir=output_dir,
            filename_stem=filename_stem,
            division_name=division_name,
        )

        if outcome.ok:
            return PostingDecision(
                action=POST_IMAGE,
                png_paths=outcome.png_paths,
                notices=outcome.notices,
            )

        problem = outcome.problem
        if problem is not None and problem.is_internal:
            log.error(
                "render_for_posting: internal problem for %s — %s",
                image_type,
                problem.detail,
            )

        if posting_origin is PostingOrigin.COMMANDED:
            return PostingDecision(
                action=REJECT_COMMAND,
                problem=problem,
                notices=outcome.notices,
            )

        return PostingDecision(
            action=POST_TEXT_FALLBACK,
            problem=problem,
            notices=outcome.notices,
        )
