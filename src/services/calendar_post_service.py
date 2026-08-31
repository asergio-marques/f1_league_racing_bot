"""Post a division's calendar, as a graphic or as text (037).

**The branch this service exists for.** When the images module is enabled and the
`calendar` aspect is toggled on, the calendar is conveyed as a generated image. When
either is off — or when the generation meets a fatal error on a posting nobody commanded —
it is conveyed in the traditional textual manner. Priority is given to the graphic where
one can be produced; the text is what the league falls back to, never what it loses.

Both forms carry the **same heading**, built here once, so the two cannot drift apart:

    📅 **Elite — Race Calendar**

and both persist their message id against the division, so whichever posted last is the
one the next replacement deletes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from db.database import get_connection

log = logging.getLogger(__name__)

CALENDAR_ASPECT = "calendar"
TEMPLATE_KEY = "calendar_template"


async def tracks_by_name(db_path: str) -> dict:
    """The track registry keyed by name.

    Rounds record a track's **name**, not an id (there is no foreign key), so this is the
    join the calendar makes for a round's country and grand prix name. A name matching no
    entry leaves both mandatory values undeterminable, which is fatal — see
    specs/037-calendar-image-generation/research.md § R7.
    """
    from types import SimpleNamespace

    from services.track_service import get_all_tracks

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
    """
    lines = [calendar_heading(division_name)]
    # Ordered by the moment each is run, which is the key the inline implementation this
    # replaced used. Byte-identical output when the module is off is the contract (SC-006),
    # so the sort key is preserved rather than swapped for the round number.
    for entry in sorted(rounds, key=lambda r: r.scheduled_at):
        unix = int(entry.scheduled_at.timestamp())
        track = entry.track_name or "Mystery"
        lines.append(f"Round {entry.round_number}: {track} — <t:{unix}:F>")
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


async def image_calendar_wanted(bot, server_id: int) -> bool:
    """Whether this server conveys its calendar as a graphic.

    Both gates must be open: the module enabled, and the `calendar` aspect toggled on.
    Either closed means the textual calendar, unchanged, exactly as before this feature.
    """
    try:
        if not await bot.module_service.is_images_enabled(server_id):
            return False
        return await bot.image_config_service.is_aspect_enabled(server_id, CALENDAR_ASPECT)
    except Exception:  # noqa: BLE001
        # A fault in the gate must never lose a league its calendar: fall to the text.
        log.exception("calendar: could not read the image gates for server %s", server_id)
        return False


async def render_calendar_image(
    bot,
    server_id: int,
    division,
    rounds,
    tracks,
    *,
    season_number=None,
    output_dir: Path | None = None,
):
    """Render one division's calendar. Returns a RenderOutcome."""
    from services.image_calendar_service import build_fill_spec, resolve_drawing
    from services.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )

    config = await bot.image_config_service.get_config(server_id)

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

    from utils.image_naming import stem_for_drawing

    outcome = await bot.image_render_service.render(
        server_id,
        TEMPLATE_KEY,
        spec_builder_with_faults(
            build_fill_spec, drawing, directories, directory_faults
        ),
        output_dir=output_dir,
        filename_stem=stem_for_drawing(drawing, TEMPLATE_KEY),
    )

    # Every other posting path reaches the log channel through `render_for_posting`, which
    # this one cannot use: its two callers differ in origin — the calendar of record is
    # scheduled and falls back to text, `/season review`'s copy is commanded and rejects —
    # and both read a `RenderOutcome` rather than a `PostingDecision`. So the report is
    # made here instead, because a notice that reaches only the `image_render_notices`
    # table reaches nobody. That is why an entire league's circuit maps could go missing
    # from a calendar with not one word said about it anywhere a manager looks.
    if outcome.notices:
        from services.image_render_service import ImageRenderService

        await ImageRenderService.report_notices(
            bot, server_id, outcome.notices, subject=f"calendar — {division.name}"
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
    bot,
    server_id: int,
    division,
    rounds,
    tracks,
    *,
    season_number=None,
) -> CalendarCommandOutcome:
    """Produce a division's calendar PNG as **command output**, posting it nowhere.

    The counterpart of :func:`image_lineup_post.render_for_command`, and it exists for the
    same reason: `/season review` shows a manager what their league will see, and that is
    not the calendar *of record*. This function writes no ``calendar_message_id``, deletes
    nothing from the calendar channel and touches no channel at all — which
    :func:`post_division_calendar` necessarily does, and is why a flag on it would not
    serve.

    The caller owns the returned path and must ``discard_render`` it.
    """
    if not await image_calendar_wanted(bot, server_id):
        return CalendarCommandOutcome()

    try:
        outcome = await render_calendar_image(
            bot, server_id, division, rounds, tracks, season_number=season_number
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
    bot,
    channel,
    division_id: int,
    *,
    content: str,
    image_path: Path | None,
    previous_message_id: int | None,
) -> int:
    """Post the calendar and delete the message it replaces, **in that order**.

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
        from services.image_render_service import discard_attachment

        attachment = discord.File(str(image_path), filename=image_path.name)
        try:
            posted = await channel.send(content, file=attachment)
        finally:
            discard_attachment(attachment)
    else:
        posted = await channel.send(content)

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
    bot,
    guild,
    server_id: int,
    division,
    rounds,
    tracks,
    *,
    season_number=None,
    commanded: bool = False,
) -> CalendarPosting:
    """Convey one division's calendar, as a graphic where one can be produced.

    *commanded* distinguishes a posting a user asked for from one reached at a schedule or
    at approval (Constitution XIV.7). An uncommanded posting falls back to the text; a
    commanded one does not — the caller is told what is at fault and nothing is posted, so
    the one person able to fix the template is given the chance.
    """
    result = CalendarPosting(division_id=division.id)

    channel = guild.get_channel(division.calendar_channel_id) if guild else None
    if channel is None:
        result.problem = "no calendar channel is configured for this division"
        return result

    from services.image_render_service import discard_render

    content = calendar_heading(division.name)
    image_path: Path | None = None

    # Everything from the render to the post sits inside one `finally`, so the picture is
    # removed whichever way this ends — posted, refused to a commanded caller, or lost to
    # a Discord fault that sends the textual calendar to the retry queue instead.
    try:
        if await image_calendar_wanted(bot, server_id):
            try:
                outcome = await render_calendar_image(
                    bot, server_id, division, rounds, tracks, season_number=season_number
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
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("calendar: posting failed for division %s", division.id)
            # A Discord fault rather than a generation fault: the *textual* calendar is
            # what is enqueued for retry (FR-020).
            from services import retry_service

            try:
                await retry_service.enqueue(
                    bot.db_path,
                    server_id,
                    division.calendar_channel_id,
                    textual_calendar(division.name, rounds),
                    f"calendar post failed: {exc}",
                )
            except Exception:  # noqa: BLE001
                log.exception("calendar: could not enqueue the retry")
            result.problem = result.problem or str(exc)
            return result

        result.message_id = message_id
        result.posted_as_image = image_path is not None
        return result
    finally:
        discard_render(image_path)
