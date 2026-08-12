"""ImageRenderService — SVG-to-PNG rasterisation and render outcome assembly.

Constitution XIV.4 splits render outcomes in two, and ``RenderOutcome`` carries that split
explicitly: ``png_paths`` is empty whenever ``problem`` is set, so no caller can receive a
partial image or mistake a degraded render for a clean one.

The rasteriser is an external binary (Inkscape) that no package declaration installs. Its
absence is fatal to the module (FR-008) and is reported rather than raised.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from dataclasses import dataclass, field as dataclass_field

from db.database import get_connection
from models.image_module import (
    PROBLEM_NOT_SVG,
    PROBLEM_RASTERISER,
    PROBLEM_UNKNOWN_IMAGE_TYPE,
    PROBLEM_UNRESOLVED_VALUE,
    PostingOrigin,
    Problem,
    RenderNotice,
    RenderOutcome,
)

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
        candidate = Path(override)
        if candidate.is_file():
            return str(candidate)
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


# ── Rasterisation ─────────────────────────────────────────────────────────

#: Discord's attachment ceiling for a non-boosted server. The canvas is declared by the
#: template (XIV.1) and so is not something this module controls, which is why exceeding
#: it is treated as a problem rather than left to fail at upload time.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

#: A render that has not finished by now is not going to.
_RASTERISE_TIMEOUT_SECONDS = 120


class RasterisationError(Exception):
    """The converter failed. A problem, never a notice."""


def rasterise(svg: bytes, destination: Path, canvas: tuple[int, int]) -> Path:
    """Convert *svg* to a PNG at *destination*. Blocking — call it off the event loop.

    The width and height are passed explicitly so the export matches the canvas the
    template declared, rather than whatever the converter infers.
    """
    executable = find_converter()
    if executable is None:
        raise RasterisationError(f"{CONVERTER_NAME} is not installed on this host.")

    width, height = canvas
    source = destination.with_suffix(".svg")
    source.write_bytes(svg)

    command = [
        executable,
        str(source),
        "--export-type=png",
        f"--export-filename={destination}",
        f"--export-width={width}",
        f"--export-height={height}",
    ]

    try:
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

    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise RasterisationError(
            f"{CONVERTER_NAME} exited {completed.returncode}"
            + (f": {detail.splitlines()[-1]}" if detail else "")
        )

    if not destination.exists():
        raise RasterisationError(f"{CONVERTER_NAME} produced no output file.")

    size = destination.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise RasterisationError(
            f"the rendered image is {size / 1024 / 1024:.1f} MB, above Discord's "
            f"{MAX_ATTACHMENT_BYTES // 1024 // 1024} MB attachment limit."
        )

    return destination


def _kind_for_layer(failed_layer: int | None) -> str:
    """The problem kind matching the validity layer that rejected a template.

    Keeps a render's problem distinguishable in the same terms the configuration command
    and the season gate use, rather than flattening every invalid template to one kind.
    """
    from models.image_module import (
        PROBLEM_MISSING_MANDATORY_FIELD,
        PROBLEM_NOT_FOUND,
    )
    from services.image_validity_service import LAYER_CATALOGUE

    if failed_layer == LAYER_CATALOGUE:
        return PROBLEM_MISSING_MANDATORY_FIELD
    return PROBLEM_NOT_FOUND


def _verify_against_data(root, spec, image_type: str) -> Problem | None:
    """Check the template against the values it is about to receive (FR-010 … FR-014).

    Three questions, in the order that makes the cheapest failure the first:

    1. Does the data have more rows than the template has slots? (FR-028)
    2. Is a mandatory field absent from the template? (FR-012)
    3. Is a mandatory field's value undeterminable? (FR-011)

    All three pass vacuously while the image type's catalogue is empty, which is every
    type in this increment. Populating one catalogue switches all three on for that type.
    """
    from models.image_catalogues import CapacityError, catalogue_for
    from models.image_module import (
        PROBLEM_CAPACITY_EXCEEDED,
        PROBLEM_MISSING_MANDATORY_FIELD,
    )
    from utils.svg_document import FieldIndex

    catalogue = catalogue_for(image_type)
    if catalogue.is_empty:
        return None

    # The data a data-fixed collection is measured against (XIV.12, v4.3.0). None for
    # every ordinal-discriminated type, which is every type but the lineup.
    binding = getattr(spec, "binding", None)

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

    # 1a. A data-fixed collection diverging from the template, in either direction
    #     (XIV.12, v4.3.0). Reported before the mandatory sweep so that a team the
    #     template does not declare is named as the divergence it is, rather than as a
    #     list of absent ids.
    divergences = catalogue.divergent_members(root, binding)
    if divergences:
        return Problem(
            kind=PROBLEM_MISSING_MANDATORY_FIELD,
            detail=(
                "; ".join(divergences[:6])
                + (f"; and {len(divergences) - 6} more" if len(divergences) > 6 else "")
            ),
            template_key=image_type,
        )

    mandatory = catalogue.all_mandatory_ids(root, binding)
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
    checkable = mandatory - catalogue.valueless_ids(root, binding)
    # XIV.3: a field taken off the canvas by a group removal or a vertical crop is not
    # unresolved. A division holding fewer members than its template declares draws none
    # of the surplus, and must not be asked for their values.
    checkable -= set(getattr(spec, "off_canvas", set())) | set(spec.remove)
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


class ImageRenderService:
    """Fill a template and rasterise it, reporting problems and notices separately."""

    def __init__(self, db_path: str, config_service, validity_service) -> None:
        self._db_path = db_path
        self._config_service = config_service
        self._validity_service = validity_service

    async def render(
        self,
        server_id: int,
        image_type: str,
        spec_builder,
        *,
        output_dir: Path | None = None,
        persist_notices: bool = True,
    ) -> RenderOutcome:
        """Render one template.

        *spec_builder* is called with the parsed template root and returns a
        :class:`FillSpec`. Keeping it a callback means the caller owns the data and this
        service owns the pipeline.
        """
        from utils.svg_document import SvgError, load_svg
        from utils.svg_fill import fill

        if not converter_available():
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_RASTERISER,
                    detail=f"{CONVERTER_NAME} is not installed on this host.",
                    template_key=image_type,
                )
            )

        reports = await self._validity_service.template_reports(server_id)
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

        try:
            root = load_svg(report.resolved_path)
        except SvgError as exc:
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_NOT_SVG,
                    detail=f"not a valid SVG file — {exc}",
                    template_key=image_type,
                )
            )

        try:
            spec = spec_builder(root)
        except Exception as exc:  # noqa: BLE001
            log.exception("render: spec build failed for %s", image_type)
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_UNRESOLVED_VALUE,
                    detail=f"the data for this image could not be assembled — {exc}",
                    template_key=image_type,
                )
            )

        # ── Verification against the concrete data (FR-010) ───────────────
        # Layer 2 checked the template when it was configured. The data has moved since:
        # a division grew, a value stopped being determinable. This is the only moment
        # both are in hand.
        problem = _verify_against_data(root, spec, image_type)
        if problem is not None:
            return RenderOutcome(problem=problem)

        try:
            result = fill(spec)
        except Exception as exc:  # noqa: BLE001 - a template surprise must not crash the bot
            log.exception("render: fill failed for %s", image_type)
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_UNRESOLVED_VALUE,
                    detail=f"the template could not be filled — {exc}",
                    template_key=image_type,
                )
            )

        # Constitution XIV.3: an unresolved field aborts the render. Notices raised
        # before the problem are still reported, so an operator sees the whole picture.
        if result.unresolved:
            if persist_notices:
                await self._persist(server_id, result.notices)
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_UNRESOLVED_VALUE,
                    detail="; ".join(result.unresolved),
                    template_key=image_type,
                ),
                notices=result.notices,
            )

        directory = output_dir or Path(tempfile.mkdtemp(prefix="f1bot_render_"))
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{image_type}.png"

        try:
            # Blocking subprocess: it MUST NOT run on the event loop. On a single-process
            # asyncio bot that would stall the scheduler, the retry worker and every
            # in-flight interaction for its duration.
            png = await asyncio.to_thread(rasterise, result.svg, destination, result.canvas)
        except RasterisationError as exc:
            if persist_notices:
                await self._persist(server_id, result.notices)
            return RenderOutcome(
                problem=Problem(
                    kind=PROBLEM_RASTERISER,
                    detail=str(exc),
                    template_key=image_type,
                ),
                notices=result.notices,
            )

        if persist_notices:
            await self._persist(server_id, result.notices)

        return RenderOutcome(png_paths=[png], notices=result.notices)

    async def _persist(self, server_id: int, notices: list[RenderNotice]) -> None:
        """Append every notice to image_render_notices (Principle V, XIV.4)."""
        if not notices:
            return
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self._db_path) as db:
            await db.executemany(
                "INSERT INTO image_render_notices "
                "(server_id, image_type, rendered_at, notice_kind, field_id, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        server_id,
                        notice.image_type,
                        notice.rendered_at or now,
                        notice.notice_kind,
                        notice.field_id,
                        notice.detail,
                    )
                    for notice in notices
                ],
            )
            await db.commit()

    @staticmethod
    def format_notices(notices: list[RenderNotice]) -> str:
        """The notice block, shared by the log channel and a command's own reply."""
        lines = ["Image render notices:"]
        lines += [
            f"  • [{notice.notice_kind}] {notice.image_type}"
            + (f" / `{notice.field_id}`" if notice.field_id else "")
            + f" — {notice.detail}"
            for notice in notices
        ]
        return "\n".join(lines)

    @staticmethod
    async def report_notices(bot, server_id: int, notices: list[RenderNotice]) -> None:
        """Surface notices to the calculation log channel (Principle V, FR-031).

        The log always. A command's own output additionally, which is the caller's job —
        it holds the interaction. Never a channel drivers read (FR-032).
        """
        if not notices:
            return
        try:
            await bot.output_router.post_log(
                server_id, ImageRenderService.format_notices(notices)
            )
        except Exception as exc:  # noqa: BLE001
            log.error("report_notices: log write failed: %s", exc)

    # ── Posting: the commanded / uncommanded split (Constitution XIV.7) ────

    async def render_for_posting(
        self,
        server_id: int,
        image_type: str,
        spec_builder,
        *,
        posting_origin: PostingOrigin,
        bot=None,
        output_dir: Path | None = None,
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
        """
        if not isinstance(posting_origin, PostingOrigin):
            raise TypeError(
                "posting_origin must be a PostingOrigin; it is never inferred "
                "(Constitution XIV.7)."
            )

        outcome = await self.render(
            server_id, image_type, spec_builder, output_dir=output_dir
        )

        if bot is not None and outcome.notices:
            await self.report_notices(bot, server_id, outcome.notices)

        if outcome.ok:
            return PostingDecision(
                action=POST_IMAGE,
                png_paths=outcome.png_paths,
                notices=outcome.notices,
            )

        problem = outcome.problem
        if problem is not None and problem.is_internal:
            log.error(
                "render_for_posting: internal problem for %s on server %s — %s",
                image_type,
                server_id,
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
