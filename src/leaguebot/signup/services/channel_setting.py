"""`/signup channel` as a change on the queue, `signup.setting.channel`.

The channel, its audit entry and its log line are one save, stamped by the queue's clock; the
setting is built by `setting_change` (`framework/services/queue/setting_change.py`, which holds what
every settings change does with a fault). It is a channel setting, so it `repairs_the_bot`: a
stop notice for a channel gone tells the league to set the channel, and the setting must still
run while the lane is stopped.

**Every refusal the bot can foresee is the check, saving nothing,** in the command's order and
words: the module on, the configuration not fixed by a confirmed season (a repair is not refused
for it: where the stored signup channel is no longer on the server, the league may replace it
mid-season), signup configured, the
channel not doing another job, then the bot's permission on the new channel and on the old one
it is to clear. The new channel is judged against everything the bot needs of it
(`channel_needs`: posting there and setting who may see it), in the words every channel check
gives (`unusable_refusal`), a channel deleted meanwhile refused as no longer on the server. Setting
who may see a channel needs both Manage Channel and Manage Permissions, and the old channel,
where it is not the new one, is judged for that pair alone, so that a move Discord will refuse for
the old one is refused before anything is saved, not left to strip the old channel of what it
holds. They are judged here and not in the command because a change ahead on the queue may have
taken the channel since the command asked.

**A server not to hand fails the check rather than standing aside**, where every other channel
check lets the change go. The signup channel's permissions are set by the change itself, which
cannot be done without the server; to save the channel and then fail to set them would leave a
signup channel nobody can sign up in. The check raises, nothing is changed, and the member is told
to run the command again.

**The overwrites follow the save, the new channel's set first and the old one's cleared last**
(`SET_PERMISSIONS`, an `After` step carrying the line), so the new channel is tried while the
old one still stands as it was. What Discord refuses that the check could not foresee
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

**Where signups are closed, the closed notice follows the permissions** (`CARRY_CLOSED`, then
`DELETE_CARRIED_CLOSED`, both `After` steps planned by the save where a notice stands in another
channel), so the signup channel always shows one notice or the other. The carry posts the notice in
the new channel, with no mentions, and keeps it as the notice with its channel; the delete takes
the old one down in a step of its own after it, through the standing-post handler, a message or a
channel already gone counting as done (which is what a replacement for a deleted channel finds). A
post Discord refuses leaves the old notice standing for the next opening to take down, and a
delete it refuses is named in the reply with its link and recorded as a failure beside the log
line. Neither stops the lane. Where no close posted a notice, nothing is posted.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import discord

from leaguebot.framework.models.change import (
    AuditRecord,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.seasons.approval_checks import channel_on_server
from leaguebot.framework.models.refusal import Refused
from leaguebot.framework.services.queue.change_types import CheckContext, Step, StepContext
from leaguebot.framework.services.queue.setting_change import (
    LEFT,
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
    unusable_refusal,
)
from leaguebot.framework.services.posting import standing_posts
from leaguebot.framework.services.posting.standing_posts import posted_in
from leaguebot.framework.utils.interaction_errors import describe_fault
from leaguebot.framework.utils.league_server import league_guild
from leaguebot.signup.services.channel_uses import channel_needs
from leaguebot.signup.services.closing_steps import CLOSED_NOTICE
from leaguebot.signup.services.information_post import post_information
from leaguebot.signup.services.signup_module_service import signup_channel_overwrites
from leaguebot.signup.services.signup_posting import post_standing_message
from leaguebot.signup.services.signup_settings import open_to_change

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.framework.services.queue.change_queue import ChangeQueue
    from leaguebot.core.services.settings.config_service import ConfigService
    from leaguebot.framework.utils.hooks import AwaitedHook
    from leaguebot.signup.services.signup_module_service import SignupModuleService

KIND = "signup.setting.channel"
COMMAND = "/signup channel"
#: The name of the step, after the save, that sets the new channel's permissions and clears the
#: old one's.
SET_PERMISSIONS = "set_permissions"
#: The name of the step, after the permissions, that posts the information message and its Sign Up
#: button in the new channel where a window is open.
POST_INFORMATION = "post_information"
#: The name of the step, after the permissions, that posts the closed notice again in the new
#: channel where signups are closed and the notice the last close posted stands in another.
CARRY_CLOSED = "carry_closed"
#: The name of the step, after the carry, that takes the old closed notice down from the channel it
#: stood in.
DELETE_CARRIED_CLOSED = "delete_carried_closed"

NOT_CONFIGURED = "❌ Signup module is not configured."


def may_not_edit(channel_id: int) -> str:
    """The refusal of the old signup channel, which the bot may not set the permissions of."""
    return (
        f"❌ The bot needs **Manage Channel** and **Manage Permissions** on <#{channel_id}> "
        "to set who may see it. The signup channel was not changed."
    )


def _may_set_permissions(guild: discord.Guild, channel: discord.TextChannel) -> bool:
    """Whether the bot may set the permissions of the old signup channel: it holds Manage Channel
    and Manage Permissions there. The new channel's are judged with the rest of what it needs
    (`unusable_refusal`)."""
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
    message with its Sign Up button in the new channel and keep its id as the window's, with the
    channel it was posted in, so that drivers can sign up in the channel at once. The old message
    is not taken down: a move while the window is open is allowed only once the old channel is gone
    from the server, and the message went with it.

    The record saves the id and its channel in the save that marks the step done. Like every step
    after a setting's save it ends on a fault and never stops the lane, so no later try finds an
    earlier copy to remove; its reply says what was not done. The step is no longer due where the
    window has closed or the signup channel has moved on since.
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
            "UPDATE signup_module_config SET signup_button_message_id = ?, "
            "signup_button_message_channel_id = ?",
            (int((result.result or {})["new"]), int((result.result or {})["channel_id"])),
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


def _carry_closed(signup: "SignupModuleService") -> After:
    """The step after the permissions, planned where signups are closed and the closed notice
    stands in another channel: post it again in the new channel, notifying nobody, and keep the new
    message and its channel as the notice, so that the signup channel always shows the closed
    notice or the Sign Up button.

    The notice and its channel are read as the step runs, never taken from the payload, and kept in
    its result beside the new ones (`old`, `old_channel_id`) for the step that takes the old one
    down (`DELETE_CARRIED_CLOSED`); a delete is its own step after the new post. The step is no
    longer due where signups have opened, the signup channel has moved on since, no notice is held
    or it already stands in the new channel. Like every step after a setting's save it ends on a
    fault and never stops the lane.
    """

    async def held(ctx: StepContext) -> tuple[int, int] | None:
        """The notice standing as the step runs and the channel it stands in, or None where
        none is held to carry from."""
        config = await signup.get_config()
        if (
            config is None or config.signups_open
            or config.signup_channel_id != int(ctx.payload["channel_id"])
            or config.signup_closed_message_id is None
        ):
            return None
        old_signup = next(
            (view.result or {}).get("old_channel_id") for view in ctx.steps if view.name == SAVE
        )
        standing_in = posted_in(config.signup_closed_message_channel_id, old_signup)
        if standing_in is None or standing_in == int(ctx.payload["channel_id"]):
            return None
        return int(config.signup_closed_message_id), int(standing_in)

    async def due(ctx: StepContext) -> bool:
        return await held(ctx) is not None

    async def run(ctx: StepContext) -> StepResult:
        standing = await held(ctx)
        if standing is None:
            raise RuntimeError("no closed notice stands to carry")
        new_id = int(ctx.payload["channel_id"])
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise RuntimeError("the league's server is not in the cache")
        left = (
            f"The closed notice was not carried to <#{new_id}>: it still stands in "
            f"<#{standing[1]}>, where the next `/signup open` takes it down."
        )
        channel = guild.get_channel(new_id)
        if not isinstance(channel, discord.TextChannel):
            raise StepFailedOnDiscord(
                "the signup channel could not be found to post in", notice=left
            )
        try:
            message_id = await post_standing_message(
                channel, CLOSED_NOTICE, mentions=discord.AllowedMentions.none(),
                refused=f"the closed notice could not be posted in channel {new_id}",
            )
        except StepFailedOnDiscord as error:
            raise StepFailedOnDiscord(error.reason, notice=left) from error
        return StepResult(result={
            "new": message_id, "channel_id": new_id, "old": standing[0],
            "old_channel_id": standing[1],
        })

    async def record(
        db: "aiosqlite.Connection", _ctx: StepContext, result: StepResult
    ) -> None:
        await db.execute(
            "UPDATE signup_module_config SET signup_closed_message_id = ?, "
            "signup_closed_message_channel_id = ?",
            (int((result.result or {})["new"]), int((result.result or {})["channel_id"])),
        )

    async def describe(ctx: StepContext) -> str:
        return f"posting the closed notice in <#{int(ctx.payload['channel_id'])}>"

    def fault_reply(payload: dict[str, Any], reply: str) -> str:
        said = f"The closed notice was not carried to <#{int(payload['channel_id'])}>"
        if said in reply:
            return reply
        return (
            f"{reply}\n⚠️ {said}: check where it stands in the signup channels, and delete any "
            "copy by hand."
        )

    return After(
        step=Step(CARRY_CLOSED, StepKind.ACT, run, still_due=due, describe=describe, record=record),
        fault_reply=fault_reply,
    )


def _delete_carried_closed() -> After:
    """The step after the carry: take the old closed notice down from the channel it stood in, once
    the copy is posted in the new one.

    The delete is the standing-post handler's (`standing_posts.delete_replaced_step`), which this
    step runs with the notice and its channel the carry kept in its result (`old`,
    `old_channel_id`) as its payload: a message or a channel already gone counts as done, which is
    what a replacement for a deleted channel finds. The step is due only where the carry posted. Like
    every step after a setting's save it ends on a fault and never stops the lane.
    """
    inner = standing_posts.delete_replaced_step(DELETE_CARRIED_CLOSED)

    def carried(ctx: StepContext) -> StepContext | None:
        posted = next(
            ((view.result or {}) for view in ctx.steps if view.name == CARRY_CLOSED), {}
        )
        if "new" not in posted:
            return None
        return dataclasses.replace(ctx, step_payload={
            "channel_id": int(posted["old_channel_id"]), "message_id": int(posted["old"]),
            "what": "closed notice", "division_name": "signups",
        })

    async def due(ctx: StepContext) -> bool:
        return carried(ctx) is not None

    async def run(ctx: StepContext) -> StepResult:
        deleting = carried(ctx)
        if deleting is None:
            raise RuntimeError("no closed notice was carried to take down the old one of")
        try:
            return await inner.run(deleting)
        except StepFailedOnDiscord as error:
            link = (error.result or {}).get("link") or f"in <#{deleting.step_payload['channel_id']}>"
            raise StepFailedOnDiscord(
                error.reason,
                notice=f"The old closed notice could not be deleted ({link}): delete it by hand.",
            ) from error

    async def describe(ctx: StepContext) -> str:
        return "taking down the old closed notice from the earlier signup channel"

    def fault_reply(_payload: dict[str, Any], reply: str) -> str:
        said = "The old closed notice could not be"
        if said in reply:
            return reply
        return (
            f"{reply}\n⚠️ {said} taken down: delete it from the earlier signup channel by hand."
        )

    return After(
        step=dataclasses.replace(inner, run=run, still_due=due, describe=describe),
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
        unusable = await unusable_refusal(guild, new_id, channel_needs(), ChannelUse("signup"))
        if unusable is not None:
            return Verdict.refuse(unusable)
        old_id = held.signup_channel_id
        if old_id is not None and old_id != new_id:
            old = guild.get_channel(old_id)
            if isinstance(old, discord.TextChannel) and not _may_set_permissions(guild, old):
                return Verdict.refuse(may_not_edit(old.id))
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        new_id = int(payload["channel_id"])
        cursor = await db.execute(
            "SELECT signup_channel_id, signups_open, selected_tracks_json, close_at, "
            "signup_closed_message_id, signup_closed_message_channel_id "
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
        elif row["signup_closed_message_id"] is not None:
            standing_in = posted_in(row["signup_closed_message_channel_id"], old_id)
            if standing_in is not None and standing_in != new_id:
                then.extend((PlannedStep(CARRY_CLOSED), PlannedStep(DELETE_CARRIED_CLOSED)))
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
        for name in (CARRY_CLOSED, DELETE_CARRIED_CLOSED):
            if notice := (results.get(name) or {}).get(LEFT):
                text += f"\n⚠️ {notice}"
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
            CARRY_CLOSED: _carry_closed(signup),
            DELETE_CARRIED_CLOSED: _delete_carried_closed(),
        },
        repairs_the_bot=True,
    )


__all__ = [
    "CARRY_CLOSED", "COMMAND", "DELETE_CARRIED_CLOSED", "KIND", "NOT_CONFIGURED", "POST_INFORMATION", "SET_PERMISSIONS", "channel_setting",
    "may_not_edit",
]
