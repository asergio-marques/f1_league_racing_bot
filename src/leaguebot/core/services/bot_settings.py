"""The four `/bot` repair settings, and the league's base and driver roles, as core `Setting`s
on the change queue.

`/bot log-channel`, `interaction-channel`, `interaction-role` and `admin-role` each ask one
change built from a `Setting` below by `setting_change` (`core/services/setting_change.py`,
which holds what every settings change does with a value already held and with a fault):
weather's switch (`weather/services/module_switch_settings.py`) is the model. Each writes a
single column of `server_configs`, so nothing else in the row — test mode, the module flags, the
other three settings — can be carried over stale from a half-built model. `/bot base-role` and
`/bot driver-role` (issue #276) are `Setting`s too, built by `_league_role_setting`
below: unlike the four, they are a league manager's ordinary commands, so they wait their turn
on the league lane like any other change rather than overtaking a stop. The command judges, once,
before ever asking the queue, a driver role Discord will not let the bot grant, which has nothing
to do with a value already held or the season; the season's configuration being fixed is judged
there too, for the member's prompt reply, and again by the setting's own `check` as the change
starts, since a confirmation queued ahead of it may have fixed the season since it was asked.

**Each of the four repair settings overtakes a stop on the league lane, instead of waiting behind
it** (`Setting.repairs_the_bot`, read by `setting_change`, which says what else it marks): these are
a league's way back in when one of the four is wrong or gone, so a stop on an unrelated job cannot
hold one up. The lane still runs one change at a time — the queue runs an overtaking change to its
own end before its lane's stopped head is tried again, never beside it — so nothing else writes
`server_configs` while one of these four does.

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

**`/bot hub-channel` is a `Setting` too, built by `hub_channel_setting`
below, but not a repair setting: it waits its turn on the league lane like the two roles.** The
cog judges all four of its refusals at the ask, before the change is even asked for: the channel
already doing another job, the channel already the hub, the bot's missing Manage Channel or
Manage Permissions, and the server not yet configured. The missing-permission refusal has
nothing to do with a value already held, so nothing repeats it once the change starts, as the
missing driver role is judged before `/bot driver-role` asks for its own and not again. The
channel already doing another job, or already this channel, and the server not yet configured
are the setting's own `check` as well, run again as the change starts, since a change queued
ahead of this one might have claimed the channel for something else, or left the server
unconfigured, since it was asked. **The old hub's
permissions cleared and the new hub's permissions set are one `After` step after the save**
(`_restyle_hub`), which `carries_line`: the two permission edits are each Discord's to refuse
without raising, so both faults, where either or both happen, sit beneath the save's own line in
one post. A fault of the bot's own — not a refusal of Discord's — makes the step raise, and
leaves the setting saved with no success line, as any other step after a save does. **The panel's
refresh is asked by a second `After` step straight after** (`_ask_hub_refresh`), which runs
whether or not `_restyle_hub` did, since it only asks for a change and so cannot itself fail.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.models.change import (
    AuditRecord,
    FollowOn,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.change_queue import CheckContext, Step, StepContext
from leaguebot.core.services.channel_registry_service import (
    ChannelUse,
    find_channel_use,
    refusal,
)
from leaguebot.core.services.hub_service import HUB_REFRESH, apply_hub_permissions, reapply_hub_permissions
from leaguebot.core.services.setting_change import After, RENAMED_OUTCOME, Saved, Setting

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
# The league's two roles (issue #276, #283) — not repair settings
# ----------------------------------------------------------------------------

#: The command as its line names it, and the label its reply gives the role, by column.
_LEAGUE_ROLE_WORDS: dict[str, tuple[str, str, str]] = {
    "base_role_id": ("/bot base-role", "Base role", "BASE_ROLE_SET"),
    "driver_role_id": ("/bot driver-role", "Driver role", "DRIVER_ROLE_SET"),
}

#: The name of the step, after the base role's save, that calls the "base role changed" hook.
#: The step names the moment, never the module that happens to subscribe to it (signup's own
#: operation, `move_base_role_overwrite`, stays signup's).
BASE_ROLE_CHANGED = "base_role_changed"

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
    """What the manager reads as the role: Discord's own mention, carried in the payload since a
    role Discord renders as `@everyone` (its default role) cannot be reconstructed from an id
    alone."""
    return str(payload["role_mention"])


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


def _replaced_role_id(ctx: StepContext) -> int | None:
    """The role a league role's save actually replaced, read as the save read it (`current` in
    *its* `Saved.result`, kept there for this), never the role the ask saw: a second
    change queued behind the first may have moved the value on by the time this one's save runs."""
    return next(
        (view.result or {}).get("replaced")
        for view in ctx.steps
        if view.name == _SAVE_STEP
    )


