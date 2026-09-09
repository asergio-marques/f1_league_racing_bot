"""verdict_announcement_service.py — Post penalty and appeal announcements.

One Discord message per penalty or appeal correction, posted to the division's
configured verdicts (penalty) channel after the respective review is approved.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import discord

from db.database import get_connection
from models.points_config import SessionType
from services import image_verdict_post
from services.image_verdict_service import VerdictKind
from utils import results_formatter

log = logging.getLogger(__name__)

#: What the announcement carries where the steward entered neither. The **message** italicises
#: it; the graphic draws the value the markup adorned and leaves the distinguishing to the
#: template's typography (Constitution XIV.16).
NOT_PROVIDED = "(not provided)"


def _for_message(value: str) -> str:
    """Apply the channel emphasis the placeholder carries in the textual announcement."""
    return f"*{NOT_PROVIDED}*" if value == NOT_PROVIDED else value


def _graphic_name(display_name: str | None, discord_user_id: int) -> str:
    """The name the graphic draws in place of a mention (XIV.16).

    Resolved by the chain every graphic of the module resolves a person by, so one driver is
    one name wherever the module names them.
    """
    from services.image_lineup_service import resolve_driver_name

    return resolve_driver_name(
        discord_user_id=discord_user_id, display_name=display_name
    )

# +Ns or -Ns  (with optional sign, digits, optional 's')
_PENALTY_RE = re.compile(r"^([+-]?\d+)s?$", re.IGNORECASE)


def translate_penalty(penalty_str: str) -> str:
    """Convert a raw penalty magnitude to a human-readable description.

    A positive magnitude is time **added** to the driver's time, which is what a
    time penalty does; a negative one is time removed, as an appeal correction does.

    | Input       | Output                    |
    |-------------|---------------------------|
    | ``+5s``     | ``5 seconds added``       |
    | ``5s``      | ``5 seconds added``       |
    | ``-3s``     | ``3 seconds removed``     |
    | ``DSQ``     | ``Disqualified``          |
    """
    if penalty_str.strip().upper() == "DSQ":
        return "Disqualified"
    m = _PENALTY_RE.match(penalty_str.strip())
    if m:
        seconds = int(m.group(1))
        if seconds < 0:
            return f"{abs(seconds)} seconds removed"
        return f"{seconds} seconds added"
    return penalty_str  # fallback: return raw value unchanged


def describe_penalty(penalty_type: str | None, time_seconds: int | None) -> str:
    """The descriptive rendering of a sanction, from the record's own two fields.

    The one place the magnitude is turned into the sentence a league reads. Both the textual
    announcement and the verdict graphic call it, so a change here reaches both by the same
    stroke — Constitution XIV.7's one rendering, two presentations.
    """
    if penalty_type == "DSQ" or time_seconds is None:
        return translate_penalty("DSQ")
    magnitude = f"+{time_seconds}s" if time_seconds >= 0 else f"{time_seconds}s"
    return translate_penalty(magnitude)


async def _get_announcement_context(db_path: str, round_id: int) -> dict:
    """Return the round's season, division, verdicts channel, tier and track record.

    The last three are the banner's, and are read here rather than in a query of its own:
    this runs once on every review that applies anything, and the round row it already
    reads carries them. The `tracks` join is the one every other posting path makes --
    `image_verdict_post._round_context` and the weather path both -- so the banner cannot
    disagree with the card beneath it about what a round is called.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT s.season_number, d.name AS division_name, d.tier AS division_tier,
                   drc.penalty_channel_id, r.round_number,
                   t.gp_name AS race_name, t.country AS country_name
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            LEFT JOIN tracks t ON t.name = r.track_name
            WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return {}
    return {
        "season_number": row["season_number"],
        "division_name": row["division_name"],
        "division_tier": row["division_tier"],
        "penalty_channel_id": row["penalty_channel_id"],
        "round_number": row["round_number"],
        "race_name": row["race_name"],
        "country_name": row["country_name"],
    }


