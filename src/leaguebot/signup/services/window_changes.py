"""The signup window's opening on the change queue (#439, slice 6b).

`/signup open` asks `signup.open`, whose check holds every refusal the command has always made
(the module, test mode, the configuration, the season's stage, the league's roles, the slots) and
the bot's permissions on the signup channel, so that a refusal names what is wrong before anything
is posted. Its jobs are three:

- `post_info` posts the information message with the Sign Up button, pinging the base role. **The
  window is recorded open in the save that marks this job done** (its record: the window, the
  button's id, the tracks, the season's move to its signup stage, the close time and the
  `SIGNUP_OPEN` audit row), so signups are open only once the message is up. A post Discord
  refuses stops the queue with signups still closed, and a Discard leaves them closed.
- `arm_close` arms the close timer, where a close time was given.
- `delete_closed` takes down the "signups closed" notice the last close left, once the information
  message stands, so that the channel always shows one or the other.

The command reads the close time and the tracks it was given, which are its own input, and the
signup channel and the notice standing in it, which the plan of the jobs reads from the payload.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services import standing_posts, track_service
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.season_lifecycle_service import (
    WINDOW_OPENS_FROM,
    advance_on_window_open_on,
    live_season_stage,
)
from leaguebot.core.utils.channel_guard import league_role_faults
from leaguebot.core.utils.league_server import league_guild
from leaguebot.signup.services.signup_posting import post
from leaguebot.signup.services.signup_views import SignupButtonView

if TYPE_CHECKING:
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

OPEN = "signup.open"
POST_INFO = "post_info"
ARM_CLOSE = "arm_close"
DELETE_CLOSED = "delete_closed"
#: What a league is told when the module is off.
MODULE_OFF = "⛔ Signup module is not enabled. Use `/module enable signup` first."
#: What the bot needs on the signup channel to post the Sign Up button.
NEEDED = "**View Channel**, **Send Messages** and **Embed Links**"

__all__ = [
    "ARM_CLOSE", "DELETE_CLOSED", "OPEN", "POST_INFO", "window_open_change",
]


def _stamp(moment: datetime, fmt: Literal["F", "R"] = "F") -> str:
    """A dynamic timestamp for *moment*, read as UTC where it carries no zone."""
    return discord.utils.format_dt(moment, fmt)


def window_open_change(
    *,
    modules: "ModuleService",
    signup: "SignupModuleService",
    config: "ConfigService",
    schedule_close: Callable[[str], None],
) -> ChangeType:
    """The `signup.open` change type, for the builder to register.

    The payload is ``track_ids`` (the tracks chosen, ids as text), ``close_at`` (the normalised
    close time or None), and ``channel_id`` and ``closed_message_id`` (the signup channel and
    the "signups closed" notice standing in it, each or None). *schedule_close* arms the close
    timer at an ISO 8601 time.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_signup_enabled():
            return Verdict.refuse(MODULE_OFF)
        server_cfg = await config.get_server_config()
        if server_cfg is not None and server_cfg.test_mode_active:
            return Verdict.refuse(
                "⛔ Signups cannot be opened while test mode is active. "
                "Turn it off with `/test-mode toggle` first."
            )
        cfg = await signup.get_config()
        if cfg is None:
            return Verdict.refuse("❌ Signup module is not configured.")
        if cfg.signups_open:
            return Verdict.refuse("❌ Signups are already open.")
        live = await live_season_stage(ctx.db_path)
        if live is None or live[1] not in WINDOW_OPENS_FROM:
            return Verdict.refuse(
                "❌ Signups can only be opened while the season is waiting for its signup "
                "window, once its configuration is confirmed, or while it is ongoing with "
                "no placements left to confirm."
            )
        guild = await league_guild(ctx.bot)
        base_role_id = server_cfg.base_role_id if server_cfg is not None else None
        driver_role_id = server_cfg.driver_role_id if server_cfg is not None else None
        missing = []
        if cfg.signup_channel_id is None:
            missing.append("`signup channel` (use `/signup channel`)")
        if base_role_id is None:
            missing.append("`base role` (use `/bot base-role`)")
        if driver_role_id is None:
            missing.append("`driver role` (use `/bot driver-role`)")
        missing += league_role_faults(guild, base_role_id, driver_role_id)
        if missing:
            return Verdict.refuse(
                "❌ Signups cannot be opened until the signup module's configuration is "
                "put right:\n" + "\n".join(f"  • {m}" for m in missing),
                reason="the signup module's configuration is not put right: " + "; ".join(missing),
            )
        if not await signup.get_slots():
            return Verdict.refuse(
                "❌ At least one availability time slot must be configured before opening signups."
            )
        found = None if guild is None else guild.get_channel(int(cfg.signup_channel_id or 0))
        if not isinstance(found, discord.TextChannel):
            return Verdict.refuse("❌ Configured signup channel not found.")
        have = found.permissions_for(found.guild.me)
        if not (have.view_channel and have.send_messages and have.embed_links):
            return Verdict.refuse(
                f"❌ The bot needs {NEEDED} on {found.mention} to post the Sign Up button. "
                "Signups were not opened."
            )
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        planned = [PlannedStep(POST_INFO, {
            "channel_id": payload["channel_id"], "track_ids": list(payload["track_ids"]),
            "close_at": payload["close_at"],
        })]
        if payload["close_at"]:
            planned.append(PlannedStep(ARM_CLOSE, {"close_at": payload["close_at"]}))
        if payload["closed_message_id"] is not None and payload["channel_id"] is not None:
            planned.append(PlannedStep(DELETE_CLOSED, {
                "channel_id": payload["channel_id"], "message_id": payload["closed_message_id"],
                "what": "signups closed notice", "division_name": "signups",
            }))
        return tuple(planned)

    async def post_info(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        payload = ctx.step_payload
        guild = await league_guild(ctx.bot)
        found = None if guild is None else guild.get_channel(int(payload["channel_id"]))
        if guild is None or not isinstance(found, discord.TextChannel):
            raise StepFailedOnDiscord("the signup channel could not be found to post in")
        server_cfg = await config.get_server_config()
        base_role_id = server_cfg.base_role_id if server_cfg is not None else None
        base_role = guild.get_role(base_role_id) if base_role_id else None
        track_ids = [str(each) for each in payload["track_ids"]]
        if track_ids:
            async with get_connection(ctx.db_path) as db:
                names = await track_service.get_track_name_map(db)
            tracks_display = "\n".join(f"• {names[each]}" for each in track_ids)
        else:
            tracks_display = "No tracks specified"
        settings = await signup.get_settings()
        slots = await signup.get_slots()
        close_at = payload["close_at"]
        close_line = ""
        if close_at:
            parsed = datetime.fromisoformat(close_at)
            close_line = f"\n**Auto-closes:** {_stamp(parsed)} ({_stamp(parsed, 'R')})"
        role_line = f"\n\n{base_role.mention} — click below to sign up!" if base_role else ""
        time_type = "Time Trial" if settings.time_type == "TIME_TRIAL" else "Short Qualification"
        embed = discord.Embed(
            title="🏁 Driver Signups Are Open!",
            description=(
                "**Available time slots:**\n"
                + "\n".join(f"• {s.display_label}" for s in slots)
                + f"\n\n**Tracks:**\n{tracks_display}"
                + f"\n\n**Time type:** {time_type}"
                + f"\n**Time image proof:** {'Required' if settings.time_image_required else 'Not required'}"
                + f"\n**Nationality:** {'Required' if settings.nationality_required else 'Not required'}"
                + close_line
                + role_line
            ),
            color=discord.Color.green(),
        )
        mentions = (
            discord.AllowedMentions(roles=[base_role]) if base_role
            else discord.AllowedMentions.none()
        )
        message_id = await post(
            found, embed=embed, view=SignupButtonView(), mentions=mentions,
            refused=f"the information message could not be posted in channel {found.id}",
        )
        audited: dict[str, Any] = {"track_ids": track_ids}
        if close_at:
            audited["close_at"] = close_at
        line = f"{ctx.named} | /signup open | Success"
        if track_ids:
            line += f"\n  track_ids: {', '.join(track_ids)}"
        if close_at:
            line += f"\n  close_time: {close_at}"
        return StepResult(
            result={"new": message_id, "channel_id": int(payload["channel_id"])}, lines=(line,),
            audits=(AuditRecord("SIGNUP_OPEN", "", audited),),
        )

    async def record(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        payload = ctx.step_payload
        await signup.set_window_open_on(
            db, int((result.result or {})["new"]), [str(each) for each in payload["track_ids"]]
        )
        await advance_on_window_open_on(db)
        if payload["close_at"]:
            await signup.set_close_at_on(db, str(payload["close_at"]))

    async def not_open(_ctx: StepContext) -> bool:
        return not await signup.get_window_state()

    async def describe_post(_ctx: StepContext) -> str:
        return "posting the signup information and opening the window"

    async def arm_close(ctx: StepContext) -> StepResult:
        schedule_close(str(ctx.step_payload["close_at"]))
        return StepResult(result={})

    async def describe_arm(_ctx: StepContext) -> str:
        return "arming the signup window's close"

    async def describe_delete(_ctx: StepContext) -> str:
        return "taking down the earlier signups closed notice"

    def outcome(ctx: OutcomeContext) -> str:
        views = {view.name: view for view in ctx.steps}
        if "discarded" in (views[POST_INFO].result or {}):
            return (
                "⚠️ Signups were not opened: the information message could not be posted and "
                "was discarded. Run `/signup open` again."
            )
        text = f"✅ Signups opened. Button posted in <#{ctx.payload['channel_id']}>."
        close_at = ctx.payload["close_at"]
        if close_at:
            moment = datetime.fromisoformat(close_at)
            armed = views.get(ARM_CLOSE)
            if armed is not None and "discarded" in (armed.result or {}):
                text += " The auto-close was not scheduled: set it with `/signup close-time add`."
            else:
                text += f" Auto-close scheduled for {_stamp(moment)} ({_stamp(moment, 'R')})."
        closed = views.get(DELETE_CLOSED)
        if closed is not None and "discarded" in (closed.result or {}):
            link = (closed.result or {}).get("link") or f"in <#{ctx.payload['channel_id']}>"
            text += f"\nThe earlier signups closed notice could not be deleted ({link}): delete it by hand."
        return text

    steps: dict[str, Step] = {
        POST_INFO: Step(
            POST_INFO, StepKind.ACT, post_info, still_due=not_open, describe=describe_post,
            record=record,
        ),
        ARM_CLOSE: Step(ARM_CLOSE, StepKind.ACT, arm_close, describe=describe_arm),
        DELETE_CLOSED: dataclasses.replace(
            standing_posts.delete_replaced_step(DELETE_CLOSED), describe=describe_delete
        ),
    }
    return ChangeType(
        kind=OPEN,
        opening=(),
        plan=plan,
        steps=steps,
        check=check,
        key=lambda payload: OPEN,
        doing=lambda payload: "Opening signups",
        outcome=outcome,
    )
