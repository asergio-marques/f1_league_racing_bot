"""verdict_announcement_service.py — Post penalty and appeal announcements.

One Discord message per penalty or appeal correction, posted to the division's
configured verdicts (penalty) channel after the respective review is approved.
"""
from __future__ import annotations

import json as _json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import discord
from types import SimpleNamespace

from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.db.database import get_connection
from leaguebot.core.services.driver_service import current_account_map_for_division
from leaguebot.results.models.points_config import SessionType
from leaguebot.image.services import image_verdict_post
from leaguebot.image.services.image_verdict_service import VerdictKind
from leaguebot.results.utils import results_formatter
from leaguebot.core.utils.input_validator import (
    is_disqualification,
    is_no_further_action,
    parse_penalty_seconds,
)
from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

#: What the announcement carries where the steward entered neither. The **message** italicises
#: it; the graphic draws the value the markup adorned and leaves the distinguishing to the
#: template's typography (Constitution XIV.16).
NOT_PROVIDED = "(not provided)"


def _for_message(value: str) -> str:
    """Apply the channel emphasis the placeholder carries in the textual announcement."""
    return f"*{NOT_PROVIDED}*" if value == NOT_PROVIDED else value


#: Who a verdict may notify: the people it mentions, and never a group (#204).
#:
#: The form refuses a role mention, ``@everyone`` or ``@here`` in a steward's text
#: (``leaguebot.core.utils.input_validator.STEWARD_TEXT``), but the form is not the only way text reaches
#: this channel: an amendment reposts every verdict a round carries from what was stored
#: (#345). So the send withholds the notification as well. The text is still posted as written
#: — a role still reads as its name — and a driver it mentions is still told.
_VERDICT_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


#: What a manager can do about a verdict that never reached the channel (#237).
#:
#: **A decided verdict cannot be announced again by the bot**, and the hint must say so. No
#: command re-announces one, and #189 records the reason one could not be built: the message
#: id of an announcement is never stored, so the bot holds nothing by which to find, edit or
#: replace one. The manager repairs the channel and posts the decision themselves.
#:
#: **Attendance sanctions are not an exception to this**, though it is tempting to write that
#: they are because `/attendance sync` does re-run the sanctions. It will not re-announce one
#: that already applied: `sack_driver` deletes the driver's `driver_season_assignments` row
#: and `move_driver` puts them in the Reserve team, so on a second run they are no longer a
#: candidate and are passed over without a word. ``attendance_service._failure_reason`` says
#: this in as many words, and ``sync_attendance``'s docstring repeats it. A hint promising
#: the sync would announce it would hand the manager the very false confidence this issue
#: exists to prevent — they would run it, read `Success`, and believe the driver was told.
_VERDICT_NO_RETRY = (
    "Repair the cause, then post the decision in the verdicts channel yourself — the bot "
    "cannot announce a verdict a second time."
)


def verdict_repair_hint() -> str:
    """The line telling a manager how to finish a verdict that was not announced (#237)."""
    return _VERDICT_NO_RETRY


def _round_label(state) -> str:
    """The round as a league knows it, not as the database numbers it.

    ``round_id`` is a primary key; a manager reading "Round 21" would go looking for round
    21 when the round is their third. The review state carries both the number and the
    division, so the one fault line that cannot read the round from the database can still
    say which round it was.
    """
    raw = getattr(state, "round_number", None)
    if raw is None:
        return "The round"
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return "The round"
    division = getattr(state, "division_name", None)
    return f"Round {number}" + (
        f" ({division})" if isinstance(division, str) and division else ""
    )


def _driver_label(driver_discord_id) -> str:
    """The driver a verdict was owed to, as a mention where one can be formed.

    A mention rather than a raw id because these lines are read by a league manager in the
    log channel and in their own reply, where an id is not a person.
    """
    try:
        return f"<@{int(driver_discord_id)}>"
    except (TypeError, ValueError):
        return "an unidentified driver"


