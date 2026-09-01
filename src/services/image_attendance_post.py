"""Post a division's attendance sheet as a graphic (041, US2).

**This module renders and never posts.** ``attendance_service.post_attendance_sheet`` owns the
posting lifecycle — one send site, one delete site, the replacement produced before the message
it replaces is destroyed (Constitution XIV.8) — and this hands it a file to attach. That is the
author's ruling of 2026-08-13 made structural: *the image path inherits the ordering* rather
than carrying a second implementation of it beside the textual one, which would drift, and
whose fallback half would be the one left deleting first.

A failed render therefore needs no fallback machinery at all: it returns ``None``, and the same
send site posts the textual sheet it would have posted anyway.

**The sheet never gates a sanction** (XIV.7). Nothing in this module is reached before
``enforce_attendance_sanctions``; the graphic is downstream of every state change it depicts and
is never a precondition of one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from models.image_module import PostingOrigin

log = logging.getLogger(__name__)

ATTENDANCE_ASPECT = "attendance"
ATTENDANCE_TEMPLATE_KEY = "attendance_template"


@dataclass
class SheetRender:
    """What the image path produced for one division's sheet.

    *png* is None wherever the textual sheet should be posted instead — the module off, the
    aspect off, the template invalid, or the render having failed. The caller does not need to
    know which: every one of them means "post the text you were going to post anyway".
    """

    png: Path | None = None
    notices: list = field(default_factory=list)
    problem: str | None = None
    rejects: bool = False

    @property
    def draws(self) -> bool:
        return self.png is not None


async def attendance_enabled(bot, server_id: int) -> bool:
    """True where the module is on, the ``attendance`` aspect is on, and the template is valid."""
    try:
        if not await bot.module_service.is_images_enabled(server_id):
            return False
        toggles = await bot.image_config_service.get_toggles(server_id)
        if not toggles.get(ATTENDANCE_ASPECT):
            return False
        reports = await bot.image_validity_service.template_reports(server_id)
        report = reports.get(ATTENDANCE_TEMPLATE_KEY)
        return report is not None and report.valid
    except Exception as exc:  # noqa: BLE001 — never break a posting on this reader
        log.error("attendance: enablement check failed for server %s: %s", server_id, exc)
        return False


async def render_sheet(
    bot,
    server_id: int,
    drawing,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> SheetRender:
    """Render *drawing* to a PNG, or report why the textual sheet should stand instead."""
    from services.image_attendance_service import build_fill_spec
    from services.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    try:
        config = await bot.image_config_service.get_config(server_id)
        directories, directory_faults = resolve_configured_directories(
            config,
            (
                ("team", "team_image_directory"),
                ("flag", "flag_directory"),
                # The marks drawn beneath a driver's total, sharing the class and the folder
                # with the standings result chips and the position-change arrows.
                ("marker", "marker_directory"),
            ),
            image_type=ATTENDANCE_TEMPLATE_KEY,
        )

        from utils.image_naming import stem_for_drawing

        decision = await bot.image_render_service.render_for_posting(
            server_id,
            ATTENDANCE_TEMPLATE_KEY,
            spec_builder_with_faults(
                build_fill_spec, drawing, directories, directory_faults
            ),
            posting_origin=origin,
            bot=bot,
            filename_stem=stem_for_drawing(drawing, ATTENDANCE_TEMPLATE_KEY),
        )
    except Exception as exc:  # noqa: BLE001 — a resolution fault, reported like any other
        log.error("attendance: render failed for server %s: %s", server_id, exc)
        return SheetRender(problem=str(exc), rejects=origin is PostingOrigin.COMMANDED)

    if decision.rejects:
        return SheetRender(
            problem=(decision.problem.detail if decision.problem else None),
            rejects=True,
            notices=decision.notices,
        )

    if not decision.posts_image:
        return SheetRender(
            problem=(decision.problem.detail if decision.problem else None),
            notices=decision.notices,
        )

    return SheetRender(png=decision.png_paths[0], notices=decision.notices)


async def report(bot, server_id: int, what: str, detail: str) -> None:
    """Report a fault to the server's logging channel, never to a driver-read channel."""
    from services.image_results_post import report as _report

    await _report(bot, server_id, what, detail)


async def report_notices(bot, server_id: int, what: str, notices) -> None:
    """Report non-fatal degradations to the logging channel (XIV.4, FR-056)."""
    from services.image_results_post import report_notices as _report_notices

    await _report_notices(bot, server_id, what, notices)
