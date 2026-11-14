"""Render a division's weather forecast as a graphic (042, US2/US3).

**This module renders and never posts.** The three phase services and the mystery notice
service own the posting lifecycle — the chain in which each phase's posting deletes its
predecessor's message, and the produce-before-destroy ordering that chain must observe
(Constitution XIV.8) — and this hands them a file to attach.

That is deliberate and it is the lesson 041 recorded: the image path *inherits* the ordering
rather than carrying a second implementation of it beside the textual one. Two orderings in one
flow drift, and the half left deleting first would be the fallback path — the one reached
because something has already gone wrong.

A failed render therefore needs no fallback machinery here: it returns no file, and the same
send site posts the textual forecast it would have posted anyway. What it hands back beside the
file is the lines saying why a picture that was due was not drawn, which the post job writes to
the log channel with its own save: this module posts nowhere, the log channel included.

**The graphic gates nothing** (XIV.7). Nothing in this module is reached until the phase has
computed its draw, persisted its result and written its calculation log. The picture is
downstream of every state change it depicts and is never a precondition of one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from leaguebot.image.models.forecast_values import ForecastValues
from leaguebot.image.models.image_module import PostingOrigin
from leaguebot.core.utils.league_bot import LeagueBot

if TYPE_CHECKING:
    import discord

log = logging.getLogger(__name__)

WEATHER_ASPECT = "weather"


@dataclass
class ForecastRender:
    """What the image path produced for one phase of one division.

    *png* is None wherever the textual forecast should be posted instead — the module off, the
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


async def weather_enabled(bot: LeagueBot, template_key: str) -> tuple[bool, str | None]:
    """Whether the graphic is to be drawn from *template_key*, and why not where it was due.

    True where the module is on, the ``weather`` aspect is on, and *template_key* is valid. The
    reason is None wherever no picture was due (the module or its weather aspect off) and
    otherwise says why a picture that was due is not drawn: the template's reason, or the fault
    that kept a switch from being read.

    The template is named per call because weather is drawn from six, and one being invalid
    must not stop the other five: a league whose sprint phase 3 file is too small still gets a
    picture for every round that is not a sprint (XIV.4, the unit of failure being one graphic).
    """
    try:
        if not await bot.module_service.is_images_enabled():
            return False, None
        toggles = await bot.image_config_service.get_toggles()
        if not toggles.get(WEATHER_ASPECT):
            return False, None
        reports = await bot.image_validity_service.template_reports()
        report = reports.get(template_key)
        if report is not None and report.valid:
            return True, None
        return False, (getattr(report, "reason", None) or f"{template_key} is not valid")
    except Exception as exc:  # noqa: BLE001 — never break a posting on this reader
        log.error("weather: enablement check failed: %s", exc, exc_info=True)
        return False, f"it could not be read whether a picture is due: {exc}"


