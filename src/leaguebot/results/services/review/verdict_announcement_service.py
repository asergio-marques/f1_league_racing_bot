"""verdict_announcement_service.py — Post penalty and appeal announcements.

One Discord message per penalty or appeal correction, posted to the division's
configured verdicts (penalty) channel after the respective review is approved.
"""
from __future__ import annotations

import json as _json
import logging
import sqlite3
from datetime import datetime
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import aiosqlite
import discord

from leaguebot.framework.utils.in_a_job import posts_in_a_job
from leaguebot.framework.services.posting.notice_handler import send_notice
from leaguebot.framework.models.change import StepFailedOnDiscord
from leaguebot.core.services.modules.module_service import SwitchNotSignedUp
from leaguebot.framework.db.database import get_connection
from leaguebot.core.services.drivers.driver_service import (
    current_account_map_for_division,
    driver_test_names_on,
)
from leaguebot.core.services.rounds import track_service
from leaguebot.core.services.seasons import season_service
from leaguebot.results.models.points_config import SessionType
from leaguebot.image.models.verdict_kind import VerdictKind
from leaguebot.results.services.publishing.results_post_service import (
    fault_of,
    picture_fallback_line,
)
from leaguebot.results.utils import results_formatter
from leaguebot.core.utils.input_validator import (
    is_disqualification,
    is_no_further_action,
    parse_penalty_seconds,
)
from leaguebot.core.services.drivers.driver_names import read_driver_names, resolve_driver_name
from leaguebot.framework.utils.hooks import AwaitedHook
from leaguebot.framework.utils.league_bot import LeagueBot

if TYPE_CHECKING:
    from leaguebot.image.services.render.image_drawing import ImageDrawing

log = logging.getLogger(__name__)


async def team_name_for_entry(db_path: str, *, team_id: int | None) -> str | None:
    """The name of the team whose car the driver drove, which the verdict's picture draws.

    The session records the division's **team** an entry drove for (#375), so for a reserve
    standing in for another driver this is the team whose car they drove and never the reserve
    team, the team being the one the *result* records. The badge is found by its shorthand, from
    `team_key_for_entry` (#381). Read from core's team readers, as the results picture's teams are.

    A stated exception (architecture.md, "A catch-all error handler"): a name that cannot be read
    answers None, and the verdict is drawn without the team, because an optional field is not
    worth a failed picture. The full error goes to the host's log. It reads no switch.
    """
    if team_id is None:
        return None
    try:
        from leaguebot.core.services.drivers.team_service import team_names_for_instances

        names = await team_names_for_instances(db_path, [int(team_id)])
    except Exception as exc:  # noqa: BLE001 — an optional field is not worth a failed render
        log.warning("verdicts: team name unreadable for team %s: %s", team_id, exc, exc_info=True)
        return None
    return names.get(int(team_id))


