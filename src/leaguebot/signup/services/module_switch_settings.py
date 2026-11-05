"""Signup's own switch-on, as a setting on the change queue.

`/module enable signup` asks one change built from the `Setting` below by `setting_change`
(`core/services/setting_change.py`, which holds what every settings change does with a value
already held and with a fault): `module_on("signup")` (`core/models/change.py`), so core's cog
names the change without importing signup, as `docs/design/architecture.md`, "How modules and
core fit together", designs it: weather's switch
(`weather/services/module_switch_settings.py`) is the model. Signup's switch-off is
`signup_off_change.py`'s own change; the switch-on here is its match.

**The switch-on is one save: the flag, a bare configuration row created only where none stands,
and the audit entry.** Creating only what is missing, rather than replacing a standing row's
values, keeps a configuration that survived a switch-off intact — the time slots and question
settings already stand again there (issue #127), and a configuration row restored from a backup
is kept rather than blanked. Principle X.6's "re-enabling starts fresh" does not apply here: the
signup specification already says what a disable keeps, and the bare row this creates where none
exists is what the module needs to be usable, not a reset of what is there.

**The flag is signup's own column on core's table, `server_configs.signup_module_enabled`**
(architecture.md, "The database"; `OWN_COLUMNS` in `tests/repository/test_architecture_rules.py`),
as weather's is.

**The check reads the season's stage again as the change starts,** not only at the ask
(`module_cog.py`'s stage refusal): the signup module is fixed from a season's configuration being
confirmed to its end, so a switch-on asked before that and run after it is refused, in the words
the ask already gives (`configuration_fixed_refusal`), rather than switching signup on behind what
the league was told.

**Once it has changed something it asks the hub's panel to be refreshed, once, as an `After`
step** (`hub_service.hub_refresh_after`): the panel offers what a module offers while it is on, so
the switch asks for that itself rather than the command doing it after every press.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import AuditRecord, PlannedStep, Verdict, module_on
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.hub_service import REFRESH_HUB, hub_refresh_after
from leaguebot.core.services.season_lifecycle_service import (
    configuration_fixed,
    configuration_fixed_refusal,
)
from leaguebot.core.services.setting_change import Saved, Setting

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.module_service import ModuleService

_ENABLE_COMMAND = "/module enable signup"


def _still_off(_payload: dict[str, Any]) -> str:
    return "The module is still off. Run `/module enable signup` again once the fault is cleared."


def module_on_signup(*, modules: "ModuleService", config: "ConfigService") -> Setting:
    """`module.on:signup`: the flag, a bare configuration row created only where missing, and the
    audit, in one save."""

    async def check(ctx: CheckContext) -> Verdict:
        if await modules.is_signup_enabled():
            return Verdict.refuse("⚠️ Signup module is already enabled.")
        season_number = await configuration_fixed(ctx.db_path)
        if season_number is not None:
            return Verdict.refuse(configuration_fixed_refusal(season_number, "enable"))
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", _payload: dict[str, Any]) -> Saved:
        await db.execute(
            "INSERT INTO signup_module_config "
            "(id, signup_channel_id, signups_open, signup_button_message_id, "
            "selected_tracks_json) "
            "VALUES (1, NULL, 0, NULL, '[]') "
            "ON CONFLICT(id) DO NOTHING"
        )
        await db.execute("UPDATE server_configs SET signup_module_enabled = 1")
        server_cfg = await config.get_server_config()
        return Saved(
            changed=True,
            audits=(
                AuditRecord(
                    change_type="MODULE_ENABLE", old_value={}, new_value={"module": "signup"}
                ),
            ),
            result={
                "base_role_set": server_cfg is not None and server_cfg.base_role_id is not None,
                "driver_role_set": server_cfg is not None and server_cfg.driver_role_id is not None,
            },
            then=(PlannedStep(REFRESH_HUB),),
        )

    def reply(_payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        saved = results["save"]
        steps = ["  • `/signup channel <channel>`"]
        if not saved["base_role_set"]:
            steps.append("  • `/bot base-role <role>`")
        if not saved["driver_role_set"]:
            steps.append("  • `/bot driver-role <role>`")
        return "✅ Signup module enabled.\nNext steps — configure with:\n" + "\n".join(steps)

    return Setting(
        kind=module_on("signup"),
        command=lambda _payload: _ENABLE_COMMAND,
        doing=lambda _payload: "Turning the signup module on",
        check=check,
        save=save,
        reply=reply,
        writes=("server_configs", "signup_module_config"),
        not_saved=_still_off,
        after={REFRESH_HUB: hub_refresh_after(_ENABLE_COMMAND)},
    )


__all__ = ["module_on_signup"]
