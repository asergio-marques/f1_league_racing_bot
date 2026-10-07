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

**Everything the approval writes is one job** (`apply`, architecture.md, "All or nothing in one
step"): every round's sessions, the points snapshot, the commitment of the placements and the move
to Ongoing are one save, reading nothing but the connection it is handed, so a fault leaves the
season in Placements with nothing written and the queue stops at it to retry. **Everything else
follows the save as a job of its own**, each of which stops the queue where it fails: letting go of
the setup held in memory, arming the timed work (after the save, so that a stop before it leaves
no job armed against a season still in Placements), each driver's roles, the notice that the
season's posts are being made, each division's lineup, calendar, opening standings and opening
sheet, and the notice's deletion. The closing job writes the one line that records the approval
and what a league admin discarded. A job tried again posts as text (Constitution XIV, rule 8).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.services import approval_checks, season_classification_service
from leaguebot.core.services.calendar_post_service import post_division_calendar, tracks_by_name
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
    unfinished,
)
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.services.season_fingerprint_service import SeasonFingerprint, take_fingerprint
from leaguebot.core.services.season_service import (
    SeasonService,
    commit_placements_on,
    create_sessions_for_round_on,
    transition_to_active_on,
)
from leaguebot.core.utils.batch_notice import delete_notice, send_notice
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.placement_service import PlacementService
    from leaguebot.core.services.scheduler_service import SchedulerService

log = logging.getLogger(__name__)

__all__ = [
    "KIND",
    "STAGE_REFUSAL",
    "TELL_KIND",
    "approval_in_hand",
    "season_approval_change",
    "ungranted_line",
    "unposted_lineup_line",
]

KIND = "season.approve"
TELL_KIND = "season.approve.tell"

ALREADY_BEING_APPROVED = (
    "⏳ This season is already being approved, so this press approves nothing. If that approval "
    "has stopped, a league manager or admin can press Retry, or a league admin Discard, on its "
    "notice in the log channel."
)
STAGE_REFUSAL = "⛔ The season is no longer in placements. **Nothing has been approved.**"

#: The job names, as the stop notice, the tests and `StepView` know them.
APPLY = "apply"
FORGET_SETUP = "forget_setup"
ARM = "arm"
GRANT_ROLES = "grant_roles"
POST_BATCH_NOTICE = "post_batch_notice"
REFRESH_LINEUP = "refresh_lineup"
POST_CALENDAR = "post_calendar"
OPENING_STANDINGS = "opening_standings"
OPENING_SHEET = "opening_sheet"
DELETE_BATCH_NOTICE = "delete_batch_notice"
TELL_REVIEW_CHANNEL = "tell_review_channel"
CLOSE = "close"

NOTICE_TEXT = "🎨 Posting lineups, calendars and opening classifications — one moment."
NOT_SAVED = (
    "Nothing has been approved: the season is still in placements. Run `/season "
    "placements-review` again."
)
NOT_FORGOTTEN = (
    "The setup the bot held in memory could not be let go of: `/round amend` may refuse this "
    "season until the bot restarts."
)
NOT_ARMED = (
    "⛔ The season's timed work was not armed: no round will open its results submission, and "
    "no forecast or check-in call will be posted. No command arms it again."
)
NOTICE_NOT_POSTED = "The notice that the season's posts were being made could not be posted."
_NOTICE_NOT_DELETED = "The notice that the season's posts were being made could not be deleted"
_BUTTON = "the ✅ Approve button of `/season placements-review`"


def channel_approved(actor_id: int | None, season_number: Any) -> str:
    """What the review's channel is told of an approval its member could not be told of."""
    return (
        f"✅ <@{actor_id}> — Season #{season_number} is approved and ongoing. Your confirmation "
        f"could not be sent to you privately; the log channel has what it said."
    )


def channel_not_approved(actor_id: int | None, season_number: Any) -> str:
    """What the review's channel is told of an approval that did not go through, its member
    having no reply left to be told in."""
    return (
        f"⛔ <@{actor_id}> — Season #{season_number} was not approved. Your reply could not be "
        f"updated; the log channel has why. Run `/season placements-review` again."
    )


