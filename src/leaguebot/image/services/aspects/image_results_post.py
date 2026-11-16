"""What a session's results picture is drawn from, and the lines its drawing hands back (039).

The picture is drawn by ``ImageDrawing.draw_results``, which the results module's posting jobs are
handed: it asks the one gate every picture asks, resolves the session into a drawing
(:func:`build_drawing`), renders it (:func:`render_png`) and hands back the file and the lines
(:func:`lines_of`) the job writes. **This module posts nothing** and reports nothing itself: the
results module posts the picture through core's standing-post handler, and a fault in drawing it is
raised for the job to say why the text stands.

**The textual path is not reformed by this module.** Where the picture is not drawn, the caller's
existing body runs exactly as it did before this feature. The graphic is an alternative output
beside the text, never a replacement for it (Constitution XIV.7).

The readers of a driver's name, nationality and team here are the ones the other pictures share;
they stay until the name chain is read through core.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.image.models.image_module import PostingOrigin
from leaguebot.image.services.aspects.image_lineup_service import resolve_driver_name
from leaguebot.core.utils.league_bot import LeagueBot

if TYPE_CHECKING:
    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import ImageRenderService

log = logging.getLogger(__name__)

RESULTS_ASPECT = "results"

#: What the toggle draws, which heads a fault's line: the aspect `/images config toggle` offers.
RESULTS_TOGGLE_NAME = "Results image"


async def _nationality_collected(db_path: str) -> bool:
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
                    "FROM server_configs",
                )
            ).fetchone()
            if config is not None and config["test_mode_active"]:
                return bool(config["test_mode_nationality_required"])

            row = await (
                await db.execute(
                    "SELECT nationality_required FROM signup_module_settings "
                    "",
                )
            ).fetchone()
    except Exception:  # noqa: BLE001 — an unreadable switch is not a reason to fail a render
        log.warning(
            "results image: the nationality switch could not be read, so flags are drawn",
            exc_info=True,
        )
        return True
    if row is None:
        return True
    return bool(row["nationality_required"])


#: The signup a graphic reads a driver's name and nationality from: their latest signup to
#: the season being drawn (issue #220), falling back to their latest signup of all where that
#: season holds none. A signup is kept, never overwritten, so a driver who has signed up in
#: several seasons is drawn under what they gave for the season on the graphic.
#:
#: A signup made from any of the driver's accounts is theirs (issue #243): one made before
#: they changed account is still what they gave for that season. Within the season an
#: approved signup outranks a later one that was not — see `SIGNUP_PRECEDENCE_SQL`.
#:
#: Takes one parameter: the season's id (None to read the latest of all).
SIGNUP_FOR_SEASON_SQL = (
    "(SELECT id FROM signup_records "
    " WHERE discord_user_id IN "
    "       (SELECT discord_user_id FROM driver_accounts WHERE driver_profile_id = dp.id) "
    " ORDER BY COALESCE(season_id = ?, 0) DESC, season_id DESC, approved DESC, id DESC "
    " LIMIT 1)"
)


async def season_of_division(db_path: str, division_id: int | None) -> int | None:
    """The season *division_id* belongs to, or None."""
    if division_id is None:
        return None
    from leaguebot.core.services.rounds.round_context import division_context

    division = await division_context(db_path, division_id)
    return division.season_id if division is not None else None


async def _driver_names(
    bot: LeagueBot, guild, user_ids: list[int], *, division_id: int | None = None
) -> dict[int, str]:
    """The name each driver is drawn under (XIV.16, and the wip-spec's person-name rule).

    The display name of their Discord account on the server at the moment of generation,
    falling through the names the league recorded for them — in their signup to the season of
    *division_id*, where one is named — and ending at the user id. A graphic carries no
    mention, so this is what stands in its place.
    """
    names: dict[int, str] = {}

    recorded: dict[int, tuple] = {}
    if user_ids:
        season_id = await season_of_division(bot.db_path, division_id)
        placeholders = ",".join("?" * len(user_ids))
        async with get_connection(bot.db_path) as db:
            rows = await (
                await db.execute(
                    f"SELECT dp.discord_user_id, dp.is_test_driver, dp.test_display_name, "
                    f"       sr.server_display_name, sr.discord_username "
                    f"FROM driver_profiles dp "
                    f"LEFT JOIN signup_records sr ON sr.id = {SIGNUP_FOR_SEASON_SQL} "
                    f"WHERE dp.discord_user_id IN ({placeholders})",
                    [season_id, *[str(uid) for uid in user_ids]],
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

        signup_display_name, signup_username, test_display_name = recorded.get(
            user_id, (None, None, None)
        )
        # One chain, the lineup's, rather than a copy of it: what a name is cleaned of before it
        # is drawn is decided there, once, for every graphic.
        names[user_id] = resolve_driver_name(
            discord_user_id=user_id,
            display_name=member.display_name if member is not None else None,
            signup_display_name=signup_display_name,
            signup_username=signup_username,
            test_display_name=test_display_name,
        )
    return names


async def _nationalities(
    bot: LeagueBot, user_ids: list[int], *, division_id: int | None = None
) -> dict[int, str | None]:
    """Each driver's recorded nationality, or None where they stated none.

    Read from their signup to the season of *division_id*, where one is named. A mock driver
    has no signup record to hold one, and carries its own instead — the same branch on
    is_test_driver the name is resolved by.
    """
    if not user_ids:
        return {}
    season_id = await season_of_division(bot.db_path, division_id)
    placeholders = ",".join("?" * len(user_ids))
    async with get_connection(bot.db_path) as db:
        rows = await (
            await db.execute(
                f"SELECT dp.discord_user_id, "
                f"       CASE WHEN dp.is_test_driver = 1 THEN dp.test_nationality "
                f"            ELSE sr.nationality END AS nationality "
                f"FROM driver_profiles dp "
                f"LEFT JOIN signup_records sr ON sr.id = {SIGNUP_FOR_SEASON_SQL} "
                f"WHERE dp.discord_user_id IN ({placeholders})",
                [season_id, *[str(uid) for uid in user_ids]],
            )
        ).fetchall()
    return {int(row["discord_user_id"]): (row["nationality"] or None) for row in rows}


async def _team_names(bot: LeagueBot, guild, division_id: int, team_ids: list[int]) -> dict[int, str]:
    """The name of each division team a session records, keyed by the team's id.

    A session records the division's **team** an entry drove for, never its Discord role
    (#375), so the name the graphic draws is read from the team itself; the team image is
    looked up by its shorthand, from ``_team_keys`` (#381). Nothing asks Discord: a role replaced mid-season, or since
    deleted, has no bearing on the rounds recorded under it. *guild* and *division_id* are
    kept for the callers' symmetry with the driver lookups beside this one.
    """
    from leaguebot.core.services.drivers.team_service import team_names_for_instances

    return await team_names_for_instances(bot.db_path, team_ids)


async def _team_keys(bot: LeagueBot, team_ids: list[int]) -> dict[int, str]:
    """The shorthand of each division team a session records, which its artwork is found by.

    Apart from the name drawn (#381): a team is drawn by its full name, and its badge file is
    named after its shorthand.
    """
    from leaguebot.core.services.drivers.team_service import team_artwork_keys_for_instances

    return await team_artwork_keys_for_instances(bot.db_path, team_ids)


async def build_drawing(
    bot: LeagueBot,
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
    from leaguebot.image.services.aspects.image_results_service import resolve_drawing

    user_ids = [row.driver_user_id for row in driver_rows]
    team_ids = [row.team_instance_id for row in driver_rows]

    config = await bot.image_config_service.get_config()

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
        driver_names=await _driver_names(
            bot, guild, user_ids, division_id=session_result.division_id
        ),
        team_names=await _team_names(
            bot, guild, session_result.division_id, team_ids
        ),
        team_keys=await _team_keys(bot, team_ids),
        nationalities=await _nationalities(
            bot, user_ids, division_id=session_result.division_id
        ),
        dsq_phase_map=dsq_phase_map or {},
        fastest_lap_colour=getattr(config, "fastest_lap_colour", None),
        nationality_collected=await _nationality_collected(bot.db_path),
    )


async def render_png(
    config_service: ImageConfigService,
    renderer: ImageRenderService,
    drawing,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
):
    """Render one session's results. Returns the render service's PostingDecision.

    A fault in resolving the directories or building the fill is raised, for the results' own
    post to say why the text stands.
    """
    from leaguebot.image.services.aspects.image_results_service import build_fill_spec
    from leaguebot.image.services.render.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    config = await config_service.get_config()
    directories, directory_faults = resolve_configured_directories(
        config,
        (
            ("team", "team_image_directory"),
            ("flag", "flag_directory"),
            ("tyre", "tyre_directory"),
        ),
        image_type=drawing.template_key,
    )

    from leaguebot.image.utils.image_naming import stem_for_drawing

    # One template draws four sessions, so the *session's* own label names the file:
    # `feature_qualifying_results` rather than the template's `qualifying_results`,
    # which would not say which of a sprint weekend's two qualifyings was drawn.
    return await renderer.render_for_posting(
        drawing.template_key,
        spec_builder_with_faults(
            build_fill_spec, drawing, directories, directory_faults
        ),
        posting_origin=origin,
        division_name=drawing.division_name,
        filename_stem=stem_for_drawing(
            drawing, subject=f"{drawing.session_name} results"
        ),
    )


def fault(what: str, detail: str) -> str:
    """The line saying why the picture of *what* was not drawn, headed by the toggle's name."""
    return f"⚠️ {RESULTS_TOGGLE_NAME} — {what}: {detail}"


def lines_of(what: str, decision) -> tuple[str, ...]:
    """The lines a render's notices and problem make, naming *what* they pertain to."""
    from leaguebot.image.services.render.image_render_service import ImageRenderService

    lines: list[str] = []
    if decision.notices:
        lines.append(ImageRenderService.format_notices(decision.notices, subject=what))
    if decision.problem is not None:
        lines.append(fault(what, decision.problem.detail))
    return tuple(lines)
