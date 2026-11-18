"""Signup's own switch-on, as a setting on the change queue.

`/module enable signup` asks one change built from the `Setting` below by `setting_change`
(`framework/services/queue/setting_change.py`, which holds what every settings change does with a value
already held and with a fault): `module_on("signup")` (`framework/models/change.py`), so core's cog
names the change without importing signup, as `docs/design/architecture.md`, "How modules and
core fit together", designs it: weather's switch
(`weather/services/module_switch_settings.py`) is the model. Signup's switch-off is
`signup_off_change.py`'s own change; the switch-on here is its match.

**The switch-on is one save: the flag, a bare configuration row created only where none stands,
the clearing of a kept channel another setting took, and the audit entries.** Creating only what is
missing, rather than replacing a standing row's values, keeps the configuration the switch-off kept
intact — the channel, the time slots and the question settings stand again as they were (issue
#127), and a configuration row restored from a backup is kept rather than blanked. The switch-on
finds the channel and settings as they stood, and checks that channel first
(`kept_channels_check`, handed `channel_uses.kept_channels`): the bare row this creates where none
exists is what the module needs to be usable, not a reset of what is there.

**The check before the save names a kept channel that is gone, or that another setting took while
the module was off, and refuses one the bot cannot use** (`core/services/modules/kept_channels.py`
holds the words): the save clears a taken one through `clear_signup_channel_on`, audited as the
channel's own change (`SIGNUP_CHANNEL_SET`, from the channel to none), with a line beneath the
Success line. The channel is the server's, not a division's, so it is "the signup channel".

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

from leaguebot.signup.models.switch import SWITCH_KEY
from leaguebot.framework.models.change import AuditRecord, PlannedStep, Verdict, module_on
from leaguebot.framework.services.queue.change_types import CheckContext
from leaguebot.core.services.modules.kept_channels import (
    found_in,
    kept_channels_check,
    named_reply,
)
from leaguebot.core.services.modules.hub_service import REFRESH_HUB, hub_refresh_after
from leaguebot.core.services.seasons.season_lifecycle_service import (
    configuration_fixed,
    configuration_fixed_refusal,
)
from leaguebot.framework.services.queue.setting_change import Saved, Setting
from leaguebot.signup.services.channel_uses import kept_channels
from leaguebot.signup.services.signup_module_service import clear_signup_channel_on

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.settings.config_service import ConfigService
    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.framework.utils.hooks import AwaitedHook

_ENABLE_COMMAND = "/module enable signup"


def _still_off(_payload: dict[str, Any]) -> str:
    return "The module is still off. Run `/module enable signup` again once the fault is cleared."


def module_on_signup(
    *, modules: "ModuleService", config: "ConfigService", channel_uses_read: "AwaitedHook"
) -> Setting:
    """`module.on:signup`: check the signup channel the module kept, then raise the flag, create a
    bare configuration row where missing and clear a kept channel another setting took, with the
    audit entries, in one save.

    *channel_uses_read* is core's hook, which the check asks who holds the kept channel through.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if await modules.is_enabled(SWITCH_KEY):
            return Verdict.refuse("⚠️ Signup module is already enabled.")
        season_number = await configuration_fixed(ctx.db_path)
        if season_number is not None:
            return Verdict.refuse(configuration_fixed_refusal(season_number, "enable"))
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        named, taken, details = found_in(payload)
        await db.execute(
            "INSERT INTO signup_module_config "
            "(id, signup_channel_id, signups_open, signup_button_message_id, "
            "selected_tracks_json) "
            "VALUES (1, NULL, 0, NULL, '[]') "
            "ON CONFLICT(id) DO NOTHING"
        )
        await db.execute("UPDATE server_configs SET signup_module_enabled = 1")
        audits = [
            AuditRecord(change_type="MODULE_ENABLE", old_value={}, new_value={"module": "signup"})
        ]
        if taken:
            old_id = await clear_signup_channel_on(db)
            if old_id is not None:
                audits.append(
                    AuditRecord("SIGNUP_CHANNEL_SET", {"channel_id": old_id}, {"channel_id": None})
                )
        server_cfg = await config.get_server_config_on(db)
        return Saved(
            changed=True,
            audits=tuple(audits),
            details=tuple(details),
            result={
                "named": named,
                "base_role_set": server_cfg is not None and server_cfg.base_role_id is not None,
                "driver_role_set": server_cfg is not None and server_cfg.driver_role_id is not None,
            },
            then=(PlannedStep(REFRESH_HUB),),
        )

    def reply(_payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        saved = results["save"]
        named = saved["named"]
        steps = [] if named else ["  • `/signup channel <channel>`"]
        if not saved["base_role_set"]:
            steps.append("  • `/bot base-role <role>`")
        if not saved["driver_role_set"]:
            steps.append("  • `/bot driver-role <role>`")
        text = named_reply("✅ Signup module enabled.", named)
        if not steps:
            return text
        return text + "\nNext steps — configure with:\n" + "\n".join(steps)

    return Setting(
        kind=module_on("signup"),
        command=lambda _payload: _ENABLE_COMMAND,
        doing=lambda _payload: "Turning the signup module on",
        check=check,
        save=save,
        reply=reply,
        writes=("server_configs", "signup_module_config"),
        not_saved=_still_off,
        before=(
            kept_channels_check(
                kept_channels, command=_ENABLE_COMMAND, channel_uses_read=channel_uses_read
            ),
        ),
        after={REFRESH_HUB: hub_refresh_after(_ENABLE_COMMAND)},
    )


__all__ = ["module_on_signup"]