def _banner_once(bot, channel, server_id: int, ctx: dict):
    """A callable that heads a run of verdicts with a banner, at most once.

    **Lazy, and that is the point.** A run can produce nothing: a record whose result
    context will not load is skipped, and every record of a run may be such a one. Posted
    eagerly, the banner would then stand alone over an empty run. Called immediately before
    the first verdict that actually goes out, it cannot.

    **One of these covers a whole approval, not one function.** Approving a penalty review
    posts the penalty verdicts and then, further down the same call, the attendance
    sanctions that review's scoring triggered — into the same channel, for the same round.
    They are one run of verdicts as a league reads them, so `finalize_penalty_review` builds
    one poster with `banner_for_round` and hands it to both paths; the second finds it
    already spent. An attendance sanction firing where no penalty was applied heads itself,
    which is the case a per-function poster left bare (decided 2026-09-09).

    Never raises, and never returns anything the caller must act on: a header failing must
    not cost a league the decisions it heads.
    """
    posted = False

    async def post() -> None:
        nonlocal posted
        if posted:
            return
        posted = True  # set before the attempt: one try per batch, whatever it returns
        try:
            from services import image_verdict_banner_post

            drawing = image_verdict_banner_post.build_drawing(
                season_number=ctx.get("season_number"),
                division_name=ctx["division_name"],
                division_tier=ctx.get("division_tier"),
                round_number=ctx.get("round_number"),
                race_name=ctx.get("race_name"),
                country_name=ctx.get("country_name"),
            )
            await image_verdict_banner_post.try_post(bot, channel, server_id, drawing)
        except Exception:
            log.exception("verdict banner: could not head the batch for server %s", server_id)

    return post


def banner_for_round(bot, db_path: str, round_id: int):
    """A shared banner poster for every verdict one approval will post.

    The same callable as :func:`_banner_once`, resolving the round's context and channel on
    the first call rather than being handed them. That is what lets a caller holding neither
    — `finalize_penalty_review`, which knows only the round — build one poster and pass it
    to the several paths that post verdicts beneath it.

    Resolving lazily costs nothing where no verdict follows: an approval applying no penalty
    and triggering no sanction never calls it, so the query is never made.
    """
    posted = False

    async def post() -> None:
        nonlocal posted
        if posted:
            return
        posted = True  # set before the attempt: one try per approval, whatever it returns
        try:
            ctx = await _get_announcement_context(db_path, round_id)
            if not ctx:
                return
            channel_id_raw = ctx.get("penalty_channel_id")
            if channel_id_raw is None:
                return  # no verdicts channel configured — skip silently
            channel = bot.get_channel(int(channel_id_raw))
            if channel is None:
                return
            server_id = getattr(getattr(channel, "guild", None), "id", 0)
            await _banner_once(bot, channel, server_id, ctx)()
        except Exception:
            log.exception("verdict banner: could not head round %s", round_id)

    return post


async def _get_result_context(db_path: str, race_result_id: int | None, qual_result_id: int | None) -> dict:
    """Return round_number, session_type, format for a race or qualifying result row."""
    async with get_connection(db_path) as db:
        if race_result_id is not None:
            cursor = await db.execute(
                """
                SELECT r.round_number, sr.session_type, r.format, r.id AS round_id,
                       r.division_id
                FROM race_session_results rsr
                JOIN session_results sr ON sr.id = rsr.session_result_id
                JOIN rounds r ON r.id = sr.round_id
                WHERE rsr.id = ?
                """,
                (race_result_id,),
            )
        elif qual_result_id is not None:
            cursor = await db.execute(
                """
                SELECT r.round_number, sr.session_type, r.format, r.id AS round_id,
                       r.division_id
                FROM qualifying_session_results qsr
                JOIN session_results sr ON sr.id = qsr.session_result_id
                JOIN rounds r ON r.id = sr.round_id
                WHERE qsr.id = ?
                """,
                (qual_result_id,),
            )
        else:
            return {}
        row = await cursor.fetchone()
    if row is None:
        return {}
    return {
        "round_number": row["round_number"],
        "session_type": row["session_type"],
        "format": row["format"],
        "round_id": row["round_id"],
        "division_id": row["division_id"],
    }