async def tell_channel(bot: Any, channel_id: int, text: str) -> None:
    """Post *text* in the review's channel, read from the league's server as the job runs.

    Raises `StepFailedOnDiscord` where the channel is no longer there, and whatever Discord
    raises where the post is refused: the job that calls it stops the queue.
    """
    guild = await _guild(bot)
    channel = as_text_channel(guild.get_channel(channel_id))
    if channel is None:
        raise StepFailedOnDiscord(
            f"the channel <#{channel_id}> no longer exists or cannot be posted in"
        )
    await send_notice(channel, text)


def ungranted_line(user_ids: list[str]) -> str:
    """The drivers a placements confirmation could not give their roles (#387).

    No command grants them again — at the mid-season confirmation, confirming again finds
    nothing left to commit — so the manager grants them by hand.
    """
    who = ", ".join(f"<@{user_id}>" for user_id in user_ids)
    return (
        f"{who} — their roles could not be granted. Give them their division's and team's "
        f"roles by hand."
    )


def unposted_lineup_line(division_name: str) -> str:
    """A division whose lineup a placements confirmation could not post (#387).

    No command posts a lineup again, so the line says when it will be: every change to the
    division's drivers — assign, unassign, release, move, sack — posts it anew.
    """
    return (
        f"**{division_name}** — its lineup could not be posted. No command posts it again; "
        f"it is posted with the next change to its drivers."
    )


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
    config: "ConfigService",
    scheduler: "SchedulerService",
    placement: "PlacementService",
    windows: Callable[[], Awaitable[tuple["AttendanceWindows | None", "WeatherWindows | None"]]],
    rasteriser_fault: Callable[[], str | None],
    snapshot_points_on: Callable[[aiosqlite.Connection, int], Awaitable[None]],
    forget_setup: Callable[[], None],
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that approves a season's placements; see the module.

    The builder hands in *modules* and *config* (which modules are on, and test mode), the
    *scheduler* and the *placement* service, *windows* (the enabled modules' windows,
    `LeagueBot.approval_windows`), *rasteriser_fault* (what is wrong with the host's drawing
    program, None where nothing is), results' *snapshot_points_on* (the points snapshot on a
    handed save, which reads results' own switch), *forget_setup* (lets go of the setup the
    season cog holds in memory) and *now* (the queue's clock).
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

    # ── The jobs ────────────────────────────────────────────────────────────────

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        season_id = int(ctx.payload["season_id"])
        season_number = int(ctx.payload["season_number"])
        cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
        season = await cursor.fetchone()
        if season is None or season["stage"] != "PLACEMENTS":
            # A backstop: the check passed, and something wrote the database after it.
            return StepResult(result={"refused": STAGE_REFUSAL})

        cursor = await db.execute(
            "SELECT r.id, r.format FROM rounds r JOIN divisions d ON d.id = r.division_id "
            "WHERE d.season_id = ? ORDER BY d.tier, r.round_number",
            (season_id,),
        )
        for each in await cursor.fetchall():
            await create_sessions_for_round_on(db, int(each["id"]), RoundFormat(each["format"]))
        await snapshot_points_on(db, season_id)
        committed = await commit_placements_on(db, season_id)
        if not await transition_to_active_on(db, season_id):
            raise RuntimeError(f"season {season_id} could not be moved to Ongoing")

        steps: list[PlannedStep] = [PlannedStep(FORGET_SETUP), PlannedStep(ARM)]
        cursor = await db.execute(
            "SELECT id, name, lineup_channel_id, calendar_channel_id FROM divisions "
            "WHERE season_id = ? ORDER BY tier, id",
            (season_id,),
        )
        divisions = list(await cursor.fetchall())
        for division in divisions:
            cursor = await db.execute(
                "SELECT dp.discord_user_id, ti.name AS team_name "
                "FROM driver_season_assignments dsa "
                "JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
                "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
                "JOIN team_instances ti ON ti.id = ts.team_instance_id "
                "WHERE dsa.division_id = ? AND dsa.committed = 1 "
                "AND dp.current_state = 'ASSIGNED' AND dp.is_test_driver = 0 "
                "ORDER BY CAST(dp.discord_user_id AS INTEGER)",
                (division["id"],),
            )
            steps.extend(
                PlannedStep(
                    GRANT_ROLES,
                    {
                        "user_id": int(driver["discord_user_id"]),
                        "division_id": int(division["id"]),
                        "division_name": str(division["name"]),
                        "team_name": str(driver["team_name"]),
                    },
                )
                for driver in await cursor.fetchall()
            )
        channel_id = ctx.payload.get("channel_id")
        if divisions and channel_id:
            steps.append(PlannedStep(POST_BATCH_NOTICE, {"channel_id": int(channel_id)}))
        for division in divisions:
            target = {"division_id": int(division["id"]), "division_name": str(division["name"])}
            if division["lineup_channel_id"]:
                steps.append(PlannedStep(REFRESH_LINEUP, target))
            if division["calendar_channel_id"]:
                steps.append(PlannedStep(POST_CALENDAR, target))
            # Whether each is due is decided as it runs, by its module's switch.
            steps.append(PlannedStep(OPENING_STANDINGS, target))
            steps.append(PlannedStep(OPENING_SHEET, target))
        if divisions and channel_id:
            steps.append(PlannedStep(DELETE_BATCH_NOTICE, {"channel_id": int(channel_id)}))

        audit = AuditRecord(
            "SEASON_APPROVED",
            {"season_id": season_id, "stage": "PLACEMENTS"},
            {
                "season_id": season_id,
                "season_number": season_number,
                "stage": "ONGOING",
                "placements_committed": committed,
            },
        )
        return StepResult(
            result={
                "season_id": season_id,
                "season_number": season_number,
                "placements_committed": committed,
            },
            audits=(audit,),
            then=tuple(steps),
        )

    async def forget_the_setup(_ctx: StepContext) -> StepResult:
        forget_setup()
        return StepResult()

    async def arm(ctx: StepContext) -> StepResult:
        """Arm every round of the season, read as the job runs, whatever the modules.

        Weather on arms each round's phases and result submission together; with it off, the
        result submission alone, save where results is on under test mode, whose `/test-mode
        advance` opens each round's wizard by hand. The check-in jobs follow with attendance on.
        Every job is armed against its round and replaces its own (`replace_existing`), so a
        retry arms nothing twice.
        """
        season_id = int(ctx.payload["season_id"])
        season_number = int(ctx.payload["season_number"])
        seasons = SeasonService(ctx.db_path)
        divisions = await seasons.get_divisions(season_id)
        meta = {each.id: (season_number, each.tier) for each in divisions}
        rounds = [
            each
            for division in divisions
            for each in await seasons.get_division_rounds(division.id)
        ]
        attendance, weather = await windows()
        if weather is not None:
            scheduler.schedule_all_rounds(
                rounds,
                division_meta=meta,
                phase_1_days=weather.phase_1_days,
                phase_2_days=weather.phase_2_days,
                phase_3_hours=weather.phase_3_hours,
            )
        else:
            server = await config.get_server_config()
            by_hand = (
                server is not None
                and server.test_mode_active
                and await modules.is_results_enabled()
            )
            if not by_hand:
                scheduler.schedule_result_submission_jobs(rounds, division_meta=meta)
        if attendance is not None:
            for each in rounds:
                number, tier = meta[each.division_id]
                scheduler.schedule_attendance_round(
                    each,
                    season_number=number,
                    division_tier=tier,
                    notice_days=attendance.notice_days,
                    last_notice_hours=attendance.last_notice_hours,
                    deadline_hours=attendance.deadline_hours,
                )
        return StepResult(result={"rounds": len(rounds)})

    async def grant_roles(ctx: StepContext) -> StepResult:
        guild = await _guild(ctx.bot)
        each = ctx.step_payload
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT mention_role_id FROM divisions WHERE id = ?", (each["division_id"],)
            )
            division = await cursor.fetchone()
        role_ids = (
            [int(division["mention_role_id"])]
            if division is not None and division["mention_role_id"]
            else []
        )
        team = await placement.get_team_role_config(str(each["team_name"]))
        if team is not None:
            role_ids.append(team.role_id)
        granted = await placement.grant_roles(guild, int(each["user_id"]), *role_ids)
        return StepResult(result={"granted": granted})

    async def post_batch_notice(ctx: StepContext) -> StepResult:
        guild = await _guild(ctx.bot)
        channel_id = int(ctx.step_payload["channel_id"])
        channel = as_text_channel(guild.get_channel(channel_id))
        if channel is None:
            raise StepFailedOnDiscord(
                f"the channel <#{channel_id}> no longer exists or cannot be posted in"
            )
        message = await send_notice(channel, NOTICE_TEXT)
        return StepResult(
            result={"message_id": message.id, "channel_id": channel_id, "link": message.jump_url}
        )

    def posted_notice(ctx: StepContext) -> dict[str, Any] | None:
        for view in ctx.steps:
            if view.name == POST_BATCH_NOTICE and (view.result or {}).get("message_id") is not None:
                return view.result
        return None

    async def notice_posted(ctx: StepContext) -> bool:
        """The notice is deleted only where it was posted: a discarded post leaves none."""
        return posted_notice(ctx) is not None

    async def refresh_lineup(ctx: StepContext) -> StepResult:
        guild = await _guild(ctx.bot)
        await placement.refresh_lineup(
            guild, int(ctx.step_payload["division_id"]), as_text=ctx.tries > 0
        )
        return StepResult()

    async def post_calendar(ctx: StepContext) -> StepResult:
        """Post one division's calendar, its division and rounds read as the job runs."""
        guild = await _guild(ctx.bot)
        season_number = ctx.payload["season_number"]
        if not season_number:
            log.warning(
                "season approval: season %s carries no number; its calendars will draw "
                "'SEASON 0'",
                ctx.payload["season_id"],
            )
        seasons = SeasonService(ctx.db_path)
        division_id = int(ctx.step_payload["division_id"])
        division = next(
            each
            for each in await seasons.get_divisions(int(ctx.payload["season_id"]))
            if each.id == division_id
        )
        posting = await post_division_calendar(
            ctx.bot,
            guild,
            division,
            await seasons.get_division_rounds(division_id),
            await tracks_by_name(ctx.db_path),
            season_number=season_number,
            raise_on_failure=True,
            as_text=ctx.tries > 0,
        )
        return StepResult(
            result={
                "division": division.name,
                "problem": posting.problem,
                "notices": list(posting.notices),
            }
        )

    async def opening_standings(ctx: StepContext) -> StepResult:
        guild = await _guild(ctx.bot)
        await season_classification_service.post_opening_standings(
            ctx.bot, guild, ctx.db_path, int(ctx.step_payload["division_id"]),
            as_text=ctx.tries > 0,
        )
        return StepResult()

    async def standings_due(_ctx: StepContext) -> bool:
        return await modules.is_results_enabled()

    async def opening_sheet(ctx: StepContext) -> StepResult:
        guild = await _guild(ctx.bot)
        await season_classification_service.post_opening_sheet(
            ctx.bot, guild, ctx.db_path, int(ctx.step_payload["division_id"]),
            as_text=ctx.tries > 0,
        )
        return StepResult()

    async def sheet_due(_ctx: StepContext) -> bool:
        return await modules.is_attendance_enabled()

    async def delete_batch_notice(ctx: StepContext) -> StepResult:
        posted = posted_notice(ctx)
        if posted is None:
            return StepResult(result={"dropped": True})
        guild = await _guild(ctx.bot)
        channel = as_text_channel(guild.get_channel(int(posted["channel_id"])))
        if channel is None:
            return StepResult(result={"gone": True})
        try:
            await delete_notice(channel.get_partial_message(int(posted["message_id"])))
        except discord.NotFound:
            raise
        except discord.HTTPException as error:
            raise StepFailedOnDiscord(
                "the notice could not be deleted", result={"link": posted.get("link")}
            ) from error
        return StepResult(result={"deleted": True})

    async def tell_review_channel(ctx: StepContext) -> StepResult:
        """Tell the channel the review was read in how the approval ended, where the member's
        reply can no longer be updated: fourteen minutes on, or after a restart."""
        number = ctx.payload["season_number"]
        text = (
            channel_approved(ctx.actor_id, number)
            if _applied(ctx)
            else channel_not_approved(ctx.actor_id, number)
        )
        await tell_channel(ctx.bot, int(ctx.payload["channel_id"]), text)
        return StepResult(result={"told": True})

    async def reply_lost(ctx: StepContext) -> bool:
        return not ctx.reply_updatable

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        saved = _applied(ctx)
        if not saved:
            refused = (_view(ctx, APPLY) or _EMPTY).result
            if refused and refused.get("refused"):
                reply = str(refused["refused"])
                return StepResult(
                    result={"closed": True},
                    lines=(refusal_line(ctx.named, _BUTTON, reply_reason(reply)),),
                )
            return StepResult(result={"closed": True})
        applied = (_view(ctx, APPLY) or _EMPTY).result or {}
        lines: list[str] = []
        report = _calendar_report(ctx)
        if report:
            lines.append(report)
        lines.append(
            f"{ctx.named} | /season placements-review | Placements confirmed\n"
            f"  season: {applied.get('season_number', ctx.payload['season_number'])}\n"
            f"  season_id: {applied.get('season_id', ctx.payload['season_id'])}"
            + "".join(f"\n  not done: {line}" for line in _left(ctx))
        )
        return StepResult(result={"closed": True}, lines=tuple(lines))

    # ── How each job is named, in the lines that say it stopped the queue ──────

    async def describe_apply(ctx: StepContext) -> str:
        return f"approving season {ctx.payload['season_number']}'s placements"

    async def describe_forget(_ctx: StepContext) -> str:
        return "letting go of the setup the bot holds in memory"

    async def describe_arm(ctx: StepContext) -> str:
        return f"arming the timed work of season {ctx.payload['season_number']}"

    async def describe_grant(ctx: StepContext) -> str:
        each = ctx.step_payload
        return (
            f"granting <@{each['user_id']}> the roles of **{each['division_name']}** and "
            f"{each['team_name']}"
        )

    async def describe_notice_post(ctx: StepContext) -> str:
        return (
            "posting the notice that the season's posts are being made in "
            f"<#{ctx.step_payload['channel_id']}>"
        )

    async def describe_notice_delete(ctx: StepContext) -> str:
        return (
            "deleting the notice that the season's posts are being made in "
            f"<#{ctx.step_payload['channel_id']}>"
        )

    def describing(template: str) -> Callable[[StepContext], Awaitable[str]]:
        async def describe(ctx: StepContext) -> str:
            return template.format(name=ctx.step_payload["division_name"])

        return describe

    async def describe_tell(ctx: StepContext) -> str:
        return (
            f"telling <#{ctx.payload['channel_id']}> how the approval of season "
            f"{ctx.payload['season_number']} ended"
        )

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the season's approval"

    def outcome(ctx: OutcomeContext) -> str:
        refused = (_view(ctx, APPLY) or _EMPTY).result
        if refused and refused.get("refused"):
            return str(refused["refused"])
        if not _applied(ctx):
            return NOT_SAVED
        applied = (_view(ctx, APPLY) or _EMPTY).result or {}
        text = (
            f"✅ **Season approved and activated!**\n"
            f"Season #{applied.get('season_number', ctx.payload['season_number'])} "
            f"(ID: {applied.get('season_id', ctx.payload['season_id'])})"
        )
        text += approval_checks.not_done_section(_left(ctx, told=False))
        report = _calendar_report(ctx)
        if report:
            head, *rest = report.splitlines()
            text += f"\n\n⚠️ **Calendar images**\n{head}\n" + "\n".join(rest)
        return text

    def doing(payload: dict[str, Any]) -> str:
        return f"Approving season {payload['season_number']}"

    steps: dict[str, Step] = {
        APPLY: Step(APPLY, StepKind.SAVE, apply, describe=describe_apply),
        FORGET_SETUP: Step(FORGET_SETUP, StepKind.ACT, forget_the_setup, describe=describe_forget),
        ARM: Step(ARM, StepKind.ACT, arm, describe=describe_arm),
        GRANT_ROLES: Step(GRANT_ROLES, StepKind.ACT, grant_roles, describe=describe_grant),
        POST_BATCH_NOTICE: Step(
            POST_BATCH_NOTICE, StepKind.ACT, post_batch_notice, describe=describe_notice_post
        ),
        REFRESH_LINEUP: Step(
            REFRESH_LINEUP, StepKind.ACT, refresh_lineup,
            describe=describing("posting the lineup of **{name}**"),
        ),
        POST_CALENDAR: Step(
            POST_CALENDAR, StepKind.ACT, post_calendar,
            describe=describing("posting the calendar of **{name}**"),
        ),
        OPENING_STANDINGS: Step(
            OPENING_STANDINGS, StepKind.ACT, opening_standings, still_due=standings_due,
            describe=describing("posting the opening standings of **{name}**"),
        ),
        OPENING_SHEET: Step(
            OPENING_SHEET, StepKind.ACT, opening_sheet, still_due=sheet_due,
            describe=describing("posting the opening attendance sheet of **{name}**"),
        ),
        DELETE_BATCH_NOTICE: Step(
            DELETE_BATCH_NOTICE, StepKind.DELETE, delete_batch_notice, still_due=notice_posted,
            describe=describe_notice_delete,
        ),
        TELL_REVIEW_CHANNEL: Step(
            TELL_REVIEW_CHANNEL, StepKind.ACT, tell_review_channel, still_due=reply_lost,
            describe=describe_tell,
        ),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(APPLY), PlannedStep(TELL_REVIEW_CHANNEL), PlannedStep(CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{KIND}:{payload['season_id']}",
        doing=doing,
        outcome=outcome,
    )


# ── What the jobs left, read by the outcome and the closing line ────────────────────

_EMPTY = StepView(name="", payload={}, result=None, done=False)


async def _guild(bot: Any) -> discord.Guild:
    """The league's server, or `GuildUnavailable` where it is not in the cache."""
    guild = await league_guild(bot)
    if guild is None:
        raise GuildUnavailable("the league's server is not in the cache")
    return guild


def _view(ctx: OutcomeContext, name: str) -> StepView | None:
    return next((view for view in ctx.steps if view.name == name), None)


def _discarded(view: StepView) -> bool:
    return "discarded" in (view.result or {})


def _applied(ctx: OutcomeContext) -> bool:
    """Whether the save went through: it is done, and neither refused nor discarded."""
    view = _view(ctx, APPLY)
    if view is None or not view.done:
        return False
    return not (_discarded(view) or (view.result or {}).get("refused"))


def _left(ctx: OutcomeContext, *, told: bool = True) -> list[str]:
    """What was not done, one line for each job a league admin discarded, in the order of the
    jobs, the drivers whose roles were not granted gathered into one line, and a discarded
    arming first. *told* adds the review's channel not told, which the member's reply cannot
    carry."""
    lines: list[str] = []
    ungranted: list[str] = []
    ungranted_at: int | None = None
    armed = True
    for view in ctx.steps:
        if not _discarded(view):
            continue
        each = view.payload
        if view.name == ARM:
            armed = False
        elif view.name == FORGET_SETUP:
            lines.append(NOT_FORGOTTEN)
        elif view.name == GRANT_ROLES:
            if ungranted_at is None:
                ungranted_at = len(lines)
                lines.append("")
            ungranted.append(str(each["user_id"]))
        elif view.name == POST_BATCH_NOTICE:
            lines.append(NOTICE_NOT_POSTED)
        elif view.name == REFRESH_LINEUP:
            lines.append(unposted_lineup_line(each["division_name"]))
        elif view.name == POST_CALENDAR:
            lines.append(
                f"**{each['division_name']}** — its calendar could not be posted. Run "
                f"`/division calendar-sync`."
            )
        elif view.name == OPENING_STANDINGS:
            lines.append(
                f"**{each['division_name']}** — its opening standings could not be posted. No "
                f"command posts them again; the first round's results post the standings as "
                f"usual."
            )
        elif view.name == OPENING_SHEET:
            lines.append(
                f"**{each['division_name']}** — its opening attendance sheet could not be "
                f"posted. No command posts it again; the first round's attendance posts the "
                f"sheet as usual."
            )
        elif view.name == DELETE_BATCH_NOTICE:
            link = (view.result or {}).get("link")
            lines.append(
                f"{_NOTICE_NOT_DELETED}{f' ({link})' if link else ''}: delete it by hand."
            )
        elif told and view.name == TELL_REVIEW_CHANNEL:
            lines.append(f"<#{ctx.payload['channel_id']}> was not told how the approval ended")
    if ungranted_at is not None:
        lines[ungranted_at] = ungranted_line(ungranted)
    return [NOT_ARMED, *lines] if not armed else lines


def _calendar_report(ctx: OutcomeContext) -> str:
    """The log line, and the reply's section, on what the calendar images met: a calendar that
    fell back to text, and the notices a drawing carried; empty where none did."""
    problems: list[str] = []
    notices: list[str] = []
    for view in ctx.steps:
        if view.name != POST_CALENDAR or _discarded(view):
            continue
        result = view.result or {}
        if result.get("problem"):
            problems.append(f"{result['division']}: {result['problem']}")
        notices.extend(f"{result['division']}: {detail}" for detail in result.get("notices") or [])
    if not problems and not notices:
        return ""
    report = [f"{ctx.named} | /season placements-review | Calendar image generation"]
    if problems:
        report.append("  Fell back to the textual calendar:")
        report += [f"    - {line}" for line in problems]
    if notices:
        report.append("  Notices:")
        report += [f"    - {line}" for line in notices]
    return "\n".join(report)
