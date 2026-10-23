"""The weather deadlines as a setting on the change queue (#439, slice 7).

`/weather config phase-1-deadline`, `phase-2-deadline` and `phase-3-deadline` each ask one
`weather.setting.deadline` change, `{"column", "value"}`, built from the `Setting` below by
`setting_change` (`core/services/setting_change.py`, which holds what every settings change does
with a value already held and with a fault). The check is the command's, in today's order, and is
run at the ask and again as the change starts: the module on, no season's placements confirmed,
the value at least one, then the ordering against the configuration as it stands then, so a
deadline set ahead of it on the queue is taken into account. A value already held is no refusal:
the save judges it (weather specification :39).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import Verdict
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.setting_change import Saved, Setting
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
            return Verdict.refuse("❌ The Weather module is not enabled.")
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


__all__ = ["DEADLINE", "deadline_setting"]
