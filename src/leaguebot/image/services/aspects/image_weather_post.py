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

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from leaguebot.image.models.forecast_values import ForecastValues
from leaguebot.image.models.image_module import PostingOrigin

if TYPE_CHECKING:
    from leaguebot.image.services.aspects.image_weather_service import WeatherDrawing
    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import ImageRenderService

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
    from leaguebot.core.services.rounds.round_context import round_context
    from leaguebot.image.services.aspects.image_weather_service import resolve_drawing

    context = await round_context(db_path, round_id)
    if context is None:
        return None

    tier = context.division.tier
    return resolve_drawing(
        phase=phase,
        division_name=context.division.name,
        round_number=context.round_number,
        round_format=context.format,
        track_name=context.track_name,
        race_name=context.gp_name,
        country_name=context.country,
        values=values,
        division_tier=tier if tier else None,
        season_number=context.division.season_number,
    )


async def render_forecast(
    config_service: ImageConfigService,
    renderer: ImageRenderService,
    drawing,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> ForecastRender:
    """Render *drawing* to a PNG, or report why the textual forecast should stand instead.

    A fault in resolving the drawing's directories or in the render itself is raised, to the
    caller's catch-all around the drawing of the forecast (`ImageDrawing.attach_forecast`), which
    hands it back as a line.
    """
    from leaguebot.image.services.aspects.image_weather_service import build_fill_spec
    from leaguebot.image.services.render.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

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
