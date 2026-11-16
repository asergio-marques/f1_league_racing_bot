"""What a session's results picture is drawn from, and the lines its drawing hands back (039).

The picture is drawn by ``ImageDrawing.draw_results``, which the results module's posting jobs are
handed: it asks the one gate every picture asks, resolves the session into a drawing
(:func:`build_drawing`), renders it (:func:`render_png`) and hands back the file and the lines
(:func:`lines_of`) the job writes. **This module posts nothing** and reports nothing itself: the
results module posts the picture through core's standing-post handler, and a fault in drawing it is
raised for the job to say why the text stands.

**The textual path is not reformed by this module.** Where the picture is not drawn, the caller's
existing body runs exactly as it did before this feature. The graphic is an alternative output
beside the text, never a replacement for it (Constitution XIV.7).

A driver's name and nationality are read through core's readers (`driver_names`), handed the hook
signup answers; the readers of a team's name and shorthand here are the ones the other pictures share.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from leaguebot.core.services.drivers.driver_names import (
    read_driver_names,
    read_driver_nationalities,
    read_nationality_collected,
)
from leaguebot.image.models.image_module import PostingOrigin

if TYPE_CHECKING:
    from leaguebot.core.utils.hooks import AwaitedHook
    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import ImageRenderService

log = logging.getLogger(__name__)

RESULTS_ASPECT = "results"

#: What the toggle draws, which heads a fault's line: the aspect `/images config toggle` offers.
RESULTS_TOGGLE_NAME = "Results image"


async def _team_names(db_path: str, guild, division_id: int, team_ids: list[int]) -> dict[int, str]:
    """The name of each division team a session records, keyed by the team's id.

    A session records the division's **team** an entry drove for, never its Discord role
    (#375), so the name the graphic draws is read from the team itself; the team image is
    looked up by its shorthand, from ``_team_keys`` (#381). Nothing asks Discord: a role replaced mid-season, or since
    deleted, has no bearing on the rounds recorded under it. *guild* and *division_id* are
    kept for the callers' symmetry with the driver lookups beside this one.
    """
    from leaguebot.core.services.drivers.team_service import team_names_for_instances

    return await team_names_for_instances(db_path, team_ids)


async def _team_keys(db_path: str, team_ids: list[int]) -> dict[int, str]:
    """The shorthand of each division team a session records, which its artwork is found by.

    Apart from the name drawn (#381): a team is drawn by its full name, and its badge file is
    named after its shorthand.
    """
    from leaguebot.core.services.drivers.team_service import team_artwork_keys_for_instances

    return await team_artwork_keys_for_instances(db_path, team_ids)


async def build_drawing(
    db_path: str,
    config_service: ImageConfigService,
    guild,
    *,
    driver_names_read: AwaitedHook,
    session_result,
    driver_rows,
    points_map,
    round_number,
    race_name: str,
    is_sprint: bool,
    result_status: str,
    division_name: str,
    division_tier=None,
    season_number=None,
    dsq_phase_map=None,
):
    """Resolve one session into a ResultsDrawing, or raise ResultsDataError."""
    from leaguebot.image.services.aspects.image_results_service import resolve_drawing

    user_ids = [row.driver_user_id for row in driver_rows]
    team_ids = [row.team_instance_id for row in driver_rows]

    config = await config_service.get_config()

    return resolve_drawing(
        session_type=session_result.session_type,
        is_sprint=is_sprint,
        result_status=result_status,
        division_name=division_name,
        division_tier=division_tier,
        season_number=season_number,
        round_number=round_number,
        race_name=race_name,
        driver_rows=driver_rows,
        points_map=points_map,
        driver_names=await read_driver_names(
            db_path, driver_names_read, guild, user_ids, division_id=session_result.division_id
        ),
        team_names=await _team_names(
            db_path, guild, session_result.division_id, team_ids
        ),
        team_keys=await _team_keys(db_path, team_ids),
        nationalities=await read_driver_nationalities(
            db_path, driver_names_read, user_ids, division_id=session_result.division_id
        ),
        dsq_phase_map=dsq_phase_map or {},
        fastest_lap_colour=getattr(config, "fastest_lap_colour", None),
        nationality_collected=await read_nationality_collected(db_path, driver_names_read),
    )


async def render_png(
    config_service: ImageConfigService,
    renderer: ImageRenderService,
    drawing,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
):
    """Render one session's results. Returns the render service's PostingDecision.

    A fault in resolving the directories or building the fill is raised, for the results' own
    post to say why the text stands.
    """
    from leaguebot.image.services.aspects.image_results_service import build_fill_spec
    from leaguebot.image.services.render.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    config = await config_service.get_config()
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
