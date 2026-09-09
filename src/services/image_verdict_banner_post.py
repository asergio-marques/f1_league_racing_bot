"""Post the banner that heads a batch of verdicts (052).

**A banner is static** (Constitution XIV.17), on the same ground the verdict beside it is: it
draws a record of the moment a review closed, and a later change of the world does not
falsify it. It persists nothing, edits nothing and deletes nothing, and XIV.8's
delete-and-repost does not arise.

Three rules are this module's own.

**It is additive.** Nothing stood above a run of verdicts before it, so with the aspect off
the verdicts channel reads exactly as it always has. Enabling it adds a header; it takes
nothing away.

**Its message carries no text** (decided 2026-09-09), as the calendar's and the two
season-boundary classifications' do not. The banner draws the season, the division and the
round, and a heading above it would only repeat the picture. The cost is accepted and is
real: a Discord text search for "Round 8" will not find it, and the attachment's filename —
``season5_division1_round8_verdict_banner.png``, composed by ``utils.image_naming`` — is the
only handle a search has. The trade was made knowingly rather than overlooked.

**A banner never costs a verdict.** It is rendered and posted before the first card of the
batch, and every failure on that path is swallowed: the cards follow whether the header
arrived or not. Where the render fails but the aspect is on, the plain heading is posted as
text instead, so a batch is still identified when the picture cannot be drawn.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from models.image_module import PostingOrigin
from services.image_verdict_banner_service import VerdictBannerDrawing

log = logging.getLogger(__name__)

BANNER_ASPECT = "verdict_banner"
BANNER_TEMPLATE_KEY = "verdict_banner_template"


@dataclass
class BannerRender:
    """What the image path produced for one banner.

    *png* is None wherever no picture is to be posted — the module off, the aspect off, the
    template invalid, or the render having failed. The caller need not know which.
    """

    png: Path | None = None
    notices: list = field(default_factory=list)
    problem: str | None = None

    @property
    def draws(self) -> bool:
        return self.png is not None


async def banner_enabled(bot, server_id: int) -> bool:
    """True where the module is on, the aspect is on, and the template is valid.

    Read separately from `verdicts_enabled`: the two aspects are independent, so a league
    may draw cards without banners or banners without cards, and an unusable banner file
    cannot stop a verdict being posted.
    """
    try:
        if not await bot.module_service.is_images_enabled(server_id):
            return False
        toggles = await bot.image_config_service.get_toggles(server_id)
        if not toggles.get(BANNER_ASPECT):
            return False
        reports = await bot.image_validity_service.template_reports(server_id)
        report = reports.get(BANNER_TEMPLATE_KEY)
        return report is not None and report.valid
    except Exception as exc:  # noqa: BLE001 — never break a posting on this reader
        log.error("verdict banner: enablement check failed for server %s: %s", server_id, exc)
        return False


def build_drawing(
    *,
    season_number,
    division_name: str,
    division_tier,
    round_number,
    race_name: str | None,
    country_name: str | None,
) -> VerdictBannerDrawing:
    """The drawing, from the context the announcement service already loaded.

    Takes no database of its own. `_get_announcement_context` reads the round once for the
    channel and the division, and joins `tracks` in the same query, so the banner adds no
    read to a path that runs on every review.
    """
    from services.image_verdict_banner_service import resolve_drawing

    return resolve_drawing(
        division_name=division_name,
        round_number=round_number,
        season_number=season_number,
        division_tier=division_tier,
        race_name=race_name,
        country_name=country_name,
    )


async def render_banner(
    bot,
    server_id: int,
    drawing: VerdictBannerDrawing,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> BannerRender:
    """Render *drawing* to a PNG, or report why no banner is to be posted."""
    from services.image_verdict_banner_service import build_fill_spec
    from services.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    try:
        config = await bot.image_config_service.get_config(server_id)
        directories, directory_faults = resolve_configured_directories(
            config,
            (("flag", "flag_directory"),),
            image_type=BANNER_TEMPLATE_KEY,
        )

        from utils.image_naming import stem_for_drawing

        decision = await bot.image_render_service.render_for_posting(
            server_id,
            BANNER_TEMPLATE_KEY,
            spec_builder_with_faults(
                build_fill_spec, drawing, directories, directory_faults
            ),
            posting_origin=origin,
            bot=bot,
            division_name=drawing.division_name,
            filename_stem=stem_for_drawing(drawing, BANNER_TEMPLATE_KEY),
        )
    except Exception as exc:  # noqa: BLE001 — a resolution fault, reported like any other
        log.error("verdict banner: render failed for server %s: %s", server_id, exc)
        return BannerRender(problem=str(exc))

    if not decision.posts_image:
        return BannerRender(
            problem=(decision.problem.detail if decision.problem else None),
            notices=decision.notices,
        )

    return BannerRender(png=decision.png_paths[0], notices=decision.notices)


def heading_text(drawing: VerdictBannerDrawing) -> str:
    """The heading the banner draws, as message text.

    Posted only where the aspect is on and the render failed. It is deliberately not the
    ordinary case: a banner that arrived says all of this already, and a heading above it
    would repeat the picture.
    """
    season = f"Season {drawing.season_number} " if drawing.season_number is not None else ""
    return f"**{season}{drawing.division_name} Round {drawing.round_number}**"


def discard(render, attachment=None) -> None:
    """Delete the banner once its posting has been attempted.

    Here rather than in the caller because a source module reaches the image module through
    its own posting façade and never through the render service — the boundary
    ``test_no_source_module_posting_path_imports_the_render_service`` exists to hold.
    """
    from services.image_render_service import discard_attachment, discard_render

    if attachment is not None:
        discard_attachment(attachment)
    discard_render(getattr(render, "png", None))


def describe(*, division_name: str, round_number, season_number=None) -> str:
    """Name the subject a notice pertains to. A banner names no session and no driver."""
    parts = []
    if season_number is not None:
        parts.append(f"Season {season_number}")
    parts.append(str(division_name))
    parts.append(f"Round {round_number}")
    parts.append("Verdict banner")
    return " · ".join(parts)


async def report(bot, server_id: int, what: str, detail: str) -> None:
    """Report a fault to the server's logging channel, never to a verdicts channel."""
    from services.image_results_post import report as _report

    await _report(bot, server_id, what, detail)


