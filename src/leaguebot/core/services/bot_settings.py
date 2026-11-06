"""The four `/bot` repair settings — log channel, interaction channel, interaction role and
league admin role — as core `Setting`s on the change queue.

`/bot log-channel`, `interaction-channel`, `interaction-role` and `admin-role` each ask one
change built from a `Setting` below by `setting_change` (`core/services/setting_change.py`,
which holds what every settings change does with a value already held and with a fault):
weather's switch (`weather/services/module_switch_settings.py`) is the model. Each writes a
single column of `server_configs`, so nothing else in the row — test mode, the module flags, the
other three settings — can be carried over stale from a half-built model.

**Each runs on `REPAIR_LANE`, not the league's** (`setting_change.REPAIR_LANE`): these are a
league's way back in when one of the four is wrong or gone, so they run beside the league's own
lane, never waiting behind it, stopped or not.

**The two channels refuse a collision at the ask, and look again in the save; the two roles
judge "already held" in the save alone.** A channel already doing another job is refused
outright, and one already holding this very setting is refused too, "Nothing was changed" —
re-running the command is not a collision with something else. `REPAIR_LANE` runs beside the
league lane, which also writes `server_configs` (the hub channel, the division channels), so a
channel can pass both lanes' checks before either has saved; the save judges the collision
again, on its own connection and under the queue's write lock, and ends as the same refusal with
nothing written where the channel has since been taken (`find_channel_use_on`,
`channel_registry_service`). A role given the one it already holds is not a collision at all, so
it is answered "Nothing changed" by `setting_change`'s own rule for a value already held, once
the change has run: no refusal, no write, no audit entry, and the hub's permissions are not set
again, a role re-set to itself changing nothing and so leaving nothing to follow from it. The two
roles need no such re-check in the save: nothing else writes the roles `server_configs` holds.

**A role setting sets the hub's permissions again as its own follow-on**
(`hub_service.hub_refresh_after` does not fit here: it asks the hub's *panel* to be refreshed,
never its permissions). The hub is seen by the interaction role and the league admin role
(`hub_service.apply_hub_permissions`), so changing either sets them again once the role is
saved, from an `After` step the save plans, never from the command.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.models.change import AuditRecord, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import CheckContext, Step, StepContext
from leaguebot.core.services.channel_registry_service import (
    ChannelUse,
    find_channel_use,
    find_channel_use_on,
    refusal,
)
from leaguebot.core.services.hub_service import reapply_hub_permissions
from leaguebot.core.services.setting_change import After, Saved, Setting

if TYPE_CHECKING:
    from leaguebot.core.services.config_service import ConfigService

_UNCONFIGURED = "⛔ This server is not configured yet — run `/bot init` first."

#: column → (its `setting_change` kind name, the command as its line names it, the label its
#: reply and acknowledgement give the setting, the audit's change type, and the channel-registry
#: setting key, None for a role).
_SETTINGS: dict[str, tuple[str, str, str, str, str | None]] = {
    "log_channel_id": (
        "log_channel", "/bot log-channel", "Log channel", "LOG_CHANNEL_SET", "log",
    ),
    "interaction_channel_id": (
        "interaction_channel", "/bot interaction-channel", "Interaction channel",
        "INTERACTION_CHANNEL_SET", "interaction",
    ),
    "interaction_role_id": (
        "interaction_role", "/bot interaction-role", "Interaction role",
        "INTERACTION_ROLE_SET", None,
    ),
    "league_admin_role_id": (
        "admin_role", "/bot admin-role", "League admin role", "LEAGUE_ADMIN_ROLE_SET", None,
    ),
}

#: The name of the step, after a role setting's save, that sets the hub's permissions again.
REAPPLY_HUB = "reapply_hub"

_HUB_NOT_UPDATED = "⚠️ The setting is saved, but the hub's permissions could not be updated."

_SAVE_STEP = "save"


def _mention(column: str, value: int) -> str:
    """*value* as the mention Discord renders for *column*'s kind: a channel or a role."""
    return f"<#{value}>" if column.endswith("channel_id") else f"<@&{value}>"