async def _graphic_name(
    bot: LeagueBot,
    guild,
    discord_user_id: int,
    *,
    fallback_display_name: str | None = None,
) -> str:
    """The name the graphic draws in place of a mention (XIV.16).

    Resolved by the module's own reader, **called rather than restated**, so one driver is one
    name wherever the module names them: the display name of their Discord account on the
    server, then the names the league recorded at signup, then the test display name of a test
    driver, and the user id only where a league holds no name at all.

    Resolving from the test display name alone — the one candidate this path used to pass —
    named every real driver by their raw user id, because a real driver has none and the chain
    fell straight through to its last resort (#141). A mock driver drew correctly, which is
    why it survived every pass made in test mode.

    *fallback_display_name* stands in where the read fails: a name is not worth a lost
    announcement, so an unreadable one leaves the behaviour exactly as it was before.
    """
    from leaguebot.image.services.image_lineup_service import resolve_driver_name

    try:
        from leaguebot.image.services.image_results_post import _driver_names

        names = await _driver_names(bot, guild, [int(discord_user_id)])
    except Exception as exc:  # noqa: BLE001 — a name is not worth a failed announcement
        log.warning("verdicts: driver name unreadable for %s: %s", discord_user_id, exc, exc_info=True)
    else:
        resolved = names.get(int(discord_user_id))
        if resolved and str(resolved).strip():
            return str(resolved).strip()

    return resolve_driver_name(
        discord_user_id=discord_user_id, display_name=fallback_display_name
    )

#: What a verdict of no further action reads, in the announcement and on the graphic alike
#: (decided 2026-09-24, #138).
NO_FURTHER_ACTION = "No further action"


def translate_penalty(penalty_str: str) -> str:
    """Convert a raw penalty magnitude to a human-readable description.

    A positive magnitude is time **added** to the driver's time, which is what a
    time penalty does; a negative one is time removed, as an appeal correction does.

    | Input       | Output                           |
    |-------------|----------------------------------|
    | ``+5s``     | ``5 seconds added``              |
    | ``5s``      | ``5 seconds added``              |
    | ``-3s``     | ``3 seconds removed``            |
    | ``DSQ``     | ``Disqualified``                 |
    | ``NFA``     | ``No further action``            |
    """
    if is_disqualification(penalty_str):
        return "Disqualified"
    if is_no_further_action(penalty_str):
        return NO_FURTHER_ACTION
    seconds = parse_penalty_seconds(penalty_str)
    if seconds is not None:
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
    # Before the fallback below, which reads a record with no seconds as a disqualification:
    # no further action carries none either, and a cleared driver would be published as
    # disqualified (#138).
    if penalty_type == "NFA":
        return NO_FURTHER_ACTION
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


def _banner_once(bot: LeagueBot, channel, ctx: dict):
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

    async def post():
        """Returns the message the banner was posted as, or None."""
        nonlocal posted
        if posted:
            return None
        posted = True  # set before the attempt: one try per batch, whatever it returns
        try:
            from leaguebot.image.services import image_verdict_banner_post

            drawing = image_verdict_banner_post.build_drawing(
                season_number=ctx.get("season_number"),
                division_name=ctx["division_name"],
                division_tier=ctx.get("division_tier"),
                round_number=ctx.get("round_number"),
                race_name=ctx.get("race_name"),
                country_name=ctx.get("country_name"),
            )
            return await image_verdict_banner_post.try_post(bot, channel, drawing)
        except Exception:
            log.exception("verdict banner: could not head the batch")
        return None

    return post


class _RecordingPoster:
    """A banner poster that keeps the banner it put up, called with no arguments like any other.

    `message` is the banner once it is up, and None until then; a sanction card beneath it reads
    it, and may come from a later path of the same approval (#345). A class rather than an
    attribute set on a closure, which is the same thing to a caller but invisible to the type
    check (#228).
    """

    message: discord.Message | None = None

    def __init__(self, post: Callable[[_RecordingPoster], Awaitable[None]]) -> None:
        self._post = post

    async def __call__(self) -> None:
        await self._post(self)


def _banner_once_recorded(bot: LeagueBot, channel, ctx, db_path: str, round_id: int):
    """`_banner_once`, recording the message it posts (#345).

    What a poster falls back to when no shared banner was handed to it. The banner is recorded
    however it was posted, or an amendment would take a run's cards down and leave the header
    that was put up by this path standing over the empty space.
    """
    once = _banner_once(bot, channel, ctx)

    async def post(poster: _RecordingPoster) -> None:
        message = await once()
        if message is not None:
            poster.message = message
        await _record_banner(db_path, round_id, getattr(channel, "id", None), message)

    return _RecordingPoster(post)


