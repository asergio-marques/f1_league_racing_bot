"""Results & Standings' own switch-on, as a setting on the change queue.

`/module enable results` asks one change built from the `Setting` below by `setting_change`
(`core/services/setting_change.py`, which holds what every settings change does with a value
already held and with a fault): `module_on("results")` (`core/models/change.py`), so core's cog
names the change without importing results. `docs/design/architecture.md`, "How modules and core
fit together", designs the gap this closes: weather's switch
(`weather/services/module_switch_settings.py`) is the model.

**The switch-on is one save: the flag and the audit entry.** A fault leaves the module off, so
the member is told to run the command again rather than being left unsure whether it half-ran.
Once it has changed something it asks the hub's panel to be refreshed, as an `After` step
(`hub_service.hub_refresh_after`): the panel offers what a module offers while it is on, so the
switch asks for that itself rather than the command doing it after every press.

The switch-off is `results_off_change.py`'s: more than a flag flip, it closes the season's rounds
and takes down its messages, so it is its own change on the queue (#439), built apart from the
switch-on here, and asks the hub's refresh in its own save already.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import AuditRecord, PlannedStep, Verdict, module_on
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.hub_service import REFRESH_HUB, hub_refresh_after
from leaguebot.core.services.season_lifecycle_service import PLACEMENTS_CONFIRMED_REFUSAL
from leaguebot.core.services.setting_change import Saved, Setting

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService

_ENABLE_COMMAND = "/module enable results"


def _still_off(_payload: dict[str, Any]) -> str:
    return "The module is still off. Run `/module enable results` again once the fault is cleared."


def module_on_results(*, modules: "ModuleService", seasons: "SeasonService") -> Setting:
    """`module.on:results`: the flag raised and the audit, in one save.

    **The check reads the season's stage again as the change starts,** not only at the ask
    (`module_cog.py`'s stage refusal): asked while no season's placements are confirmed, and run
    once an approval waiting ahead of it on the lane has confirmed them, the save still finds
    them confirmed and refuses, in the words the ask already gives, rather than switching
    results on mid-season.
    """

    async def check(_ctx: CheckContext) -> Verdict:
        if await modules.is_results_enabled():
            return Verdict.refuse("⚠️ Results & Standings module is already enabled.")
        if await seasons.get_confirmed_season() is not None:
            return Verdict.refuse(PLACEMENTS_CONFIRMED_REFUSAL)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", _payload: dict[str, Any]) -> Saved:
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, 1)"
        )
        return Saved(
            changed=True,
            audits=(
                AuditRecord(
                    change_type="MODULE_ENABLE", old_value={}, new_value={"module": "results"}
                ),
            ),
            then=(PlannedStep(REFRESH_HUB),),
        )

    def reply(_payload: dict[str, Any], _results: Mapping[str, dict[str, Any]]) -> str:
        return "✅ Results & Standings module enabled."

    return Setting(
        kind=module_on("results"),
        command=lambda _payload: _ENABLE_COMMAND,
        doing=lambda _payload: "Turning Results & Standings on",
        check=check,
        save=save,
        reply=reply,
        writes=("results_module_config",),
        not_saved=_still_off,
        after={REFRESH_HUB: hub_refresh_after(_ENABLE_COMMAND)},
    )


__all__ = ["module_on_results"]
