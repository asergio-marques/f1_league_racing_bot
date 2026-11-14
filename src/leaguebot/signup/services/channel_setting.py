"""`/signup channel` as a change on the queue, `signup.setting.channel`.

The channel, its audit entry and its log line are one save, stamped by the queue's clock; the
setting is built by `setting_change` (`core/services/queue/setting_change.py`, which holds what
every settings change does with a fault). It is a channel setting, so it `repairs_the_bot`: a
stop notice for a channel gone tells the league to set the channel, and the setting must still
run while the lane is stopped.

**Every refusal the bot can foresee is the check, saving nothing,** in the command's order and
words: the module on, the configuration not fixed by a confirmed season (a repair is not refused
for it: where the stored signup channel is no longer on the server, the league may replace it
mid-season), signup configured, the
channel not doing another job, then the bot's permission on the new channel and on the old one
it is to clear. Setting who may see a channel needs both Manage Channel and Manage Permissions,
and the old channel is judged as well as the new so that a move Discord will refuse for the old
one is refused before anything is saved, not left to strip the old channel of what it holds.
They are judged here and not in the command because a change ahead on the queue may have taken
the channel since the command asked.

**The overwrites follow the save, the new channel's set first and the old one's cleared last**
(`SET_PERMISSIONS`, an `After` step carrying the line). A refusal on the new channel then never
leaves the old one already stripped. What Discord refuses that the check could not foresee
(`discord.HTTPException`, caught by name) leaves the channel saved and is named, in the reply
and beside the line, by `describe_fault`; the old channel is cleared all the same, since it is
no longer the signup channel. A fault of the bot's own in the step leaves the channel saved too,
with the failure line, and the reply says its permissions were not set (the core
specification's "a step after the save leaves the setting saved and says what was not done").

**Where a window is open, the information message with its Sign Up button follows the
permissions** (`POST_INFORMATION`, an `After` step planned by the save), posted in the new
channel and kept as the window's button message, so drivers can sign up in the channel at once.
It is no line's carrier: a fault in it leaves the channel saved and the reply says what was not
posted.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import discord

from leaguebot.core.models.change import AuditRecord, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.seasons.approval_checks import channel_on_server
from leaguebot.core.models.refusal import Refused
from leaguebot.core.services.queue.change_types import CheckContext, Step, StepContext
from leaguebot.core.services.queue.setting_change import (
    RENAMED_OUTCOME,
    SAVE,
    After,
    Saved,
    Setting,
)
from leaguebot.core.services.settings.channel_registry_service import (
    ChannelUse,
    find_channel_use,
    refusal,
)
from leaguebot.core.utils.interaction_errors import describe_fault
from leaguebot.core.utils.league_server import league_guild
from leaguebot.signup.services.information_post import post_information
from leaguebot.signup.services.signup_module_service import signup_channel_overwrites
from leaguebot.signup.services.signup_settings import open_to_change

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.queue.change_queue import ChangeQueue
    from leaguebot.core.services.settings.config_service import ConfigService
    from leaguebot.core.utils.hooks import AwaitedHook
    from leaguebot.signup.services.signup_module_service import SignupModuleService

KIND = "signup.setting.channel"
COMMAND = "/signup channel"
#: The name of the step, after the save, that sets the new channel's permissions and clears the
#: old one's.
SET_PERMISSIONS = "set_permissions"
#: The name of the step, after the permissions, that posts the information message and its Sign Up
#: button in the new channel where a window is open.
POST_INFORMATION = "post_information"

NOT_CONFIGURED = "❌ Signup module is not configured."


def may_not_edit(channel_id: int) -> str:
    """The refusal of a channel the bot may not set the permissions of."""
    return (
        f"❌ The bot needs **Manage Channel** and **Manage Permissions** on <#{channel_id}> "
        "to set who may see it. The signup channel was not changed."
    )


def _may_set_permissions(guild: discord.Guild, channel: discord.TextChannel) -> bool:
    held = channel.permissions_for(guild.me)
    return bool(held.manage_channels and held.manage_roles)


def _set_permissions(config: "ConfigService") -> After:
    """The step after the save: set the new channel's permissions, then clear the old one's.

    A refusal Discord gives either is reported in the step's result, not raised, so that the
    other is still tried and both sit beneath the save's line in one post.
    """

    async def run(ctx: StepContext) -> StepResult:
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise RuntimeError("the league's server is not in the cache")
        new_id = int(ctx.payload["channel_id"])
        channel = guild.get_channel(new_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"channel {new_id} is gone from guild {guild.id}")
        old_id = next(
            (view.result or {}).get("old_channel_id") for view in ctx.steps if view.name == SAVE
        )

        faults: list[str] = []
        overwrites = signup_channel_overwrites(guild, await config.get_server_config())
        try:
            await channel.edit(overwrites=overwrites)
        except discord.HTTPException as error:
            faults.append(
                f"The permissions on <#{new_id}> could not be set: {describe_fault(error)}."
            )
        if old_id is not None and old_id != new_id:
            old = guild.get_channel(old_id)
            if isinstance(old, discord.TextChannel):
                try:
                    await old.edit(overwrites={})
                except discord.HTTPException as error:
                    faults.append(
                        f"The permissions on the old signup channel <#{old_id}> could not be "
                        f"cleared: {describe_fault(error)}. They need putting right by hand."
                    )
        result: dict[str, Any] = {"faults": faults}
        if faults:
            result[RENAMED_OUTCOME] = "Success, with faults"
        return StepResult(
            result=result, lines=tuple(f"  {fault}" for fault in faults)
        )

    def fault_reply(payload: dict[str, Any], _reply: str) -> str:
        return (
            f"⚠️ <#{int(payload['channel_id'])}> is saved as the signup channel, but its "
            "permissions were not set. They need setting by hand."
        )

    return After(
        step=Step(SET_PERMISSIONS, StepKind.ACT, run), fault_reply=fault_reply, carries_line=True
    )


def _post_information(
    signup: "SignupModuleService", config: "ConfigService", queue: Callable[[], "ChangeQueue"]
) -> After:
    """The step after the permissions, planned where a window is open: post the information
    message with its Sign Up button in the new channel and keep its id as the window's, so that
    drivers can sign up in the channel at once.

    The record saves the id in the save that marks the step done. Like every step after a setting's
    save it ends on a fault and never stops the lane, so no later try finds an earlier copy to
    remove; its reply says what was not done. The step is no longer due where the window has
    closed or the signup channel has moved on since.
    """

    async def due(ctx: StepContext) -> bool:
        held = await signup.get_config()
        return (
            held is not None and held.signups_open
            and held.signup_channel_id == int(ctx.payload["channel_id"])
        )

    async def run(ctx: StepContext) -> StepResult:
        channel_id = int(ctx.payload["channel_id"])
        message_id = await post_information(
            ctx.bot, signup=signup, config=config, queue=queue(), channel_id=channel_id,
            track_ids=[str(each) for each in ctx.step_payload["track_ids"]],
            close_at=ctx.step_payload["close_at"],
        )
        return StepResult(result={"new": message_id, "channel_id": channel_id})

    async def record(
        db: "aiosqlite.Connection", _ctx: StepContext, result: StepResult
    ) -> None:
        await db.execute(
            "UPDATE signup_module_config SET signup_button_message_id = ?",
            (int((result.result or {})["new"]),),
        )

    async def describe(ctx: StepContext) -> str:
        return f"posting the signup information in <#{int(ctx.payload['channel_id'])}>"

    def fault_reply(payload: dict[str, Any], reply: str) -> str:
        return (
            f"{reply}\n⚠️ The information message with its Sign Up button could not be posted in "
            f"<#{int(payload['channel_id'])}>: drivers cannot sign up until signups are closed "
            "and opened again."
        )

    return After(
        step=Step(
            POST_INFORMATION, StepKind.ACT, run, still_due=due, describe=describe, record=record
        ),
        fault_reply=fault_reply,
    )


def channel_setting(
    *,
    modules: "ModuleService",
    signup: "SignupModuleService",
    config: "ConfigService",
    channel_uses_read: "AwaitedHook",
    queue: Callable[[], "ChangeQueue"],
) -> Setting:
    """`/signup channel`: the channel the information message, the closed notice and the
    wizards' channels are made in. *queue* is the change queue the Sign Up button is handed."""

    async def check(ctx: CheckContext) -> Verdict:
        async def stored_channel_gone() -> bool:
            stored = await signup.get_config()
            guild = await league_guild(ctx.bot)
            return (
                stored is not None and stored.signup_channel_id is not None
                and guild is not None
                and not await channel_on_server(guild, int(stored.signup_channel_id))
            )

        opened = await open_to_change(
            modules, ctx.db_path, COMMAND, repairing=stored_channel_gone
        )
        if opened:
            return Verdict.refuse(opened)
        held = await signup.get_config()
        if held is None:
            return Verdict.refuse(NOT_CONFIGURED)
        new_id = int(ctx.payload["channel_id"])
        # A channel does one job (decided 2026-09-06): every configurable channel of the server
        # is checked, the interaction channel among them.
        use = await find_channel_use(
            ctx.db_path, new_id, channel_uses_read=channel_uses_read
        )
        if use is not None:
            return Verdict.refuse(
                refusal(f"<#{new_id}>", use, same_setting=(use == ChannelUse("signup")))
            )
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise RuntimeError("the league's server is not in the cache")
        old_id = held.signup_channel_id
        judged = [new_id] if old_id is None or old_id == new_id else [new_id, old_id]
        for channel_id in judged:
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel) and not _may_set_permissions(
                guild, channel
            ):
                return Verdict.refuse(may_not_edit(channel.id))
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        new_id = int(payload["channel_id"])
        cursor = await db.execute(
            "SELECT signup_channel_id, signups_open, selected_tracks_json, close_at "
            "FROM signup_module_config"
        )
        row = await cursor.fetchone()
        if row is None:
            raise Refused(NOT_CONFIGURED)
        old_id = row["signup_channel_id"]
        if old_id == new_id:
            return Saved(changed=False)
        await db.execute("UPDATE signup_module_config SET signup_channel_id = ?", (new_id,))
        then = [PlannedStep(SET_PERMISSIONS)]
        if row["signups_open"]:
            then.append(PlannedStep(POST_INFORMATION, {
                "track_ids": json.loads(row["selected_tracks_json"]), "close_at": row["close_at"],
            }))
        return Saved(
            changed=True,
            audits=(
                AuditRecord(
                    "SIGNUP_CHANNEL_SET", {"channel_id": old_id}, {"channel_id": new_id}
                ),
            ),
            details=(f"channel: #{payload['channel_name']}",),
            result={"old_channel_id": old_id},
            then=tuple(then),
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        channel_id = int(payload["channel_id"])
        if not results[SAVE]["changed"]:
            return f"ℹ️ <#{channel_id}> is already the signup channel. Nothing was changed."
        text = f"✅ Signup channel set to <#{channel_id}>."
        for fault in (results.get(SET_PERMISSIONS) or {}).get("faults", ()):
            text += f"\n⚠️ {fault}"
        return text

    return Setting(
        kind=KIND,
        command=lambda _payload: COMMAND,
        doing=lambda payload: f"Setting the signup channel to <#{int(payload['channel_id'])}>",
        check=check,
        save=save,
        reply=reply,
        writes=("signup_module_config",),
        after={
            SET_PERMISSIONS: _set_permissions(config),
            POST_INFORMATION: _post_information(signup, config, queue),
        },
        repairs_the_bot=True,
    )


__all__ = [
    "COMMAND", "KIND", "NOT_CONFIGURED", "POST_INFORMATION", "SET_PERMISSIONS", "channel_setting",
    "may_not_edit",
]
