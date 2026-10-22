"""The points configurations as settings on the change queue (#439, slice 7).

Each `/results config …` command that changes the league's points configurations asks one
`results.setting.…` change, built from a `Setting` below by `setting_change`
(`core/services/setting_change.py`, which holds what every settings change does with a value
already held and with a fault). The check of each is the command's, in today's order and words,
run at the ask and again as the change starts, so a configuration removed, a module turned off or
a season taken up by a change ahead of it on the queue is taken into account. Every check refuses
first where the results module is off.

A name typed is held to the rules every name is held to in the command, at once, before it is
asked (`NAME.check`); a change's check judges only the state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import Verdict
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.setting_change import Saved, Setting
from leaguebot.results.services import points_config_service

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService

CONFIG_ADD = "results.setting.config_add"
CONFIG_REMOVE = "results.setting.config_remove"

MODULE_OFF = "❌ The Results & Standings module is not enabled on this server."


def _not_found(name: str) -> str:
    return f"❌ Config **{name}** not found."


def _config_add(modules: "ModuleService") -> Setting:
    """`/results config add`: a configuration, every position at nought points."""

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_results_enabled():
            return Verdict.refuse(MODULE_OFF)
        name = str(ctx.payload["config"])
        if await points_config_service.config_exists(ctx.db_path, name):
            return Verdict.refuse(f"❌ A config named **{name}** already exists on this server.")
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        return await points_config_service.create_config_on(db, str(payload["config"]))

    def reply(payload: dict[str, Any], _results: Any) -> str:
        return f"✅ Config **{payload['config']}** created. All positions default to 0 points."

    return Setting(
        kind=CONFIG_ADD,
        command=lambda _payload: "/results config add",
        doing=lambda payload: f"Adding points configuration **{payload['config']}**",
        check=check,
        save=save,
        reply=reply,
        writes=("points_config_store",),
    )


def _config_remove(modules: "ModuleService") -> Setting:
    """`/results config remove`, straight or by its confirmation button.

    A request not yet confirmed is refused where a season in setup now stands on the
    configuration: the command asks first when one does (`confirmed` False means none stood when
    it was asked), and a season that took the configuration up meanwhile has not been asked about.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_results_enabled():
            return Verdict.refuse(MODULE_OFF)
        name = str(ctx.payload["config"])
        if not await points_config_service.config_exists(ctx.db_path, name):
            return Verdict.refuse(_not_found(name))
        if not ctx.payload.get("confirmed"):
            standing = await points_config_service.setup_seasons_linking(ctx.db_path, name)
            if standing:
                seasons = ", ".join(f"Season #{number}" for _, number in standing)
                return Verdict.refuse(
                    f"❌ **{name}** was attached to {seasons} since you asked, so it was not "
                    "removed. Run `/results config remove` again to be asked first."
                )
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        return await points_config_service.remove_config_on(db, str(payload["config"]))

    def reply(payload: dict[str, Any], _results: Any) -> str:
        return f"✅ Config **{payload['config']}** removed."

    return Setting(
        kind=CONFIG_REMOVE,
        command=lambda _payload: "/results config remove",
        doing=lambda payload: f"Removing points configuration **{payload['config']}**",
        check=check,
        save=save,
        reply=reply,
        # The deletion cascades to the configuration's positions and fastest laps, which the
        # queue's authoriser sees as writes.
        writes=(
            "points_config_store", "points_config_entries", "points_config_fl",
            "season_points_links",
        ),
    )


def points_settings(*, modules: "ModuleService", seasons: "SeasonService") -> tuple[Setting, ...]:
    """The points configurations' settings: the services are read through those handed in."""
    del seasons  # handed now for the settings that read the season (the append, the detach)
    return (_config_add(modules), _config_remove(modules))


__all__ = ["CONFIG_ADD", "CONFIG_REMOVE", "MODULE_OFF", "points_settings"]
