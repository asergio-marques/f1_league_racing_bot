"""Draw a round's two championships as graphics, or stand aside (040).

**One decision in one place**, as 039's results posting is. Every occasion that reposts the
textual standings reaches ``results_post_service.produce_standings``, the posting jobs of
`review_posting` among them, which asks the image drawing (`ImageDrawing.draw_standings`) for the
pictures and posts them itself: this module draws and posts nothing.

Two things make this module unlike the five before it.

**It draws two pictures from one call.** The textual standings are a single message carrying both
championships; the graphics are two, the driver standings first and the constructors second, each
with its own id column on the round's row in `round_standings_posts`. Every other aspect draws
one attachment per call and lets its caller loop.

**Failure is per championship** (Constitution XIV.4). A drivers graphic that will not draw
falls back to the drivers section **alone** — never to the whole textual message, which
would print the constructors table a second time beside the graphic that just drew it. That
is why ``results_post_service`` keeps section formatting and message composition apart, and
why :class:`StandingsDrawn` says which championships drew rather than simply whether it ran.

**The textual path is not reformed by this module.** Where the image flow does not run — no
drawing in scope, the module disabled, the `standings` toggle off, neither template valid —
`StandingsDrawn.applicable` is False and the caller's body runs exactly as it did before 040
(Constitution XIV.7).
"""
from __future__ import annotations

from dataclasses import dataclass

import discord

from leaguebot.image.models.image_module import PostingOrigin
from leaguebot.image.models.standings_drawing import (
    CONSTRUCTORS_TEMPLATE_KEY,
    DRIVERS_TEMPLATE_KEY,
)

#: The standings' own aspect and the name its toggle goes by, which heads each line it hands back.
STANDINGS_ASPECT = "standings"
STANDINGS_TOGGLE_NAME = "Standings image"

#: Championship -> the template that draws it, in the order the two are posted.
TEMPLATE_KEYS: dict[str, str] = {
    "drivers": DRIVERS_TEMPLATE_KEY,
    "constructors": CONSTRUCTORS_TEMPLATE_KEY,
}


@dataclass(frozen=True)
class StandingsDrawn:
    """What the image module drew of a round's two championships, for the standings to post.

    *applicable* is False where the image flow stood aside wholesale (no switch on and no
    template valid), and the caller's untouched textual body is what should run, message for
    message as before 040. Where it is True, a championship whose file is None is one the caller
    posts as its text section, and that section alone. *lines* are what the drawing had to report
    (its render notices, and the faults of pictures that were due and not drawn), headed by the
    toggle's name, for the job that posts the standings to write.
    """

    applicable: bool = False
    drivers: discord.File | None = None
    constructors: discord.File | None = None
    lines: tuple[str, ...] = ()

    def file_of(self, championship: str) -> discord.File | None:
        """The picture of *championship* ("drivers" or "constructors"), or None."""
        return self.drivers if championship == "drivers" else self.constructors


def fault(what: str, detail: str) -> str:
    """The line saying why the picture of *what* was not drawn, headed by the toggle's name."""
    return f"⚠️ {STANDINGS_TOGGLE_NAME} — {what}: {detail}"


def lines_of(what: str, decision) -> tuple[str, ...]:
    """The lines a render's notices and problem make, naming *what* they pertain to."""
    from leaguebot.image.services.render.image_render_service import ImageRenderService

    lines: list[str] = []
    if decision.notices:
        lines.append(ImageRenderService.format_notices(decision.notices, subject=what))
    if decision.problem is not None:
        lines.append(fault(what, decision.problem.detail))
    return tuple(lines)


# ── Render ────────────────────────────────────────────────────────────────


async def render_png(config_service, renderer, drawing):
    """Render one championship. Returns the render service's PostingDecision.

    A fault in resolving the directories or building the fill is raised, for the drawing to
    name as the reason this championship's text stands.
    """
    from leaguebot.image.services.render.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )
    from leaguebot.image.services.aspects.image_standings_service import build_fill_spec

    config = await config_service.get_config()
    directories, directory_faults = resolve_configured_directories(
        config,
        (
            ("team", "team_image_directory"),
            ("flag", "flag_directory"),
            ("track", "track_image_directory"),
            # The only aspect that draws position-change arrows, and the class the
            # result marks share with them.
            ("marker", "marker_directory"),
        ),
        image_type=drawing.template_key,
    )

    from leaguebot.image.utils.image_naming import stem_for_drawing

    return await renderer.render_for_posting(
        drawing.template_key,
        spec_builder_with_faults(
            build_fill_spec, drawing, directories, directory_faults
        ),
        posting_origin=PostingOrigin.SCHEDULED,
        division_name=drawing.division_name,
        filename_stem=stem_for_drawing(drawing),
    )
