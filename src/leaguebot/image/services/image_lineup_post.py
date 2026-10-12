"""Post a division's lineup as a graphic, or stand aside (038).

**This module exists to keep one decision in one place.** The lineup has five posting
surfaces against the calendar's two, and the image path and the textual path must not drift
apart across five call sites.

The shape every caller uses:

    outcome = await try_post(bot, guild, division_id, origin=PostingOrigin.SCHEDULED)
    if outcome.applicable:
        return                      # the graphic was posted, or the caller was told why
    ...the textual body...

Both paths post first, and neither deletes: the message a lineup replaces is deleted by a job
of the change queue after the post, which also saves the new id (#439; Constitution XIV rule 8;
image spec :706). A picture Discord refuses stands aside, so the caller's text is posted in the
same try. Where the image flow does not run — the module disabled, the `lineup` toggle off, no
template configured — ``try_post`` answers ``NOT_APPLICABLE`` and the caller's textual body runs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import discord

from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.db.database import get_connection
from leaguebot.image.models.image_module import PostingOrigin
from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

#: The one template this aspect draws. Named here rather than inline because the render
#: body labels its directory resolution with it as well as passing it to the renderer, and
#: the two must not be able to drift.
LINEUP_TEMPLATE_KEY = "lineup_template"

#: The image flow does not apply — the caller falls through to its textual body.
NOT_APPLICABLE = "NOT_APPLICABLE"
#: The graphic was produced and posted; the caller does nothing further.
POSTED = "POSTED"
#: The graphic could not be produced and the posting was **commanded**, so the caller was
#: told what is at fault and must post nothing.
REJECTED = "REJECTED"


@dataclass
class LineupPostOutcome:
    action: str = NOT_APPLICABLE
    message: str | None = None
    notices: list = field(default_factory=list)
    png_path: Path | None = None
    #: The message the graphic was posted as, where it was; the caller's job saves it.
    message_id: int | None = None

    @property
    def applicable(self) -> bool:
        """True where this module handled the posting and the caller must not."""
        return self.action != NOT_APPLICABLE


async def lineup_enabled(bot: LeagueBot) -> bool:
    """True where the module is on, the `lineup` aspect is on, and a template is named."""
    try:
        if not await bot.module_service.is_images_enabled():
            return False
        toggles = await bot.image_config_service.get_toggles()
        if not toggles.get("lineup"):
            return False
        reports = await bot.image_validity_service.template_reports()
        report = reports.get(LINEUP_TEMPLATE_KEY)
        return report is not None and report.valid
    except Exception as exc:  # noqa: BLE001 — never break a posting on this reader
        log.error("lineup: enablement check failed: %s", exc, exc_info=True)
        return False


def seated_members(guild, teams) -> dict[str, discord.Member]:
    """The Discord member behind each occupied seat, as user id -> Member.

    One reader for what the posting path and the preview path both need. Both once carried
    this loop verbatim, and both now want the Member for a second purpose -- the display name
    it answers, and the profile picture the portrait is obtained from -- so a second divergent
    copy would have been two.

    A seat with no driver, a driver the guild cannot resolve (they have left), and a mock
    driver carrying a synthetic id all simply do not appear.
    """
    members: dict[str, discord.Member] = {}
    if guild is None:
        return members
    for team in teams:
        for seat in team.seats:
            if seat.discord_user_id is None:
                continue
            member = guild.get_member(int(seat.discord_user_id))
            if member is not None:
                members[str(seat.discord_user_id)] = member
    return members


async def build_drawing(bot: LeagueBot, guild, division_id: int, *, include_uncommitted: bool = False):
    """Resolve the division into a LineupDrawing, or raise LineupDataError.

    A placement made mid-season and not yet confirmed is left out (#220), for the lineup of
    record shows who races. *include_uncommitted* draws it in, for the one caller that must
    see the lineup as it **will** stand: the mid-season placements review, which draws what
    confirming will post and withholds its button where that will not draw (#374).
    """
    from leaguebot.image.services.image_lineup_service import resolve_drawing
    from leaguebot.image.services.image_results_post import SIGNUP_FOR_SEASON_SQL

    async with get_connection(bot.db_path) as db:
        division = await (
            await db.execute(
                "SELECT d.id, d.name, d.tier, s.id AS season_id, s.season_number "
                "FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
        ).fetchone()
        if division is None:
            from leaguebot.image.services.image_lineup_service import LineupDataError

            raise LineupDataError(f"division {division_id} no longer exists")
        season_id = division["season_id"]

        instances = await (
            await db.execute(
                "SELECT id, name, full_name, max_seats, is_reserve FROM team_instances "
                "WHERE division_id = ? ORDER BY is_reserve ASC, id ASC",
                (division_id,),
            )
        ).fetchall()

        teams = []
        for instance in instances:
            seats = await (
                await db.execute(
                    # A mock driver has no signup record to hold a nationality and carries
                    # its own, read by the same branch on is_test_driver the name is.
                    "SELECT ts.seat_number, dp.discord_user_id, dp.is_test_driver, "
                    "       dp.test_display_name, sr.server_display_name, "
                    "       sr.discord_username, "
                    "       CASE WHEN dp.is_test_driver = 1 THEN dp.test_nationality "
                    "            ELSE sr.nationality END AS nationality "
                    "FROM team_seats ts "
                    # A placement not yet confirmed mid-season is not drawn (issue #220),
                    # unless the caller asks for the lineup as it will stand (#374).
                    "LEFT JOIN driver_season_assignments dsa "
                    "       ON dsa.team_seat_id = ts.id AND dsa.division_id = ? "
                    "      AND (dsa.committed = 1 OR ? OR NOT EXISTS ("
                    "          SELECT 1 FROM seasons s WHERE s.id = dsa.season_id "
                    "          AND s.status = 'ACTIVE')) "
                    "LEFT JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
                    f"LEFT JOIN signup_records sr ON sr.id = {SIGNUP_FOR_SEASON_SQL} "
                    "WHERE ts.team_instance_id = ? ORDER BY ts.seat_number",
                    (division_id, int(include_uncommitted), season_id, instance["id"]),
                )
            ).fetchall()
            teams.append(
                SimpleNamespace(
                    name=instance["name"],
                    full_name=instance["full_name"],
                    is_reserve=bool(instance["is_reserve"]),
                    seats=[
                        SimpleNamespace(
                            seat_number=row["seat_number"],
                            discord_user_id=row["discord_user_id"],
                            server_display_name=row["server_display_name"],
                            discord_username=row["discord_username"],
                            test_display_name=row["test_display_name"],
                            nationality=row["nationality"],
                        )
                        for row in seats
                    ],
                )
            )


    # The suppression switch (FR-009): a lineup with no flags at all is exactly what a
    # league that switched nationality collection off configured, and raises nothing. Read
    # through the module's single reader rather than a copy of it — this path carried its
    # own, and its own read the wrong table.
    from leaguebot.image.services.image_results_post import _nationality_collected

    collected = await _nationality_collected(bot.db_path)

    # The first link of the name chain is the account's display name on the server *at the
    # moment of generation*, which only the guild can answer (research R9).
    members = seated_members(guild, teams)
    display_names = {
        user_id: member.display_name for user_id, member in members.items()
    }

    return (
        division,
        resolve_drawing(
            division_name=division["name"],
            division_tier=division["tier"],
            season_number=division["season_number"],
            teams=teams,
            display_names=display_names,
            nationality_collected=collected,
        ),
        list(members.values()),
    )


async def render_png(
    bot: LeagueBot,
    guild,
    division_id: int,
    origin: PostingOrigin,
    *,
    include_uncommitted: bool = False,
    obtain_missing_portraits: bool = False,
):
    """Render one division's lineup. Returns the render service's PostingDecision.

    *include_uncommitted* is `build_drawing`'s, and is for command output alone.
    *obtain_missing_portraits* is `refresh_before_render`'s *obtain_missing*, and is for
    the placements review alone.
    """
    from leaguebot.image.services.image_lineup_service import build_fill_spec
    from leaguebot.image.services.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    _division, drawing, members = await build_drawing(
        bot, guild, division_id, include_uncommitted=include_uncommitted
    )

    config = await bot.image_config_service.get_config()
    directories, directory_faults = resolve_configured_directories(
        config,
        (
            ("team", "team_image_directory"),
            ("flag", "flag_directory"),
            ("driver", "driver_image_directory"),
        ),
        image_type=LINEUP_TEMPLATE_KEY,
    )

    # Bring the portraits up to date before the fill looks for them, where the league asked
    # for that. The drawing is already built, but it carries the user id as the datum and the
    # file is not read until the fill -- so this is still in time. Never raises: a portrait
    # that cannot be obtained resolves exactly as it would have done otherwise.
    from leaguebot.image.services.driver_portrait_service import refresh_before_render

    await refresh_before_render(
        bot,
        members,
        config=config,
        directory=directories.get("driver"),
        obtain_missing=obtain_missing_portraits,
    )

    from leaguebot.image.utils.image_naming import stem_for_drawing

    return await bot.image_render_service.render_for_posting(
        LINEUP_TEMPLATE_KEY,
        spec_builder_with_faults(
            build_fill_spec, drawing, directories, directory_faults
        ),
        posting_origin=origin,
        bot=bot,
        division_name=drawing.division_name,
        filename_stem=stem_for_drawing(drawing, LINEUP_TEMPLATE_KEY),
    )


async def try_post(
    bot: LeagueBot,
    guild: discord.Guild | None,
    division_id: int,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> LineupPostOutcome:
    """Post this division's lineup as a graphic, or stand aside.

    **The replacement ordering (FR-025).** The PNG is produced first and posted; the message it
    replaces is deleted by a job after the post, never here. A render that fails therefore
    leaves the channel holding the message it had — the league keeps a lineup rather than
    losing one — and the caller falls back to text.
    """
    if guild is None:
        return LineupPostOutcome()

    if not await lineup_enabled(bot):
        return LineupPostOutcome()

    async with get_connection(bot.db_path) as db:
        row = await (
            await db.execute(
                "SELECT d.name, d.lineup_channel_id "
                "FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
        ).fetchone()

    if row is None or row["lineup_channel_id"] is None:
        return LineupPostOutcome()

    channel = as_text_channel(guild.get_channel(row["lineup_channel_id"]))
    if channel is None:
        try:
            channel = as_text_channel(await guild.fetch_channel(row["lineup_channel_id"]))
        except (discord.NotFound, discord.HTTPException):
            return LineupPostOutcome()
    if not isinstance(channel, discord.TextChannel):
        return LineupPostOutcome()

    try:
        decision = await render_png(bot, guild, division_id, origin)
    except Exception as exc:  # noqa: BLE001 — a resolution fault, reported like any other
        log.error("lineup: render failed for division %s: %s", division_id, exc, exc_info=True)
        await _report(bot, row["name"], str(exc))
        if origin is PostingOrigin.COMMANDED:
            return LineupPostOutcome(action=REJECTED, message=f"❌ {exc}")
        return LineupPostOutcome()

    if decision.rejects:
        return LineupPostOutcome(
            action=REJECTED,
            message=decision.caller_message(f"the lineup of {row['name']}"),
            notices=decision.notices,
        )

    if not decision.posts_image:
        # An uncommanded posting whose render failed: the caller's textual body runs, and
        # the previously posted message is left exactly where it is until it does.
        if decision.problem is not None:
            await _report(bot, row["name"], decision.problem.detail)
        return LineupPostOutcome()

    from leaguebot.image.services.image_render_service import discard_attachment

    png = decision.png_paths[0]
    attachment = discord.File(str(png), filename=png.name)
    try:
        message = await channel.send(file=attachment)
    except discord.HTTPException as exc:
        log.error("lineup: could not post image for division %s: %s", division_id, exc)
        return LineupPostOutcome()
    finally:
        # The picture has served its purpose either way: posted, or replaced by the
        # textual body the caller falls back to. Nothing reads it again. Discarded through
        # the attachment so the handle is closed before the file is removed.
        discard_attachment(attachment)

    # No ``png_path``: the file was discarded the moment the send returned. Only
    # ``render_for_command`` still hands one back, because its two cog callers post it
    # themselves and discard it themselves.
    return LineupPostOutcome(
        action=POSTED, notices=decision.notices, message_id=message.id
    )


async def render_for_command(
    bot: LeagueBot,
    guild,
    division_id: int,
    *,
    include_uncommitted: bool = False,
    obtain_missing_portraits: bool = False,
) -> LineupPostOutcome:
    """Produce a division's lineup PNG as **command output**, posting it nowhere.

    Used by `/team lineup` and `/season placements-review`. Constitution XIV.7 makes a commanded
    posting reject rather than fall back, so a fault comes back as ``REJECTED`` with the
    message to show the caller.

    These images are output of a command and **not the lineup of record** (FR-028): this
    function writes no ``lineup_message_id``, deletes nothing from the lineup channel, and
    records no audit entry. That separation is the whole reason it exists beside
    :func:`try_post` rather than being a flag on it — and why *include_uncommitted*, which
    draws a lineup as it will stand once mid-season placements are confirmed, is offered
    here and not there.

    *obtain_missing_portraits* is set by `/season placements-review` alone. It obtains the
    portrait of each seated driver who has none, whichever update trigger the league chose,
    so the review is not judged on a placeholder the league will not see (image
    specification, "The drawing of placements under review"). `/team lineup` does not set
    it, and nor does a posting.
    """
    if guild is None or not await lineup_enabled(bot):
        return LineupPostOutcome()

    try:
        decision = await render_png(
            bot,
            guild,
            division_id,
            PostingOrigin.COMMANDED,
            include_uncommitted=include_uncommitted,
            obtain_missing_portraits=obtain_missing_portraits,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("lineup: command render failed for division %s: %s", division_id, exc, exc_info=True)
        return LineupPostOutcome(action=REJECTED, message=f"❌ {exc}")

    if decision.posts_image:
        return LineupPostOutcome(
            action=POSTED, notices=decision.notices, png_path=decision.png_paths[0]
        )

    return LineupPostOutcome(
        action=REJECTED,
        message=decision.caller_message("the lineup"),
        notices=decision.notices,
    )


async def _report(bot: LeagueBot, division_name: str, detail: str) -> None:
    """Send a fault to the server's logging channel, never to the lineup channel.

    The lineup channel is read by the drivers of the league and not by its staff
    (Constitution XIV.4, FR-020).
    """
    try:
        await bot.output_router.post_log(
            f"Lineup image | {division_name} | {detail}",
        )
    except Exception as exc:  # noqa: BLE001
        log.error("lineup: could not report to the log channel: %s", exc, exc_info=True)