def _replaced_reply(season_number: int) -> str:
    return (
        f"\nThe role it replaces is no longer on the server, so it could be replaced although "
        f"Season {season_number}'s configuration is fixed."
    )


def season_fixed_refusal(command: str, season_number: int) -> str:
    """Why *command* is refused while Season *season_number*'s configuration is fixed, word for
    word as `_set_league_role` (`bot_cog.py`) refuses it at the ask, and as the setting's own
    `check` refuses it again as the change starts (#283)."""
    return (
        f"❌ The league's roles are fixed for Season {season_number} now that its "
        f"configuration has been confirmed. `{command}` is available again once the "
        "season has ended, or while a new season is in configuration — or at once, "
        "should the role be deleted from the server."
    )


async def _season_fixed_on(db: aiosqlite.Connection) -> int | None:
    """`configuration_fixed`'s own judgement (`season_lifecycle_service`), read on the save's own
    connection rather than by opening a second one on its path: the save has no Discord access to
    re-check a role's being gone for itself, so it instead leans on `check`'s having already
    proved it, moments before on the same lane, whenever the season is fixed now."""
    from leaguebot.core.models.season import SeasonStage

    cursor = await db.execute(
        "SELECT season_number, stage FROM seasons "
        "WHERE status IN ('SETUP', 'ACTIVE') ORDER BY id DESC LIMIT 1",
    )
    row = await cursor.fetchone()
    if row is None or row["stage"] == SeasonStage.CONFIGURATION.value:
        return None
    return int(row["season_number"])


