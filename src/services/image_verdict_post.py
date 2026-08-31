"""Post one verdict as a graphic (043, US2 and US3).

**A verdict is static** (Constitution XIV.17), on the second ground that rule admits: it draws
a **record of a decision taken**, not a view of a state. Every value was settled at the moment
the decision was issued, and a later change of the world does not falsify it — a driver
renaming their Discord account does not make the verdict wrong. A correction arrives as a
verdict of its own, beside the first.

Two consequences shape this module:

* it **persists nothing**. No message id is recorded, nothing is edited, replaced or deleted,
  and XIV.8's delete-and-repost does not arise in any form. There is no state to reconcile on
  the fallback path either — a failed render simply leaves the textual announcement to be sent
  at the same point.
* the graphic **displaces the whole announcement but the mention** (XIV.7). Heading, driver
  line, sanction, description and justification all move onto the canvas; the message keeps the
  mention alone, a mention being the one thing a picture cannot carry (XIV.16).

**A verdict never gates a sanction.** Nothing here is reached before the review is finalised or
the sanction enforced. The graphic is downstream of every state change it depicts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from db.database import get_connection
from models.image_module import PostingOrigin
from services.image_verdict_service import VerdictDrawing, VerdictKind

log = logging.getLogger(__name__)

VERDICTS_ASPECT = "verdicts"
VERDICTS_TEMPLATE_KEY = "verdicts_template"


@dataclass
class VerdictRender:
    """What the image path produced for one verdict.

    *png* is None wherever the textual announcement should be posted instead — the module off,
    the aspect off, the template invalid, or the render having failed. The caller need not know
    which: every one means "post the text you were going to post anyway".
    """

    png: Path | None = None
    notices: list = field(default_factory=list)
    problem: str | None = None
    rejects: bool = False

    @property
    def draws(self) -> bool:
        return self.png is not None


async def verdicts_enabled(bot, server_id: int) -> bool:
    """True where the module is on, the ``verdicts`` aspect is on, and the template is valid."""
    try:
        if not await bot.module_service.is_images_enabled(server_id):
            return False
        toggles = await bot.image_config_service.get_toggles(server_id)
        if not toggles.get(VERDICTS_ASPECT):
            return False
        reports = await bot.image_validity_service.template_reports(server_id)
        report = reports.get(VERDICTS_TEMPLATE_KEY)
        return report is not None and report.valid
    except Exception as exc:  # noqa: BLE001 — never break a posting on this reader
        log.error("verdicts: enablement check failed for server %s: %s", server_id, exc)
        return False


async def _round_context(db_path: str, round_id: int) -> dict:
    """The tier, format and grand prix name of a round — what the announcement omits.

    Read exactly as the weather graphic reads them, so the two cannot disagree about what a
    round is called.
    """
    try:
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                """
                SELECT d.tier AS division_tier, r.format AS round_format,
                       t.gp_name AS race_name
                FROM rounds r
                JOIN divisions d ON d.id = r.division_id
                LEFT JOIN tracks t ON t.name = r.track_name
                WHERE r.id = ?
                """,
                (round_id,),
            )
            row = await cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 — an optional field is not worth a failed render
        log.warning("verdicts: round context unreadable for round %s: %s", round_id, exc)
        return {}
    if row is None:
        return {}
    return {
        "division_tier": row["division_tier"],
        "round_format": row["round_format"],
        "race_name": row["race_name"],
    }


async def _driver_nationality(
    db_path: str, server_id: int, discord_user_id: int
) -> str | None:
    """The nationality recorded for the driver — the datum a flag is resolved from.

    ``signup_records`` is keyed by (server_id, discord_user_id) and carries no
    driver_profile_id. This joined that phantom column, so the query raised on every
    verdict and the ``except`` returned None: no verdict graphic had ever drawn a driver
    flag. It now joins as the other posting paths join.

    A mock driver has no signup record to hold a nationality and carries its own, read by
    the same branch on is_test_driver the name is.
    """
    try:
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                """
                SELECT CASE WHEN dp.is_test_driver = 1 THEN dp.test_nationality
                            ELSE sr.nationality END AS nationality
                FROM driver_profiles dp
                LEFT JOIN signup_records sr
                       ON sr.server_id = dp.server_id
                      AND sr.discord_user_id = CAST(dp.discord_user_id AS TEXT)
                WHERE dp.server_id = ?
                  AND CAST(dp.discord_user_id AS INTEGER) = ?
                ORDER BY dp.id DESC LIMIT 1
                """,
                (server_id, discord_user_id),
            )
            row = await cursor.fetchone()
    except Exception:  # noqa: BLE001
        return None
    return (row["nationality"] or None) if row else None


async def team_name_for_entry(
    bot, guild, *, server_id: int, division_id: int, role_id: int | None
) -> str | None:
    """The team whose car the driver drove, resolved as the results graphic resolves it.

    The session records the Discord **role** an entry drove for; the division's team holding
    that role is what the graphic names, and what the badge is looked up by. Where the division
    holds no such team the role's own name stands in, which is what the textual table does.

    For a reserve standing in for another driver this is the team whose car they drove and
    never the reserve team, because the role is the one the *result* records.
    """
    if role_id is None:
        return None
    try:
        from services.image_results_post import _team_names

        names = await _team_names(bot, guild, server_id, division_id, [int(role_id)])
    except Exception as exc:  # noqa: BLE001 — an optional field is not worth a failed render
        log.warning("verdicts: team name unreadable for role %s: %s", role_id, exc)
        return None
    return names.get(int(role_id))


async def build_drawing(
    bot,
    *,
    db_path: str,
    round_id: int,
    kind: VerdictKind,
    server_id: int,
    season_number,
    division_name: str,
    round_number,
    session_label: str | None,
    driver_name: str,
    driver_discord_id: int,
    penalty_description: str,
    description_text: str,
    justification_text: str,
    team_name: str | None = None,
) -> VerdictDrawing:
    """Resolve one verdict into the values its graphic draws.

    Everything here is *read*. Nothing is computed and nothing decided: the sanction is the
    rendering the announcement service produced, the session label is the one it used, and the
    name is resolved by the chain every graphic of the module resolves a person by.
    """
    from services.image_verdict_service import resolve_mentions

    context = await _round_context(db_path, round_id)
    nationality = await _driver_nationality(db_path, server_id, driver_discord_id)

    # Whether the league collects nationality at all. A league that switched it off draws no
    # flag and is told nothing (XIV.4's configured absence); one that collects it and holds
    # none for this driver has an ordinary emptied optional field, and is told.
    from services.image_results_post import _nationality_collected

    collected = await _nationality_collected(db_path, server_id)

    # A mention a person wrote into free text is resolved in place to the name it addresses;
    # the graphic mentions nobody (XIV.16, v4.8.0).
    def _name_for(_user_id: str) -> str:
        return driver_name

    race_name = context.get("race_name")
    if str(context.get("round_format") or "").upper() == "MYSTERY":
        race_name = "Mystery GP"

    return VerdictDrawing(
        kind=kind,
        season_number=season_number,
        division_name=division_name,
        division_tier=context.get("division_tier"),
        round_number=round_number,
        race_name=race_name,
        session_name=session_label if kind is not VerdictKind.ATTENDANCE_SANCTION else None,
        driver_name=driver_name,
        driver_nationality=nationality,
        team_name=team_name if kind is not VerdictKind.ATTENDANCE_SANCTION else None,
        penalty=penalty_description,
        description=resolve_mentions(description_text, _name_for),
        justification=resolve_mentions(justification_text, _name_for),
        nationality_collected=collected,
    )


async def render_verdict(
    bot,
    server_id: int,
    drawing: VerdictDrawing,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> VerdictRender:
    """Render *drawing* to a PNG, or report why the textual announcement should stand."""
    from services.image_verdict_service import build_fill_spec
    from services.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    try:
        config = await bot.image_config_service.get_config(server_id)
        directories, directory_faults = resolve_configured_directories(
            config,
            (
                ("flag", "flag_directory"),
                ("team", "team_image_directory"),
            ),
            image_type=VERDICTS_TEMPLATE_KEY,
        )

        from utils.image_naming import stem_for_drawing

        decision = await bot.image_render_service.render_for_posting(
            server_id,
            VERDICTS_TEMPLATE_KEY,
            spec_builder_with_faults(
                build_fill_spec, drawing, directories, directory_faults
            ),
            posting_origin=origin,
            bot=bot,
            filename_stem=stem_for_drawing(drawing, VERDICTS_TEMPLATE_KEY),
        )
    except Exception as exc:  # noqa: BLE001 — a resolution fault, reported like any other
        log.error("verdicts: render failed for server %s: %s", server_id, exc)
        return VerdictRender(problem=str(exc), rejects=origin is PostingOrigin.COMMANDED)

    if decision.rejects:
        return VerdictRender(
            problem=(decision.problem.detail if decision.problem else None),
            rejects=True,
            notices=decision.notices,
        )

    if not decision.posts_image:
        return VerdictRender(
            problem=(decision.problem.detail if decision.problem else None),
            notices=decision.notices,
        )

    return VerdictRender(png=decision.png_paths[0], notices=decision.notices)


def discard(render, attachment=None) -> None:
    """Delete the verdict graphic once its announcement has been attempted.

    The companion to :func:`render_verdict`, and here rather than in the caller because a
    source module reaches the image module through its own posting façade and never
    through the render service — the boundary
    ``test_no_source_module_posting_path_imports_the_render_service`` exists to hold.

    *attachment* is given where one was built, so that its handle is closed before the
    file is removed rather than trusting the send to have closed it. The path is swept
    afterwards regardless, for the case where the render succeeded and the attachment was
    never built.
    """
    from services.image_render_service import discard_attachment, discard_render

    if attachment is not None:
        discard_attachment(attachment)
    discard_render(getattr(render, "png", None))


def describe(
    *,
    division_name: str,
    round_number,
    session_name: str | None,
    driver_name: str,
    season_number=None,
) -> str:
    """Name the subject a notice pertains to: season, division, round, session and driver."""
    parts = []
    if season_number is not None:
        parts.append(f"Season {season_number}")
    parts.append(str(division_name))
    parts.append(f"Round {round_number}")
    parts.append(str(session_name) if session_name else "Attendance Sanction")
    parts.append(str(driver_name))
    return " · ".join(parts)


async def report(bot, server_id: int, what: str, detail: str) -> None:
    """Report a fault to the server's logging channel, never to a verdicts channel."""
    from services.image_results_post import report as _report

    await _report(bot, server_id, what, detail)


async def report_notices(bot, server_id: int, what: str, notices) -> None:
    """Report non-fatal degradations to the logging channel (XIV.4)."""
    from services.image_results_post import report_notices as _report_notices

    await _report_notices(bot, server_id, what, notices)
