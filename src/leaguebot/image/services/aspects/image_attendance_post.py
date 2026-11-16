"""Post a division's attendance sheet as a graphic (041, US2).

**This module renders and never posts.** ``attendance_sheet.post_attendance_sheet`` owns the
posting — one send site, the replacement produced and posted before the message it replaces
is deleted (Constitution XIV.8; the deletion is the change queue's job after the post) — and this
hands it a file to attach. That is the
author's ruling of 2026-08-13 made structural: *the image path inherits the ordering* rather
than carrying a second implementation of it beside the textual one, which would drift, and
whose fallback half would be the one left deleting first.

A failed render therefore needs no fallback machinery at all: the image drawing hands back no
file, and the same send site posts the textual sheet it would have posted anyway. What the render
reported, its notices and the fault of a picture that was due and not drawn, comes back as lines
for the job that posts the sheet to write, headed by the toggle's name.

**The sheet never gates a sanction** (XIV.7). Nothing in this module is reached before
`owed_sanctions` and `apply_sanction_on` have saved the sanctions; the graphic is downstream of every state change it depicts and
is never a precondition of one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from leaguebot.image.models.image_module import PostingOrigin

if TYPE_CHECKING:
    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import ImageRenderService

ATTENDANCE_ASPECT = "attendance"
ATTENDANCE_TEMPLATE_KEY = "attendance_template"

#: The toggle's name in `/images config toggle` ("Attendance sheet") and what it draws, which heads
#: a fault's line.
ATTENDANCE_TOGGLE_NAME = "Attendance sheet image"


@dataclass
class SheetRender:
    """What the image path produced for one division's sheet.

    *png* is None wherever the textual sheet should be posted instead — the template invalid or
    the render having failed. The caller does not need to know which: every one of them means
    "post the text you were going to post anyway".
    """

    png: Path | None = None
    notices: list = field(default_factory=list)
    problem: str | None = None
    rejects: bool = False

    @property
    def draws(self) -> bool:
        return self.png is not None


def fault(what: str, detail: str) -> str:
    """The line saying why the picture of *what* was not drawn, headed by the toggle's name."""
    return f"⚠️ {ATTENDANCE_TOGGLE_NAME} — {what}: {detail}"


def lines_of(what: str, render: SheetRender) -> tuple[str, ...]:
    """The lines a render's notices and problem make, naming *what* they pertain to."""
    from leaguebot.image.services.render.image_render_service import ImageRenderService

    lines: list[str] = []
    if render.notices:
        lines.append(ImageRenderService.format_notices(render.notices, subject=what))
    if render.problem:
        lines.append(fault(what, render.problem))
    return tuple(lines)


async def render_sheet(
    config_service: ImageConfigService,
    renderer: ImageRenderService,
    drawing,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> SheetRender:
    """Render *drawing* to a PNG, or report why the textual sheet should stand instead.

    A fault in resolving the directories or building the fill is raised, for the image drawing's
    catch-all to hand back as a line.
    """
    from leaguebot.image.services.aspects.image_attendance_service import build_fill_spec
    from leaguebot.image.services.render.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )
    from leaguebot.image.utils.image_naming import stem_for_drawing

    config = await config_service.get_config()
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

    decision = await renderer.render_for_posting(
        ATTENDANCE_TEMPLATE_KEY,
        spec_builder_with_faults(build_fill_spec, drawing, directories, directory_faults),
        posting_origin=origin,
        division_name=drawing.division_name,
        filename_stem=stem_for_drawing(drawing, ATTENDANCE_TEMPLATE_KEY),
    )

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
