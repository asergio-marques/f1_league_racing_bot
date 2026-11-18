"""A division's check-in call (RSVP) and attendance channels, as settings on the change queue.

`/attendance channel rsvp` and `/attendance channel attendance` each ask one change built from the
`Setting` below by `setting_change` (`framework/services/queue/setting_change.py`, which holds what every
settings change does with a value already held and with a fault), `{"division_id",
"division_name", "value", "channel_name"}`, for the division by its id: the command finds it from
the name a manager typed and carries that name for the reply and the line, so a division renamed
while the change waits still gets its channel. The two share one body and differ in their column,
their words and their audit entry.

**The refusals are the check, in the command's order and words**, made at the ask and again as the
change starts, since a change ahead of it may have switched attendance off, ended the season,
deleted the division or given the channel to another job: the module on; a season live; the
division in it; the channel free of any other job (`channel_registry_service`), the channel this
very division already has being refused as "Nothing was changed"; and last the channel one the bot
has the permissions the setting's channel needs in (`division_channels.channel_needs`, with the
*drawing* the builder is handed, judged by `unusable_refusal` against the league's server as it
stands when the check runs).

**The save writes the division's column and the audit entry with the division, together**, the
audit entry stamped by the queue's clock (`division_channels` holds the upserts); the column it
replaces is read in the save, never at the ask, so a change waiting behind another for the same
division records what that one left, and its reply reads "set" or "updated" by it.

**Each overtakes a stopped job on the league lane** (`Setting.repairs_the_bot`): a stop notice for
a check-in call or a sheet with no channel tells the league to set the channel and press Retry,
which a change waiting behind the stop could never do.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.attendance.models.switch import SWITCH_KEY
from leaguebot.attendance.services.settings.division_channels import (
    channel_needs,
    set_attendance_channel,
    set_rsvp_channel,
)
from leaguebot.framework.models.change import AuditRecord, Verdict
from leaguebot.framework.services.queue.change_types import CheckContext
from leaguebot.core.services.settings.channel_registry_service import (
    ChannelUse,
    find_channel_use,
    refusal,
    unusable_refusal,
)
from leaguebot.core.services.settings.division_settings import NO_SEASON_REFUSAL
from leaguebot.framework.services.queue.setting_change import Saved, Setting
from leaguebot.framework.utils.league_server import league_guild

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.image.services.render.image_drawing import ImageDrawing
    from leaguebot.core.services.seasons.season_service import SeasonService
    from leaguebot.framework.utils.hooks import AwaitedHook


RSVP_CHANNEL = "attendance.setting.rsvp_channel"
ATTENDANCE_CHANNEL = "attendance.setting.attendance_channel"

NOT_ENABLED = "❌ The Attendance module is not enabled."


def division_not_found(name: str) -> str:
    """Why a division *name* the manager typed is refused by `/attendance channel`."""
    return f'❌ Division "{name}" not found.'


def _channel_setting(
    *,
    kind: str,
    key: str,
    called: str,
    write: Callable[["aiosqlite.Connection", int, int | None], Awaitable[int | None]],
    change_type: str,
    modules: "ModuleService",
    seasons: "SeasonService",
    channel_uses_read: "AwaitedHook",
    drawing: "ImageDrawing | None",
) -> Setting:
    """The setting of the *key* channel (the channel registry's key, which is also the word of the
    command), *called* the name the acknowledgement gives it (its reply capitalises it), *write*
    its column's upsert and *change_type* the audit entry of the channel moving from one to
    another in a division. *channel_uses_read* is core's hook that names the module holding a
    channel, and *drawing* the image module's, which says whether the channel's picture could
    attach a file."""

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        value = int(payload["value"])
        if not await modules.is_enabled(SWITCH_KEY):
            return Verdict.refuse(NOT_ENABLED)
        season = await seasons.get_setup_or_active_season()
        if season is None:
            return Verdict.refuse(NO_SEASON_REFUSAL)
        division = await seasons.get_division(int(payload["division_id"]))
        if division is None or division.season_id != season.id:
            return Verdict.refuse(division_not_found(payload["division_name"]))
        use = await find_channel_use(
            ctx.db_path, value, channel_uses_read=channel_uses_read
        )
        if use is not None:
            return Verdict.refuse(
                refusal(f"<#{value}>", use, same_setting=(use == ChannelUse(key, division.name)))
            )
        unusable = await unusable_refusal(
            await league_guild(ctx.bot),
            value,
            (await channel_needs(drawing))[key],
            ChannelUse(key, division.name),
        )
        if unusable is not None:
            return Verdict.refuse(unusable)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        value = int(payload["value"])
        division_id = int(payload["division_id"])
        details = (f"division: {payload['division_name']}", f"channel: #{payload['channel_name']}")
        old_value = await write(db, division_id, value)
        if old_value == value:
            return Saved(changed=False, details=details)
        return Saved(
            changed=True,
            audits=(
                AuditRecord(
                    change_type, {"channel_id": old_value}, {"channel_id": value}, division_id
                ),
            ),
            details=details,
            result={"replaced": old_value},
        )

    def doing(payload: dict[str, Any]) -> str:
        return (
            f"Setting the {called} channel for **{payload['division_name']}** to "
            f"<#{int(payload['value'])}>"
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        saved = results["save"]
        mention = f"<#{int(payload['value'])}>"
        name = payload["division_name"]
        if not saved["changed"]:
            # `check` refuses a channel the division already holds before the save runs, at the
            # ask and as the change starts, with nothing able to write between; this guard
            # answers in the same words should the save ever find it so.
            return refusal(mention, ChannelUse(key, name), same_setting=True)
        verb = "set" if saved["replaced"] is None else "updated"
        return f"✅ {called[:1].upper()}{called[1:]} channel for {name} {verb} to {mention}."

    return Setting(
        kind=kind,
        command=lambda _payload: f"/attendance channel {key}",
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("attendance_division_config",),
        repairs_the_bot=True,
    )


def attendance_channel_settings(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    channel_uses_read: "AwaitedHook",
    drawing: "ImageDrawing | None",
) -> tuple[Setting, ...]:
    """The RSVP channel's setting and the attendance channel's: the module and the season are read
    through the services handed in, what holds a channel through *channel_uses_read*, and whether a
    picture could attach a file through *drawing*."""
    return (
        _channel_setting(
            kind=RSVP_CHANNEL, key="rsvp", called="RSVP", write=set_rsvp_channel,
            change_type="RSVP_CHANNEL_SET", modules=modules, seasons=seasons,
            channel_uses_read=channel_uses_read, drawing=drawing,
        ),
        _channel_setting(
            kind=ATTENDANCE_CHANNEL, key="attendance", called="attendance",
            write=set_attendance_channel, change_type="ATTENDANCE_CHANNEL_SET",
            modules=modules, seasons=seasons,
            channel_uses_read=channel_uses_read, drawing=drawing,
        ),
    )


__all__ = [
    "ATTENDANCE_CHANNEL", "NOT_ENABLED", "RSVP_CHANNEL",
    "attendance_channel_settings", "division_not_found",
]
