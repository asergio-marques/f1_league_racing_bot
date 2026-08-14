"""Render a division's weather forecast as a graphic (042, US2/US3).

**This module renders and never posts.** The three phase services and the mystery notice
service own the posting lifecycle — the chain in which each phase's posting deletes its
predecessor's message, and the produce-before-destroy ordering that chain must observe
(Constitution XIV.8) — and this hands them a file to attach.

That is deliberate and it is the lesson 041 recorded: the image path *inherits* the ordering
rather than carrying a second implementation of it beside the textual one. Two orderings in one
flow drift, and the half left deleting first would be the fallback path — the one reached
because something has already gone wrong.

A failed render therefore needs no fallback machinery here: it returns ``None``, and the same
send site posts the textual forecast it would have posted anyway.

**The graphic gates nothing** (XIV.7). Nothing in this module is reached until the phase has
computed its draw, persisted its result and written its calculation log. The picture is
downstream of every state change it depicts and is never a precondition of one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from models.image_module import PostingOrigin

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


async def weather_enabled(bot, server_id: int, template_key: str) -> bool:
    """True where the module is on, the ``weather`` aspect is on, and *template_key* is valid.

    The template is named per call because weather is drawn from six, and one being invalid
    must not stop the other five: a league whose sprint phase 3 file is too small still gets a
    picture for every round that is not a sprint (XIV.4, the unit of failure being one graphic).
    """
    try:
        if not await bot.module_service.is_images_enabled(server_id):
            return False
        toggles = await bot.image_config_service.get_toggles(server_id)
        if not toggles.get(WEATHER_ASPECT):
            return False
        reports = await bot.image_validity_service.template_reports(server_id)
        report = reports.get(template_key)
        return report is not None and report.valid
    except Exception as exc:  # noqa: BLE001 — never break a posting on this reader
        log.error("weather: enablement check failed for server %s: %s", server_id, exc)
        return False


async def build_drawing_for_round(bot, round_id: int, phase: int):
    """Load everything a weather graphic draws for *phase* of *round_id*.

    Reads and never computes. The likelihood is the one phase 1 persisted, the type on each
    session is the one phase 2 drew, and the sequence is the one phase 3 drew (Principle IV).
    Returns None where the round cannot be found at all.

    The phase services select only what their *message* needs, so the heading data a graphic
    carries — the season number, the division's tier, the grand prix name and the country — is
    loaded here rather than by widening four queries the textual path does not need widened.
    """
    import json

    from db.database import get_connection
    from services.image_weather_service import resolve_drawing

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

        sessions: list[dict] = []
        if phase in (2, 3):
            session_cursor = await db.execute(
                "SELECT session_type, phase2_slot_type, phase3_slots "
                "FROM sessions WHERE round_id = ? ORDER BY id",
                (round_id,),
            )
            for entry in await session_cursor.fetchall():
                slots = []
                if phase == 3 and entry["phase3_slots"]:
                    try:
                        slots = list(json.loads(entry["phase3_slots"]) or [])
                    except (TypeError, ValueError):
                        slots = []
                sessions.append(
                    {
                        "session_type": entry["session_type"],
                        "slot_type": entry["phase2_slot_type"],
                        "slots": slots,
                    }
                )

        # The likelihood phase 1 computed and stored. Phases 2 and 3 carry that same value
        # though neither of their messages does — XIV.7 (v4.7.0) admitting a value the text
        # path published in another message of the same flow.
        rain = None
        rain_cursor = await db.execute(
            "SELECT payload FROM phase_results "
            "WHERE round_id = ? AND phase_number = 1 AND status = 'ACTIVE' "
            "ORDER BY id DESC LIMIT 1",
            (round_id,),
        )
        rain_row = await rain_cursor.fetchone()
        if rain_row is not None:
            try:
                payload = json.loads(rain_row["payload"]) or {}
                for key in ("rpc", "Rpc", "rain_probability"):
                    if key in payload:
                        rain = float(payload[key])
                        break
            except (TypeError, ValueError):
                rain = None

    tier = row["tier"]
    return resolve_drawing(
        phase=phase,
        division_name=row["division_name"],
        round_number=row["round_number"],
        round_format=row["format"],
        track_name=row["track_name"],
        race_name=row["gp_name"],
        country_name=row["country"],
        rain_probability=rain,
        sessions=sessions,
        division_tier=tier if tier else None,
        season_number=row["season_number"],
    )


async def render_forecast(
    bot,
    server_id: int,
    drawing,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> ForecastRender:
    """Render *drawing* to a PNG, or report why the textual forecast should stand instead."""
    from services.image_weather_service import build_fill_spec
    from utils.paths import resolve_within_project_root

    try:
        config = await bot.image_config_service.get_config(server_id)
        directories: dict[str, Path] = {}
        for asset_class, column in (
            ("track", "track_image_directory"),
            ("weather", "weather_icon_directory"),
        ):
            try:
                directories[asset_class] = resolve_within_project_root(
                    getattr(config, column)
                )
            except Exception:  # noqa: BLE001
                pass

        decision = await bot.image_render_service.render_for_posting(
            server_id,
            drawing.template_key,
            lambda root: build_fill_spec(drawing, root, asset_directories=directories),
            posting_origin=origin,
            bot=bot,
        )
    except Exception as exc:  # noqa: BLE001 — a resolution fault, reported like any other
        log.error("weather: render failed for server %s: %s", server_id, exc)
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


async def report(bot, server_id: int, what: str, detail: str) -> None:
    """Report a fault to the server's logging channel, never to a forecast channel."""
    from services.image_results_post import report as _report

    await _report(bot, server_id, what, detail)


async def report_notices(bot, server_id: int, what: str, notices) -> None:
    """Report non-fatal degradations to the logging channel (XIV.4, FR-059)."""
    from services.image_results_post import report_notices as _report_notices

    await _report_notices(bot, server_id, what, notices)


async def attach_forecast(bot, round_id: int, phase: int, server_id: int):
    """The ``discord.File`` a weather occasion rides on, or None to post the text.

    The one entry point the phase services and the mystery notice service call. Every reason a
    graphic is not drawn — the module off, the aspect off, the template invalid, the round
    unreadable, the render failed — returns None, and the caller posts the textual forecast it
    was going to post anyway (XIV.7).

    Notices are reported here rather than by the caller, so the four occasions do not each
    carry a copy (FR-059).
    """
    import discord

    from services.image_weather_service import weather_template_key

    try:
        drawing = await build_drawing_for_round(bot, round_id, phase)
        if drawing is None:
            return None

        if not await weather_enabled(bot, server_id, drawing.template_key):
            return None

        render = await render_forecast(bot, server_id, drawing)

        what = describe(
            division_name=drawing.division_name,
            round_number=drawing.round_number,
            phase=0 if drawing.is_mystery else phase,
            season_number=drawing.season_number,
        )
        if render.notices:
            await report_notices(bot, server_id, what, render.notices)
        if render.problem:
            await report(bot, server_id, what, render.problem)
        if not render.draws:
            return None

        return discord.File(str(render.png), filename=f"weather_{phase}.png")
    except Exception as exc:  # noqa: BLE001 — a graphic never breaks a forecast
        log.error("weather: could not attach a graphic to round %s: %s", round_id, exc)
        return None