async def team_key_for_entry(db_path: str, *, team_id: int | None) -> str | None:
    """The shorthand of the team whose car the driver drove, which its badge is found by.

    A stated exception (architecture.md, "A catch-all error handler"): a shorthand that cannot be
    read answers None, and the verdict is drawn without the team's badge, because an optional
    field is not worth a failed picture. The full error goes to the host's log. It reads no
    switch.
    """
    if team_id is None:
        return None
    try:
        from leaguebot.core.services.drivers.team_service import team_artwork_keys_for_instances

        keys = await team_artwork_keys_for_instances(db_path, [int(team_id)])
    except Exception as exc:  # noqa: BLE001 — an optional field is not worth a failed render
        log.warning("verdicts: team shorthand unreadable for team %s: %s", team_id, exc, exc_info=True)
        return None
    return keys.get(int(team_id))

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
    db_path: str,
    driver_names_read: AwaitedHook,
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

    *fallback_display_name* stands in where the read of the recorded names fails (a database
    error): a name is not worth a lost announcement, so an unreadable one leaves the behaviour
    exactly as it was before. Any other fault is a defect and is raised.
    """
    try:
        names = await read_driver_names(db_path, driver_names_read, guild, [int(discord_user_id)])
    except sqlite3.Error as exc:
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
    stroke — Constitution XIV.7's one rendering, two presentations. The graphic is handed the
    wording by the module that composes it, and a preview takes it through the preview sources.
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
        facts = await season_service.round_facts_on(db, round_id)
        if facts is None:
            return {}
        cursor = await db.execute(
            "SELECT penalty_channel_id FROM division_results_config WHERE division_id = ?",
            (facts.division_id,),
        )
        config = await cursor.fetchone()
        track = (
            await track_service.get_track_by_name(db, facts.track_name)
            if facts.track_name is not None
            else None
        )
    return {
        "season_number": facts.season_number,
        "division_name": facts.division_name,
        "division_tier": facts.division_tier,
        "penalty_channel_id": config["penalty_channel_id"] if config is not None else None,
        "round_number": facts.round_number,
        "race_name": track["gp_name"] if track is not None else None,
        "country_name": track["country"] if track is not None else None,
    }


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
                SELECT sr.session_type, sr.round_id
                FROM race_session_results rsr
                JOIN session_results sr ON sr.id = rsr.session_result_id
                WHERE rsr.id = ?
                """,
                (race_result_id,),
            )
        elif qual_result_id is not None:
            cursor = await db.execute(
                """
                SELECT sr.session_type, sr.round_id
                FROM qualifying_session_results qsr
                JOIN session_results sr ON sr.id = qsr.session_result_id
                WHERE qsr.id = ?
                """,
                (qual_result_id,),
            )
        else:
            return {}
        row = await cursor.fetchone()
        if row is None:
            return {}
        facts = await season_service.round_facts_on(db, row["round_id"])
    if facts is None:
        return {}
    return {
        "round_number": facts.round_number,
        "session_type": row["session_type"],
        "format": facts.format.value,
        "round_id": facts.round_id,
        "division_id": facts.division_id,
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


_SANCTION_PUT_RIGHT = (
    "fix the verdict template; no command posts a sanction's card again, and the text card stands"
)
_VERDICT_PUT_RIGHT = (
    "fix the verdict template, then, once the round is final, amend the round with "
    "`/results rounds amend` to announce it again"
)


class VerdictRefused(Exception):
    """Discord refused the post of a verdict, as a picture or, where the picture could not be
    drawn, as the text that stands in its place, *cause* being its refusal. The picture is drawn
    on the first try alone, so *lines*, what it said when drawn, and *fault*, why it was not
    posted where it was not drawn, are kept with the stop (`FALLBACK_ATTRIBUTE`) for the try that
    posts the text to hand back."""

    def __init__(
        self, cause: discord.HTTPException, lines: tuple[str, ...], fault: str | None = None
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.lines = lines
        self.fault = fault


#: What a stop raised for a refused verdict carries the lines of its picture under: a callable
#: from the job's member and change to the lines (`_fallback_lines`), for the job to compose.
FALLBACK_ATTRIBUTE = "verdict_fallback"


def _refused_stop(
    refused: VerdictRefused, what: str, division_name: str, round_number, put_right: str
) -> StepFailedOnDiscord:
    """The stop for a refused verdict (*what*, "the verdict" or "the sanction's announcement"),
    with its picture's lines kept on it for the job to hand back once the text posts."""
    stop = StepFailedOnDiscord(
        f"{what} could not be posted: {refused.cause}"
    ).carrying(refused.lines)
    setattr(stop, FALLBACK_ATTRIBUTE, _fallback_lines(
        SentVerdict(None, refused.fault, refused.lines), division_name, round_number, put_right,
    ))
    return stop


class SentVerdict(NamedTuple):
    """What :func:`_send_verdict` sent: the *message*, the *fault* that kept the verdict's
    picture from posting where it went out as text in its place (``None`` otherwise), and the
    *lines* its picture was drawn with, the render's notices and the fault of a picture drawn
    not at all, for the job to write to the log."""

    message: "object | None"
    fault: str | None = None
    lines: tuple[str, ...] = ()


def _fallback_lines(
    sent: SentVerdict, division_name: str, round_number: int, put_right: str,
) -> "Callable[[str, str], tuple[str, ...]] | None":
    """The lines a job writes to the log for a verdict as it went out: what its picture was
    drawn with, then, where it could not be drawn, the line saying so ("the verdict picture could
    not be drawn"), as the job's own member and change are given: ``None`` where there is nothing
    to say (image specification, :1401)."""
    fault = sent.fault
    if fault is None and not sent.lines:
        return None

    def lines(named: str, what: str) -> tuple[str, ...]:
        if fault is None:
            return sent.lines
        return sent.lines + (picture_fallback_line(
            named, what, picture="verdict", division=division_name,
            round_number=round_number, fault=fault, put_right=put_right,
        ),)

    return lines


class AnnouncedVerdict(NamedTuple):
    """What :func:`announce_verdict` posted: the message's id and its channel's, which the job's
    record saves, and *fallback*, the line the job writes to the log where the verdict's picture
    went out as text (``None`` where it did not; image specification, :1401)."""

    message_id: int
    channel_id: int
    fallback: "Callable[[str, str], tuple[str, ...]] | None" = None


@posts_in_a_job
async def _send_verdict(
    drawing: ImageDrawing | None,
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
) -> SentVerdict:
    """Post one verdict: as a graphic where the toggle allows, as text otherwise.

    *as_text* posts the text where the toggle allows a graphic: a job's retry, the picture
    being attempted on the first try alone (Constitution XIV rule 8, #439).

    Returns the message it sent, so the caller can record which message carries this verdict
    (#189), with the fault of a picture that fell back to text, for the job to say so in the log
    (image specification, :1401). Without that the bot held nothing by which to find an announcement again, and an
    amendment left a decision standing that contradicted the classification it was applied to.

    The graphic **displaces the whole announcement but the mention** (Constitution XIV.7): its
    heading, driver line, sanction, description and justification all move onto the canvas and
    the message keeps the mention alone. A failed render falls back to the text this function
    would have sent anyway — no state is persisted either way, so there is nothing to reconcile.

    Called only after the review has been finalised or the sanction enforced. A graphic is
    downstream of every state change it depicts and is never a precondition of one.
    """
    attachment = None
    fault: str | None = None
    lines: tuple[str, ...] = ()
    if not as_text and drawing is not None:
        try:
            attachment, lines = await drawing.draw_verdict(
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
        except SwitchNotSignedUp:
            raise
        except Exception as exc:  # noqa: BLE001 — a graphic never costs a league its announcement (XIV.4)
            # A fault the drawing does not hand back as a line, so it goes back with the message
            # for the job to say why the text stands.
            log.exception("verdict graphic failed for driver %s", driver_discord_id)
            fault = fault_of(exc)
            attachment = None

    if attachment is not None:
        try:
            try:
                message = await send_notice(
                    target_channel, f"<@{driver_discord_id}>", mentions=_VERDICT_MENTIONS,
                    file=attachment,
                )
            except discord.HTTPException as refused:
                raise VerdictRefused(refused, lines) from refused
            return SentVerdict(message, None, lines)
        finally:
            if drawing is not None:  # a picture is drawn only through it
                drawing.discard_attachment(attachment)

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
    try:
        message = await send_notice(target_channel, content, mentions=_VERDICT_MENTIONS)
    except discord.HTTPException as refused:
        raise VerdictRefused(refused, lines, fault) from refused
    return SentVerdict(message, fault, lines)


async def announce_sanction(
    bot: LeagueBot,
    driver_names_read: AwaitedHook,
    db_path: str,
    round_id: int,
    driver_discord_id: int,
    driver_display_name: str | None,
    penalty_label: str,
    description_text: str,
    justification_text: str,
    *,
    drawing: ImageDrawing | None,
    as_text: bool = False,
) -> "Callable[[str, str], tuple[str, ...]] | None":
    """Post a sanction's card in the round's verdicts channel, raising where it cannot.

    The card's three texts are handed in: the sanction is attendance's, and so is its wording,
    which attendance's implementation of the hook composes and hands to this.

    What a queued sanction announcement is (#439): it raises where it cannot post, for the queue
    to stop on and try again. The card goes beneath the heading the round's verdicts already
    stand under, which the job's own record notes as heading a sanction card in the save that
    marks the card posted (`review_verdicts`). A round whose verdicts posted no heading (no
    penalty was applied) is headed by a job of its own, planned ahead of the first card; this
    posts no heading, so a card whose heading was discarded stands beneath none. *as_text*
    leaves the picture out of the card.

    Gives the lines the job writes to the log, as :func:`announce_verdict` does: what the card's
    picture was drawn with, and where it went out as text the line saying why (``None`` where
    there is nothing to say; image specification, :1401). *drawing* is the image module's.

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

    try:
        sent = await _send_verdict(
            drawing,
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
                db_path,
                driver_names_read,
                getattr(channel, "guild", None),
                driver_discord_id,
                fallback_display_name=driver_display_name,
            ),
            penalty_description=penalty_label,
            description_text=description_text,
            justification_text=justification_text,
            as_text=as_text,
        )
    except VerdictRefused as refused:
        raise _refused_stop(
            refused, "the sanction's announcement", ctx["division_name"], ctx["round_number"],
            _SANCTION_PUT_RIGHT,
        ) from refused.cause
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(f"the sanction's announcement could not be posted: {exc}") from exc
    return _fallback_lines(
        sent, ctx["division_name"], ctx["round_number"],
        _SANCTION_PUT_RIGHT,
    )


async def announce_verdict(
    bot: LeagueBot,
    driver_names_read: AwaitedHook,
    db_path: str,
    round_id: int,
    table: str,
    record: dict,
    *,
    drawing: ImageDrawing | None,
    as_text: bool = False,
) -> AnnouncedVerdict:
    """Announce one verdict in the round's verdicts channel, raising where it cannot.

    What a queued `announce_verdict` job is (#439): *table* is ``penalty_records`` or
    ``appeal_records`` and *record* the row's data as the approval's save wrote it (its id, the
    result it was applied to, the driver, the penalty, the description and the justification).
    This raises where it cannot announce, for the queue to stop on and try again: a division with no verdicts
    channel set, a verdicts channel not in the server and a result that cannot be read each
    fail it, the results specification saying such a division is "reported, not skipped". The
    heading is the round's `announce_heading` job and not this one's. *as_text* leaves the
    picture out, as a retry does (Constitution XIV, rule 8).

    Gives the message's id and its channel's, which the job's record saves with the verdict, and
    the lines the job writes to the log, given the member and the change it ran for: what the
    picture was drawn with and, where it fell back to text, why. *drawing* is the image module's.
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
        driver_discord_id = current_of.get(
            int(record["driver_user_id"]), int(record["driver_user_id"])
        )
        test_names = await driver_test_names_on(db, [driver_discord_id])
    test_display_name: str | None = test_names.get(driver_discord_id)
    is_sprint = str(result_ctx["format"]).upper() == "SPRINT"
    session_label = results_formatter.format_session_label(
        SessionType(result_ctx["session_type"]), is_sprint=is_sprint
    )
    guild = getattr(channel, "guild", None)
    try:
        sent = await _send_verdict(
            drawing,
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
                db_path, driver_names_read, guild, driver_discord_id,
                fallback_display_name=test_display_name
            ),
            penalty_description=describe_penalty(
                record.get("penalty_type"), record.get("time_seconds")
            ),
            description_text=record.get("description") or NOT_PROVIDED,
            justification_text=record.get("justification") or NOT_PROVIDED,
            team_name=await team_name_for_entry(db_path, team_id=record.get("team_instance_id")),
            team_key=await team_key_for_entry(db_path, team_id=record.get("team_instance_id")),
            as_text=as_text,
        )
    except VerdictRefused as refused:
        raise _refused_stop(
            refused, "the verdict", ctx["division_name"], result_ctx["round_number"],
            _VERDICT_PUT_RIGHT,
        ) from refused.cause
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(f"the verdict could not be posted: {exc}") from exc
    if sent.message is None:
        raise StepFailedOnDiscord("the verdict could not be posted")
    fallback = _fallback_lines(
        sent, ctx["division_name"], result_ctx["round_number"],
        _VERDICT_PUT_RIGHT,
    )
    return AnnouncedVerdict(int(getattr(sent.message, "id")), int(channel.id), fallback)


def _parse_chunk_ids(raw):
    """The stored chunk list of an announcement, via the one parser that reads them."""
    from leaguebot.results.services.publishing.results_post_service import _parse_ids

    return _parse_ids(raw)
