"""Post a division's calendar, as a graphic or as text (037).

**The branch this service exists for.** When the images module is enabled and the
`calendar` aspect is toggled on, the calendar is conveyed as a generated image. When
either is off — or when the generation meets a fatal error on a posting nobody commanded —
it is conveyed in the traditional textual manner. Priority is given to the graphic where
one can be produced; the text is what the league falls back to, never what it loses.

**Only the textual form carries a heading:**

    📅 **Elite — Race Calendar**

A graphic does not. The picture draws the division's own name and says what it is, so a
line of message text above it repeats the picture rather than introducing it, and the
lineup graphic has never carried one either (2026-09-07). The heading is still built in
one place, because the text is posted from two — the first try and the retry that follows a
refusal, both of them the change queue's.

Both forms persist their message id against the division, so whichever posted last is the
one the next replacement deletes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    GuildUnavailable,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.change_queue import (
    CheckContext,
    ChangeType,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason
from leaguebot.core.utils.season_gate import stage_refusal

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService

log = logging.getLogger(__name__)

CALENDAR_ASPECT = "calendar"
#: A round's status once it is called off; read from the round model rather than restated.
CANCELLED_STATUS = RoundStatus.CANCELLED.value
TEMPLATE_KEY = "calendar_template"


async def tracks_by_name(db_path: str) -> dict:
    """The track registry keyed by name.

    Rounds record a track's **name**, not an id (there is no foreign key), so this is the
    join the calendar makes for a round's country and grand prix name. A name matching no
    entry leaves both mandatory values undeterminable, which is fatal — see
    specs/037-calendar-image-generation/research.md § R7.
    """
    from types import SimpleNamespace

    from leaguebot.core.services.track_service import get_all_tracks

    async with get_connection(db_path) as db:
        rows = await get_all_tracks(db)
    return {
        row["name"]: SimpleNamespace(
            name=row["name"], gp_name=row["gp_name"], country=row["country"]
        )
        for row in rows
    }


def calendar_heading(division_name: str) -> str:
    """The heading both forms carry. The textual flow's own string, kept in one place."""
    return f"\U0001f4c5 **{division_name} — Race Calendar**"


def textual_calendar(division_name: str, rounds) -> str:
    """The traditional posting: a heading and one line per round.

    A round's time is a Discord timestamp, which every reader sees in their own zone —
    the one thing the graphic cannot do (Constitution XIV.15).

    A cancelled round keeps its line, struck through and marked, so the calendar still shows
    the season as it was planned and says which weekend was called off (#175). Every other
    line is unchanged, byte for byte.
    """
    lines = [calendar_heading(division_name)]
    # Ordered by the moment each is run, which is the key the inline implementation this
    # replaced used. Byte-identical output when the module is off is the contract (SC-006),
    # so the sort key is preserved rather than swapped for the round number.
    for entry in sorted(rounds, key=lambda r: r.scheduled_at):
        # Stored as plain UTC: a naive time read as the host's own would show each round an
        # hour early on a host in summer time (`discord_ts` reads it the same way).
        moment = entry.scheduled_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        unix = int(moment.timestamp())
        track = entry.track_name or "Mystery"
        line = f"Round {entry.round_number}: {track} — <t:{unix}:F>"
        if getattr(entry, "status", None) == CANCELLED_STATUS:
            line = f"~~{line}~~ — Cancelled"
        lines.append(line)
    return "\n".join(lines)


@dataclass
class CalendarPosting:
    """What one division's calendar posting produced."""

    division_id: int
    posted_as_image: bool = False
    message_id: int | None = None
    problem: str | None = None
    notices: list[str] = field(default_factory=list)

    @property
    def fell_back(self) -> bool:
        """True where a graphic was wanted, could not be made, and text stood in."""
        return self.problem is not None and not self.posted_as_image


async def image_calendar_wanted(bot: LeagueBot) -> bool:
    """Whether this server conveys its calendar as a graphic.

    Both gates must be open: the module enabled, and the `calendar` aspect toggled on.
    Either closed means the textual calendar, unchanged, exactly as before this feature.
    """
    try:
        if not await bot.module_service.is_images_enabled():
            return False
        return await bot.image_config_service.is_aspect_enabled(CALENDAR_ASPECT)
    except Exception:  # noqa: BLE001
        # A fault in the gate must never lose a league its calendar: fall to the text.
        log.exception("calendar: could not read the image gates")
        return False