def _league_role_setting(
    column: str, *, config: "ConfigService", placement: "PlacementService"
) -> Setting:
    """`/bot base-role` or `/bot driver-role` on *column*: a league manager's own command, not
    one of the four repair settings above, so it waits its turn on the league lane.

    The payload carries the role asked for (*value*, *role_name*, *role_mention*) and *guild_id*,
    and no judgement of the ask's: `check` and `save` judge the season and the role they replace
    afresh, against the server's and the database's state as the change starts. A
    driver role Discord will not let the bot grant is judged only at the ask, since it has
    nothing to do with a value already held and never reaches this `Setting`.

    **The season's configuration being fixed is judged again as the change starts** (`check`),
    against the server's state then rather than as it was asked: a confirmation queued ahead of
    this change may have fixed the season since, as `season_fixed_refusal` already refuses it at
    the ask. It also re-judges whether the role **stored now** is still gone from the server —
    the stored column, never the role the ask saw, since a change waiting ahead on
    the lane may already have replaced it: a second gone-role replacement queued behind the
    first must be judged against the role the first one actually left stored, or it would let a
    role still on the server, and held by every driver, be swept aside while the season stays
    fixed.

    **The save reads the role it replaces itself** (`current`, kept in its result as *replaced*),
    never the role the ask saw, for the same reason. `check`'s having passed is `save`'s own proof
    that *current* is gone whenever the season is fixed, since no queued change lands between them
    on the lane, so `save` judges the grant to every driver from the season's fixedness alone,
    queried fresh on its own connection. One writer is not yet a queued change: the
    configuration's confirmation sets the season's stage from its command, so it can land
    between the two. It goes onto the queue with the season's other structural commands, and the
    window closes then.

    **The base role's save plans two follow-ons the driver role never needs:** calling the "base
    role changed" hook (`BASE_ROLE_CHANGED`), which moves the signup channel's overwrite without
    core naming signup, and setting the hub's permissions again (`REAPPLY_HUB`, shared with the
    four repair settings' role columns above) — the hub is seen by the base role too. **Either
    role, replaced because the old one is gone from the server, plans a third:** granting the new
    role to every driver (`GRANT_TO_DRIVERS`), which `carries_line` (`setting_change`) so that its
    given/not-given detail sits beneath the save's own line, one post, and a fault there leaves
    the role saved with only the failure line.
    """
    command, label, change_type = _LEAGUE_ROLE_WORDS[column]
    is_base_role = column == "base_role_id"

    async def check(ctx: CheckContext) -> Verdict:
        from leaguebot.core.services.season_lifecycle_service import configuration_fixed

        config_now = await config.get_server_config()
        if config_now is None:
            return Verdict.refuse(_UNCONFIGURED)
        season_number = await configuration_fixed(ctx.db_path)
        if season_number is not None:
            stored_role_id = getattr(config_now, column)
            guild = ctx.bot.get_guild(int(ctx.payload["guild_id"]))
            replacing_gone = (
                stored_role_id is not None
                and (guild is None or guild.get_role(stored_role_id) is None)
            )
            if not replacing_gone:
                return Verdict.refuse(season_fixed_refusal(command, season_number))
        return Verdict.go()

    async def save(db: aiosqlite.Connection, payload: dict[str, Any]) -> Saved:
        value = int(payload["value"])
        cursor = await db.execute(
            f"SELECT {column} FROM server_configs WHERE server_id IS NOT NULL"
        )
        row = await cursor.fetchone()
        current = None if row is None else row[column]
        role_detail = f"role: {payload['role_name']} (<@&{value}>)"
        if current == value:
            return Saved(changed=False, details=(role_detail,))
        if not await config.set_core_setting_on(db, column, value):
            return Saved(changed=False)
        details = [role_detail]
        then: list[PlannedStep] = []
        if is_base_role:
            then.append(PlannedStep(BASE_ROLE_CHANGED))
            then.append(PlannedStep(REAPPLY_HUB))
        result: dict[str, Any] = {"replaced": current}
        season_number = await _season_fixed_on(db)
        if season_number is not None:
            details.append("replaced: a role no longer on the server")
            result["season_number"] = season_number
            then.append(PlannedStep(GRANT_TO_DRIVERS))
        return Saved(
            changed=True,
            audits=(AuditRecord(change_type, {"role_id": current}, {"role_id": value}),),
            details=tuple(details),
            result=result,
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
            text += _replaced_reply(results[_SAVE_STEP]["season_number"])
            if not granted.get("failed"):
                text += _given_reply(granted, is_base_role=is_base_role)
        return text

    after: dict[str, After] = {}
    if is_base_role:
        after[BASE_ROLE_CHANGED] = _move_signup_overwrite()
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
    builder makes, with the server, the role the save actually replaced (`_replaced_role_id`,
    never the role the ask saw) and the role now set, handing no connection
    (signup's subscriber opens its own). Core never reaches `bot.signup_module_service`
    itself. The hook's subscribers never raise, each logging its own faults; the step itself
    can fail only finding the server or the role gone, and then the role stays saved and the
    log takes the failure line, as for any step after a setting's save.
    """

    async def run(ctx: StepContext) -> StepResult:
        guild, role = _guild_and_role(ctx)
        await ctx.bot.base_role_changed.call(guild, _replaced_role_id(ctx), role)
        return StepResult()

    return After(step=Step(BASE_ROLE_CHANGED, StepKind.ACT, run), fault_reply=lambda _p, r: r)


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
    """What the grant to every driver tells the manager, once it has run.

    Everybody already holds `@everyone`, the base role included, so the other-members reminder
    that follows every other base role's grant does not: it would send a league to hand out a
    role every member already holds.
    """
    if result.get("given") == "everyone":
        return "\nEverybody holds it already."
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


# ----------------------------------------------------------------------------
# The hub channel — not a repair setting
# ----------------------------------------------------------------------------

#: The name of the step, after the hub channel's save, that stands the old hub down and sets
#: the new one's permissions.
RESTYLE_HUB = "restyle_hub"


def _restyle_hub(config: "ConfigService") -> After:
    """The `After` step the hub channel's save plans: stand the old hub down and set the new
    hub's permissions.

    Both permission edits report a Discord refusal rather than raising
    (`apply_hub_permissions`, and the clearing below), so both faults, where either or both
    happen, sit beneath the save's own line in one post (`carries_line=True`). A fault of the
    bot's own makes this step raise instead, caught by `setting_change`'s `on_fault`, which
    leaves the setting saved with no success line; the refresh is still asked, from the step
    straight after (`_ask_hub_refresh`), which cannot itself fail since it only asks for a
    change.
    """

    async def run(ctx: StepContext) -> StepResult:
        guild = ctx.bot.get_guild(int(ctx.payload["guild_id"]))
        if guild is None:
            raise RuntimeError(f"guild {ctx.payload['guild_id']} is gone")
        channel = guild.get_channel(int(ctx.payload["value"]))
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"channel {ctx.payload['value']} is gone from guild {guild.id}")

        faults: list[str] = []
        old_channel_id = next(
            (view.result or {}).get("old_channel_id") for view in ctx.steps
            if view.name == _SAVE_STEP
        )
        if old_channel_id is not None:
            old = guild.get_channel(old_channel_id)
            if isinstance(old, discord.TextChannel):
                try:
                    await old.edit(overwrites={})
                except discord.HTTPException as exc:
                    faults.append(
                        f"The permissions on the old hub <#{old_channel_id}> could not be "
                        f"cleared: {exc}"
                    )
        fault = await apply_hub_permissions(config, guild, channel)
        if fault is not None:
            faults.append(fault)
        result: dict[str, Any] = {"faults": faults}
        if faults:
            result[RENAMED_OUTCOME] = "Success, with faults"
        return StepResult(
            result=result,
            lines=("\n".join(f"  {fault}" for fault in faults),) if faults else (),
        )

    def fault_reply(_payload: dict[str, Any], reply: str) -> str:
        return f"{reply}\n{_HUB_NOT_UPDATED}"

    return After(
        step=Step(RESTYLE_HUB, StepKind.ACT, run), fault_reply=fault_reply, carries_line=True
    )


#: The name of the step, after `RESTYLE_HUB`, that asks the queue for the panel's refresh.
REFRESH_HUB_PANEL = "refresh_hub_panel"


def _ask_hub_refresh(command: str) -> After:
    """The `After` step straight after `RESTYLE_HUB` that asks `HUB_REFRESH`, naming *command*
    and the panel the hub leaves where it has moved, so the refresh deletes it once the new
    one is up (`hub_refresh_change`'s `DELETE_OLD`). It does no work of its own, so it cannot
    fail, and runs whether or not `RESTYLE_HUB` did (`hub_service.hub_refresh_after` is the
    same shape for a module switch).
    """

    async def run(ctx: StepContext) -> StepResult:
        saved = next(view.result or {} for view in ctx.steps if view.name == _SAVE_STEP)
        old_channel_id, old_message_id = saved.get("old_channel_id"), saved.get("old_message_id")
        payload = {"command": command} | (
            {"old_channel_id": old_channel_id, "old_message_id": old_message_id}
            if old_channel_id is not None and old_message_id is not None else {}
        )
        return StepResult(follow_ons=(
            FollowOn(HUB_REFRESH, payload, "posting the hub panel"),
        ))

    def fault_reply(_payload: dict[str, Any], reply: str) -> str:
        return reply

    return After(step=Step(REFRESH_HUB_PANEL, StepKind.ACT, run), fault_reply=fault_reply)


def hub_channel_setting(*, config: "ConfigService") -> Setting:
    """`/bot hub-channel`: the channel every member of the league uses the bot from."""
    command = "/bot hub-channel"

    async def check(ctx: CheckContext) -> Verdict:
        value = int(ctx.payload["value"])
        use = await find_channel_use(ctx.db_path, value)
        if use is not None:
            return Verdict.refuse(
                refusal(f"<#{value}>", use, same_setting=(use == ChannelUse("hub")))
            )
        if await config.get_server_config() is None:
            return Verdict.refuse(_UNCONFIGURED)
        return Verdict.go()

    async def save(db: aiosqlite.Connection, payload: dict[str, Any]) -> Saved:
        value = int(payload["value"])
        cursor = await db.execute(
            "SELECT hub_channel_id, hub_message_id FROM server_configs "
            "WHERE server_id IS NOT NULL"
        )
        row = await cursor.fetchone()
        old_channel_id = None if row is None else row["hub_channel_id"]
        old_message_id = None if row is None else row["hub_message_id"]
        if not await config.set_core_setting_on(db, "hub_channel_id", value):
            return Saved(changed=False)
        await config.set_core_setting_on(db, "hub_message_id", None)
        return Saved(
            changed=True,
            audits=(AuditRecord("HUB_CHANNEL_SET", {"channel_id": old_channel_id}, {"channel_id": value}),),
            details=(f"channel: <#{value}>",),
            result={"old_channel_id": old_channel_id, "old_message_id": old_message_id},
            then=(PlannedStep(RESTYLE_HUB), PlannedStep(REFRESH_HUB_PANEL)),
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Setting **Hub channel** to <#{int(payload['value'])}>"

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        value = int(payload["value"])
        if not results[_SAVE_STEP]["changed"]:
            # `check`'s own refusal catches a value already held before the save ever runs
            # (it is judged at the ask, never here); what reaches this branch is the server
            # having come unclaimed since `check` last ran.
            return "ℹ️ Nothing was changed: the server is not configured."
        text = f"✅ **Hub channel** set to <#{value}>. Its panel follows on the change queue."
        restyled = results.get(RESTYLE_HUB)
        if restyled is not None:
            for fault in restyled.get("faults", ()):
                text += f"\n⚠️ {fault}"
        return text

    return Setting(
        kind="core.setting.hub_channel_id",
        command=lambda _payload: command,
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("server_configs",),
        after={
            RESTYLE_HUB: _restyle_hub(config),
            REFRESH_HUB_PANEL: _ask_hub_refresh(command),
        },
    )


__all__ = [
    "GRANT_TO_DRIVERS",
    "BASE_ROLE_CHANGED",
    "REAPPLY_HUB",
    "admin_role_setting",
    "base_role_setting",
    "driver_role_setting",
    "hub_channel_setting",
    "interaction_channel_setting",
    "interaction_role_setting",
    "log_channel_setting",
    "season_fixed_refusal",
]
