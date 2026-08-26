"""Post one session's results as a graphic, or stand aside (039).

**One decision in one place**, as 038's lineup posting is. Every occasion that reposts a
session's textual table reaches ``results_post_service.post_session_results``, so hooking
there covers all six of them — first provisional posting, penalty phase closed, appeal
phase closed, resynchronised by command, amendment approved, and a points-configuration
change causing recalculation — with one branch and no reachability argument.

The shape the caller uses:

    outcome = await try_post(bot, guild, channel, ...)
    if outcome.applicable:
        return outcome.message_id      # the graphic was posted, or the caller was told why
    ...existing textual body, unchanged...

**The textual path is not reformed by this module.** Where the image flow does not run —
no bot in scope, the module disabled, the `results` toggle off, no valid template — the
caller's existing body runs exactly as it did before this feature. The graphic is an
alternative output beside the text, never a replacement for it (Constitution XIV.7).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import discord

from db.database import get_connection
from models.image_module import PostingOrigin

log = logging.getLogger(__name__)

#: The image flow does not apply — the caller falls through to its textual body.
NOT_APPLICABLE = "NOT_APPLICABLE"
#: The graphic was produced and posted; the caller does nothing further.
POSTED = "POSTED"
#: The graphic could not be produced and the posting was **commanded**, so nothing is
#: posted and the caller is told what is at fault.
REJECTED = "REJECTED"


@dataclass
class ResultsPostOutcome:
    action: str = NOT_APPLICABLE
    message: str | None = None
    message_id: int | None = None
    notices: list = field(default_factory=list)
    png_path: Path | None = None

    @property
    def applicable(self) -> bool:
        """True where this module handled the posting and the caller must not."""
        return self.action != NOT_APPLICABLE


async def results_enabled(bot, server_id: int, template_key: str) -> bool:
    """True where the module is on, the `results` aspect is on, and *template_key* is valid.

    The aspect is what a league toggles; the two templates behind it are checked one at a
    time, so a sound qualifying template still draws while a faulty race template falls back
    to text (XIV.4 — the unit of failure is one graphic).
    """
    try:
        if not await bot.module_service.is_images_enabled(server_id):
            return False
        toggles = await bot.image_config_service.get_toggles(server_id)
        if not toggles.get("results"):
            return False
        reports = await bot.image_validity_service.template_reports(server_id)
        report = reports.get(template_key)
        return report is not None and report.valid
    except Exception as exc:  # noqa: BLE001 — never break a posting on this reader
        log.error("results: enablement check failed for server %s: %s", server_id, exc)
        return False


async def _nationality_collected(db_path: str, server_id: int) -> bool:
    """Whether the league collects a driver's nationality at all.

    Where it does not, a graphic with no flags is exactly what was configured and raises
    nothing (XIV.4's configured absence). Where it does, a driver who stated none is an
    ordinary emptied optional field and reports as one.

    While test mode is active the test-mode switch stands in for the signup one, so a
    maintainer may see the graphics of a server under test both with flags and without
    them without disturbing the setting real signups run on.

    This is the module's single reader of the switch. The lineup and the preview call it
    rather than each carrying a copy: it read the wrong table in two of the three copies
    until 2026-08-18, and one place to be wrong is enough.
    """
    try:
        async with get_connection(db_path) as db:
            config = await (
                await db.execute(
                    "SELECT test_mode_active, test_mode_nationality_required "
                    "FROM server_configs WHERE server_id = ?",
                    (server_id,),
                )
            ).fetchone()
            if config is not None and config["test_mode_active"]:
                return bool(config["test_mode_nationality_required"])

            row = await (
                await db.execute(
                    "SELECT nationality_required FROM signup_module_settings "
                    "WHERE server_id = ?",
                    (server_id,),
                )
            ).fetchone()
    except Exception:  # noqa: BLE001 — an unreadable switch is not a reason to fail a render
        return True
    if row is None:
        return True
    return bool(row["nationality_required"])


async def _driver_names(bot, guild, user_ids: list[int]) -> dict[int, str]:
    """The name each driver is drawn under (XIV.16, and the wip-spec's person-name rule).

    The display name of their Discord account on the server at the moment of generation,
    falling through the names the league recorded for them, and ending at the user id. A
    graphic carries no mention, so this is what stands in its place.
    """
    names: dict[int, str] = {}

    recorded: dict[int, tuple] = {}
    if user_ids:
        placeholders = ",".join("?" * len(user_ids))
        async with get_connection(bot.db_path) as db:
            rows = await (
                await db.execute(
                    f"SELECT dp.discord_user_id, dp.is_test_driver, dp.test_display_name, "
                    f"       sr.server_display_name, sr.discord_username "
                    f"FROM driver_profiles dp "
                    f"LEFT JOIN signup_records sr "
                    f"       ON sr.server_id = dp.server_id "
                    f"      AND sr.discord_user_id = CAST(dp.discord_user_id AS TEXT) "
                    f"WHERE dp.discord_user_id IN ({placeholders})",
                    [str(uid) for uid in user_ids],
                )
            ).fetchall()
        for row in rows:
            recorded[int(row["discord_user_id"])] = (
                row["server_display_name"],
                row["discord_username"],
                row["test_display_name"] if row["is_test_driver"] else None,
            )

    for user_id in user_ids:
        member = guild.get_member(user_id) if guild is not None else None
        if member is None and guild is not None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.HTTPException):
                member = None

        candidates = [member.display_name if member is not None else None]
        candidates.extend(recorded.get(user_id, (None, None, None)))
        candidates.append(str(user_id))
        names[user_id] = next(
            (value for value in candidates if value and str(value).strip()), str(user_id)
        )
    return names


async def _nationalities(bot, user_ids: list[int]) -> dict[int, str | None]:
    """Each driver's recorded nationality, or None where they stated none.

    A mock driver has no signup record to hold one, and carries its own instead — the
    same branch on is_test_driver the name is resolved by.
    """
    if not user_ids:
        return {}
    placeholders = ",".join("?" * len(user_ids))
    async with get_connection(bot.db_path) as db:
        rows = await (
            await db.execute(
                f"SELECT dp.discord_user_id, "
                f"       CASE WHEN dp.is_test_driver = 1 THEN dp.test_nationality "
                f"            ELSE sr.nationality END AS nationality "
                f"FROM driver_profiles dp "
                f"LEFT JOIN signup_records sr "
                    f"       ON sr.server_id = dp.server_id "
                    f"      AND sr.discord_user_id = CAST(dp.discord_user_id AS TEXT) "
                f"WHERE dp.discord_user_id IN ({placeholders})",
                [str(uid) for uid in user_ids],
            )
        ).fetchall()
    return {int(row["discord_user_id"]): (row["nationality"] or None) for row in rows}


async def _team_names(
    bot, guild, server_id: int, division_id: int, role_ids: list[int]
) -> dict[int, str]:
    """The name of the division's team holding each role, falling back to the role's own.

    A session records the Discord **role** an entry drove for, not a name. A role is mapped
    to a team *name* at server scope by ``team_role_configs``, and the division holds a team
    instance of that name; that instance's name is what the graphic draws, and what the team
    image is looked up by. Where the division holds no such team, the role's own name stands
    in — the honest fallback, and the one the textual table already uses.
    """
    names: dict[int, str] = {}
    if not role_ids:
        return names

    placeholders = ",".join("?" * len(role_ids))
    async with get_connection(bot.db_path) as db:
        rows = await (
            await db.execute(
                f"SELECT trc.role_id AS role_id, ti.name AS name "
                f"FROM team_role_configs trc "
                f"JOIN team_instances ti "
                f"  ON ti.name = trc.team_name AND ti.division_id = ? "
                f"WHERE trc.server_id = ? AND trc.role_id IN ({placeholders})",
                [division_id, server_id, *role_ids],
            )
        ).fetchall()
    for row in rows:
        try:
            names[int(row["role_id"])] = row["name"]
        except (TypeError, ValueError):
            continue

    for role_id in role_ids:
        if role_id in names:
            continue
        role = guild.get_role(role_id) if guild is not None else None
        names[role_id] = role.name if role is not None else f"Role {role_id}"
    return names


async def build_drawing(
    bot,
    guild,
    *,
    session_result,
    driver_rows,
    points_map,
    round_number,
    race_name: str,
    is_sprint: bool,
    result_status: str,
    division_name: str,
    division_tier=None,
    season_number=None,
    dsq_phase_map=None,
):
    """Resolve one session into a ResultsDrawing, or raise ResultsDataError."""
    from services.image_results_service import resolve_drawing

    server_id = guild.id if guild is not None else 0
    user_ids = [row.driver_user_id for row in driver_rows]
    role_ids = [row.team_role_id for row in driver_rows]

    config = await bot.image_config_service.get_config(server_id)

    return resolve_drawing(
        session_type=session_result.session_type,
        is_sprint=is_sprint,
        result_status=result_status,
        division_name=division_name,
        division_tier=division_tier,
        season_number=season_number,
        round_number=round_number,
        race_name=race_name,
        driver_rows=driver_rows,
        points_map=points_map,
        driver_names=await _driver_names(bot, guild, user_ids),
        team_names=await _team_names(
            bot, guild, server_id, session_result.division_id, role_ids
        ),
        nationalities=await _nationalities(bot, user_ids),
        dsq_phase_map=dsq_phase_map or {},
        fastest_lap_colour=getattr(config, "fastest_lap_colour", None),
        nationality_collected=await _nationality_collected(bot.db_path, server_id),
    )


async def render_png(bot, server_id: int, drawing, origin: PostingOrigin):
    """Render one session's results. Returns the render service's PostingDecision."""
    from services.image_results_service import build_fill_spec
    from services.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    config = await bot.image_config_service.get_config(server_id)
    directories, directory_faults = resolve_configured_directories(
        config,
        (
            ("team", "team_image_directory"),
            ("flag", "flag_directory"),
            ("tyre", "tyre_directory"),
        ),
        image_type=drawing.template_key,
    )

    return await bot.image_render_service.render_for_posting(
        server_id,
        drawing.template_key,
        spec_builder_with_faults(
            build_fill_spec, drawing, directories, directory_faults
        ),
        posting_origin=origin,
        bot=bot,
    )


async def try_post(
    bot,
    guild,
    channel,
    *,
    heading: str,
    label: str,
    session_result,
    driver_rows,
    points_map,
    round_number,
    race_name: str,
    is_sprint: bool,
    result_status: str,
    division_name: str,
    division_tier=None,
    season_number=None,
    dsq_phase_map=None,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> ResultsPostOutcome:
    """Post this session's classification as a graphic, or stand aside.

    **The replacement ordering.** An attachment cannot be introduced into a message already
    posted, so the image flow replaces rather than edits: the PNG is produced first, the new
    message posted, and only then is the previous one deleted. A render that fails therefore
    leaves the channel holding the message it had, and the caller falls back to text.

    The heading and the lifecycle label stay **message text** — the graphic carries the table
    alone (XIV.16).
    """
    if bot is None or guild is None or channel is None:
        return ResultsPostOutcome()

    server_id = guild.id
    try:
        from services.image_results_service import template_key_for

        template_key = template_key_for(session_result.session_type)
    except Exception as exc:  # noqa: BLE001
        log.error("results: unknown session type %s: %s", session_result.session_type, exc)
        return ResultsPostOutcome()

    if not await results_enabled(bot, server_id, template_key):
        return ResultsPostOutcome()

    session_label = f"{division_name} round {round_number}"

    try:
        drawing = await build_drawing(
            bot,
            guild,
            session_result=session_result,
            driver_rows=driver_rows,
            points_map=points_map,
            round_number=round_number,
            race_name=race_name,
            is_sprint=is_sprint,
            result_status=result_status,
            division_name=division_name,
            division_tier=division_tier,
            season_number=season_number,
            dsq_phase_map=dsq_phase_map,
        )
        decision = await render_png(bot, server_id, drawing, origin)
    except Exception as exc:  # noqa: BLE001 — a resolution fault, reported like any other
        log.error("results: render failed for session %s: %s", session_result.id, exc)
        await report(bot, server_id, drawing_label(drawing=None, fallback=session_label), str(exc))
        if origin is PostingOrigin.COMMANDED:
            return ResultsPostOutcome(action=REJECTED, message=f"❌ {exc}")
        return ResultsPostOutcome()

    if decision.rejects:
        return ResultsPostOutcome(
            action=REJECTED,
            message=decision.caller_message(
                f"the {drawing.session_name.lower()} results of {session_label}"
            ),
            notices=decision.notices,
        )

    if not decision.posts_image:
        # An uncommanded posting whose render failed: the caller's textual body runs, and
        # the previously posted message is left where it is until it does.
        if decision.problem is not None:
            await report(
                bot,
                server_id,
                f"{session_label} — {drawing.session_name}",
                decision.problem.detail,
            )
        return ResultsPostOutcome()

    from services.image_render_service import discard_attachment

    png = decision.png_paths[0]
    # Discarded through the attachment rather than the path: the file object holds the
    # handle open, and closing it here rather than trusting the send to have done it keeps
    # the cleanup independent of how far the send got.
    attachment = discord.File(str(png), filename="results.png")
    try:
        message = await channel.send(f"{heading}\n{label}", file=attachment)
    except discord.HTTPException as exc:
        log.error("results: could not post image for session %s: %s", session_result.id, exc)
        # A Discord failure rather than a generation one: the caller falls through and the
        # **textual** table is what gets posted and, if need be, enqueued for retry.
        return ResultsPostOutcome()
    finally:
        discard_attachment(attachment)

    # Only now is the previous message removed. The channel never holds nothing, and a
    # failed rebuild leaves the league the results it had.
    if session_result.results_message_id is not None:
        try:
            previous = await channel.fetch_message(session_result.results_message_id)
            await previous.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async with get_connection(bot.db_path) as db:
        await db.execute(
            "UPDATE session_results SET results_message_id = ? WHERE id = ?",
            (message.id, session_result.id),
        )
        await db.commit()

    if decision.notices:
        await report_notices(
            bot, server_id, f"{session_label} — {drawing.session_name}", decision.notices
        )

    # No ``png_path``: the file was discarded the moment the send returned, and handing
    # back a path to something deleted is worse than handing back nothing.
    return ResultsPostOutcome(
        action=POSTED, message_id=message.id, notices=decision.notices
    )


def drawing_label(*, drawing, fallback: str) -> str:
    """A human label for a report, whether or not the drawing was built."""
    if drawing is None:
        return fallback
    return f"{fallback} — {drawing.session_name}"


async def report(bot, server_id: int, what: str, detail: str) -> None:
    """Report a fault to the server's logging channel, and never to a driver-read channel."""
    try:
        await bot.output_router.post_log(
            server_id, f"⚠️ Results image — {what}: {detail}"
        )
    except Exception as exc:  # noqa: BLE001
        log.error("results: could not report to the log channel: %s", exc)


async def report_notices(bot, server_id: int, what: str, notices) -> None:
    """Report every non-fatal degradation, naming the session it pertains to.

    `what` reaches the log as the block's subject. It used to be accepted and dropped,
    which left a reader of a busy log unable to tell which session a notice came from.
    """
    if not notices:
        return
    try:
        from services.image_render_service import ImageRenderService

        await ImageRenderService.report_notices(bot, server_id, notices, subject=what)
    except Exception as exc:  # noqa: BLE001
        log.error("results: could not report notices: %s", exc)