async def render_calendar_image(
    bot: LeagueBot,
    division,
    rounds,
    tracks,
    *,
    season_number=None,
    output_dir: Path | None = None,
):
    """Render one division's calendar. Returns a RenderOutcome."""
    from leaguebot.image.services.image_calendar_service import build_fill_spec, resolve_drawing
    from leaguebot.image.services.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    config = await bot.image_config_service.get_config()

    drawing = resolve_drawing(
        division_name=division.name,
        division_tier=getattr(division, "tier", None),
        season_number=season_number,
        rounds=rounds,
        tracks=tracks,
        date_format=getattr(config, "date_format", None),
        time_format=getattr(config, "time_format", None),
        time_zone=getattr(config, "time_zone", None),
    )

    # The calendar draws both imagery classes, so it needs both directories (044). They
    # are resolved by the helper every other posting path uses, which anchors them to the
    # project root and keeps the reason any rejection was made. Resolving them here by
    # hand produced *relative* paths, which resolve against the temporary directory the
    # rasteriser reads the SVG from rather than against the project — so every circuit map
    # and every country flag a league supplied was silently absent from the drawing, while
    # the packaged `mystery.svg`, resolved absolutely, appeared beside them.
    directories, directory_faults = resolve_configured_directories(
        config,
        (("track", "track_image_directory"), ("flag", "flag_directory")),
        image_type=TEMPLATE_KEY,
    )

    from leaguebot.image.utils.image_naming import stem_for_drawing

    outcome = await bot.image_render_service.render(
        TEMPLATE_KEY,
        spec_builder_with_faults(
            build_fill_spec, drawing, directories, directory_faults
        ),
        output_dir=output_dir,
        filename_stem=stem_for_drawing(drawing, TEMPLATE_KEY),
        division_name=drawing.division_name,
    )

    # Every other posting path reaches the log channel through `render_for_posting`, which
    # this one cannot use: its two callers differ in origin — the calendar of record is
    # scheduled and falls back to text, `/season placements-review`'s copy is commanded and rejects —
    # and both read a `RenderOutcome` rather than a `PostingDecision`. So the report is
    # made here instead, because a notice that is not reported reaches nobody at all — the
    # log channel and a commanding command's output are the only places one is seen, and
    # since 046 they are the only places one goes. That is why an entire league's circuit
    # maps could go missing from a calendar with not one word said about it anywhere a
    # manager looks.
    if outcome.notices:
        from leaguebot.image.services.image_render_service import ImageRenderService

        await ImageRenderService.report_notices(
            bot, outcome.notices, subject=f"calendar — {division.name}"
        )

    return outcome


#: The calendar's command-output states, mirroring ``image_lineup_post``'s. Named here
#: rather than imported so the calendar's own branch keeps its vocabulary in one file.
#:
#: The league does not convey its calendar as a graphic — the caller posts its text.
NOT_APPLICABLE = "NOT_APPLICABLE"
#: A graphic was drawn and is the caller's to send and discard.
DREW = "DREW"
#: A graphic was wanted and could not be made. Constitution XIV.7 makes a **commanded**
#: posting reject rather than substitute text, so the message says what is at fault.
REJECTED = "REJECTED"


@dataclass
class CalendarCommandOutcome:
    """What a command-output calendar render produced."""

    action: str = NOT_APPLICABLE
    message: str | None = None
    notices: list = field(default_factory=list)
    png_path: Path | None = None

    @property
    def drew(self) -> bool:
        return self.action == DREW


async def render_for_command(
    bot: LeagueBot,
    division,
    rounds,
    tracks,
    *,
    season_number=None,
) -> CalendarCommandOutcome:
    """Produce a division's calendar PNG as **command output**, posting it nowhere.

    The counterpart of :func:`image_lineup_post.render_for_command`, and it exists for the
    same reason: `/season placements-review` shows a manager what their league will see, and that is
    not the calendar *of record*. This function writes no ``calendar_message_id``, deletes
    nothing from the calendar channel and touches no channel at all — which
    :func:`post_division_calendar` necessarily does, and is why a flag on it would not
    serve.

    The caller owns the returned path and must ``discard_render`` it.
    """
    if not await image_calendar_wanted(bot):
        return CalendarCommandOutcome()

    try:
        outcome = await render_calendar_image(
            bot, division, rounds, tracks, season_number=season_number
        )
    except Exception as exc:  # noqa: BLE001 — a resolution fault, reported like any other
        log.exception("calendar: command render raised for division %s", division.id)
        return CalendarCommandOutcome(action=REJECTED, message=f"\u274c {exc}")

    notices = [n.detail for n in (outcome.notices or [])]
    if outcome.problem is not None:
        return CalendarCommandOutcome(
            action=REJECTED,
            message=f"\u274c The calendar image could not be drawn \u2014 {outcome.problem.detail}",
            notices=notices,
        )
    if not outcome.png_paths:
        return CalendarCommandOutcome(
            action=REJECTED,
            message="\u274c The calendar image could not be drawn.",
            notices=notices,
        )

    return CalendarCommandOutcome(
        action=DREW, notices=notices, png_path=outcome.png_paths[0]
    )