def _bot_setting(column: str, config: "ConfigService") -> Setting:
    """One of the four `/bot` repair settings on *column*, *config* reading and writing it."""
    kind_name, command, label, change_type, channel_setting = _SETTINGS[column]
    is_role = channel_setting is None

    async def check(ctx: CheckContext) -> Verdict:
        value = int(ctx.payload["value"])
        if channel_setting is not None:
            use = await find_channel_use(ctx.db_path, value)
            if use is not None:
                return Verdict.refuse(
                    refusal(
                        _mention(column, value), use,
                        same_setting=(use == ChannelUse(channel_setting)),
                    )
                )
        if await config.get_server_config() is None:
            return Verdict.refuse(_UNCONFIGURED)
        return Verdict.go()

    async def save(db: aiosqlite.Connection, payload: dict[str, Any]) -> Saved:
        value = int(payload["value"])
        if channel_setting is not None:
            use = await find_channel_use_on(db, value)
            if use is not None:
                message = refusal(
                    _mention(column, value), use,
                    same_setting=(use == ChannelUse(channel_setting)),
                )
                return Saved(changed=False, outcome="Refused", result={"refused": message})
        cursor = await db.execute(
            f"SELECT {column} FROM server_configs WHERE server_id IS NOT NULL"
        )
        row = await cursor.fetchone()
        old_value = None if row is None else row[column]
        key = "role_id" if is_role else "channel_id"
        if is_role and old_value == value:
            return Saved(changed=False, details=(f"{column}: {_mention(column, value)}",))
        if not await config.set_core_setting_on(db, column, value):
            return Saved(changed=False)
        then = (PlannedStep(REAPPLY_HUB),) if is_role else ()
        return Saved(
            changed=True,
            audits=(AuditRecord(change_type, {key: old_value}, {key: value}),),
            details=(f"{column}: {_mention(column, value)}",),
            then=then,
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Setting **{label}** to {_mention(column, int(payload['value']))}"

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        value = int(payload["value"])
        mention = _mention(column, value)
        refused = results[_SAVE_STEP].get("refused")
        if refused is not None:
            return str(refused)
        if not results[_SAVE_STEP]["changed"]:
            return f"ℹ️ Nothing changed: **{label}** is already {mention}."
        return f"✅ **{label}** set to {mention}."

    after = {REAPPLY_HUB: _reapply_hub(command, config)} if is_role else {}

    return Setting(
        kind=f"core.setting.{kind_name}",
        command=lambda _payload: command,
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("server_configs",),
        after=after,
        repairs_the_bot=True,
    )


def _reapply_hub(command: str, config: "ConfigService") -> After:
    """The `After` step a role setting's save plans: set the hub's permissions again.

    A refusal Discord returns is reported beside the success reply and logged as its own line,
    never failing the step (`reapply_hub_permissions` returns it rather than raising); any other
    fault is the queue's own `Step.on_fault` to catch, which leaves the role saved. The line
    names *command* as the log channel reads a command naming what failed elsewhere
    (`interaction_errors.describe`), backtick-wrapped: the save's own success line, by contrast,
    names it as `setting_change`'s line always does, unwrapped.
    """

    async def run(ctx: StepContext) -> StepResult:
        fault = await reapply_hub_permissions(ctx.bot, config)
        if fault is None:
            return StepResult()
        return StepResult(
            lines=(f"{ctx.named} | `{command}` | Hub permissions not updated: {fault}",)
        )

    def fault_reply(_payload: dict[str, Any], reply: str) -> str:
        return f"{reply}\n{_HUB_NOT_UPDATED}"

    return After(step=Step(REAPPLY_HUB, StepKind.ACT, run), fault_reply=fault_reply)


def log_channel_setting(*, config: "ConfigService") -> Setting:
    """`/bot log-channel`: the log channel the bot writes its calculation log to."""
    return _bot_setting("log_channel_id", config)


def interaction_channel_setting(*, config: "ConfigService") -> Setting:
    """`/bot interaction-channel`: the channel the bot accepts commands in."""
    return _bot_setting("interaction_channel_id", config)


def interaction_role_setting(*, config: "ConfigService") -> Setting:
    """`/bot interaction-role`: the role allowed to use bot commands."""
    return _bot_setting("interaction_role_id", config)


def admin_role_setting(*, config: "ConfigService") -> Setting:
    """`/bot admin-role`: the role that governs the bot and may undo a league entire."""
    return _bot_setting("league_admin_role_id", config)


__all__ = [
    "REAPPLY_HUB",
    "admin_role_setting",
    "interaction_channel_setting",
    "interaction_role_setting",
    "log_channel_setting",
]