async def report_notices(bot, server_id: int, what: str, notices) -> None:
    """Report non-fatal degradations to the logging channel (XIV.4)."""
    from services.image_results_post import report_notices as _report_notices

    await _report_notices(bot, server_id, what, notices)


async def try_post(bot, channel, server_id: int, drawing: VerdictBannerDrawing) -> bool:
    """Post the banner above a batch of verdicts. Returns whether anything was posted.

    Never raises. A banner is a header, and a header failing must not cost a league the
    decisions it heads, so every fault on this path is logged and swallowed.
    """
    import discord

    if not await banner_enabled(bot, server_id):
        return False

    subject = describe(
        division_name=drawing.division_name,
        round_number=drawing.round_number,
        season_number=drawing.season_number,
    )

    render = None
    attachment = None
    try:
        render = await render_banner(bot, server_id, drawing)

        if render.notices:
            await report_notices(bot, server_id, subject, render.notices)

        if not render.draws:
            if render.problem:
                await report(bot, server_id, subject, render.problem)
            #  The aspect is on and the picture could not be drawn, so the batch is headed
            #  in words rather than not at all.
            await channel.send(heading_text(drawing))
            return True

        attachment = discord.File(str(render.png), filename=render.png.name)
        await channel.send(file=attachment)
        return True
    except Exception:
        log.exception("verdict banner: could not post for server %s", server_id)
        return False
    finally:
        if render is not None:
            discard(render, attachment)