async def replace_calendar_message(
    bot: LeagueBot,
    channel,
    division_id: int,
    *,
    content: str | None,
    image_path: Path | None,
    previous_message_id: int | None,
    read_previous: bool = False,
) -> int:
    """Post the calendar and delete the message it replaces, **in that order**.

    *read_previous* reads the message to replace from the division once the new one is up,
    in place of *previous_message_id*, which a caller may hold from before it waited.

    *content* is None for a graphic, which carries no message text of its own, and the
    textual calendar otherwise. Discord accepts a message with an attachment and no text;
    it would refuse one with neither, which is why None never reaches the branch below
    that sends without a file.

    The ordering is the contract. The previous message is deleted only once its
    replacement has been posted successfully, so a failure can never leave the channel
    with no calendar at all.

    The deletion is **not** suppressed under test mode. The suppression that governs
    forecast messages is for a terminal cleanup; this is half of a replacement, and a
    calendar channel holds one calendar in test mode exactly as in live running.
    """
    import discord

    if image_path is not None:
        # Closed here, where the attachment is, so the file's handle is released before
        # the caller's `finally` removes it. The caller still discards the path, which is
        # what covers a picture that never reached a send at all.
        from leaguebot.image.services.image_render_service import discard_attachment

        attachment = discord.File(str(image_path), filename=image_path.name)
        try:
            posted = await channel.send(content, file=attachment)
        finally:
            discard_attachment(attachment)
    else:
        posted = await channel.send(content)

    if read_previous:
        async with get_connection(bot.db_path) as db:
            cursor = await db.execute(
                "SELECT calendar_message_id FROM divisions WHERE id = ?", (division_id,)
            )
            held = await cursor.fetchone()
        previous_message_id = None if held is None else held["calendar_message_id"]

    if previous_message_id and int(previous_message_id) != posted.id:
        try:
            await channel.get_partial_message(int(previous_message_id)).delete()
        except discord.HTTPException as exc:
            # The replacement is already up; a stale or hand-deleted message is not worth
            # failing over. The id is overwritten below either way.
            log.warning(
                "calendar: could not delete the replaced message %s: %s",
                previous_message_id,
                exc,
            )

    async with get_connection(bot.db_path) as db:
        await db.execute(
            "UPDATE divisions SET calendar_message_id = ? WHERE id = ?",
            (str(posted.id), division_id),
        )
        await db.commit()
    return posted.id


