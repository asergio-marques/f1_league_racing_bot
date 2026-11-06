"""The four `/bot` repair settings, and the league's base and driver roles, as core `Setting`s
on the change queue.

`/bot log-channel`, `interaction-channel`, `interaction-role` and `admin-role` each ask one
change built from a `Setting` below by `setting_change` (`core/services/setting_change.py`,
which holds what every settings change does with a value already held and with a fault):
weather's switch (`weather/services/module_switch_settings.py`) is the model. Each writes a
single column of `server_configs`, so nothing else in the row — test mode, the module flags, the
other three settings — can be carried over stale from a half-built model. `/bot base-role` and
`/bot driver-role` (issue #276, #283 S13b) are `Setting`s too, built by `_league_role_setting`
below: unlike the four, they are a league manager's ordinary commands, so they wait their turn
on the league lane like any other change rather than overtaking a stop, and the command itself
judges the refusals that have nothing to do with a value already held — the season's
configuration being fixed, and a driver role Discord will not let the bot grant — before ever
asking the queue, since both need the server itself (the season, the role's place in Discord's
hierarchy) and not only the value.

**Each of the four repair settings overtakes a stop on the league lane, instead of waiting
behind it** (`Setting.repairs_the_bot`, read by `setting_change`): these are a league's way back
in when one of the four is wrong or gone, so a stop on an unrelated job cannot hold one up. The
lane still runs one change at a time — the queue runs an overtaking change to its own end before
its lane's stopped head is tried again, never beside it — so nothing else writes `server_configs`
while one of these four does.

**The two channels refuse a collision at the ask; the four roles judge "already held" in the save
alone.** A channel already doing another job is refused outright, and one already holding this
very setting is refused too, "Nothing was changed" — re-running the command is not a collision
with something else. Nothing runs beside the save to land a second writer between the check and
it, so the check made as the change starts stands. A role given the one it already holds is not a
collision at all, so it is answered "Nothing changed" by `setting_change`'s own rule for a value
already held, once the change has run: no refusal, no write, no audit entry, and the hub's
permissions are not set again, a role re-set to itself changing nothing and so leaving nothing to
follow from it.

**A role setting sets the hub's permissions again as its own follow-on**
(`hub_service.hub_refresh_after` does not fit here: it asks the hub's *panel* to be refreshed,
never its permissions). The hub is seen by the interaction role, the league admin role and the
base role (`hub_service.apply_hub_permissions`), so changing any of the three sets them again
once the role is saved, from an `After` step the save plans, never from the command.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.models.change import AuditRecord, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import CheckContext, Step, StepContext
from leaguebot.core.services.channel_registry_service import (
    ChannelUse,
    find_channel_use,
    refusal,
)
from leaguebot.core.services.hub_service import reapply_hub_permissions
from leaguebot.core.services.setting_change import After, Saved, Setting

if TYPE_CHECKING:
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.placement_service import PlacementService

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


# ----------------------------------------------------------------------------
# The league's two roles (issue #276, #283 S13b) — not repair settings
# ----------------------------------------------------------------------------

#: The command as its line names it, and the label its reply gives the role, by column.
_LEAGUE_ROLE_WORDS: dict[str, tuple[str, str, str]] = {
    "base_role_id": ("/bot base-role", "Base role", "BASE_ROLE_SET"),
    "driver_role_id": ("/bot driver-role", "Driver role", "DRIVER_ROLE_SET"),
}

#: The name of the step, after the base role's save, that moves the signup channel's overwrite.
MOVE_SIGNUP_OVERWRITE = "move_signup_overwrite"

#: The name of the step, after a role replaced because it is gone from the server, that grants
#: the new role to every driver.
GRANT_TO_DRIVERS = "grant_to_drivers"

_GIVEN_TO_EVERYONE = "  given to: everybody, as @everyone"

#: What a replaced base role adds: the bot knows its drivers and nobody else.
_OTHER_MEMBERS = (
    " The league's other members need it too: give it to them by hand, the bot knowing only "
    "its drivers."
)

_GRANT_FAULT_REPLY = (
    "\n⚠️ The role is saved, but it could not be given to the drivers: give it to them by hand."
)


def _role_mention(payload: dict[str, Any]) -> str:
    return f"<@&{int(payload['value'])}>"


def _guild_and_role(ctx: StepContext) -> tuple[discord.Guild, discord.Role]:
    """The guild and the role a league role setting's follow-on reads from *ctx*, raised by name
    rather than narrowed away: the change was asked for on a guild holding this very role, so a
    restart apart, Discord still has both while the follow-on runs."""
    guild = ctx.bot.get_guild(int(ctx.payload["guild_id"]))
    if guild is None:
        raise RuntimeError(f"guild {ctx.payload['guild_id']} is gone")
    role = guild.get_role(int(ctx.payload["value"]))
    if role is None:
        raise RuntimeError(f"role {ctx.payload['value']} is gone from guild {guild.id}")
    return guild, role


def _replaced_reply(payload: dict[str, Any]) -> str:
    return (
        f"\nThe role it replaces is no longer on the server, so it could be replaced although "
        f"Season {payload['season_number']}'s configuration is fixed."
    )


def _league_role_setting(
    column: str, *, config: "ConfigService", placement: "PlacementService"
) -> Setting:
    """`/bot base-role` or `/bot driver-role` on *column*: a league manager's own command, not
    one of the four repair settings above, so it waits its turn on the league lane.

    The payload carries what the command has already judged against the server, which the save
    takes as read rather than asking the database or Discord again: *old_role_id* (the role it
    may replace), *guild_id*, *replacing_gone* (whether that role is gone from the server, so it
    may be replaced while the season's configuration is fixed — decided 2026-09-22, #374) and
    *season_number* (for the reply's wording, where *replacing_gone*). The command's own
    refusals — the season's configuration being fixed without a gone role to replace, and a
    driver role Discord will not let the bot grant — have nothing to do with a value already
    held, so they are judged there and never reach this `Setting`.

    **The base role's save plans two follow-ons the driver role never needs:** moving the
    signup channel's overwrite (`MOVE_SIGNUP_OVERWRITE`), and setting the hub's permissions
    again (`REAPPLY_HUB`, shared with the four repair settings' role columns above) — the hub is
    seen by the base role too. **Either role, replaced because the old one is gone from the
    server, plans a third:** granting the new role to every driver (`GRANT_TO_DRIVERS`), which
    `carries_line` (`setting_change`) so that its given/not-given detail sits beneath the save's
    own line, one post, and a fault there leaves the role saved with only the failure line.
    """
    command, label, change_type = _LEAGUE_ROLE_WORDS[column]
    is_base_role = column == "base_role_id"

    async def check(_ctx: CheckContext) -> Verdict:
        return Verdict.go()

    async def save(db: aiosqlite.Connection, payload: dict[str, Any]) -> Saved:
        value = int(payload["value"])
        old_role_id = payload.get("old_role_id")
        cursor = await db.execute(
            f"SELECT {column} FROM server_configs WHERE server_id IS NOT NULL"
        )
        row = await cursor.fetchone()
        current = None if row is None else row[column]
        role_detail = f"role: {payload['role_name']} ({_role_mention(payload)})"
        if current == value:
            return Saved(changed=False, details=(role_detail,))
        if not await config.set_core_setting_on(db, column, value):
            return Saved(changed=False)
        details = [role_detail]
        then: list[PlannedStep] = []
        if is_base_role:
            then.append(PlannedStep(MOVE_SIGNUP_OVERWRITE))
            then.append(PlannedStep(REAPPLY_HUB))
        if payload.get("replacing_gone"):
            details.append("replaced: a role no longer on the server")
            then.append(PlannedStep(GRANT_TO_DRIVERS))
        return Saved(
            changed=True,
            audits=(AuditRecord(change_type, {"role_id": old_role_id}, {"role_id": value}),),
            details=tuple(details),
            then=tuple(then),
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Setting **{label}** to {_role_mention(payload)}"

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        mention = _role_mention(payload)
        if not results[_SAVE_STEP]["changed"]:
            return f"ℹ️ Nothing changed: **{label}** is already {mention}."
        text = f"✅ **{label}** set to {mention}."
        granted = results.get(GRANT_TO_DRIVERS)
        if granted is not None:
            text += _replaced_reply(payload)
            if not granted.get("failed"):
                text += _given_reply(granted, is_base_role=is_base_role)
        return text

    after: dict[str, After] = {}
    if is_base_role:
        after[MOVE_SIGNUP_OVERWRITE] = _move_signup_overwrite()
        after[REAPPLY_HUB] = _reapply_hub(command, config)
    after[GRANT_TO_DRIVERS] = _grant_to_drivers(placement, is_base_role=is_base_role)

    return Setting(
        kind=f"core.setting.{column}",
        command=lambda _payload: command,
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("server_configs",),
        after=after,
    )


def _move_signup_overwrite() -> After:
    """The `After` step the base role's save plans: call the "base role changed" hook the
    builder makes, with the server, the role replaced and the role now set, handing no
    connection (signup's subscriber opens its own). Core never reaches `bot.signup_module_service`
    itself (#283, S13b). The hook's subscribers never raise, each logging its own faults, so this
    step never fails.
    """

    async def run(ctx: StepContext) -> StepResult:
        guild, role = _guild_and_role(ctx)
        await ctx.bot.base_role_changed.call(guild, ctx.payload.get("old_role_id"), role)
        return StepResult()

    return After(step=Step(MOVE_SIGNUP_OVERWRITE, StepKind.ACT, run), fault_reply=lambda _p, r: r)


def _grant_to_drivers(placement: "PlacementService", *, is_base_role: bool) -> After:
    """The `After` step a role replaced because it is gone from the server plans: grant the new
    role to every driver who lost the old one with it (decided 2026-09-22, #374).

    `carries_line=True`: the given/not-given detail is written beneath the save's own line only
    once this step has run, so a fault here leaves the role saved and the log channel only the
    queue's failure line (`setting_change`'s module docstring, "Recorded twice, in one save, but
    for a step that carries the line").
    """

    async def run(ctx: StepContext) -> StepResult:
        guild, role = _guild_and_role(ctx)
        if role.is_default():
            return StepResult(result={"given": "everyone"}, lines=(_GIVEN_TO_EVERYONE,))
        outcome = await placement.grant_to_every_driver(guild, role.id)
        return StepResult(
            result={"granted": outcome.granted, "not_granted": outcome.not_granted},
            lines=(
                f"  given to: {outcome.granted} driver(s)"
                + "".join(f"\n  not given to: <@{uid}>" for uid in outcome.not_granted),
            ),
        )

    def fault_reply(_payload: dict[str, Any], reply: str) -> str:
        text = reply + _GRANT_FAULT_REPLY
        if is_base_role:
            text += _OTHER_MEMBERS
        return text

    return After(
        step=Step(GRANT_TO_DRIVERS, StepKind.ACT, run),
        fault_reply=fault_reply,
        carries_line=True,
    )


def _given_reply(result: Mapping[str, Any], *, is_base_role: bool) -> str:
    """What the grant to every driver tells the manager, once it has run."""
    if result.get("given") == "everyone":
        told = "Everybody holds it already."
    else:
        told = f"It has been given to {result['granted']} driver(s)."
        if result["not_granted"]:
            told += (
                " It could not be given to "
                + ", ".join(f"<@{uid}>" for uid in result["not_granted"])
                + " — give it to them by hand."
            )
    if is_base_role:
        told += _OTHER_MEMBERS
    return f"\n{told}"


def base_role_setting(*, config: "ConfigService", placement: "PlacementService") -> Setting:
    """`/bot base-role`: the role every member of the league holds."""
    return _league_role_setting("base_role_id", config=config, placement=placement)


def driver_role_setting(*, config: "ConfigService", placement: "PlacementService") -> Setting:
    """`/bot driver-role`: the role granted when a signup is approved."""
    return _league_role_setting("driver_role_id", config=config, placement=placement)


__all__ = [
    "GRANT_TO_DRIVERS",
    "MOVE_SIGNUP_OVERWRITE",
    "REAPPLY_HUB",
    "admin_role_setting",
    "base_role_setting",
    "driver_role_setting",
    "interaction_channel_setting",
    "interaction_role_setting",
    "log_channel_setting",
]
