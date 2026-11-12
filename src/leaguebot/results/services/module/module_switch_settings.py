"""Results & Standings' own switch-on, as a setting on the change queue.

`/module enable results` asks one change built from the `Setting` below by `setting_change`
(`core/services/queue/setting_change.py`, which holds what every settings change does with a value
already held and with a fault): `module_on("results")` (`core/models/change.py`), so core's cog
names the change without importing results. `docs/design/architecture.md`, "How modules and core
fit together", designs the gap this closes: weather's switch
(`weather/services/module_switch_settings.py`) is the model.

**The switch-on is one save: the flag and the audit entry.** A fault leaves the module off, so
the member is told to run the command again rather than being left unsure whether it half-ran.
Once it has changed something it asks the hub's panel to be refreshed, as an `After` step
(`hub_service.hub_refresh_after`): the panel offers what a module offers while it is on, so the
switch asks for that itself rather than the command doing it after every press.

**It arms the sweep that undoes a lapsed amendment,** as a further `After` step, so the lapse rule
holds from the switch-on and not only from the next restart, whose start-up turn arms it too
(`wiring.arm_amendment_sweep`). Under the core specification's rule for work after a setting, an
arming that fails leaves results on, does not stop the lane, and says what was not done.

The switch-off is `results_off_change.py`'s: more than a flag flip, it closes the season's rounds
and takes down its messages, so it is its own change on the queue (#439), built apart from the
switch-on here, and asks the hub's refresh in its own save already.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import (
    AuditRecord,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
    module_on,
)
from leaguebot.core.services.queue.change_types import CheckContext, Step, StepContext
from leaguebot.core.services.modules.hub_service import REFRESH_HUB, hub_refresh_after
from leaguebot.core.services.seasons.season_lifecycle_service import PLACEMENTS_CONFIRMED_REFUSAL
from leaguebot.core.services.queue.setting_change import After, Saved, Setting

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.seasons.season_service import SeasonService

#: The name of the step, after the switch-on's save, that arms the amendment sweep.
ARM_SWEEP = "arm_amendment_sweep"

_ENABLE_COMMAND = "/module enable results"

_SWEEP_NOT_ARMED = (
    "⚠️ The module is on, but the check that undoes an amendment left unapproved past its time "
    "limit is not set. Restarting the bot sets it."
)


async def results_enabled_on(db: "aiosqlite.Connection") -> bool:
    """Whether Results & Standings is on, read on *db*: the module's switch reader, which the
    builder signs up with `ModuleService` and the module's own work asks. A league that has never
    switched it on has no row, and reads as off."""
    row = await (await db.execute("SELECT module_enabled FROM results_module_config")).fetchone()
    return row is not None and bool(row["module_enabled"])


def _still_off(_payload: dict[str, Any]) -> str:
    return "The module is still off. Run `/module enable results` again once the fault is cleared."


def _arm_sweep(scheduler: "SchedulerService") -> After:
    """The step that arms the amendment sweep once the flag is up.

    It may fail without holding up the queue: the switch-on stays saved, and the reply says the
    sweep is not set.
    """

    async def run(_ctx: StepContext) -> StepResult:
        scheduler.schedule_amendment_sweep()
        return StepResult()

    def fault_reply(_payload: dict[str, Any], reply: str) -> str:
        return f"{reply}\n{_SWEEP_NOT_ARMED}"

    return After(step=Step(ARM_SWEEP, StepKind.ACT, run), fault_reply=fault_reply)


def module_on_results(
    *, modules: "ModuleService", seasons: "SeasonService", scheduler: "SchedulerService"
) -> Setting:
    """`module.on:results`: the flag raised and the audit, in one save, then the amendment sweep
    armed in a step of its own, after the hub's refresh.

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
            then=(PlannedStep(REFRESH_HUB), PlannedStep(ARM_SWEEP)),
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
        after={
            REFRESH_HUB: hub_refresh_after(_ENABLE_COMMAND),
            ARM_SWEEP: _arm_sweep(scheduler),
        },
    )


__all__ = ["ARM_SWEEP", "module_on_results"]
