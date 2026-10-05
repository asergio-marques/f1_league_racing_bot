"""Turning Results & Standings off, carried out on the change queue (#439, defect 8).

`/module disable results` used to commit the flag, erase the season over many calls to Discord and
only then close the rounds waiting on results; a stop in between stranded rounds nothing could
close, and a season that could never be completed. It is now one change, in steps the queue saves
and resumes after a stop:

1. **`switch_off`**, one save: the season's rows are erased, the flag goes down, attendance goes
   with it where the league confirmed the cascade, and the rounds waiting on results are closed.
   The ids of every message and channel to take down are read first, from the rows being erased,
   and travel in the steps that follow, since nothing could find them once the rows are gone.
   The order is today's: the results go before the rounds close, so the closing finds no results
   to mark former drivers by. It also asks, as changes of the bot's, for the hub's panel to be
   refreshed and for the season to be wound down, as changes of their own, which run after the
   take-downs and the close, in the queue's order (`hub_service.hub_refresh_change`, `season_lifecycle_service.wind_down_change`).
2. **`take_down`**, one for each message or channel: `results_purge_service.take_down`. **Each is
   a job like any other**, so a removal that fails stops the queue until it is cleared (decided with
   the owner, 2026-10-02, withdrawing the earlier "tried once"): the bot tries it again on the
   queue's schedule, and a league manager may Retry it. A league admin may Discard it, and the
   change's outcome then names the message with a link, for removal by hand, and does not count it
   as removed (the results specification). A failure's ``left`` is kept on the job, so a discard
   names only the messages still standing.
3. **`close`**, one save: it counts what went, forgets the banners taken down so that one left
   standing keeps its record, and writes the closing line. A message left standing is one in a
   discarded job's ``left``, or every message of an item whose job was discarded with none (the
   server out of the cache), linked from the ids the item carries.

**The outcome reads the jobs, not the closing job's result**, since that job may itself be
discarded. A discarded switch-off changed nothing, so the reply says only that. Any discard after
it also tells the league that the season can still be completed and that some of its messages may
remain (`SWITCHED_OFF_THEN_FAULTED`), whatever job was discarded.

The module is registered by the builder with the cascade it is handed: `attendance_off_on` is how
attendance switches itself off on a connection, so that this module imports none of attendance's.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    FollowOn,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
    module_off,
)
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.hub_service import HUB_REFRESH
from leaguebot.core.services.season_lifecycle_service import (
    FROZEN_FOR_COMPLETION_REFUSAL,
    WIND_DOWN,
    modules_frozen_for_completion,
)
from leaguebot.core.services.season_service import end_rounds_awaiting_results_on
from leaguebot.results.services.results_purge_service import (
    MESSAGE_KINDS,
    erase_season_on,
    message_link,
    take_down,
)
from leaguebot.results.services.verdict_announcement_service import _forget_banners_on

__all__ = ["results_off_change", "ALREADY_DISABLED"]

ALREADY_DISABLED = "⚠️ Results & Standings module is already disabled."
NOTHING_CHANGED = "Nothing was changed: Results & Standings is still on."
SWITCHED_OFF_THEN_FAULTED = (
    "Results & Standings is off and every round waiting on results is closed, so the season "
    "can still be completed, but some of its results, standings and verdicts may still be "
    "posted — delete them by hand."
)
_CASCADE_REPLY = (
    "✅ Attendance module disabled with it. Its per-division check-in and attendance channels "
    "have been cleared; its timings, penalties and thresholds are kept."
)

_SWITCH_OFF = "switch_off"
_TAKE_DOWN = "take_down"
_CLOSE = "close"


def results_off_change(
    *, attendance_off_on: Callable[[aiosqlite.Connection], Awaitable[bool]]
) -> ChangeType:
    """The change that turns Results & Standings off, with attendance where the payload asks.

    *attendance_off_on* switches attendance off on the connection it is handed, committing
    nothing, and says whether attendance was on. The payload is ``{"cascade_attendance": bool}``.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute("SELECT module_enabled FROM results_module_config")
            row = await cursor.fetchone()
        if row is None or not row["module_enabled"]:
            return Verdict.refuse(ALREADY_DISABLED)
        if await modules_frozen_for_completion(ctx.db_path):
            return Verdict.refuse(FROZEN_FOR_COMPLETION_REFUSAL)
        return Verdict.go()

    async def switch_off(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        if ctx.actor_id is None or ctx.actor_name is None:
            raise RuntimeError("turning results off is the act of a member, and none is recorded")
        erased = await erase_season_on(db)
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, 0)"
        )
        cascaded = False
        if ctx.payload.get("cascade_attendance"):
            cascaded = await attendance_off_on(db)
        closed = await end_rounds_awaiting_results_on(
            db,
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            now=datetime.now(timezone.utc),
        )

        counts = {
            "rounds": erased.rounds,
            "sessions": erased.sessions,
            "standings": erased.standings,
            "messages": erased.messages,
            "verdicts": erased.verdicts,
            "submission_channels": erased.submission_channels,
            "amend_channels": erased.amend_channels,
            "rounds_closed": len(closed),
        }
        audits = [AuditRecord("MODULE_DISABLE", {"module": "results"}, {})]
        lines = [f"{ctx.named} | /module disable results | Success" + _erased_summary(counts)]
        if cascaded:
            audits.append(AuditRecord("ATTENDANCE_MODULE_CASCADE_DISABLED", {}, {}))
            lines.append(f"{ctx.named} | /module disable attendance | Success")
        if erased.rounds:
            audits.append(AuditRecord("RESULTS_SEASON_PURGED", {}, counts))

        then = [PlannedStep(_TAKE_DOWN, {"item": item}) for item in erased.items]
        if erased.items:
            then.append(PlannedStep(_CLOSE))
        return StepResult(
            result={**counts, "cascaded": cascaded},
            audits=tuple(audits),
            lines=tuple(lines),
            then=tuple(then),
            follow_ons=(
                FollowOn(
                    HUB_REFRESH,
                    {"command": "`/module disable`"},
                    f"Refreshing the hub panel after {ctx.what}",
                ),
                FollowOn(WIND_DOWN, {}, f"Winding the season down after {ctx.what}"),
            ),
        )

    async def take_down_step(ctx: StepContext) -> StepResult:
        return StepResult(result=await take_down(ctx.bot, ctx.step_payload["item"]))

    async def describe_switch_off(_ctx: StepContext) -> str:
        return "turning Results & Standings off"

    async def describe_take_down(ctx: StepContext) -> str:
        """The removal as a stop names it: what it removes, with the link of each message, so that
        a manager sees what to put right before pressing Retry."""
        item = ctx.step_payload["item"]
        links = ", ".join(
            message_link(item["server_id"], item["channel_id"], m) for m in item["message_ids"]
        )
        return f"removing the {item['label']}" + (f" ({links})" if links else "")

    async def describe_close(_ctx: StepContext) -> str:
        return "counting what was removed"

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        tally = _tally(ctx)
        await _forget_banners_on(db, tally["banners_down"])
        lines: tuple[str, ...] = ()
        if tally["had_messages"]:
            left = tally["left"]
            line = (
                f"{ctx.named} | /module disable results | Messages removed\n"
                f"  results and standings messages removed: {tally['messages']}\n"
                f"  verdicts removed: {tally['verdicts']}"
            )
            if left:
                line += f"\n  left standing, to delete by hand: {len(left)}\n" + "\n".join(
                    f"  {link}" for link in left
                )
            lines = (line,)
        return StepResult(
            result={"messages": tally["messages"], "verdicts": tally["verdicts"],
                    "left": tally["left"]},
            lines=lines,
        )

    def outcome(ctx: OutcomeContext) -> str:
        done = {view.name: view for view in ctx.steps if view.name != _TAKE_DOWN}
        counts = (done[_SWITCH_OFF].result if _SWITCH_OFF in done else None) or {}
        if "discarded" in counts:
            return NOTHING_CHANGED
        taken = _tally(ctx)
        reply = "✅ Results & Standings module disabled."
        if counts.get("cascaded"):
            reply += "\n" + _CASCADE_REPLY
        if counts.get("rounds"):
            closed = counts.get("rounds_closed", 0)
            tail = f", {closed} round(s) closed with no results." if closed else "."
            reply += (
                f"\n🗑️ This season's results are gone: {counts['sessions']} session "
                f"result(s) and {counts['standings']} standings row(s) deleted, "
                f"{taken['messages']} results and standings message(s) and "
                f"{taken['verdicts']} verdict(s) removed" + tail
                + "\nPoints configurations and division channels are kept."
            )
        if any((view.result or {}).get("discarded") for view in ctx.steps):
            reply += "\n⚠️ " + SWITCHED_OFF_THEN_FAULTED
        left = taken["left"]
        if left:
            reply += (
                f"\n⚠️ {len(left)} message(s) could not be removed — delete them by hand:\n"
                + "\n".join(left)
            )
        return reply

    return ChangeType(
        kind=module_off("results"),
        opening=(PlannedStep(_SWITCH_OFF),),
        steps={
            _SWITCH_OFF: Step(_SWITCH_OFF, StepKind.SAVE, switch_off, describe=describe_switch_off),
            _TAKE_DOWN: Step(
                _TAKE_DOWN, StepKind.DELETE, take_down_step, describe=describe_take_down
            ),
            _CLOSE: Step(_CLOSE, StepKind.SAVE, close, describe=describe_close),
        },
        check=check,
        key=lambda payload: (
            f"{module_off('results')}|cascade={bool(payload.get('cascade_attendance'))}"
        ),
        doing=lambda _payload: "Turning Results & Standings off",
        outcome=outcome,
    )