async def build_drawing_for_round(
    bot: LeagueBot, round_id: int, phase: int, *, values: ForecastValues
):
    """Load everything a weather graphic draws for *phase* of *round_id*.

    Reads and never computes. What the forecast says, the likelihood and each session's draw, is
    what the weather module hands in *values*; what this reads is the heading a graphic carries,
    which the textual forecast does not: the season number, the division's tier, the grand prix
    name and the country. Returns None where the round cannot be found at all.
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.image.services.image_weather_service import resolve_drawing

    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT r.round_number, r.format, r.track_name, r.division_id, "
            "       d.name AS division_name, d.tier, "
            "       s.season_number, "
            "       t.gp_name, t.country "
            "FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id "
            "JOIN seasons s ON s.id = d.season_id "
            "LEFT JOIN tracks t ON t.name = r.track_name "
            "WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

    tier = row["tier"]
    return resolve_drawing(
        phase=phase,
        division_name=row["division_name"],
        round_number=row["round_number"],
        round_format=row["format"],
        track_name=row["track_name"],
        race_name=row["gp_name"],
        country_name=row["country"],
        values=values,
        division_tier=tier if tier else None,
        season_number=row["season_number"],
    )


async def render_forecast(
    bot: LeagueBot,
    drawing,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> ForecastRender:
    """Render *drawing* to a PNG, or report why the textual forecast should stand instead."""
    from leaguebot.image.services.image_weather_service import build_fill_spec
    from leaguebot.image.services.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    try:
        config = await bot.image_config_service.get_config()
        directories, directory_faults = resolve_configured_directories(
            config,
            (
                ("flag", "flag_directory"),
                ("weather", "weather_icon_directory"),
            ),
            image_type=drawing.template_key,
        )

        from leaguebot.image.utils.image_naming import stem_for_drawing

        decision = await bot.image_render_service.render_for_posting(
            drawing.template_key,
            spec_builder_with_faults(
                build_fill_spec, drawing, directories, directory_faults
            ),
            posting_origin=origin,
            bot=bot,
            division_name=drawing.division_name,
            filename_stem=stem_for_drawing(drawing),
        )
    except Exception as exc:  # noqa: BLE001 — a resolution fault, reported like any other
        log.error("weather: render failed: %s", exc, exc_info=True)
        return ForecastRender(problem=str(exc), rejects=origin is PostingOrigin.COMMANDED)

    if decision.rejects:
        return ForecastRender(
            problem=(decision.problem.detail if decision.problem else None),
            rejects=True,
            notices=decision.notices,
        )

    if not decision.posts_image:
        return ForecastRender(
            problem=(decision.problem.detail if decision.problem else None),
            notices=decision.notices,
        )

    return ForecastRender(png=decision.png_paths[0], notices=decision.notices)


def describe(*, division_name: str, round_number, phase: int, season_number=None) -> str:
    """What a report names, so a manager can find the forecast it pertains to (FR-059)."""
    parts = []
    if season_number is not None:
        parts.append(f"season {season_number}")
    parts.append(str(division_name))
    parts.append(f"round {round_number}")
    parts.append("mystery notice" if phase == 0 else f"phase {phase}")
    return ", ".join(parts)


async def attach_forecast(
    bot: LeagueBot, round_id: int, phase: int, *, values: ForecastValues
) -> tuple[discord.File | None, tuple[str, ...]]:
    """The ``discord.File`` a weather occasion rides on, or None to post the text, and the lines
    saying why a picture that was due was not drawn.

    The one entry point the weather post job calls, handing the *values* the forecast's words
    were composed as. Every reason a graphic is not drawn — the module off, the aspect off, the
    template invalid, the round unreadable, the render failed — hands back no file, and the caller
    posts the textual forecast it was going to post anyway (XIV.7).

    A picture that was not due (the module or its weather aspect off) says nothing. Every other
    reason, and every notice the render gave, is handed back as a line for the log channel, which
    the caller writes in its own save, naming the forecast it pertains to (FR-059).

    The one catch-all here is around the drawing, a graphic riding on a post: a fault in any of
    it is a line, never a break in the forecast.
    """
    import discord

    what = f"phase {phase}"
    try:
        drawing = await build_drawing_for_round(bot, round_id, phase, values=values)
        if drawing is None:
            return None, ()

        what = describe(
            division_name=drawing.division_name,
            round_number=drawing.round_number,
            phase=0 if drawing.is_mystery else phase,
            season_number=drawing.season_number,
        )
        due, why = await weather_enabled(bot, drawing.template_key)
        if not due:
            return None, () if why is None else (_fault(what, why),)

        render = await render_forecast(bot, drawing)
        lines = _lines_of(what, render)
        if render.png is None:  # `render.draws`, in a form the type check can follow
            return None, lines

        return discord.File(str(render.png), filename=Path(render.png).name), lines
    except Exception as exc:  # noqa: BLE001 — a graphic never breaks a forecast
        log.error("weather: could not attach a graphic to round %s: %s", round_id, exc, exc_info=True)
        return None, (_fault(what, str(exc)),)


def _fault(what: str, detail: str) -> str:
    """The line saying why the picture of *what* was not drawn."""
    return f"⚠️ Weather image — {what}: {detail}"


def _lines_of(what: str, render: ForecastRender) -> tuple[str, ...]:
    """The lines a render's notices and problem make, naming *what* they pertain to."""
    from leaguebot.image.services.image_render_service import ImageRenderService

    lines: list[str] = []
    if render.notices:
        lines.append(ImageRenderService.format_notices(render.notices, subject=what))
    if render.problem:
        lines.append(_fault(what, render.problem))
    return tuple(lines)