def _build_announcement_message(
    season_number: int | None,
    division_name: str,
    round_number: int,
    session_label: str,
    driver_discord_id: int,
    penalty_description: str,
    description_text: str,
    justification_text: str,
    driver_display_name: str | None = None,
) -> str:
    """Build the full announcement message per contract."""
    season_prefix = f"Season {season_number} " if season_number is not None else ""
    heading = f"**{season_prefix}{division_name} Round {round_number} \u2014 {session_label}**"
    separator = "\u2501" * 35
    driver_ref = f"<@{driver_discord_id}>"
    if driver_display_name:
        driver_ref += f" ({driver_display_name})"
    return (
        f"{heading}\n"
        f"{separator}\n"
        f"**Driver**: {driver_ref}\n"
        f"**Penalty**: {penalty_description}\n"
        f"**Description**: {_for_message(description_text)}\n"
        f"**Justification**: {_for_message(justification_text)}"
    )


async def _send_verdict(
    bot,
    target_channel,
    *,
    server_id: int,
    db_path: str,
    round_id: int,
    kind,
    season_number,
    division_name: str,
    round_number,
    session_label: str | None,
    driver_discord_id: int,
    driver_display_name: str | None,
    driver_name: str,
    penalty_description: str,
    description_text: str,
    justification_text: str,
    team_name: str | None = None,
) -> None:
    """Post one verdict: as a graphic where the toggle allows, as text otherwise.

    The graphic **displaces the whole announcement but the mention** (Constitution XIV.7): its
    heading, driver line, sanction, description and justification all move onto the canvas and
    the message keeps the mention alone. A failed render falls back to the text this function
    would have sent anyway — no state is persisted either way, so there is nothing to reconcile.

    Called only after the review has been finalised or the sanction enforced. A graphic is
    downstream of every state change it depicts and is never a precondition of one.
    """
    from services import image_verdict_post

    render = None
    if await image_verdict_post.verdicts_enabled(bot, server_id):
        try:
            drawing = await image_verdict_post.build_drawing(
                bot,
                db_path=db_path,
                round_id=round_id,
                kind=kind,
                server_id=server_id,
                season_number=season_number,
                division_name=division_name,
                round_number=round_number,
                session_label=session_label,
                driver_name=driver_name,
                driver_discord_id=driver_discord_id,
                penalty_description=penalty_description,
                description_text=description_text,
                justification_text=justification_text,
                team_name=team_name,
            )
            render = await image_verdict_post.render_verdict(bot, server_id, drawing)
        except Exception:  # noqa: BLE001 — a graphic never costs a league its announcement
            log.exception("verdict graphic failed for driver %s", driver_discord_id)
            render = None

        subject = image_verdict_post.describe(
            division_name=division_name,
            round_number=round_number,
            session_name=session_label,
            driver_name=driver_name,
            season_number=season_number,
        )
        if render is not None and render.notices:
            await image_verdict_post.report_notices(
                bot, server_id, subject, render.notices
            )
        if render is not None and render.problem:
            await image_verdict_post.report(bot, server_id, subject, render.problem)

    if render is not None and render.draws:
        import discord as _discord

        # Named, rather than left to Discord to read the path's basename: the render
        # service already wrote a name saying which season, division and round this
        # verdict belongs to, and leaking the raw template key was never intended.
        attachment = _discord.File(str(render.png), filename=Path(render.png).name)
        try:
            await target_channel.send(f"<@{driver_discord_id}>", file=attachment)
        finally:
            image_verdict_post.discard(render, attachment)
        return

    content = _build_announcement_message(
        season_number,
        division_name,
        round_number,
        session_label or "Attendance Sanction",
        driver_discord_id,
        penalty_description,
        description_text,
        justification_text,
        driver_display_name=driver_display_name,
    )
    await target_channel.send(content)


