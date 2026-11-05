"""Results & Standings' own switch-on, as a setting on the change queue.

`/module enable results` asks one change built from the `Setting` below by `setting_change`
(`core/services/setting_change.py`, which holds what every settings change does with a value
already held and with a fault): `module_on("results")` (`core/models/change.py`), so core's cog
names the change without importing results. `docs/design/architecture.md`, "How modules and core
fit together", designs the gap this closes: weather's switch
(`weather/services/module_switch_settings.py`) is the model.

**The switch-on is one save: the flag and the audit entry.** A fault leaves the module off, so
the member is told to run the command again rather than being left unsure whether it half-ran.

The switch-off is already its own change on the queue (`results_off_change.py`, #439) and is
unchanged by this: it is more than a flag flip, closing the season's rounds and taking down its
messages, and that shape is not this change's to touch.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import AuditRecord, Verdict, module_on
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.setting_change import Saved, Setting

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.module_service import ModuleService

_ENABLE_COMMAND = "/module enable results"


def _still_off(_payload: dict[str, Any]) -> str:
    return "The module is still off. Run `/module enable results` again once the fault is cleared."


def module_on_results(*, modules: "ModuleService") -> Setting:
    """`module.on:results`: the flag raised and the audit, in one save."""

    async def check(_ctx: CheckContext) -> Verdict:
        if await modules.is_results_enabled():
            return Verdict.refuse("⚠️ Results & Standings module is already enabled.")
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
    )


__all__ = ["module_on_results"]