def banner_for_round(bot: LeagueBot, db_path: str, round_id: int):
    """A shared banner poster for every verdict one approval will post.

    The same callable as :func:`_banner_once`, resolving the round's context and channel on
    the first call rather than being handed them. That is what lets a caller holding neither
    — `finalize_penalty_review`, which knows only the round — build one poster and pass it
    to the several paths that post verdicts beneath it.

    Resolving lazily costs nothing where no verdict follows: an approval applying no penalty
    and triggering no sanction never calls it, so the query is never made.
    """
    posted = False

    async def post(poster: _RecordingPoster) -> None:
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
            message = await _banner_once(bot, channel, ctx)()
            poster.message = message
            await _record_banner(db_path, round_id, channel_id_raw, message)
        except Exception:
            log.exception("verdict banner: could not head round %s", round_id)

    return _RecordingPoster(post)


async def _record_banner(db_path: str, round_id: int, channel_id, message) -> None:
    """Note which message heads a round's run of verdicts, so it can be taken down with them.

    A banner belongs to no verdict record — it is a message of its own above the cards — so
    without this an amendment removed a round's announcements and left the header standing over
    the empty space, then posted a fresh one below it, once per amendment (#345).
    """
    message_id = getattr(message, "id", None)
    if message_id is None:
        return
    try:
        async with get_connection(db_path) as db:
            await _record_banner_on(
                db, round_id, channel_id, message_id, now=datetime.now(timezone.utc)
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — the banner went out; only the record of it failed
        log.exception("could not record the banner of round %s", round_id)


async def _record_banner_on(
    db: aiosqlite.Connection, round_id: int, channel_id, message_id: int, *, now: datetime
) -> None:
    """Note the banner of a round's verdicts on *db*, committing nothing and swallowing nothing.

    What `_record_banner` writes, for a job's ``record``, which saves it with the job's done
    mark: a failure here is the job's failure, and the post is sent again, not left unrecorded.
    The time is *now*, from the queue's clock.
    """
    await db.execute(
        "INSERT INTO verdict_banner_messages "
        "(round_id, channel_id, message_id, posted_at) VALUES (?, ?, ?, ?)",
        (round_id, str(channel_id), str(message_id), now.isoformat()),
    )


async def _banners_of_on(db: aiosqlite.Connection, round_id: int) -> list[tuple[str, int]]:
    """Every banner recorded for *round_id*, as (channel id, message id), oldest first; on *db*."""
    cursor = await db.execute(
        "SELECT channel_id, message_id FROM verdict_banner_messages "
        "WHERE round_id = ? ORDER BY id",
        (round_id,),
    )
    rows = await cursor.fetchall()
    found: list[tuple[str, int]] = []
    for row in rows:
        try:
            found.append((row["channel_id"], int(row["message_id"])))
        except (TypeError, ValueError):
            continue
    return found


async def _banners_of(db_path: str, round_id: int) -> list[tuple[str, int]]:
    """Every banner recorded for *round_id*, as (channel id, message id), oldest first."""
    async with get_connection(db_path) as db:
        return await _banners_of_on(db, round_id)


async def _mark_banner_over_sanction(db_path: str, banner) -> None:
    """Note that *banner* heads an attendance sanction card (decided 2026-09-21).

    A sanction card is no verdict record: no replay re-announces it or takes it down. So a
    banner over one is kept by every replay, or the card would be left without its header.

    *banner* is the message the card's own poster put up, picture or words — spent earlier by
    the approval the card belongs to, or posted for it just now. Since #246 a header goes up
    whether or not the aspect is on, so the switch being off no longer leaves a card bare;
    only a post that failed does. Such a card marks nothing: the header that merely came
    before it in the channel heads another run, and marked it would never come down. Never
    raises: the card went out, and only this note of it failed.
    """
    banner_id = getattr(banner, "id", None)
    if banner_id is None:
        return
    try:
        async with get_connection(db_path) as db:
            await _mark_banner_over_sanction_on(db, banner_id)
            await db.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not note banner %s as heading a sanction card", banner_id)


async def _mark_banner_over_sanction_on(db: aiosqlite.Connection, banner_id: int) -> None:
    """Note that the banner *banner_id* heads a sanction card, on *db*; commits nothing."""
    await db.execute(
        "UPDATE verdict_banner_messages SET heads_sanctions = 1 WHERE message_id = ?",
        (str(banner_id),),
    )


async def _banners_heading_sanctions_on(
    db: aiosqlite.Connection, message_ids: list[int]
) -> set[int]:
    """Which of *message_ids* are banners with an attendance sanction card beneath them; on *db*."""
    if not message_ids:
        return set()
    placeholders = ", ".join("?" for _ in message_ids)
    cursor = await db.execute(
        f"SELECT message_id FROM verdict_banner_messages "  # noqa: S608
        f"WHERE heads_sanctions = 1 AND message_id IN ({placeholders})",
        [str(message_id) for message_id in message_ids],
    )
    return {int(row["message_id"]) for row in await cursor.fetchall()}


async def _forget_banners_on(db: aiosqlite.Connection, message_ids: list[int]) -> None:
    """Drop the records of banners that have been taken down, on *db*; commits nothing."""
    if not message_ids:
        return
    placeholders = ", ".join("?" for _ in message_ids)
    await db.execute(
        f"DELETE FROM verdict_banner_messages WHERE message_id IN ({placeholders})",  # noqa: S608
        [str(message_id) for message_id in message_ids],
    )


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


async def _record_announcement_on(
    db: aiosqlite.Connection, table: str, record_id: int, message_id: int, channel_id: int | None
) -> None:
    """Record which message carries a verdict on *db*; commits nothing, swallows nothing.

    Written for a job's ``record``, which saves it with the job's done mark, so that where a
    verdict went is never lost to a stop between the post and the mark (#189). *table* is ``penalty_records`` or ``appeal_records``, written as a literal.
    """
    await db.execute(
        f"UPDATE {table} SET announcement_message_id = ?, "  # noqa: S608 — literal table
        "announcement_message_ids = ?, announcement_channel_id = ? WHERE id = ?",
        (str(message_id), _json.dumps([message_id]),
         str(channel_id) if channel_id is not None else None, record_id),
    )


async def _send_verdict(
    bot: LeagueBot,
    target_channel,
    *,
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
    team_key: str | None = None,
    as_text: bool = False,
) -> "object | None":
    """Post one verdict: as a graphic where the toggle allows, as text otherwise.

    *as_text* posts the text where the toggle allows a graphic: a job's retry, the picture
    being attempted on the first try alone (Constitution XIV rule 8, #439).

    Returns the message it sent, so the caller can record which message carries this verdict
    (#189). Without that the bot held nothing by which to find an announcement again, and an
    amendment left a decision standing that contradicted the classification it was applied to.

    The graphic **displaces the whole announcement but the mention** (Constitution XIV.7): its
    heading, driver line, sanction, description and justification all move onto the canvas and
    the message keeps the mention alone. A failed render falls back to the text this function
    would have sent anyway — no state is persisted either way, so there is nothing to reconcile.

    Called only after the review has been finalised or the sanction enforced. A graphic is
    downstream of every state change it depicts and is never a precondition of one.
    """
    from leaguebot.image.services import image_verdict_post

    render = None
    if not as_text and await image_verdict_post.verdicts_enabled(bot):
        try:
            drawing = await image_verdict_post.build_drawing(
                bot,
                guild=getattr(target_channel, "guild", None),
                db_path=db_path,
                round_id=round_id,
                kind=kind,
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
                team_key=team_key,
            )
            render = await image_verdict_post.render_verdict(bot, drawing)
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
                bot, subject, render.notices
            )
        if render is not None and render.problem:
            await image_verdict_post.report(bot, subject, render.problem)

    if render is not None and render.png is not None:  # `render.draws`, followed by the check
        import discord as _discord

        # Named, rather than left to Discord to read the path's basename: the render
        # service already wrote a name saying which season, division and round this
        # verdict belongs to, and leaking the raw template key was never intended.
        attachment = _discord.File(str(render.png), filename=Path(render.png).name)
        try:
            return await target_channel.send(
                f"<@{driver_discord_id}>", file=attachment, allowed_mentions=_VERDICT_MENTIONS
            )
        finally:
            image_verdict_post.discard(render, attachment)

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
    return await target_channel.send(content, allowed_mentions=_VERDICT_MENTIONS)


