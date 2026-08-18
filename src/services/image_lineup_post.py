"""Post a division's lineup as a graphic, or stand aside (038).

**This module exists to keep one decision in one place.** The lineup has five posting
surfaces against the calendar's two, and FR-025/FR-025a require the image path and the
textual path to differ in exactly one respect — the order of the delete and the build.
Spreading that across five call sites is how the two would drift apart.

The shape every caller uses:

    outcome = await try_post(bot, guild, division_id, origin=PostingOrigin.SCHEDULED)
    if outcome.applicable:
        return                      # the graphic was posted, or the caller was told why
    ...existing textual body, unchanged...

**The textual path is not reformed by this module.** Where the image flow does not run —
the module disabled, the `lineup` toggle off, no template configured — ``try_post``
answers ``NOT_APPLICABLE`` and the caller's existing body runs exactly as it did before
this feature, delete-then-build order included (FR-025a). That order was specified in
specs/028-season-signup-flow/ and is deliberately not reopened here.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import discord

from db.database import get_connection
from models.image_module import PostingOrigin

log = logging.getLogger(__name__)

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

    @property
    def applicable(self) -> bool:
        """True where this module handled the posting and the caller must not."""
        return self.action != NOT_APPLICABLE


async def lineup_enabled(bot, server_id: int) -> bool:
    """True where the module is on, the `lineup` aspect is on, and a template is named."""
    try:
        if not await bot.module_service.is_images_enabled(server_id):
            return False
        toggles = await bot.image_config_service.get_toggles(server_id)
        if not toggles.get("lineup"):
            return False
        reports = await bot.image_validity_service.template_reports(server_id)
        report = reports.get("lineup_template")
        return report is not None and report.valid
    except Exception as exc:  # noqa: BLE001 — never break a posting on this reader
        log.error("lineup: enablement check failed for server %s: %s", server_id, exc)
        return False


async def build_drawing(bot, guild, division_id: int):
    """Resolve the division into a LineupDrawing, or raise LineupDataError."""
    from services.image_lineup_service import resolve_drawing

    async with get_connection(bot.db_path) as db:
        division = await (
            await db.execute(
                "SELECT d.id, d.name, d.tier, s.server_id, s.season_number "
                "FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
        ).fetchone()
        if division is None:
            from services.image_lineup_service import LineupDataError

            raise LineupDataError(f"division {division_id} no longer exists")

        instances = await (
            await db.execute(
                "SELECT id, name, max_seats, is_reserve FROM team_instances "
                "WHERE division_id = ? ORDER BY is_reserve ASC, name ASC",
                (division_id,),
            )
        ).fetchall()

        teams = []
        for instance in instances:
            seats = await (
                await db.execute(
                    "SELECT ts.seat_number, dp.discord_user_id, dp.is_test_driver, "
                    "       dp.test_display_name, sr.server_display_name, "
                    "       sr.discord_username, sr.nationality "
                    "FROM team_seats ts "
                    "LEFT JOIN driver_season_assignments dsa "
                    "       ON dsa.team_seat_id = ts.id AND dsa.division_id = ? "
                    "LEFT JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
                    "LEFT JOIN signup_records sr "
                    "       ON sr.server_id = dp.server_id "
                    "      AND sr.discord_user_id = CAST(dp.discord_user_id AS TEXT) "
                    "WHERE ts.team_instance_id = ? ORDER BY ts.seat_number",
                    (division_id, instance["id"]),
                )
            ).fetchall()
            teams.append(
                SimpleNamespace(
                    name=instance["name"],
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
        # league that switched nationality collection off configured, and raises nothing.
        collected = True
        try:
            row = await (
                await db.execute(
                    "SELECT nationality_required FROM signup_module_settings "
                    "WHERE server_id = ?",
                    (division["server_id"],),
                )
            ).fetchone()
            if row is not None:
                collected = bool(row["nationality_required"])
        except Exception:  # noqa: BLE001 — a league without the signup module collects
            collected = True

    # The first link of the name chain is the account's display name on the server *at the
    # moment of generation*, which only the guild can answer (research R9).
    display_names: dict[str, str] = {}
    if guild is not None:
        for team in teams:
            for seat in team.seats:
                if seat.discord_user_id is None:
                    continue
                member = guild.get_member(int(seat.discord_user_id))
                if member is not None:
                    display_names[str(seat.discord_user_id)] = member.display_name

    return division, resolve_drawing(
        division_name=division["name"],
        division_tier=division["tier"],
        season_number=division["season_number"],
        teams=teams,
        display_names=display_names,
        nationality_collected=collected,
    )


async def render_png(bot, server_id: int, guild, division_id: int, origin: PostingOrigin):
    """Render one division's lineup. Returns the render service's PostingDecision."""
    from services.image_lineup_service import build_fill_spec
    from services.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    _division, drawing = await build_drawing(bot, guild, division_id)

    config = await bot.image_config_service.get_config(server_id)
    directories, directory_faults = resolve_configured_directories(
        config,
        (
            ("team", "team_image_directory"),
            ("flag", "flag_directory"),
            ("driver", "driver_image_directory"),
        ),
        image_type=LINEUP_TEMPLATE_KEY,
    )

    return await bot.image_render_service.render_for_posting(
        server_id,
        "lineup_template",
        spec_builder_with_faults(
            build_fill_spec, drawing, directories, directory_faults
        ),
        posting_origin=origin,
        bot=bot,
    )


async def try_post(
    bot,
    guild: discord.Guild | None,
    division_id: int,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> LineupPostOutcome:
    """Post this division's lineup as a graphic, or stand aside.

    **The replacement ordering (FR-025).** The PNG is produced first; only once it exists
    is the previously posted message deleted and the new one posted in its place. A render
    that fails therefore leaves the channel holding the message it had — the league keeps a
    lineup rather than losing one — and the caller falls back to text.
    """
    if guild is None:
        return LineupPostOutcome()

    server_id = guild.id
    if not await lineup_enabled(bot, server_id):
        return LineupPostOutcome()

    async with get_connection(bot.db_path) as db:
        row = await (
            await db.execute(
                "SELECT d.name, d.lineup_channel_id, d.lineup_message_id, s.server_id "
                "FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
        ).fetchone()

    if row is None or row["lineup_channel_id"] is None:
        return LineupPostOutcome()

    channel = guild.get_channel(row["lineup_channel_id"])
    if channel is None:
        try:
            channel = await guild.fetch_channel(row["lineup_channel_id"])
        except (discord.NotFound, discord.HTTPException):
            return LineupPostOutcome()
    if not isinstance(channel, discord.TextChannel):
        return LineupPostOutcome()

    try:
        decision = await render_png(bot, server_id, guild, division_id, origin)
    except Exception as exc:  # noqa: BLE001 — a resolution fault, reported like any other
        log.error("lineup: render failed for division %s: %s", division_id, exc)
        await _report(bot, server_id, row["name"], str(exc))
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
            await _report(bot, server_id, row["name"], decision.problem.detail)
        return LineupPostOutcome()

    png = decision.png_paths[0]
    try:
        message = await channel.send(file=discord.File(str(png), filename="lineup.png"))
    except discord.HTTPException as exc:
        log.error("lineup: could not post image for division %s: %s", division_id, exc)
        return LineupPostOutcome()

    # Only now is the previous message removed. This is the whole of FR-025: the channel
    # never holds nothing, and a failed rebuild leaves the league its existing lineup.
    if row["lineup_message_id"] is not None:
        try:
            previous = await channel.fetch_message(row["lineup_message_id"])
            await previous.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    now = datetime.now(timezone.utc).isoformat()
    async with get_connection(bot.db_path) as db:
        await db.execute(
            "UPDATE divisions SET lineup_message_id = ? WHERE id = ?",
            (message.id, division_id),
        )
        await db.execute(
            "INSERT INTO audit_entries "
            "(server_id, actor_id, actor_name, division_id, change_type, old_value, "
            " new_value, timestamp) "
            "VALUES (?, 0, 'system', ?, 'SIGNUP_LINEUP_POSTED', '', ?, ?)",
            (
                row["server_id"],
                division_id,
                json.dumps(
                    {
                        "channel_id": row["lineup_channel_id"],
                        "division": row["name"],
                        "form": "image",
                    }
                ),
                now,
            ),
        )
        await db.commit()

    return LineupPostOutcome(
        action=POSTED, notices=decision.notices, png_path=png
    )


async def render_for_command(bot, guild, division_id: int) -> LineupPostOutcome:
    """Produce a division's lineup PNG as **command output**, posting it nowhere.

    Used by `/team lineup` and `/season review`. Constitution XIV.7 makes a commanded
    posting reject rather than fall back, so a fault comes back as ``REJECTED`` with the
    message to show the caller.

    These images are output of a command and **not the lineup of record** (FR-028): this
    function writes no ``lineup_message_id``, deletes nothing from the lineup channel, and
    records no audit entry. That separation is the whole reason it exists beside
    :func:`try_post` rather than being a flag on it.
    """
    if guild is None or not await lineup_enabled(bot, guild.id):
        return LineupPostOutcome()

    try:
        decision = await render_png(
            bot, guild.id, guild, division_id, PostingOrigin.COMMANDED
        )
    except Exception as exc:  # noqa: BLE001
        log.error("lineup: command render failed for division %s: %s", division_id, exc)
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


async def _report(bot, server_id: int, division_name: str, detail: str) -> None:
    """Send a fault to the server's logging channel, never to the lineup channel.

    The lineup channel is read by the drivers of the league and not by its staff
    (Constitution XIV.4, FR-020).
    """
    try:
        await bot.output_router.post_log(
            server_id,
            f"Lineup image | {division_name} | {detail}",
        )
    except Exception as exc:  # noqa: BLE001
        log.error("lineup: could not report to the log channel: %s", exc)