async def post_penalty_announcements(
    bot,
    state,  # PenaltyReviewState
    applied_penalties: list,
    *,
    head=None,
) -> None:
    """Post one announcement per applied penalty to the verdicts channel.

    Skips silently if the verdicts channel is not configured or inaccessible.
    Does not block finalization on any error.

    *head* is the approval's shared banner poster where the caller built one, so that the
    attendance sanctions posted later in the same approval fall under this run's banner
    rather than raising a second. Absent one, this run heads itself.
    """
    if not applied_penalties:
        return

    db_path: str = state.db_path
    round_id: int = state.round_id

    ctx = await _get_announcement_context(db_path, round_id)
    if not ctx:
        log.warning("post_penalty_announcements: could not load context for round %s", round_id)
        return

    penalty_channel_id_raw = ctx.get("penalty_channel_id")
    if penalty_channel_id_raw is None:
        return  # no verdicts channel configured — skip silently

    target_channel = bot.get_channel(int(penalty_channel_id_raw))
    if target_channel is None:
        log.error(
            "post_penalty_announcements: verdicts channel %s inaccessible for round %s — skipping",
            penalty_channel_id_raw,
            round_id,
        )
        return

    season_number = ctx["season_number"]
    division_name = ctx["division_name"]
    server_id = getattr(getattr(target_channel, "guild", None), "id", 0)
    KIND = VerdictKind.PENALTY
    head_the_batch = head or _banner_once(bot, target_channel, server_id, ctx)

    for record in applied_penalties:
        try:
            race_result_id = record.get("race_result_id") if hasattr(record, "get") else getattr(record, "race_result_id", None)
            qual_result_id = record.get("qual_result_id") if hasattr(record, "get") else getattr(record, "qual_result_id", None)
            driver_discord_id: int = record.get("driver_user_id") if hasattr(record, "get") else getattr(record, "driver_user_id", 0)

            result_ctx = await _get_result_context(db_path, race_result_id, qual_result_id)
            if not result_ctx:
                log.warning("post_penalty_announcements: no result context for record %r", record)
                continue

            round_number: int = result_ctx["round_number"]
            session_type_str: str = result_ctx["session_type"]
            is_sprint: bool = str(result_ctx["format"]).upper() == "SPRINT"
            st = SessionType(session_type_str)
            session_label = results_formatter.format_session_label(st, is_sprint=is_sprint)

            # Resolve test display name
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT test_display_name FROM driver_profiles WHERE CAST(discord_user_id AS INTEGER) = ?",
                    (driver_discord_id,),
                )
                dp_row = await cursor.fetchone()
            test_display_name: str | None = dp_row["test_display_name"] if dp_row else None

            penalty_type = record.get("penalty_type") if hasattr(record, "get") else getattr(record, "penalty_type", "")
            time_seconds = record.get("time_seconds") if hasattr(record, "get") else getattr(record, "time_seconds", None)
            description_text = record.get("description") if hasattr(record, "get") else getattr(record, "description", "")
            justification_text = record.get("justification") if hasattr(record, "get") else getattr(record, "justification", "")

            penalty_description = describe_penalty(penalty_type, time_seconds)

            team_name = await image_verdict_post.team_name_for_entry(
                bot,
                getattr(target_channel, "guild", None),
                server_id=server_id,
                division_id=result_ctx["division_id"],
                role_id=record.get("team_role_id")
                if hasattr(record, "get")
                else getattr(record, "team_role_id", None),
            )

            await head_the_batch()

            await _send_verdict(
                bot,
                target_channel,
                server_id=server_id,
                db_path=db_path,
                round_id=result_ctx["round_id"],
                kind=KIND,
                season_number=season_number,
                division_name=division_name,
                round_number=round_number,
                session_label=session_label,
                driver_discord_id=driver_discord_id,
                driver_display_name=test_display_name,
                driver_name=_graphic_name(test_display_name, driver_discord_id),
                penalty_description=penalty_description,
                description_text=description_text or NOT_PROVIDED,
                justification_text=justification_text or NOT_PROVIDED,
                team_name=team_name,
            )

        except Exception:
            log.exception(
                "post_penalty_announcements: error posting announcement for record %r", record
            )


