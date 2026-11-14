"""The information message with its Sign Up button: the one post of it, for `signup.open` and for
`/signup channel` where it replaces the channel of an open window.

The message names the slots, the tracks, the settings and the close time, and pings the base role.
Both callers post it the same way, so a window moved to a new channel shows what an opened one
does.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.services.rounds import track_service
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild
from leaguebot.signup.services.signup_posting import post_standing_message
from leaguebot.signup.services.signup_views import SignupButtonView

if TYPE_CHECKING:
    from leaguebot.core.services.queue.change_queue import ChangeQueue
    from leaguebot.core.services.settings.config_service import ConfigService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

__all__ = ["post_information", "stamp"]


def stamp(moment: datetime, fmt: Literal["F", "R"] = "F") -> str:
    """A dynamic timestamp for *moment*, read as UTC where it carries no zone."""
    return discord.utils.format_dt(moment, fmt)


async def post_information(
    bot: LeagueBot,
    *,
    signup: "SignupModuleService",
    config: "ConfigService",
    queue: "ChangeQueue",
    channel_id: int,
    track_ids: Sequence[str],
    close_at: str | None,
) -> int:
    """Post the information message and its Sign Up button in *channel_id*, and give the id of the
    message posted.

    Raises `StepFailedOnDiscord` where the channel cannot be found or Discord refuses the post.
    The caller records the id, in the save that marks its job done.
    """
    guild = await league_guild(bot)
    found = None if guild is None else guild.get_channel(int(channel_id))
    if guild is None or not isinstance(found, discord.TextChannel):
        raise StepFailedOnDiscord("the signup channel could not be found to post in")
    server_cfg = await config.get_server_config()
    base_role_id = server_cfg.base_role_id if server_cfg is not None else None
    base_role = guild.get_role(base_role_id) if base_role_id else None
    tracks = [str(each) for each in track_ids]
    if tracks:
        async with get_connection(bot.db_path) as db:
            names = await track_service.get_track_name_map(db)
        tracks_display = "\n".join(f"• {names[each]}" for each in tracks)
    else:
        tracks_display = "No tracks specified"
    settings = await signup.get_settings()
    slots = await signup.get_slots()
    close_line = ""
    if close_at:
        parsed = datetime.fromisoformat(close_at)
        close_line = f"\n**Auto-closes:** {stamp(parsed)} ({stamp(parsed, 'R')})"
    role_line = f"\n\n{base_role.mention} — click below to sign up!" if base_role else ""
    time_type = "Time Trial" if settings.time_type == "TIME_TRIAL" else "Short Qualification"
    embed = discord.Embed(
        title="🏁 Driver Signups Are Open!",
        description=(
            "**Available time slots:**\n"
            + "\n".join(f"• {s.display_label}" for s in slots)
            + f"\n\n**Tracks:**\n{tracks_display}"
            + f"\n\n**Time type:** {time_type}"
            + f"\n**Time image proof:** {'Required' if settings.time_image_required else 'Not required'}"
            + f"\n**Nationality:** {'Required' if settings.nationality_required else 'Not required'}"
            + close_line
            + role_line
        ),
        color=discord.Color.green(),
    )
    mentions = (
        discord.AllowedMentions(roles=[base_role]) if base_role
        else discord.AllowedMentions.none()
    )
    return await post_standing_message(
        found, embed=embed, view=SignupButtonView(queue), mentions=mentions,
        refused=f"the information message could not be posted in channel {found.id}",
    )
