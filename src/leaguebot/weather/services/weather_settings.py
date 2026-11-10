"""The weather deadlines and a division's forecast channel, as settings on the change queue.

Each is built from a `Setting` below by `setting_change` (`core/services/setting_change.py`,
which holds what every settings change does with a value already held and with a fault).

**The deadlines.** `/weather config phase-1-deadline`, `phase-2-deadline` and `phase-3-deadline`
each ask one `weather.setting.deadline` change, `{"column", "value"}`. The check is the command's,
in its order, and is run at the ask and again as the change starts: the module on, no season's
placements confirmed, the value at least one, then the ordering against the configuration as it
stands then, so a deadline set ahead of it on the queue is taken into account. A value already
held is no refusal: the save judges it (weather specification :39).

**The forecast channel.** `/weather channel` asks one `weather.setting.forecast_channel` change,
`{"division_id", "division_name", "value", "channel_name"}`, for the division by its id: the
command finds it from the name a manager typed and carries that name for the reply and the line,
so a division renamed while the change waits still gets its channel. The refusals are the
check, in the command's order and words, made at the ask and again as the change starts, since a
change ahead of it may have switched weather off, ended the season, deleted the division or given
the channel to another job: the module on, a season live, the division in it, then the channel
free of any other job (`channel_registry_service`), the channel this very division already has
being refused as "Nothing was changed". The save writes the division's column and the audit
entry with the division, together, the audit entry stamped by the queue's clock; the column it
replaces is read in the save, never at the ask, so a change waiting behind another for the same
division records what that one left, and its reply reads "set" or "updated" by it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import AuditRecord, Verdict
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.channel_registry_service import (
    ChannelUse,
    find_channel_use,
    refusal,
)
from leaguebot.core.services.division_settings import NO_SEASON_REFUSAL, division_not_found
from leaguebot.core.services.setting_change import Saved, Setting
from leaguebot.weather.services.forecast_channel import set_division_forecast_channel
from leaguebot.weather.services.weather_config_service import (
    deadline_refusal,
    get_weather_pipeline_config,
    set_deadline_on,
)

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService

DEADLINE = "weather.setting.deadline"
FORECAST_CHANNEL = "weather.setting.forecast_channel"

NOT_ENABLED = "❌ The Weather module is not enabled."

#: Each deadline column: its command, the name a refusal gives it, its unit and its unit's short
#: form, as the replies word them.
_PHASES: dict[str, tuple[str, str, str, str]] = {
    "phase_1_days": ("phase-1-deadline", "Phase 1", "day(s)", "d"),
    "phase_2_days": ("phase-2-deadline", "Phase 2", "day(s)", "d"),
    "phase_3_hours": ("phase-3-deadline", "Phase 3", "hour(s)", "h"),
}

_CONFIRMED = "❌ Phase deadline configuration cannot be changed once a season's placements are confirmed."


def _shown(payload: dict[str, Any]) -> str:
    """The payload's value in its unit: "6 day(s)"."""
    return f"{payload['value']} {_PHASES[payload['column']][2]}"


def deadline_setting(*, modules: "ModuleService", seasons: "SeasonService") -> Setting:
    """The deadline setting: the module and the season are read through the services handed in."""

    async def check(ctx: CheckContext) -> Verdict:
        column = str(ctx.payload["column"])
        value = int(ctx.payload["value"])
        _command, phase, unit, _short = _PHASES[column]
        if not await modules.is_weather_enabled():
            return Verdict.refuse(NOT_ENABLED)
        if await seasons.get_confirmed_season() is not None:
            return Verdict.refuse(_CONFIRMED)
        if value < 1:
            return Verdict.refuse(
                f"❌ {phase} deadline must be at least 1 {unit.removesuffix('(s)')}."
            )
        refusal = deadline_refusal(await get_weather_pipeline_config(ctx.db_path), column, value)
        if refusal is not None:
            return Verdict.refuse(f"❌ {refusal}")
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        return await set_deadline_on(db, str(payload["column"]), int(payload["value"]))

    def command(payload: dict[str, Any]) -> str:
        return f"/weather config {_PHASES[payload['column']][0]}"

    def doing(payload: dict[str, Any]) -> str:
        return f"Setting the {_PHASES[payload['column']][1]} deadline to {_shown(payload)}"

    def reply(payload: dict[str, Any], results: Any) -> str:
        column = str(payload["column"])
        phase = _PHASES[column][1]
        saved = results["save"]
        if not saved["changed"]:
            return f"ℹ️ Nothing changed: the {phase} deadline is already **{_shown(payload)}**."
        others = ", ".join(
            f"{_PHASES[name][1]}: {saved[name]}{_PHASES[name][3]}"
            for name in _PHASES
            if name != column
        )
        return f"✅ {phase} deadline set to **{_shown(payload)}** before round. ({others})"

    return Setting(
        kind=DEADLINE,
        command=command,
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("weather_pipeline_config",),
    )


def forecast_channel_setting(*, modules: "ModuleService", seasons: "SeasonService") -> Setting:
    """The forecast channel setting: the module and the season are read through the services
    handed in."""

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        if not await modules.is_weather_enabled():
            return Verdict.refuse(NOT_ENABLED)
        season = await seasons.get_setup_or_active_season()
        if season is None:
            return Verdict.refuse(NO_SEASON_REFUSAL)
        division = await seasons.get_division(int(payload["division_id"]))
        if division is None or division.season_id != season.id:
            return Verdict.refuse(division_not_found(payload["division_name"]))
        value = int(payload["value"])
        use = await find_channel_use(
            ctx.db_path, value, channel_uses_read=ctx.bot.channel_uses_read
        )
        if use is not None:
            return Verdict.refuse(
                refusal(
                    f"<#{value}>", use, same_setting=(use == ChannelUse("weather", division.name))
                )
            )
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        value = int(payload["value"])
        division_id = int(payload["division_id"])
        details = (f"division: {payload['division_name']}", f"channel: #{payload['channel_name']}")
        old_value = await set_division_forecast_channel(db, division_id, value)
        if old_value == value:
            return Saved(changed=False, details=details)
        return Saved(
            changed=True,
            audits=(
                AuditRecord(
                    "DIVISION_CHANNEL_SET",
                    {"channel_type": "weather", "channel_id": old_value},
                    {"channel_type": "weather", "channel_id": value},
                    division_id,
                ),
            ),
            details=details,
            result={"replaced": old_value},
        )

    def doing(payload: dict[str, Any]) -> str:
        return (
            f"Setting the weather forecast channel for **{payload['division_name']}** to "
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
            return (
                f"ℹ️ {mention} is already the weather forecast channel for **{name}**. "
                f"Nothing was changed."
            )
        verb = "set" if saved["replaced"] is None else "updated"
        return f"✅ Weather forecast channel for **{name}** {verb} to {mention}."

    return Setting(
        kind=FORECAST_CHANNEL,
        command=lambda _payload: "/weather channel",
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("divisions",),
        repairs_the_bot=True,
    )


__all__ = ["DEADLINE", "FORECAST_CHANNEL", "deadline_setting", "forecast_channel_setting"]