def _sanction_texts(sanction_type: str, driver_ref: str, threshold: int) -> tuple[str, str, str]:
    """The label, description and justification of an attendance sanction's card."""
    if sanction_type == "AUTOSACK":
        return (
            "Sacked",
            "Sacked due to accumulation of attendance points.",
            f"{driver_ref} has reached the {threshold} attendance point limit in order to be "
            "removed from their full-time seat. Therefore, they have been removed from all "
            "driving seats effective immediately, and their current full-time seat will be "
            "offered to another driver.",
        )
    return (
        "Moved to Reserve",
        "Moved to Reserve due to accumulation of attendance points.",
        f"{driver_ref} has reached the {threshold} attendance point limit in order to be "
        "removed from their full-time seat. Therefore, they have been demoted to a reserve "
        "driver effective immediately, and their current full-time seat will be offered to "
        "another driver.",
    )


async def announce_sanction(
    bot: LeagueBot,
    db_path: str,
    round_id: int,
    driver_discord_id: int,
    driver_display_name: str | None,
    sanction_type: str,  # "AUTOSACK" or "AUTORESERVE"
    threshold: int,
    *,
    as_text: bool = False,
) -> None:
    """Post a sanction's card in the round's verdicts channel, raising where it cannot.

    What a queued sanction announcement is (#439): where `post_autosanction_announcement`
    returns what it could not announce, this raises, for the queue to stop on and try again,
    and the lines it would have returned are the failure's reason. The card goes beneath the
    heading the round's verdicts already stand under, **read back from its row** and not from
    a poster held in memory, so a stop and a restart between the verdicts and the sanctions
    post no second one. A round whose verdicts posted no heading (no penalty was applied) is
    headed here, as an attendance sanction has always headed itself. *as_text* leaves the
    picture out of the card.

    A division with no verdicts channel set is a failure, not a card skipped: a verdicts
    channel is one the season cannot be approved without.
    """
    ctx = await _get_announcement_context(db_path, round_id)
    if not ctx:
        raise StepFailedOnDiscord(f"round {round_id} could not be read")
    channel_id_raw = ctx.get("penalty_channel_id")
    if channel_id_raw is None:
        raise StepFailedOnDiscord(f"{ctx['division_name']} has no verdicts channel set")
    channel = bot.get_channel(int(channel_id_raw))
    if channel is None:
        raise StepFailedOnDiscord(
            f"{ctx['division_name']}'s verdicts channel (id {int(channel_id_raw)}) is not in "
            "the server"
        )

    banners = await _banners_of(db_path, round_id)
    if not banners:
        poster = _banner_once_recorded(bot, channel, ctx, db_path, round_id)
        await poster()
        banner_id = getattr(poster.message, "id", None)
    else:
        banner_id = banners[-1][1]

    driver_ref = f"<@{driver_discord_id}>"
    if driver_display_name:
        driver_ref += f" ({driver_display_name})"
    penalty_label, description_text, justification_text = _sanction_texts(
        sanction_type, driver_ref, threshold
    )
    try:
        card = await _send_verdict(
            bot,
            channel,
            db_path=db_path,
            round_id=round_id,
            kind=VerdictKind.ATTENDANCE_SANCTION,
            season_number=ctx["season_number"],
            division_name=ctx["division_name"],
            round_number=ctx["round_number"],
            session_label=None,
            driver_discord_id=driver_discord_id,
            driver_display_name=driver_display_name,
            driver_name=await _graphic_name(
                bot,
                getattr(channel, "guild", None),
                driver_discord_id,
                fallback_display_name=driver_display_name,
            ),
            penalty_description=penalty_label,
            description_text=description_text,
            justification_text=justification_text,
            as_text=as_text,
        )
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(f"the sanction's announcement could not be posted: {exc}") from exc
    if card is not None and banner_id is not None:
        await _mark_banner_over_sanction(db_path, SimpleNamespace(id=banner_id))


