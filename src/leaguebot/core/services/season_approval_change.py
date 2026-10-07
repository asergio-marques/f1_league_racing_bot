"""Approving a season's placements, carried out on the change queue (#439, slice 4a).

✅ Approve on `/season placements-review` runs its gates and, under test mode, the backup question
at the press, in the season cog, then asks the queue for this change. It used to do everything
inside the button: the sessions, the points snapshot, the arming, the commitment of the placements
and the move to Ongoing each in a commit of their own, then every role grant and every posting,
none of it recorded, so a stop after the season was Ongoing left the rest undone for good. This
module holds the change; the cog holds the press.

**The check, at the press and again when the change runs** (`check`), refuses in this order, the
first that fails being the refusal: another approval of the season in hand, queued, running or
stopped, **naming no job** and asking no backup question (`approval_in_hand`); the season no longer
in Placements; the season changed since the review (the review's own fingerprint, which covers
everything the press's gates read of the season); a date gone by (the clock); a channel no longer on
the server (Discord); the rasteriser gone from the host, where images is on. The press keeps every
gate in its own words and order, so a refusal there answers the member at once; the check is what
stands between a season changed while the approval waited on the queue and its commitment. It
imports nothing from a module and no cog, and reaches a module's service only through what the
builder hands it (`season_approval_change`'s parameters); `ctx.bot` is used for Discord alone, the
league's server.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import Verdict
from leaguebot.core.services import approval_checks
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    unfinished,
)
from leaguebot.core.services.season_fingerprint_service import SeasonFingerprint, take_fingerprint
from leaguebot.core.services.season_service import SeasonService
from leaguebot.core.utils.league_server import league_guild

if TYPE_CHECKING:
    from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows
    from leaguebot.core.services.module_service import ModuleService

__all__ = ["KIND", "STAGE_REFUSAL", "TELL_KIND", "approval_in_hand", "season_approval_change"]

KIND = "season.approve"
TELL_KIND = "season.approve.tell"

ALREADY_BEING_APPROVED = (
    "⏳ This season is already being approved, so this press approves nothing. If that approval "
    "has stopped, a league manager or admin can press Retry, or a league admin Discard, on its "
    "notice in the log channel."
)
STAGE_REFUSAL = "⛔ The season is no longer in placements. **Nothing has been approved.**"


async def approval_in_hand(
    db_path: str, season_id: int, *, excluding: int | None = None
) -> bool:
    """Whether an approval of *season_id* is queued, running or stopped on the queue.

    No job number is wanted: a second approval is refused without naming the job (the owner's
    answer, "Refuse but don't name the job"). *excluding* leaves out the change with that id, so
    that the approval's own check does not find itself. Read through core's `unfinished`.
    """
    return any(
        payload.get("season_id") == season_id
        for payload in await unfinished(db_path, [KIND], excluding=excluding)
    )


def season_approval_change(
    *,
    modules: "ModuleService",
    windows: Callable[[], Awaitable[tuple["AttendanceWindows | None", "WeatherWindows | None"]]],
    rasteriser_fault: Callable[[], str | None],
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that approves a season's placements; see the module.

    The builder hands in *modules* (which modules are on), *windows* (the enabled modules'
    windows, `LeagueBot.approval_windows`), *rasteriser_fault* (what is wrong with the host's
    drawing program, None where nothing is) and *now* (the queue's clock).
    """

    async def check(ctx: CheckContext) -> Verdict:
        season_id = int(ctx.payload["season_id"])
        if await approval_in_hand(ctx.db_path, season_id, excluding=ctx.change_id):
            return Verdict.refuse(ALREADY_BEING_APPROVED)

        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
            season = await cursor.fetchone()
        if season is None or season["stage"] != "PLACEMENTS":
            return Verdict.refuse(STAGE_REFUSAL)

        # The report read is the report approved: the press compares the review's fingerprint,
        # and so does the run, since the season may have changed while it waited.
        current = await take_fingerprint(ctx.bot, season_id)
        changed = SeasonFingerprint(areas=dict(ctx.payload["fingerprint"])).differs_from(current)
        if changed:
            bullets = "\n".join(f"• {area}" for area in changed)
            return Verdict.refuse(
                f"⛔ Your season has changed since this review, so the report above no longer "
                f"describes it:\n{bullets}\n"
                f"Run `/season placements-review` again and approve from the fresh report. "
                f"**Nothing has been approved.**",
                f"the season has changed since this review:\n{bullets}",
            )

        # What the fingerprint does not cover: the clock, the server and the host.
        seasons = SeasonService(ctx.db_path)
        divisions = await seasons.get_divisions(season_id)
        rounds = {each.id: await seasons.get_division_rounds(each.id) for each in divisions}
        attendance, weather = await windows()
        dates = approval_checks.date_refusal(
            divisions, rounds, now=now(), attendance=attendance, weather=weather
        )
        if dates is not None:
            return Verdict.refuse(dates.reply, dates.reason)

        guild = await league_guild(ctx.bot)
        channels = await approval_checks.division_channel_faults(
            modules, ctx.db_path, season_id, guild
        )
        if channels:
            bullets = "\n".join(f"• {line}" for line in channels)
            return Verdict.refuse(
                f"⛔ Season cannot be approved:\n{bullets}",
                "the season cannot be approved:\n" + "\n".join(channels),
            )

        if await modules.is_images_enabled():
            fault = rasteriser_fault()
            if fault:
                return Verdict.refuse(
                    f"❌ Season cannot be approved — the image module is not correctly "
                    f"configured:\n• {fault}",
                    f"the image module is not correctly configured:\n{fault}",
                )
        return Verdict.go()

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    def doing(payload: dict[str, Any]) -> str:
        return f"Approving season {payload['season_number']}"

    return ChangeType(
        kind=KIND,
        opening=(),
        steps={},
        check=check,
        key=lambda payload: f"{KIND}:{payload['season_id']}",
        doing=doing,
        outcome=outcome,
    )