async def post_appeal_announcements(
    bot,
    state,  # PenaltyReviewState
    applied_corrections: list,
    *,
    head=None,
) -> None:
    """Post one announcement per applied appeal correction to the verdicts channel.

    Identical contract to :func:`post_penalty_announcements`.
    Skips silently if the verdicts channel is not configured or inaccessible.
    """
    if not applied_corrections:
        return

    db_path: str = state.db_path
    round_id: int = state.round_id

    ctx = await _get_announcement_context(db_path, round_id)
    if not ctx:
        log.warning("post_appeal_announcements: could not load context for round %s", round_id)
        return

    penalty_channel_id_raw = ctx.get("penalty_channel_id")
    if penalty_channel_id_raw is None:
        return  # no verdicts channel configured — skip silently

    target_channel = bot.get_channel(int(penalty_channel_id_raw))
    if target_channel is None:
        log.error(
            "post_appeal_announcements: verdicts channel %s inaccessible for round %s — skipping",
            penalty_channel_id_raw,
            round_id,
        )
        return

    season_number = ctx["season_number"]
    division_name = ctx["division_name"]
    server_id = getattr(getattr(target_channel, "guild", None), "id", 0)
    KIND = VerdictKind.APPEAL
    head_the_batch = head or _banner_once(bot, target_channel, server_id, ctx)

    for record in applied_corrections:
        try:
            race_result_id = record.get("race_result_id") if hasattr(record, "get") else getattr(record, "race_result_id", None)
            qual_result_id = record.get("qual_result_id") if hasattr(record, "get") else getattr(record, "qual_result_id", None)
            driver_discord_id: int = record.get("driver_user_id") if hasattr(record, "get") else getattr(record, "driver_user_id", 0)

            result_ctx = await _get_result_context(db_path, race_result_id, qual_result_id)
            if not result_ctx:
                log.warning("post_appeal_announcements: no result context for record %r", record)
                continue

            round_number: int = result_ctx["round_number"]
            session_type_str: str = result_ctx["session_type"]
            is_sprint: bool = str(result_ctx["format"]).upper() == "SPRINT"
            st = SessionType(session_type_str)
            session_label = results_formatter.format_session_label(st, is_sprint=is_sprint)

            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT test_display_name FROM driver_profiles WHERE CAST(discord_user_id AS INTEGER) = ?",
                    (driver_discord_id,),
                )
                dp_row = await cursor.fetchone()
            test_display_name: str | None = dp_row["test_display_name"] if dp_row else None

            penalty_type = record.get("penalty_type") if hasattr(record, "get") else getattr(record, "penalty_type", "")
            time_seconds = record.get("time_seconds") if hasattr(record, "get") else getattr(record, "time_seconds", None)
            description_text = record.get("description") if hasattr(record, "get") else getattr(record, "description", "")
            justification_text = record.get("justification") if hasattr(record, "get") else getattr(record, "justification", "")

            penalty_description = describe_penalty(penalty_type, time_seconds)

            team_name = await image_verdict_post.team_name_for_entry(
                bot,
                getattr(target_channel, "guild", None),
                server_id=server_id,
                division_id=result_ctx["division_id"],
                role_id=record.get("team_role_id")
                if hasattr(record, "get")
                else getattr(record, "team_role_id", None),
            )

            await head_the_batch()

            await _send_verdict(
                bot,
                target_channel,
                server_id=server_id,
                db_path=db_path,
                round_id=result_ctx["round_id"],
                kind=KIND,
                season_number=season_number,
                division_name=division_name,
                round_number=round_number,
                session_label=session_label,
                driver_discord_id=driver_discord_id,
                driver_display_name=test_display_name,
                driver_name=_graphic_name(test_display_name, driver_discord_id),
                penalty_description=penalty_description,
                description_text=description_text or NOT_PROVIDED,
                justification_text=justification_text or NOT_PROVIDED,
                team_name=team_name,
            )

        except Exception:
            log.exception(
                "post_appeal_announcements: error posting announcement for record %r", record
            )