async def announce_verdict(
    bot: LeagueBot,
    db_path: str,
    round_id: int,
    table: str,
    record: dict,
    *,
    as_text: bool = False,
) -> tuple[int, int]:
    """Announce one verdict in the round's verdicts channel, raising where it cannot.

    What a queued `announce_verdict` job is (#439): *table* is ``penalty_records`` or
    ``appeal_records`` and *record* the row's data as the approval's save wrote it (its id, the
    result it was applied to, the driver, the penalty, the description and the justification).
    This raises where it cannot announce, for the queue to stop on and try again: a division with no verdicts
    channel set, a verdicts channel not in the server and a result that cannot be read each
    fail it, the results specification saying such a division is "reported, not skipped". The
    heading is the round's `announce_heading` job and not this one's. *as_text* leaves the
    picture out, as a retry does (Constitution XIV, rule 8).

    Gives the message's id and its channel's, which the job's record saves with the verdict.
    """
    ctx = await _get_announcement_context(db_path, round_id)
    if not ctx:
        raise StepFailedOnDiscord(f"round {round_id} could not be read")
    channel_id_raw = ctx.get("penalty_channel_id")
    if channel_id_raw is None:
        raise StepFailedOnDiscord(f"{ctx['division_name']} has no verdicts channel set")
    channel = bot.get_channel(int(channel_id_raw))
    if channel is None:
        raise StepFailedOnDiscord(
            f"{ctx['division_name']}'s verdicts channel (id {int(channel_id_raw)}) is not in "
            "the server"
        )
    result_ctx = await _get_result_context(
        db_path, record.get("race_result_id"), record.get("qual_result_id")
    )
    if not result_ctx:
        raise LookupError(
            f"the result of {_driver_label(record.get('driver_user_id'))}'s verdict could not be read"
        )
    # The verdict names the driver by the account they use now, whichever the result was
    # recorded under (issue #243).
    async with get_connection(db_path) as db:
        current_of = await current_account_map_for_division(db, result_ctx["division_id"])
        cursor = await db.execute(
            "SELECT test_display_name FROM driver_profiles "
            "WHERE CAST(discord_user_id AS INTEGER) = ?",
            (current_of.get(int(record["driver_user_id"]), int(record["driver_user_id"])),),
        )
        profile = await cursor.fetchone()
    driver_discord_id = current_of.get(int(record["driver_user_id"]), int(record["driver_user_id"]))
    test_display_name: str | None = profile["test_display_name"] if profile else None
    is_sprint = str(result_ctx["format"]).upper() == "SPRINT"
    session_label = results_formatter.format_session_label(
        SessionType(result_ctx["session_type"]), is_sprint=is_sprint
    )
    guild = getattr(channel, "guild", None)
    try:
        message = await _send_verdict(
            bot,
            channel,
            db_path=db_path,
            round_id=result_ctx["round_id"],
            kind=VerdictKind.PENALTY if table == "penalty_records" else VerdictKind.APPEAL,
            season_number=ctx["season_number"],
            division_name=ctx["division_name"],
            round_number=result_ctx["round_number"],
            session_label=session_label,
            driver_discord_id=driver_discord_id,
            driver_display_name=test_display_name,
            driver_name=await _graphic_name(
                bot, guild, driver_discord_id, fallback_display_name=test_display_name
            ),
            penalty_description=describe_penalty(
                record.get("penalty_type"), record.get("time_seconds")
            ),
            description_text=record.get("description") or NOT_PROVIDED,
            justification_text=record.get("justification") or NOT_PROVIDED,
            team_name=await image_verdict_post.team_name_for_entry(
                bot, guild, division_id=result_ctx["division_id"],
                team_id=record.get("team_instance_id"),
            ),
            team_key=await image_verdict_post.team_key_for_entry(
                bot, team_id=record.get("team_instance_id")
            ),
            as_text=as_text,
        )
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(f"the verdict could not be posted: {exc}") from exc
    if message is None:
        raise StepFailedOnDiscord("the verdict could not be posted")
    return int(getattr(message, "id")), int(channel.id)