def _erased_summary(counts: dict[str, Any]) -> str:
    """The switch-off line's detail: what was erased, and how many messages are still to remove."""
    if not counts["rounds"]:
        return ""
    return (
        f"\n  season results deleted: {counts['sessions']} session results, "
        f"{counts['standings']} standings rows\n"
        f"  rounds closed with no results: {counts['rounds_closed']}\n"
        f"  messages to remove: {counts['messages']} results and standings, "
        f"{counts['verdicts']} verdicts"
    )


def _tally(ctx: OutcomeContext) -> dict[str, Any]:
    """What the take-downs did: what was removed, what is left standing, and which banners went.

    A message is left standing where its job was discarded: those in the failure's ``left`` that
    the job kept, or, where it kept none (the server was out of the cache), every message the item
    carries. A message never counts as removed unless its job went through.
    """
    messages = verdicts = 0
    banners_down: list[int] = []
    left: list[str] = []
    had_messages = False
    for view in ctx.steps:
        if view.name != _TAKE_DOWN:
            continue
        item = view.payload["item"]
        kind = item["kind"]
        had_messages = had_messages or kind in MESSAGE_KINDS
        result = view.result or {}
        if result.get("discarded") is not None:
            ids = result.get("left")
            if ids is None:
                ids = item["message_ids"]
            left.extend(message_link(item["server_id"], item["channel_id"], m) for m in ids)
        elif result.get("removed") and not result.get("gone"):
            if kind in ("results", "standings"):
                messages += 1
            elif kind == "verdict":
                verdicts += 1
            elif kind == "banner":
                banners_down.append(int(item["anchor"]))
    return {
        "messages": messages, "verdicts": verdicts, "banners_down": banners_down,
        "left": left, "had_messages": had_messages,
    }