async def post_autosanction_announcement(
    bot,
    db_path: str,
    round_id: int,
    driver_discord_id: int,
    driver_display_name: str | None,
    sanction_type: str,  # "AUTOSACK" or "AUTORESERVE"
    threshold: int,
    head=None,
) -> None:
    """Post a verdict-channel announcement for an autosack or autoreserve action.

    Skips silently if the verdicts channel is not configured or inaccessible.

    **An attendance sanction is a verdict** (decided 2026-09-09) and is headed like one.
    *head* is the run's shared banner poster: the sanctions of one round come from a loop in
    `enforce_attendance_sanctions`, which builds one and passes it to every call, so a round
    sanctioning three drivers raises one banner and not three. Where that run was itself
    reached from a penalty approval, the poster is that approval's and is already spent by
    the penalty verdicts, so the sanctions fall under their banner rather than a second.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT s.season_number, d.name AS division_name,
                   drc.penalty_channel_id, r.round_number
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cursor.fetchone()

    if row is None:
        log.warning("post_autosanction_announcement: could not load context for round %s", round_id)
        return

    penalty_channel_id_raw = row["penalty_channel_id"]
    if penalty_channel_id_raw is None:
        return  # no verdicts channel configured — skip silently

    target_channel = bot.get_channel(int(penalty_channel_id_raw))
    if target_channel is None:
        log.error(
            "post_autosanction_announcement: verdicts channel %s inaccessible — skipping",
            penalty_channel_id_raw,
        )
        return

    season_number: int | None = row["season_number"]
    division_name: str = row["division_name"]
    round_number: int = row["round_number"]

    driver_ref = f"<@{driver_discord_id}>"
    if driver_display_name:
        driver_ref += f" ({driver_display_name})"

    if sanction_type == "AUTOSACK":
        penalty_label = "Sacked"
        description_text = "Sacked due to accumulation of attendance points."
        justification_text = (
            f"{driver_ref} has reached the {threshold} attendance point limit in order to be "
            "removed from their full-time seat. Therefore, they have been removed from all "
            "driving seats effective immediately, and their current full-time seat will be "
            "offered to another driver."
        )
    else:  # AUTORESERVE
        penalty_label = "Moved to Reserve"
        description_text = "Moved to Reserve due to accumulation of attendance points."
        justification_text = (
            f"{driver_ref} has reached the {threshold} attendance point limit in order to be "
            "removed from their full-time seat. Therefore, they have been demoted to a reserve "
            "driver effective immediately, and their current full-time seat will be offered to "
            "another driver."
        )

    try:
        #  After every check that could still make this a no-op, so a banner is never
        #  posted over a sanction that is not announced.
        if head is not None:
            await head()
        else:
            await banner_for_round(bot, db_path, round_id)()

        await _send_verdict(
            bot,
            target_channel,
            server_id=getattr(getattr(target_channel, "guild", None), "id", 0),
            db_path=db_path,
            round_id=round_id,
            kind=VerdictKind.ATTENDANCE_SANCTION,
            season_number=season_number,
            division_name=division_name,
            round_number=round_number,
            session_label=None,
            driver_discord_id=driver_discord_id,
            driver_display_name=driver_display_name,
            driver_name=_graphic_name(driver_display_name, driver_discord_id),
            penalty_description=penalty_label,
            description_text=description_text,
            justification_text=justification_text,
        )
    except Exception:
        log.exception(
            "post_autosanction_announcement: error posting announcement for driver %s",
            driver_discord_id,
        )
