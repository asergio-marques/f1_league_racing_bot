"""A division's lineup channel and calendar channel, as core `Setting`s on the change queue.

`/division lineup-channel` and `/division calendar-channel` each ask one change built from a
`Setting` below by `setting_change` (`core/services/setting_change.py`, which holds what every
settings change does with a fault). Both are every season's, whatever modules are on, so they are
core's and wait their turn on the league lane like any other change; the division's other
channels are its modules' own.

**The division is identified by its id**, which the command finds from the name a manager typed
and carries in the payload with that name and the channel's. The change is for the division asked
for, whatever it is called when it runs: a division renamed while the change waits still gets its
channel, and the reply and the line name it as it was typed.

**The refusals are the setting's `check`, made at the ask and again as the change starts**, since
a change queued ahead of this one may have ended the season, deleted the division or given the
channel to another job since it was asked: no season live, the division not in it, a channel
already doing another job, and the channel this very division already has ("Nothing was
changed"), each in the words the ask gave. A channel already doing another job is judged by the
channel registry (`channel_registry_service`), the one rule every channel command answers to.

**The save writes the division's column and the audit entry, with the division, together**, the
audit entry stamped by the queue's clock; the column it replaces is read in the save, never at the
ask, so a change waiting behind another for the same division records what that one left, and its
reply reads "set" or "updated" by it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import sole_row
from leaguebot.core.models.change import AuditRecord, Verdict
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.channel_registry_service import (
    ChannelUse,
    find_channel_use,
    refusal,
)
from leaguebot.core.services.setting_change import Saved, Setting

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService

NO_SEASON_REFUSAL = (
    "❌ No season is live. A division's channels belong to the season being built or raced — "
    "start one with `/season setup`."
)

_SAVE_STEP = "save"

#: division column → (the channel registry's key for the setting, the command as its line names
#: it, the label its reply gives the setting, the audit's change type, the statement that reads
#: the channel it holds, the statement that sets it). The statements are written out, a column
#: spliced in at run time being a write the architecture's scan cannot read.
_CHANNELS: dict[str, tuple[str, str, str, str, str, str]] = {
    "lineup_channel_id": (
        "lineup", "/division lineup-channel", "Lineup channel", "SIGNUP_LINEUP_CHANNEL_SET",
        "SELECT lineup_channel_id AS channel_id FROM divisions WHERE id = ?",
        "UPDATE divisions SET lineup_channel_id = ? WHERE id = ?",
    ),
    "calendar_channel_id": (
        "calendar", "/division calendar-channel", "Calendar channel",
        "DIVISION_CALENDAR_CHANNEL_SET",
        "SELECT calendar_channel_id AS channel_id FROM divisions WHERE id = ?",
        "UPDATE divisions SET calendar_channel_id = ? WHERE id = ?",
    ),
}


def division_not_found(name: str) -> str:
    """Why a division *name* the manager typed is refused: it is not in the current season."""
    return f"❌ Division **{name}** not found in the current season."


def _division_channel(column: str, seasons: "SeasonService") -> Setting:
    """The setting of a division's *column*, *seasons* judging which season is live."""
    key, command, label, change_type, read, write = _CHANNELS[column]

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        season = await seasons.get_setup_or_active_season()
        if season is None:
            return Verdict.refuse(NO_SEASON_REFUSAL)
        division = await seasons.get_division(int(payload["division_id"]))
        if division is None or division.season_id != season.id:
            return Verdict.refuse(division_not_found(payload["division_name"]))
        value = int(payload["value"])
        use = await find_channel_use(ctx.db_path, value)
        if use is not None:
            return Verdict.refuse(
                refusal(
                    f"<#{value}>", use, same_setting=(use == ChannelUse(key, division.name))
                )
            )
        return Verdict.go()

    async def save(db: aiosqlite.Connection, payload: dict[str, Any]) -> Saved:
        value = int(payload["value"])
        division_id = int(payload["division_id"])
        old_value = (await sole_row(await db.execute(read, (division_id,))))["channel_id"]
        details = (f"division: {payload['division_name']}", f"channel: #{payload['channel_name']}")
        if old_value == value:
            return Saved(changed=False, details=details)
        await db.execute(write, (value, division_id))
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
            f"Setting the {key} channel for **{payload['division_name']}** to "
            f"<#{int(payload['value'])}>"
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        saved = results[_SAVE_STEP]
        mention = f"<#{int(payload['value'])}>"
        name = payload["division_name"]
        if not saved["changed"]:
            return f"ℹ️ Nothing changed: {label} for **{name}** is already {mention}."
        verb = "set" if saved["replaced"] is None else "updated"
        return f"✅ {label} for **{name}** {verb} to {mention}."

    return Setting(
        kind=f"core.setting.{column}",
        command=lambda _payload: command,
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("divisions",),
    )


def division_channel_settings(*, seasons: "SeasonService") -> tuple[Setting, ...]:
    """The lineup channel's setting and the calendar channel's."""
    return tuple(_division_channel(column, seasons) for column in _CHANNELS)


__all__ = ["NO_SEASON_REFUSAL", "division_channel_settings", "division_not_found"]
