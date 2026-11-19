"""Attach the check-in call graphic (041, US3).

**This module has exactly one caller**, and that is the whole of its design. The image drawing's
``attach_check_in_call`` is reached from the initial post in the check-in call's ``post_call`` job
(``check_in_call``) and from nowhere else: not
from the button callbacks, not from the reserve distribution, not from the deadline handler,
not from the embed rebuild. Each of those edits the embed in place, and the attachment rides
through untouched.

That single call site is the strongest guard available on Constitution XIV.17. The rule places
the static obligation on the author and states plainly that the module cannot detect a breach —
a stale picture under a current message reports nothing and looks correct. What the design *can*
do is make the mistake structurally hard: a future session adding a redraw has to add an import
to a module that has none, which is visible in review in a way a wrong catalogue entry is not.

**The graphic displaces nothing** (XIV.7). The role mention, the embed, its roster, its status
indicators and its three buttons are composed exactly as the textual flow composes them, with or
without the ``rsvp`` toggle. The only difference is the presence of a ``file=``.

Its fallback is therefore the message posted **without** the attachment — there is no text to
restore, because none was ever given up.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.fault_source import FaultWords

if TYPE_CHECKING:
    import discord

    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import ImageRenderService

RSVP_ASPECT = "rsvp"
RSVP_TEMPLATE_KEY = "rsvp_template"

#: The toggle's name in `/images config toggle` ("Check-in call") and what it draws, which heads a
#: fault's line.
RSVP_TOGGLE_NAME = "Check-in call image"


def describe(*, season_number: Any, division_name: str, round_number: Any) -> str:
    """What a line names, so a manager can find the call it pertains to: the round as a league
    reads it, with its season and its division (image spec, render notices), "check-in call of
    round 3 in Season 3 **Pro**", the season left out only where its number is unknown."""
    season = f"Season {season_number} " if season_number is not None else ""
    return f"check-in call of round {round_number} in {season}**{division_name}**"


def fault(what: str, detail: str) -> str:
    """The line saying why the picture of *what* was not drawn, headed by the toggle's name."""
    return f"⚠️ {RSVP_TOGGLE_NAME} — {what}: {detail}"


async def render_check_in(
    config_service: ImageConfigService,
    renderer: ImageRenderService,
    what: str,
    *,
    division_name: str,
    round_number: Any,
    round_format: Any,
    scheduled_at: Any,
    track_name: str | None,
    race_name: str | None,
    country_name: str | None,
    season_number: Any,
    division_tier: Any,
    deadline_at: Any,
) -> tuple[discord.File | None, tuple[str, ...]]:
    """Render the check-in call's graphic for *what*, and hand back the file to attach, or None to
    post the call without one, and the lines saying what the render reported: its notices, and
    the fault of a picture that was due and not drawn.

    A fault is raised, for the image drawing's catch-all to hand back as a line.
    """
    import discord

    from leaguebot.image.models.image_module import PostingOrigin
    from leaguebot.image.services.aspects.image_rsvp_service import build_fill_spec, resolve_drawing
    from leaguebot.image.services.render.image_render_service import (
        ImageRenderService,
        resolve_configured_directories,
        spec_builder_with_faults,
    )
    from leaguebot.image.utils.image_naming import stem_for_drawing

    config = await config_service.get_config()
    drawing = resolve_drawing(
        division_name=division_name,
        round_number=round_number,
        round_format=round_format,
        scheduled_at=scheduled_at,
        deadline_at=deadline_at,
        track_name=track_name,
        race_name=race_name,
        country_name=country_name,
        division_tier=division_tier,
        season_number=season_number,
        date_format=getattr(config, "date_format", None),
        time_format=getattr(config, "time_format", None),
        time_zone=getattr(config, "time_zone", None),
    )
    # The check-in graphic may draw both imagery classes, so both directories are resolved (044).
    # Each answers its own miss with its own fallback.
    directories, directory_faults = resolve_configured_directories(
        config,
        (("track", "track_image_directory"), ("flag", "flag_directory")),
        image_type=RSVP_TEMPLATE_KEY,
    )
    decision = await renderer.render_for_posting(
        RSVP_TEMPLATE_KEY,
        spec_builder_with_faults(build_fill_spec, drawing, directories, directory_faults),
        posting_origin=PostingOrigin.SCHEDULED,
        division_name=drawing.division_name,
        filename_stem=stem_for_drawing(drawing, RSVP_TEMPLATE_KEY),
    )
    lines: list[str] = []
    if decision.notices:
        lines.append(ImageRenderService.format_notices(decision.notices, subject=what))
    if not decision.posts_image:
        if decision.problem is not None:
            lines.append(FaultWords(fault(what, decision.problem.detail), decision.problem.source))
        return None, tuple(lines)
    png = decision.png_paths[0]
    return discord.File(str(png), filename=Path(png).name), tuple(lines)