async def post_autosanction_announcement(
    bot: LeagueBot,
    db_path: str,
    round_id: int,
    driver_discord_id: int,
    driver_display_name: str | None,
    sanction_type: str,  # "AUTOSACK" or "AUTORESERVE"
    threshold: int,
    head=None,
) -> list[str]:
    """Post a verdict-channel announcement for an autosack or autoreserve action.

    Returns what it could not announce, as lines a league can read (#237). Its caller adds
    them to ``SanctionOutcome.posting_faults``, which is where "the sanction took effect but
    its announcement could not be posted" already belonged — the `except` around the call
    site only ever caught what *raised*, and every exit below returned quietly instead, so
    the report #239 built has been promising a line it could not produce.

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

    sanction_label = "autosack" if sanction_type == "AUTOSACK" else "autoreserve"

    if row is None:
        log.warning("post_autosanction_announcement: could not load context for round %s", round_id)
        return [
            f"the {sanction_label} of {_driver_label(driver_discord_id)} was applied, but "
            f"round {round_id} could not be read, so it was not announced"
        ]

    division_name_for_fault: str = row["division_name"] or "the division"

    penalty_channel_id_raw = row["penalty_channel_id"]
    if penalty_channel_id_raw is None:
        log.error(
            "post_autosanction_announcement: no verdicts channel for round %s", round_id
        )
        return [
            f"the {sanction_label} of {_driver_label(driver_discord_id)} was applied, but "
            f"**{division_name_for_fault}** has no verdicts channel set, so it was not announced"
        ]

    target_channel = bot.get_channel(int(penalty_channel_id_raw))
    if target_channel is None:
        log.error(
            "post_autosanction_announcement: verdicts channel %s inaccessible — skipping",
            penalty_channel_id_raw,
        )
        return [
            f"the {sanction_label} of {_driver_label(driver_discord_id)} was applied, but "
            f"**{division_name_for_fault}**'s verdicts channel (id "
            f"{int(penalty_channel_id_raw)}) is not in the server, so it was not announced"
        ]

    season_number: int | None = row["season_number"]
    division_name: str = row["division_name"]
    round_number: int = row["round_number"]

    driver_ref = f"<@{driver_discord_id}>"
    if driver_display_name:
        driver_ref += f" ({driver_display_name})"

    penalty_label, description_text, justification_text = _sanction_texts(
        sanction_type, driver_ref, threshold
    )

    try:
        #  After every check that could still make this a no-op, so a banner is never
        #  posted over a sanction that is not announced.
        poster = head if head is not None else banner_for_round(bot, db_path, round_id)
        await poster()

        card = await _send_verdict(
            bot,
            target_channel,
            db_path=db_path,
            round_id=round_id,
            kind=VerdictKind.ATTENDANCE_SANCTION,
            season_number=season_number,
            division_name=division_name,
            round_number=round_number,
            session_label=None,
            driver_discord_id=driver_discord_id,
            driver_display_name=driver_display_name,
            driver_name=await _graphic_name(
                bot,
                getattr(target_channel, "guild", None),
                driver_discord_id,
                fallback_display_name=driver_display_name,
            ),
            penalty_description=penalty_label,
            description_text=description_text,
            justification_text=justification_text,
        )
        if card is not None:
            await _mark_banner_over_sanction(db_path, getattr(poster, "message", None))
        return []
    except Exception as exc:  # noqa: BLE001 — recorded with the sanction's outcome
        log.exception(
            "post_autosanction_announcement: error posting announcement for driver %s",
            driver_discord_id,
        )
        return [
            f"the {sanction_label} of {_driver_label(driver_discord_id)} was applied, but "
            f"its announcement failed: {exc}"
        ]


def _parse_chunk_ids(raw):
    """The stored chunk list of an announcement, via the one parser that reads them."""
    from leaguebot.results.services.results_post_service import _parse_ids

    return _parse_ids(raw)


async def _rounds_from(db_path: str, division_id: int, from_round_id: int) -> list[dict]:
    """The division's rounds from *from_round_id* forward, in round order.

    Inclusive of the round named. Cancelled rounds are left out: they have no classification
    for a verdict to describe, and their messages are not part of the sequence a league reads.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.id AS round_id, r.round_number
            FROM rounds r
            WHERE r.division_id = ?
              AND r.status != 'CANCELLED'
              AND r.round_number >= (SELECT round_number FROM rounds WHERE id = ?)
            ORDER BY r.round_number
            """,
            (division_id, from_round_id),
        )
        return [dict(row) for row in await cursor.fetchall()]

