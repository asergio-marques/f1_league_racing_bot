"""Post the banner that heads a batch of verdicts (052).

**A banner is static** (Constitution XIV.17), on the same ground the verdict beside it is: it
draws a record of the moment a review closed, and a later change of the world does not
falsify it. It persists nothing, edits nothing and deletes nothing, and XIV.8's
delete-and-repost does not arise.

Three rules are this module's own.

**Every batch is headed, the aspect choosing only how** (decided 2026-09-21, issue #246,
reversing 2026-09-09). Where the aspect is on and the picture can be drawn the header is the
banner; where it is off, the module is off, the template is unusable or the render failed,
the header is ``heading_text`` (`leaguebot.image.models.verdict_banner_drawing`) posted as words. This is the fallback every other image aspect
makes — a picture replacing a text output, not adding one — and STW-VER-026 and STW-VER-027
require it: a batch of verdicts names the round it belongs to however the league is
configured.

It was once additive: nothing stood above a run of verdicts before the banner existed, so
with the aspect off the channel read as it always had. That left a league not using the
banner unable to tell where one batch ended and the next began, the heading existing in the
code but reachable only on a failed render. The banner is now a *picture of* the heading
rather than a thing of its own.

**A drawn banner's message carries no text**, as the calendar's and the two season-boundary
classifications' do not. The banner draws the season, the division and the round, and a
heading beside it would only repeat the picture. The cost is accepted and is real: a Discord
text search for "Round 8" will not find it, and the attachment's filename —
``season5_division1_round8_verdict_banner.png``, composed by ``leaguebot.image.utils.image_naming`` — is the
only handle a search has. A league that would rather have searchable headers switches the
aspect off and gets them in words, which is now a choice between two headers rather than
between a header and none.

**The picture is drawn for the caller and posted by it.** `ImageDrawing.draw_banner` hands back the
picture, or none where the text heading is to stand, with the lines saying what the drawing had to
report; it never raises a fault of the drawing and never posts. Both callers, a round's review and
`/attendance sync`, head their verdicts and cards with a job (`ANNOUNCE_HEADING`) that posts what it
is handed and stops the queue where Discord refuses it: a heading Discord refuses stops the queue until
it is retried or discarded, and what waits behind it goes out beneath none only once it is discarded.

**A header is an aid to reading a channel and is no record of a decision** (decided 2026-09-21): it
exists so a reader can tell at a glance which round the verdicts beneath it pertain to. Two things
follow, and a later reader is apt to reach for either. It carries no repost or amendment marking,
though a text heading now *could* carry one where a drawn banner could not: a re-headed batch
reads identically to the one it replaced, and that is intended. And a batch that goes unheaded is
a want of organisation, not a correctness defect, which is why a discarded heading is allowed to
let the verdicts through.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from leaguebot.image.models.image_module import PostingOrigin
from leaguebot.image.services.aspects.image_verdict_banner_service import VerdictBannerDrawing

if TYPE_CHECKING:
    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import ImageRenderService

log = logging.getLogger(__name__)

BANNER_ASPECT = "verdict_banner"
BANNER_TEMPLATE_KEY = "verdict_banner_template"

#: The toggle's name in `/images config toggle` ("Verdict banner") and what it draws, which heads
#: a fault's line.
BANNER_TOGGLE_NAME = "Verdict banner image"


@dataclass
class BannerRender:
    """What the image path produced for one banner.

    *png* is None wherever no picture is to be drawn: the render having failed. The caller need
    not know which. It says nothing about whether a header is posted: each of those cases heads
    the batch in words instead (#246).
    """

    png: Path | None = None
    notices: list = field(default_factory=list)
    problem: str | None = None

    @property
    def draws(self) -> bool:
        return self.png is not None


def fault(what: str, detail: str) -> str:
    """The line saying why the picture of *what* was not drawn, headed by the toggle's name."""
    return f"⚠️ {BANNER_TOGGLE_NAME} — {what}: {detail}"


def lines_of(what: str, render: BannerRender) -> tuple[str, ...]:
    """The lines a render's notices and problem make, naming *what* they pertain to."""
    from leaguebot.image.services.render.image_render_service import ImageRenderService

    lines: list[str] = []
    if render.notices:
        lines.append(ImageRenderService.format_notices(render.notices, subject=what))
    if render.problem:
        lines.append(fault(what, render.problem))
    return tuple(lines)


async def render_banner(
    config_service: ImageConfigService,
    renderer: ImageRenderService,
    drawing: VerdictBannerDrawing,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> BannerRender:
    """Render *drawing* to a PNG, or report why no banner is to be posted.

    A fault in resolving the directories or building the fill is raised, for the drawing to hand
    back as the line saying why the heading is in words.
    """
    from leaguebot.image.services.aspects.image_verdict_banner_service import build_fill_spec
    from leaguebot.image.services.render.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )
    from leaguebot.image.utils.image_naming import stem_for_drawing

    config = await config_service.get_config()
    directories, directory_faults = resolve_configured_directories(
        config,
        (("flag", "flag_directory"),),
        image_type=BANNER_TEMPLATE_KEY,
    )

    decision = await renderer.render_for_posting(
        BANNER_TEMPLATE_KEY,
        spec_builder_with_faults(build_fill_spec, drawing, directories, directory_faults),
        posting_origin=origin,
        division_name=drawing.division_name,
        filename_stem=stem_for_drawing(drawing, BANNER_TEMPLATE_KEY),
    )

    if not decision.posts_image:
        return BannerRender(
            problem=(decision.problem.detail if decision.problem else None),
            notices=decision.notices,
        )

    return BannerRender(png=decision.png_paths[0], notices=decision.notices)


def describe(*, division_name: str, round_number, season_number=None) -> str:
    """Name the subject a notice pertains to. A banner names no session and no driver."""
    parts = []
    if season_number is not None:
        parts.append(f"Season {season_number}")
    parts.append(str(division_name))
    parts.append(f"Round {round_number}")
    parts.append("Verdict banner")
    return " · ".join(parts)
