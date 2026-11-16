"""What a session's results picture is drawn from, and the lines its drawing hands back (039).

The picture is drawn by ``ImageDrawing.draw_results``, which the results module's posting jobs are
handed, and which they hand the drawing they composed after asking the one gate every picture asks
(``aspect_wanted``): it renders it (:func:`render_png`) and hands back the file and the lines
(:func:`lines_of`) the job writes. **This module posts nothing** and reports nothing itself: the
results module posts the picture through core's standing-post handler, and a fault in drawing it is
raised for the job to say why the text stands.

**The textual path is not reformed by this module.** Where the picture is not drawn, the caller's
existing body runs exactly as it did before this feature. The graphic is an alternative output
beside the text, never a replacement for it (Constitution XIV.7).

The readers of a team's name and shorthand here are the ones the verdict and standings pictures
share; the results' own are composed by the results module.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from leaguebot.image.models.image_module import PostingOrigin
from leaguebot.image.models.results_drawing import RESULTS_ASPECT  # noqa: F401 — re-exported

if TYPE_CHECKING:
    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import ImageRenderService

log = logging.getLogger(__name__)

#: What the toggle draws, which heads a fault's line: the aspect `/images config toggle` offers.
RESULTS_TOGGLE_NAME = "Results image"


async def render_png(
    config_service: ImageConfigService,
    renderer: ImageRenderService,
    drawing,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
):
    """Render one session's results. Returns the render service's PostingDecision.

    The colour of the fastest lap is the image configuration's, so it is applied to the drawing
    here and not where the results module composed it.

    A fault in resolving the directories or building the fill is raised, for the results' own
    post to say why the text stands.
    """
    from leaguebot.image.services.aspects.image_results_service import build_fill_spec
    from leaguebot.image.services.render.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    config = await config_service.get_config()
    drawing = dataclasses.replace(
        drawing, fastest_lap_colour=getattr(config, "fastest_lap_colour", None)
    )
    directories, directory_faults = resolve_configured_directories(
        config,
        (
            ("team", "team_image_directory"),
            ("flag", "flag_directory"),
            ("tyre", "tyre_directory"),
        ),
        image_type=drawing.template_key,
    )

    from leaguebot.image.utils.image_naming import stem_for_drawing

    # One template draws four sessions, so the *session's* own label names the file:
    # `feature_qualifying_results` rather than the template's `qualifying_results`,
    # which would not say which of a sprint weekend's two qualifyings was drawn.
    return await renderer.render_for_posting(
        drawing.template_key,
        spec_builder_with_faults(
            build_fill_spec, drawing, directories, directory_faults
        ),
        posting_origin=origin,
        division_name=drawing.division_name,
        filename_stem=stem_for_drawing(
            drawing, subject=f"{drawing.session_name} results"
        ),
    )


def fault(what: str, detail: str) -> str:
    """The line saying why the picture of *what* was not drawn, headed by the toggle's name."""
    return f"⚠️ {RESULTS_TOGGLE_NAME} — {what}: {detail}"


def lines_of(what: str, decision) -> tuple[str, ...]:
    """The lines a render's notices and problem make, naming *what* they pertain to."""
    from leaguebot.image.services.render.image_render_service import ImageRenderService

    lines: list[str] = []
    if decision.notices:
        lines.append(ImageRenderService.format_notices(decision.notices, subject=what))
    if decision.problem is not None:
        lines.append(fault(what, decision.problem.detail))
    return tuple(lines)
