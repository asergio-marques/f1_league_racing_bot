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

from db.database import get_connection
from models.image_module import RenderNotice, RenderOutcome

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
            return RenderOutcome(problem=f"{CONVERTER_NAME} is not installed on this host.")

        reports = await self._validity_service.template_reports(server_id)
        report = reports.get(image_type)
        if report is None:
            return RenderOutcome(problem=f"`{image_type}` is not a known template.")
        if not report.valid:
            return RenderOutcome(problem=f"{image_type}: {report.reason}")

        try:
            root = load_svg(report.resolved_path)
        except SvgError as exc:
            return RenderOutcome(problem=f"{image_type}: {exc}")

        try:
            spec = spec_builder(root)
            result = fill(spec)
        except Exception as exc:  # noqa: BLE001 - a template surprise must not crash the bot
            log.exception("render: fill failed for %s", image_type)
            return RenderOutcome(problem=f"{image_type}: could not be filled — {exc}")

        # Constitution XIV.3: an unresolved field aborts the render. Notices raised
        # before the problem are still reported, so an operator sees the whole picture.
        if result.unresolved:
            if persist_notices:
                await self._persist(server_id, result.notices)
            return RenderOutcome(
                problem=f"{image_type}: " + "; ".join(result.unresolved),
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
            return RenderOutcome(problem=f"{image_type}: {exc}", notices=result.notices)

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
    async def report_notices(bot, server_id: int, notices: list[RenderNotice]) -> None:
        """Surface notices to the calculation log channel (Principle V)."""
        if not notices:
            return
        lines = ["Image render notices:"]
        lines += [
            f"  • [{notice.notice_kind}] {notice.image_type}"
            + (f" / `{notice.field_id}`" if notice.field_id else "")
            + f" — {notice.detail}"
            for notice in notices
        ]
        try:
            await bot.output_router.post_log(server_id, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            log.error("report_notices: log write failed: %s", exc)
