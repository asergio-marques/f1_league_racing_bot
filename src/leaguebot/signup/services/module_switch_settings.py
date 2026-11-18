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

**The bot's permissions on the channel are recorded before the save** (`RECORD_PERMISSIONS`): for
each target `/signup channel` sets one on (`signup_channel_overwrites`, read as the league's roles
stand now, so a base role changed while signup was off is the one put back), the overwrite it holds
there now and the one wanted. The record changes nothing on Discord, so it declares no undo. It is
a step of its own, marked done before the permissions are put back, so that a restore run again
after a stop reads the first record: what a take-back restores is what stood before the first
attempt, not what the stop left. The channel the check named, or none kept, is skipped.

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

import discord

from leaguebot.signup.models.switch import SWITCH_KEY
from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import (
    AuditRecord,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
    module_on,
)
from leaguebot.framework.services.queue.change_types import CheckContext, Step, StepContext
from leaguebot.framework.services.queue.setting_change import Before
from leaguebot.framework.utils.league_server import league_guild
from leaguebot.core.services.modules.kept_channels import (
    CHECK_KEPT,
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
from leaguebot.signup.services.signup_module_service import (
    clear_signup_channel_on,
    signup_channel_overwrites,
)

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.settings.config_service import ConfigService
    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.framework.utils.hooks import AwaitedHook

#: The name of the step, before the save, that records the bot's permissions on the signup channel
#: as they stand, and as they are to be put back.
RECORD_PERMISSIONS = "record_permissions"

#: The kinds of target the record names: @everyone, the bot's own member, and a role by its id.
EVERYONE, BOT, ROLE = "everyone", "bot", "role"

_ENABLE_COMMAND = "/module enable signup"


def _pair(overwrite: discord.PermissionOverwrite) -> dict[str, int]:
    """*overwrite* as the two numbers Discord holds it as, to keep in a step's result."""
    allow, deny = overwrite.pair()
    return {"allow": allow.value, "deny": deny.value}


def _still_off(_payload: dict[str, Any]) -> str:
    return "The module is still off. Run `/module enable signup` again once the fault is cleared."


def _record_permissions(config: "ConfigService") -> Before:
    """The step that records, before the save, what is to be put on the signup channel and what
    stands there: `{"channel_id", "targets": [{"kind": `EVERYONE`, `BOT` or `ROLE`, "id": the
    role's, None for the other two, which the server names itself, "prior": the overwrite held
    now, None where there is none, "wanted": the overwrite `/signup channel` sets}]}`, in the order `signup_channel_overwrites` gives the targets, or `{"skip": True}` where
    no channel is kept or the check named the one that is (gone, or another setting's), which is
    set again by hand. It reaches the server through `league_guild` and raises where the server or
    the channel is missing. It changes nothing on Discord, and so declares no undo."""

    async def run(ctx: StepContext) -> StepResult:
        checked = next(
            (view.result or {} for view in ctx.steps if view.name == CHECK_KEPT), {}
        )
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute("SELECT signup_channel_id FROM signup_module_config")
            row = await cursor.fetchone()
        if row is None or row["signup_channel_id"] is None or checked.get("named"):
            return StepResult(result={"skip": True})
        channel_id = int(row["signup_channel_id"])
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise StepFailedOnDiscord("the league's server is out of the bot's reach")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise StepFailedOnDiscord(f"the signup channel {channel_id} is not on the server")
        wanted = signup_channel_overwrites(guild, await config.get_server_config())
        targets: list[dict[str, Any]] = []
        for target, overwrite in wanted.items():
            held = channel.overwrites_for(target)
            kind = (
                EVERYONE if target is guild.default_role else BOT if target is guild.me else ROLE
            )
            targets.append({
                "kind": kind,
                "id": target.id if kind == ROLE else None,
                "prior": None if held.is_empty() else _pair(held),
                "wanted": _pair(overwrite),
            })
        return StepResult(result={"channel_id": channel_id, "targets": targets})

    return Before(Step(RECORD_PERMISSIONS, StepKind.ACT, run))


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
            _record_permissions(config),
        ),
        after={REFRESH_HUB: hub_refresh_after(_ENABLE_COMMAND)},
    )


__all__ = ["RECORD_PERMISSIONS", "module_on_signup"]