async def post_division_calendar(
    bot: LeagueBot,
    guild,
    division,
    rounds,
    tracks,
    *,
    season_number=None,
    commanded: bool = False,
    raise_on_failure: bool = False,
    as_text: bool = False,
) -> CalendarPosting:
    """Convey one division's calendar, as a graphic where one can be produced.

    *commanded* distinguishes a posting a user asked for from one reached at a schedule or
    at approval (Constitution XIV.7). An uncommanded posting falls back to the text; a
    commanded one does not — the caller is told what is at fault and nothing is posted, so
    the one person able to fix the template is given the chance.

    *raise_on_failure* is the form a job on the change queue calls (a season's approval; a
    cancellation's repost after it). A calendar channel that is set and no longer on the
    server, or a send Discord refuses, raises `StepFailedOnDiscord` from the Discord fault,
    and nothing goes to the old retry queue: the queue's own stop and retry are the retry.
    The calendar replaced is read from the division as it posts (`replace_calendar_message`'s
    *read_previous*), not taken from *division*, which a job may have read before the queue
    waited, so an approval that waited cannot delete a calendar already gone and leave the
    newer one standing. A calendar channel
    never set still returns the posting with its problem, there being nothing to retry. A
    posting not raising reports a fault in its problem; it queues nothing for retry.

    *as_text* posts the textual calendar without drawing: the retry of a job whose picture
    could not be posted goes as text (Constitution XIV, rule 8).
    """
    from leaguebot.core.models.change import StepFailedOnDiscord

    result = CalendarPosting(division_id=division.id)

    channel = guild.get_channel(division.calendar_channel_id) if guild else None
    if channel is None:
        if raise_on_failure and division.calendar_channel_id:
            raise StepFailedOnDiscord(
                f"the calendar channel <#{division.calendar_channel_id}> of {division.name} "
                "is no longer on the server"
            )
        result.problem = "no calendar channel is configured for this division"
        return result

    from leaguebot.image.services.image_render_service import discard_render

    # A graphic carries no heading. The picture draws the division's own name and says
    # what it is, so a line of message text above it repeats the picture rather than
    # introducing it — the lineup graphic has never carried one either. The textual
    # calendar keeps its heading below, where it is the only thing naming the list.
    content: str | None = None
    image_path: Path | None = None

    # Everything from the render to the post sits inside one `finally`, so the picture is
    # removed whichever way this ends — posted, refused to a commanded caller, or lost to
    # a Discord fault that stops the change queue at the posting.
    try:
        if not as_text and await image_calendar_wanted(bot):
            try:
                outcome = await render_calendar_image(
                    bot, division, rounds, tracks, season_number=season_number
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("calendar: render raised for division %s", division.id)
                outcome = None
                result.problem = str(exc)
            else:
                result.notices = [n.detail for n in (outcome.notices or [])]
                if outcome.problem is not None:
                    result.problem = outcome.problem.detail
                elif outcome.png_paths:
                    image_path = outcome.png_paths[0]

            if result.problem is not None and commanded:
                # Commanded: reject, post nothing, delete nothing.
                return result

        if image_path is None:
            # Either the league conveys its calendar as text, or a graphic was wanted and
            # could not be made and this posting is not one a user commanded.
            content = textual_calendar(division.name, rounds)

        try:
            message_id = await replace_calendar_message(
                bot,
                channel,
                division.id,
                content=content,
                image_path=image_path,
                previous_message_id=getattr(division, "calendar_message_id", None),
                read_previous=raise_on_failure,
            )
        except Exception as exc:  # noqa: BLE001
            if raise_on_failure:
                import discord

                if isinstance(exc, discord.HTTPException):
                    raise StepFailedOnDiscord(
                        f"the calendar of {division.name} could not be posted: {exc}"
                    ) from exc
                raise
            log.exception("calendar: posting failed for division %s", division.id)
            result.problem = result.problem or str(exc)
            return result

        result.message_id = message_id
        result.posted_as_image = image_path is not None
        return result
    finally:
        discard_render(image_path)


# ── /division calendar-sync, on the change queue ─────────────────────────────

#: The kind of the change that posts a division's calendar again on a member's command.
CALENDAR_SYNC = "calendar.sync"
POST_CALENDAR = "post_calendar"
CLOSE = "close"
_COMMAND = "`/division calendar-sync`"


def calendar_steps(seasons: "SeasonService") -> dict[str, Step]:
    """The jobs that post a division's calendar, for a change type's `steps`.

    The payload is `{"division_id", "division_name", "season_id"}`, and `"commanded": True` for a
    posting a member asked for (Constitution XIV.7). The season's number is the job's own or the
    change's. The division and its rounds are read as the job runs, and a retry posts the text
    (rule 8).
    """

    def field(ctx: StepContext, key: str) -> Any:
        """The job's own value, else the change's."""
        return ctx.step_payload.get(key, ctx.payload.get(key))

    async def division_of(ctx: StepContext) -> Any:
        wanted = int(field(ctx, "division_id"))
        return next(
            (each for each in await seasons.get_divisions(int(field(ctx, "season_id")))
             if each.id == wanted),
            None,
        )

    async def still_due(ctx: StepContext) -> bool:
        return await division_of(ctx) is not None

    async def post_calendar(ctx: StepContext) -> StepResult:
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        division = await division_of(ctx)
        if division is None:
            raise LookupError(f"division {field(ctx, 'division_id')} is no longer there")
        season_number = field(ctx, "season_number")
        if not season_number:
            log.warning(
                "calendar: season %s carries no number; the calendar of %s will draw 'SEASON 0'",
                field(ctx, "season_id"),
                division.name,
            )
        posting = await post_division_calendar(
            ctx.bot,
            guild,
            division,
            await seasons.get_division_rounds(division.id),
            await tracks_by_name(ctx.db_path),
            season_number=season_number,
            commanded=bool(field(ctx, "commanded")),
            raise_on_failure=True,
            as_text=ctx.tries > 0,
        )
        return StepResult(
            result={
                "division": division.name,
                "problem": posting.problem,
                "fell_back": posting.fell_back,
                "notices": list(posting.notices),
                "rejected": posting.problem is not None and posting.message_id is None,
                "posted_as_image": posting.posted_as_image,
            }
        )

    async def describe(ctx: StepContext) -> str:
        return f"posting the calendar of **{field(ctx, 'division_name')}**"

    return {POST_CALENDAR: Step(POST_CALENDAR, StepKind.ACT, post_calendar,
                                still_due=still_due, describe=describe)}


def calendar_sync_change(seasons: "SeasonService") -> ChangeType:
    """`/division calendar-sync` carried out on the change queue, whole: it only posts.

    A member's change, acknowledged at once and the acknowledgement updated with the outcome.
    Its check holds the command's gates again as the change runs (core specification, "How a
    change is carried out"): the season's stage, the division still of the season and its
    calendar channel. Repeatable, a repost reading what it posts when it runs. A commanded
    posting: a render that fails posts nothing and the closing job writes the refusal.
    """

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        season = await seasons.get_setup_or_active_season()
        if season is None:
            return Verdict.refuse(str(stage_refusal(None, "division calendar-sync")))
        refusal = stage_refusal(season.stage, "division calendar-sync")
        if refusal is not None:
            return Verdict.refuse(refusal)
        name = payload["division_name"]
        division = next(
            (each for each in await seasons.get_divisions(season.id)
             if each.id == int(payload["division_id"])),
            None,
        )
        if division is None:
            return Verdict.refuse(f"❌ Division **{name}** not found in the current season.")
        if not division.calendar_channel_id:
            return Verdict.refuse(
                f"❌ **{name}** has no calendar channel configured. "
                f"Set one with `/division calendar-channel` first."
            )
        return Verdict.go()

    def view(ctx: OutcomeContext, name: str) -> StepView | None:
        return next((each for each in reversed(ctx.steps) if each.name == name), None)

    def discarded(each: StepView | None) -> bool:
        return each is not None and "discarded" in (each.result or {})

    def rejection(ctx: OutcomeContext) -> str | None:
        posted = view(ctx, POST_CALENDAR)
        result = (posted.result if posted is not None else None) or {}
        if discarded(posted) or not result.get("rejected"):
            return None
        return (
            f"❌ The calendar for **{ctx.payload['division_name']}** was not posted — "
            f"{result['problem']}\nNothing was deleted; the previous calendar still stands."
        )

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        posted = view(ctx, POST_CALENDAR)
        result = (posted.result if posted is not None else None) or {}
        if discarded(posted) or not result:
            return StepResult(result={"closed": True})
        rejected = rejection(ctx)
        if rejected is not None:
            return StepResult(
                result={"closed": True},
                lines=(refusal_line(ctx.named, _COMMAND, reply_reason(rejected)),),
            )
        drawn = "image" if result.get("posted_as_image") else "text"
        return StepResult(
            result={"closed": True},
            lines=(
                f"{ctx.named} | /division calendar-sync | Success\n"
                f"  division: {ctx.payload['division_name']}\n"
                f"  posted as: {drawn}\n"
                + "".join(f"  notice: {each}\n" for each in result.get("notices", [])),
            ),
        )

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the calendar sync"

    def outcome(ctx: OutcomeContext) -> str:
        name = ctx.payload["division_name"]
        posted = view(ctx, POST_CALENDAR)
        if discarded(posted):
            return (
                f"⛔ The calendar for **{name}** was not posted: a league admin discarded it. "
                f"The previous calendar still stands; run `/division calendar-sync` again."
            )
        rejected = rejection(ctx)
        if rejected is not None:
            return rejected
        result = (posted.result if posted is not None else None) or {}
        drawn = "image" if result.get("posted_as_image") else "text"
        lines = [f"✅ Calendar for **{name}** reposted as {drawn}."]
        if result.get("notices"):
            lines.append("")
            lines.append("**Notices**")
            lines.extend(f"  • {detail}" for detail in result["notices"])
        return "\n".join(lines)

    return ChangeType(
        kind=CALENDAR_SYNC,
        opening=(
            PlannedStep(POST_CALENDAR, {"commanded": True}),
            PlannedStep(CLOSE),
        ),
        steps={
            **calendar_steps(seasons),
            CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
        },
        check=check,
        key=lambda payload: f"{CALENDAR_SYNC}:{payload['division_id']}",
        doing=lambda payload: f"Posting the calendar of **{payload['division_name']}** again",
        outcome=outcome,
        repeatable=True,
    )
