"""A division's check-in call (RSVP) and attendance channels, as settings on the change queue.

`/attendance channel rsvp` and `/attendance channel attendance` each ask one change built from the
`Setting` below by `setting_change` (`core/services/queue/setting_change.py`, which holds what every
settings change does with a value already held and with a fault), `{"division_id",
"division_name", "value", "channel_name"}`, for the division by its id: the command finds it from
the name a manager typed and carries that name for the reply and the line, so a division renamed
while the change waits still gets its channel. The two share one body and differ in their column,
their words and their audit entry.

**The refusals are the check, in the command's order and words**, made at the ask and again as the
change starts, since a change ahead of it may have switched attendance off, ended the season,
deleted the division or given the channel to another job: the module on; a channel the bot may post
in (read from the channel as the bot finds it by its id, so a channel gone or one the bot has lost
its permission in is refused as the command refuses it); a season live; the division in it; then
the channel free of any other job (`channel_registry_service`), the channel this very division
already has being refused as "Nothing was changed".

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

from leaguebot.attendance.services.division_channels import (
    set_attendance_channel,
    set_rsvp_channel,
)
from leaguebot.core.models.change import AuditRecord, Verdict
from leaguebot.core.services.queue.change_types import CheckContext
from leaguebot.core.services.settings.channel_registry_service import (
    ChannelUse,
    as_text_channel,
    find_channel_use,
    refusal,
)
from leaguebot.core.services.settings.division_settings import NO_SEASON_REFUSAL
from leaguebot.core.services.queue.setting_change import Saved, Setting

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.seasons.season_service import SeasonService


RSVP_CHANNEL = "attendance.setting.rsvp_channel"
ATTENDANCE_CHANNEL = "attendance.setting.attendance_channel"

NOT_ENABLED = "❌ The Attendance module is not enabled."
NO_ACCESS = "❌ Cannot access that channel. Ensure the bot has permission to post there."


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
    channel_of: Callable[[int], object],
) -> Setting:
    """The setting of the *key* channel (the channel registry's key, which is also the word of the
    command), *called* the name the acknowledgement gives it (its reply capitalises it), *write*
    its column's upsert and *change_type* the audit entry of the channel moving from one to
    another in a division. *channel_of* finds a channel by its id, for the bot's access to it to
    be judged."""

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        value = int(payload["value"])
        if not await modules.is_attendance_enabled():
            return Verdict.refuse(NOT_ENABLED)
        if not _may_post(channel_of(value)):
            return Verdict.refuse(NO_ACCESS)
        season = await seasons.get_setup_or_active_season()
        if season is None:
            return Verdict.refuse(NO_SEASON_REFUSAL)
        division = await seasons.get_division(int(payload["division_id"]))
        if division is None or division.season_id != season.id:
            return Verdict.refuse(division_not_found(payload["division_name"]))
        use = await find_channel_use(
            ctx.db_path, value, channel_uses_read=ctx.bot.channel_uses_read
        )
        if use is not None:
            return Verdict.refuse(
                refusal(f"<#{value}>", use, same_setting=(use == ChannelUse(key, division.name)))
            )
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


def _may_post(channel: object) -> bool:
    """Whether the bot finds *channel* and may post in it."""
    found = as_text_channel(channel)
    return found is not None and found.permissions_for(found.guild.me).send_messages


def attendance_channel_settings(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    channel_of: Callable[[int], object],
) -> tuple[Setting, ...]:
    """The RSVP channel's setting and the attendance channel's: the module and the season are read
    through the services handed in, and the channel the bot finds by its id through
    *channel_of*."""
    return (
        _channel_setting(
            kind=RSVP_CHANNEL, key="rsvp", called="RSVP", write=set_rsvp_channel,
            change_type="RSVP_CHANNEL_SET", modules=modules, seasons=seasons,
            channel_of=channel_of,
        ),
        _channel_setting(
            kind=ATTENDANCE_CHANNEL, key="attendance", called="attendance",
            write=set_attendance_channel, change_type="ATTENDANCE_CHANNEL_SET",
            modules=modules, seasons=seasons, channel_of=channel_of,
        ),
    )


__all__ = [
    "ATTENDANCE_CHANNEL", "NOT_ENABLED", "NO_ACCESS", "RSVP_CHANNEL",
    "attendance_channel_settings", "division_not_found",
]
