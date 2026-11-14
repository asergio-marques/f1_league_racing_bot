"""`/signup close-time add`, `cancel` and `modify` as changes on the queue.

Each asks one change, `signup.close_time.<command>`, built from the `Setting` below by
`setting_change` (`core/services/queue/setting_change.py`, which holds what every settings change
does with a fault). The close time stored on the window, its audit entry and its log line are one
save, stamped by the queue's clock. The close timer (`window_close.CLOSE_TIMER`) is armed or
disarmed by a step after the save, which the setting does not need: a fault arming or disarming it
leaves the close time saved, the reply says the auto-close was not armed or not removed, and the
bot arms it when it next starts (`window_close.recover_missed_close`).

**The refusals are the check, in the command's order and words,** made at the ask and again as the
change starts: the module on, the window open, a close time armed (add) or not (cancel, modify),
and then the time the command was given. The command parses that time, which is its own input, as
`/signup open close_time:` does (one parser, decided 2026-09-15), and hands the check the result or
the refusal; the check gives it in its place in the order. The kinds do not carry ".setting.":
they are not among the settings core's lane overtakes a stop with, and a close time repairs
nothing.

**A time given that is already the armed one is "Nothing changed"**, judged in the save on the
connection that writes: no audit entry, no timer armed or disarmed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import AuditRecord, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.queue.change_types import CheckContext, Step, StepContext
from leaguebot.core.services.queue.setting_change import After, Saved, Setting
from leaguebot.core.utils.formatting import discord_ts
from leaguebot.signup.services.signup_settings import NOT_ENABLED
from leaguebot.signup.services.window_close import (
    CLOSE_TIMER,
    CLOSE_TIMER_KEY,
    close_time_of,
    same_instant,
)

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

KIND_PREFIX = "signup.close_time."
ARM_TIMER = "arm_close_timer"
DISARM_TIMER = "disarm_close_timer"

NOT_OPEN = (
    "❌ Signups are not currently open, so there is no auto-close time to "
    "manage. Set one when you open the window with "
    "`/signup open close_time:`."
)


def kind_of(command: str) -> str:
    """The kind of change of `/signup close-time <command>`."""
    return KIND_PREFIX + command


def _when(iso: str) -> str:
    moment = close_time_of(iso)
    return f"{discord_ts(moment)} ({discord_ts(moment, 'R')})"


async def _stored_on(db: "aiosqlite.Connection") -> str | None:
    row = await (await db.execute("SELECT close_at FROM signup_module_config WHERE id = 1")).fetchone()
    return None if row is None else row["close_at"]


async def _set_on(db: "aiosqlite.Connection", close_at: str | None) -> None:
    await db.execute("UPDATE signup_module_config SET close_at = ?", (close_at,))
    await db.execute("UPDATE signup_windows SET close_at = ? WHERE closed_at IS NULL", (close_at,))


def _arm(scheduler: Callable[[], "SchedulerService"]) -> After:
    """The step that arms the close timer for the close time just saved."""

    async def run(ctx: StepContext) -> StepResult:
        scheduler().arm_once(CLOSE_TIMER, CLOSE_TIMER_KEY, close_time_of(ctx.step_payload["close_at"]))
        return StepResult()

    def fault_reply(payload: dict[str, Any], _reply: str) -> str:
        return (
            f"⚠️ The auto-close time is saved, but the auto-close was not armed. The bot arms "
            f"it when it next starts: {_when(payload['close_at'])}."
        )

    return After(step=Step(ARM_TIMER, StepKind.ACT, run), fault_reply=fault_reply)


def _disarm(scheduler: Callable[[], "SchedulerService"]) -> After:
    """The step that removes the close timer once the close time is cleared."""

    async def run(_ctx: StepContext) -> StepResult:
        scheduler().disarm(CLOSE_TIMER, CLOSE_TIMER_KEY)
        return StepResult()

    def fault_reply(_payload: dict[str, Any], _reply: str) -> str:
        return (
            "⚠️ The auto-close time is cleared, but its timer was not removed. It closes "
            "nothing, as no close time is set."
        )

    return After(step=Step(DISARM_TIMER, StepKind.ACT, run), fault_reply=fault_reply)


def close_time_settings(
    *,
    modules: "ModuleService",
    signup: "SignupModuleService",
    scheduler: Callable[[], "SchedulerService"],
) -> tuple[Setting, ...]:
    """The three close-time settings: add, cancel and modify."""

    async def open_window(ctx: CheckContext) -> tuple[str | None, str | None]:
        """The refusal of a module off or a window not open, and the close time stored."""
        if not await modules.is_signup_enabled():
            return NOT_ENABLED, None
        cfg = await signup.get_config()
        if cfg is None or not cfg.signups_open:
            return NOT_OPEN, None
        return None, cfg.close_at

    def given(payload: dict[str, Any]) -> Verdict | None:
        error = payload.get("close_error")
        return Verdict.refuse(error) if error else None

    async def check_add(ctx: CheckContext) -> Verdict:
        refusal, stored = await open_window(ctx)
        if refusal:
            return Verdict.refuse(refusal)
        if stored is not None:
            return Verdict.refuse(
                f"❌ Signups already auto-close at {_when(stored)}. Use `/signup close-time "
                "modify` to change it, or `/signup close-time cancel` to clear it."
            )
        return given(ctx.payload) or Verdict.go()

    async def check_cancel(ctx: CheckContext) -> Verdict:
        refusal, stored = await open_window(ctx)
        if refusal:
            return Verdict.refuse(refusal)
        if stored is None:
            return Verdict.refuse(
                "❌ No auto-close time is set. Signups stay open until you run `/signup close`."
            )
        return Verdict.go()

    async def check_modify(ctx: CheckContext) -> Verdict:
        refusal, stored = await open_window(ctx)
        if refusal:
            return Verdict.refuse(refusal)
        if stored is None:
            return Verdict.refuse(
                "❌ No auto-close time is set, so there is nothing to change. Use "
                "`/signup close-time add` to arm one."
            )
        return given(ctx.payload) or Verdict.go()

    async def save_add(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        close_at = str(payload["close_at"])
        await _set_on(db, close_at)
        return Saved(
            changed=True,
            audits=(AuditRecord("SIGNUP_CLOSE_TIME_ADD", "", close_at),),
            details=(f"close_time: {close_at}",),
            result={"close_at": close_at},
            then=(PlannedStep(ARM_TIMER, {"close_at": close_at}),),
        )

    async def save_cancel(db: "aiosqlite.Connection", _payload: dict[str, Any]) -> Saved:
        previous = await _stored_on(db)
        await _set_on(db, None)
        return Saved(
            changed=True,
            audits=(AuditRecord("SIGNUP_CLOSE_TIME_CANCEL", previous, ""),),
            details=(f"was: {previous}",),
            then=(PlannedStep(DISARM_TIMER),),
        )

    async def save_modify(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        close_at = str(payload["close_at"])
        previous = await _stored_on(db)
        if previous is not None and same_instant(previous, close_at):
            return Saved(
                changed=False,
                details=(f"close_time: {previous}", "reason: it is already the armed time"),
                result={"close_at": previous},
            )
        await _set_on(db, close_at)
        return Saved(
            changed=True,
            audits=(AuditRecord("SIGNUP_CLOSE_TIME_MODIFY", previous, close_at),),
            details=(f"was: {previous}", f"close_time: {close_at}"),
            result={"close_at": close_at},
            then=(PlannedStep(ARM_TIMER, {"close_at": close_at}),),
        )

    def reply_add(_payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        return f"✅ Signups will auto-close at {_when(results['save']['close_at'])}."

    def reply_cancel(_payload: dict[str, Any], _results: Mapping[str, dict[str, Any]]) -> str:
        return (
            "✅ Auto-close time cleared. Signups stay open until you close them with "
            "`/signup close`."
        )

    def reply_modify(_payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        saved = results["save"]
        if not saved["changed"]:
            return f"ℹ️ Signups already auto-close at {_when(saved['close_at'])}. Nothing was changed."
        return f"✅ Signups will now auto-close at {_when(saved['close_at'])}."

    def setting(command: str, check, save, reply, doing: str, after: dict[str, After]) -> Setting:
        return Setting(
            kind=kind_of(command),
            command=lambda _payload: f"/signup close-time {command}",
            doing=lambda _payload: doing,
            check=check,
            save=save,
            reply=reply,
            writes=("signup_module_config", "signup_windows"),
            after=after,
        )

    return (
        setting("add", check_add, save_add, reply_add, "Setting the signup auto-close time",
                {ARM_TIMER: _arm(scheduler)}),
        setting("cancel", check_cancel, save_cancel, reply_cancel,
                "Clearing the signup auto-close time", {DISARM_TIMER: _disarm(scheduler)}),
        setting("modify", check_modify, save_modify, reply_modify,
                "Changing the signup auto-close time", {ARM_TIMER: _arm(scheduler)}),
    )


__all__ = ["ARM_TIMER", "DISARM_TIMER", "KIND_PREFIX", "NOT_OPEN", "close_time_settings", "kind_of"]
