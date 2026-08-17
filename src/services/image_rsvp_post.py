"""Attach the check-in call graphic (041, US3).

**This module has exactly one caller**, and that is the whole of its design. ``try_attach`` is
reached from the initial post in ``rsvp_service.run_rsvp_notice`` and from nowhere else: not
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

import logging
from pathlib import Path

log = logging.getLogger(__name__)

RSVP_ASPECT = "rsvp"
RSVP_TEMPLATE_KEY = "rsvp_template"


async def rsvp_enabled(bot, server_id: int) -> bool:
    """True where the module is on, the ``rsvp`` aspect is on, and the template is valid."""
    try:
        if not await bot.module_service.is_images_enabled(server_id):
            return False
        toggles = await bot.image_config_service.get_toggles(server_id)
        if not toggles.get(RSVP_ASPECT):
            return False
        reports = await bot.image_validity_service.template_reports(server_id)
        report = reports.get(RSVP_TEMPLATE_KEY)
        return report is not None and report.valid
    except Exception as exc:  # noqa: BLE001 — never break a posting on this reader
        log.error("rsvp: enablement check failed for server %s: %s", server_id, exc)
        return False


async def try_attach(
    bot,
    server_id: int,
    *,
    division_name: str,
    round_number,
    round_format,
    scheduled_at,
    track_name: str | None,
    race_name: str | None = None,
    country_name: str | None = None,
    season_number=None,
    division_tier=None,
    deadline_hours: int | None = None,
):
    """The graphic to attach to a check-in call, or None to post the call without one.

    Returns ``None`` for every reason a call might carry no picture — the module off, the
    aspect off, the template invalid, the render failed — because the answer to all four is the
    same: post the call exactly as the textual flow composes it.

    **Nothing raised here escapes.** A graphic must never prevent the call from being posted,
    nor the round's attendance rows from being opened (XIV.7). Those rows are the record every
    later penalty is computed from, and a template typo must not cost a league one.
    """
    if bot is None:
        return None

    try:
        import discord

        from models.image_module import PostingOrigin
        from services.attendance_service import derive_checkin_deadline
        from services.image_rsvp_service import build_fill_spec, resolve_drawing
        from utils.paths import resolve_within_project_root

        if not await rsvp_enabled(bot, server_id):
            return None

        config = await bot.image_config_service.get_config(server_id)

        deadline_at = None
        if deadline_hours is not None and scheduled_at is not None:
            deadline_at = derive_checkin_deadline(scheduled_at, deadline_hours)

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

        # The check-in graphic may draw both imagery classes, so both directories are
        # resolved (044). Each answers its own miss with its own fallback.
        directories: dict[str, Path] = {}
        for asset_class, column in (
            ("track", "track_image_directory"),
            ("flag", "flag_directory"),
        ):
            try:
                directories[asset_class] = resolve_within_project_root(
                    getattr(config, column)
                )
            except Exception:  # noqa: BLE001
                pass

        decision = await bot.image_render_service.render_for_posting(
            server_id,
            RSVP_TEMPLATE_KEY,
            lambda root: build_fill_spec(drawing, root, asset_directories=directories),
            posting_origin=PostingOrigin.SCHEDULED,
            bot=bot,
        )

        label = f"{division_name} — check-in for round {round_number}"
        if decision.notices:
            await _report_notices(bot, server_id, label, decision.notices)
        if not decision.posts_image:
            if decision.problem is not None:
                await _report(bot, server_id, label, decision.problem.detail)
            return None

        return discord.File(str(decision.png_paths[0]), filename="checkin.png")
    except Exception as exc:  # noqa: BLE001 — the call must post whatever happens here
        log.error(
            "rsvp: the check-in graphic could not be drawn for server %s: %s",
            server_id, exc,
        )
        return None


async def _report(bot, server_id: int, what: str, detail: str) -> None:
    from services.image_results_post import report

    await report(bot, server_id, what, detail)


async def _report_notices(bot, server_id: int, what: str, notices) -> None:
    from services.image_results_post import report_notices

    await report_notices(bot, server_id, what, notices)
