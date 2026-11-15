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

if TYPE_CHECKING:
    from leaguebot.image.services.aspects.image_weather_service import WeatherDrawing
    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import ImageRenderService

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


async def build_drawing_for_round(
    db_path: str, round_id: int, phase: int, *, values: ForecastValues
):
    """Load everything a weather graphic draws for *phase* of *round_id*.

    Reads and never computes. What the forecast says, the likelihood and each session's draw, is
    what the weather module hands in *values*; what this reads is the heading a graphic carries,
    which the textual forecast does not: the season number, the division's tier, the grand prix
    name and the country. Returns None where the round cannot be found at all.
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.image.services.aspects.image_weather_service import resolve_drawing

    async with get_connection(db_path) as db:
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
    config_service: ImageConfigService,
    renderer: ImageRenderService,
    drawing,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> ForecastRender:
    """Render *drawing* to a PNG, or report why the textual forecast should stand instead."""
    from leaguebot.image.services.aspects.image_weather_service import build_fill_spec
    from leaguebot.image.services.render.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    try:
        config = await config_service.get_config()
        directories, directory_faults = resolve_configured_directories(
            config,
            (
                ("flag", "flag_directory"),
                ("weather", "weather_icon_directory"),
            ),
            image_type=drawing.template_key,
        )

        from leaguebot.image.utils.image_naming import stem_for_drawing

        decision = await renderer.render_for_posting(
            drawing.template_key,
            spec_builder_with_faults(
                build_fill_spec, drawing, directories, directory_faults
            ),
            posting_origin=origin,
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


def describe(*, division_name: str, round_number, phase: int) -> str:
    """What a report names, so a manager can find the forecast it pertains to (FR-059).

    The round is named as a league reads it, "round 3 in **Pro**", as core names a round in the log
    channel.
    """
    return f"round {round_number} in **{division_name}**, {phase_name(phase)}"


def phase_name(phase: int) -> str:
    """What a line calls *phase*: phase 0 is the mystery round's notice, never a phase."""
    return "the mystery round notice" if phase == 0 else f"phase {phase}"


def named(drawing: WeatherDrawing, phase: int) -> str:
    """What a report names of *drawing*'s forecast: its round, as a league reads it, and its phase."""
    return describe(
        division_name=drawing.division_name,
        round_number=drawing.round_number,
        phase=0 if drawing.is_mystery or phase == 0 else phase,
    )


def fault(what: str, detail: str) -> str:
    """The line saying why the picture of *what* was not drawn."""
    return f"⚠️ Weather image — {what}: {detail}"


def lines_of(what: str, render: ForecastRender) -> tuple[str, ...]:
    """The lines a render's notices and problem make, naming *what* they pertain to."""
    from leaguebot.image.services.render.image_render_service import ImageRenderService

    lines: list[str] = []
    if render.notices:
        lines.append(ImageRenderService.format_notices(render.notices, subject=what))
    if render.problem:
        lines.append(fault(what, render.problem))
    return tuple(lines)
