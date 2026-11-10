"""Images' own switch, on and off, as two settings on the change queue.

`/module enable images` and `/module disable images` each ask one change built from a
`Setting` below by `setting_change` (`core/services/setting_change.py`, which holds what every
settings change does with a value already held and with a fault): `module_on("images")` and
`module_off("images")` (`core/models/change.py`), so core's cog names the change without
importing image. `docs/design/architecture.md`, "How modules and core fit together", designs the
gap this closes: weather's switch (`weather/services/module_switch_settings.py`) is the model.

**The switch-on is one save.** The configuration row and every aspect toggle are created where
missing (`create_defaults_on`, the same `INSERT OR IGNORE`s `ImageConfigService.create_with_defaults`
commits for its own callers, run here on the save's own connection and left uncommitted so the
module stays off until everything below has been written too); the flag is raised and the audit
entry written, so a fault anywhere in the save leaves none of it: no
configuration, no flag, no audit row — never the module on with nothing configured, and never a
part-made configuration a later enable would build on. The module enables even without the host's
rasteriser (FR-007, FR-008), so the reply adds the notice where one is missing, read here rather
than at the check: refusing would leave a league unable to finish setting the module up while
waiting for Inkscape to be installed, which is exactly the case the notice exists to help with.

**The switch-off is one save: the flag and the audit entry.** It deletes nothing and resets no
toggle, which is the Principle X.6 exception for configuration that cannot go stale (FR-004a) — no
image config value names a Discord channel, role or message, so none of those can become a stale
binding while the module is off. It removes nothing else: the daily portrait job that `pfp_daily`
arms stays armed while the module is off.

**Each switch asks the hub's panel to be refreshed, once, as an `After` step**
(`hub_service.hub_refresh_after`), rather than the command doing it after every press.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import AuditRecord, PlannedStep, Verdict, module_off, module_on
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.hub_service import REFRESH_HUB, hub_refresh_after
from leaguebot.core.services.season_lifecycle_service import (
    FROZEN_FOR_COMPLETION_REFUSAL,
    PLACEMENTS_CONFIRMED_REFUSAL,
    modules_frozen_for_completion,
)
from leaguebot.core.services.setting_change import Saved, Setting
from leaguebot.image.services.image_config_service import create_defaults_on

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService

_ENABLE_COMMAND = "/module enable images"
_DISABLE_COMMAND = "/module disable images"


async def images_enabled_on(db: "aiosqlite.Connection") -> bool:
    """Whether the image module is on, read on *db*: the module's switch reader, which the builder
    signs up with `ModuleService` and the module's own work asks. A league that has never switched
    it on has no row, and reads as off."""
    row = await (await db.execute("SELECT module_enabled FROM image_config")).fetchone()
    return row is not None and bool(row["module_enabled"])


_ENABLED = "✅ Image module enabled. All output aspects start disabled — use `/images config toggle`."


def _still_off(_payload: dict[str, Any]) -> str:
    return "The module is still off. Run `/module enable images` again once the fault is cleared."


def module_on_images(*, modules: "ModuleService", seasons: "SeasonService") -> Setting:
    """`module.on:images`: the configuration created where missing, the flag and the audit, in
    one save.

    **The check reads the season's stage again as the change starts,** not only at the ask
    (`module_cog.py`'s stage refusal): asked while no season's placements are confirmed, and run
    once an approval waiting ahead of it on the lane has confirmed them, the save still finds
    them confirmed and refuses, in the words the ask already gives, rather than switching
    images on mid-season.
    """

    async def check(_ctx: CheckContext) -> Verdict:
        if await modules.is_images_enabled():
            return Verdict.refuse("⚠️ Image module is already enabled.")
        if await seasons.get_confirmed_season() is not None:
            return Verdict.refuse(PLACEMENTS_CONFIRMED_REFUSAL)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", _payload: dict[str, Any]) -> Saved:
        await create_defaults_on(db)
        await db.execute("UPDATE image_config SET module_enabled = 1")
        return Saved(
            changed=True,
            audits=(
                AuditRecord(change_type="IMAGE_MODULE_ENABLED", old_value={}, new_value={}),
            ),
            then=(PlannedStep(REFRESH_HUB),),
        )

    def reply(_payload: dict[str, Any], _results: Mapping[str, dict[str, Any]]) -> str:
        from leaguebot.image.services.image_render_service import (
            converter_absent_message,
            converter_available,
        )

        if converter_available(use_cache=False):
            return _ENABLED
        return f"{_ENABLED}\n\n{converter_absent_message()}"

    return Setting(
        kind=module_on("images"),
        command=lambda _payload: _ENABLE_COMMAND,
        doing=lambda _payload: "Turning the image module on",
        check=check,
        save=save,
        reply=reply,
        writes=("image_config", "image_aspect_toggles"),
        not_saved=_still_off,
        after={REFRESH_HUB: hub_refresh_after(_ENABLE_COMMAND)},
    )


def module_off_images(*, modules: "ModuleService") -> Setting:
    """`module.off:images`: the flag lowered and the audit entry written, in one save. Nothing is
    deleted and nothing is removed (Principle X.6's exception, which image claims).

    **The check reads Pending completion again as the change starts,** as `results_off_change`'s
    already does: asked before the season reaches it, and run once a change waiting ahead of it
    on the lane has finished the last division, the save still finds the season Pending
    completion and refuses, rather than switching images off in it.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_images_enabled():
            return Verdict.refuse("⚠️ Image module is already disabled.")
        if await modules_frozen_for_completion(ctx.db_path):
            return Verdict.refuse(FROZEN_FOR_COMPLETION_REFUSAL)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", _payload: dict[str, Any]) -> Saved:
        await db.execute("UPDATE image_config SET module_enabled = 0")
        return Saved(
            changed=True,
            audits=(
                AuditRecord(change_type="IMAGE_MODULE_DISABLED", old_value={}, new_value={}),
            ),
            then=(PlannedStep(REFRESH_HUB),),
        )

    def reply(_payload: dict[str, Any], _results: Mapping[str, dict[str, Any]]) -> str:
        return (
            "✅ Image module disabled. Every aspect reverts to text output.\n"
            "Your configuration is kept — re-enabling restores it exactly."
        )

    return Setting(
        kind=module_off("images"),
        command=lambda _payload: _DISABLE_COMMAND,
        doing=lambda _payload: "Turning the image module off",
        check=check,
        save=save,
        reply=reply,
        writes=("image_config",),
        after={REFRESH_HUB: hub_refresh_after(_DISABLE_COMMAND)},
    )


__all__ = ["module_off_images", "module_on_images"]
