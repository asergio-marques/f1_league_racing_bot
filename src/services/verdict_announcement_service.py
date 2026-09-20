"""verdict_announcement_service.py — Post penalty and appeal announcements.

One Discord message per penalty or appeal correction, posted to the division's
configured verdicts (penalty) channel after the respective review is approved.
"""
from __future__ import annotations

import json as _json
import logging
import re
from pathlib import Path

import discord

from db.database import get_connection
from services.channel_registry_service import missing_channel_fault
from services.driver_service import current_account_map_for_division
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


def _n_verdicts(count: int) -> str:
    """``one verdict`` or ``N verdicts``, so a fault line reads as English either way."""
    return "one verdict" if count == 1 else f"{count} verdicts"


def _round_label(state) -> str:
    """The round as a league knows it, not as the database numbers it.

    ``round_id`` is a primary key; a manager reading "Round 21" would go looking for round
    21 when the round is their third. The review state carries both the number and the
    division, so the one fault line that cannot read the round from the database can still
    say which round it was.
    """
    try:
        number = int(getattr(state, "round_number", None))
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
    bot,
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
    from services.image_lineup_service import resolve_driver_name

    try:
        from services.image_results_post import _driver_names

        names = await _driver_names(bot, guild, [int(discord_user_id)])
    except Exception as exc:  # noqa: BLE001 — a name is not worth a failed announcement
        log.warning("verdicts: driver name unreadable for %s: %s", discord_user_id, exc)
    else:
        resolved = names.get(int(discord_user_id))
        if resolved and str(resolved).strip():
            return str(resolved).strip()

    return resolve_driver_name(
        discord_user_id=discord_user_id, display_name=fallback_display_name
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


def _banner_once(bot, channel, ctx: dict):
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
            await image_verdict_banner_post.try_post(bot, channel, drawing)
        except Exception:
            log.exception("verdict banner: could not head the batch")

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
            await _banner_once(bot, channel, ctx)()
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


def _record_id(record) -> int | None:
    """The row id of a verdict record, whether it arrived as a dict or a dataclass.

    Every other field in these two functions is read through the same pair of accessors; the
    id was simply never needed until there was something to write back against it.
    """
    value = record.get("id") if hasattr(record, "get") else getattr(record, "id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _record_announcement(
    db_path: str, table: str, record_id: int | None, message, channel_id: int | None
) -> None:
    """Record which message carries a verdict, so it can be found again (#189).

    ``penalty_records`` and ``appeal_records`` stored the channel an announcement went to and
    nothing more, so the bot could not edit, delete or replace one by any route. An amendment
    therefore rescored the classification a verdict was applied to and left the verdict itself
    standing, contradicting it, with no command to put it right.

    Both the anchor id and the chunk list are written. A verdict is one message today, but the
    column pair is the one every other posting uses and a batch that grows past Discord's limit
    would otherwise reintroduce the guesswork `_delete_posting` exists to remove (#345).

    A failure here is logged, never raised: the verdict *was* announced, and losing the record
    of where is a smaller harm than turning a delivered announcement into a reported fault.
    """
    if record_id is None or message is None:
        return
    message_id = getattr(message, "id", None)
    if message_id is None:
        return
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                f"UPDATE {table} SET announcement_message_id = ?, "  # noqa: S608 — literal table
                "announcement_message_ids = ?, announcement_channel_id = ? WHERE id = ?",
                (str(message_id), _json.dumps([message_id]), 
                 str(channel_id) if channel_id is not None else None, record_id),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — the announcement went out; only the record of it failed
        log.exception("could not record the announcement of %s row %s", table, record_id)


async def _send_verdict(
    bot,
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
) -> "object | None":
    """Post one verdict: as a graphic where the toggle allows, as text otherwise.

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
    from services import image_verdict_post

    render = None
    if await image_verdict_post.verdicts_enabled(bot):
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

    if render is not None and render.draws:
        import discord as _discord

        # Named, rather than left to Discord to read the path's basename: the render
        # service already wrote a name saying which season, division and round this
        # verdict belongs to, and leaking the raw template key was never intended.
        attachment = _discord.File(str(render.png), filename=Path(render.png).name)
        try:
            return await target_channel.send(f"<@{driver_discord_id}>", file=attachment)
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
    return await target_channel.send(content)


async def post_penalty_announcements(
    bot,
    state,  # PenaltyReviewState
    applied_penalties: list,
    *,
    head=None,
) -> list[str]:
    """Post one announcement per applied penalty to the verdicts channel.

    Returns the verdicts it could not announce, as lines a league can read, and an empty
    list where every one of them went out. Does not block finalization on any error.

    **A verdict that is not announced is a fault, not a silence** (#237). The penalty is
    applied either way: the classification changes, the driver loses the places, and the
    only thing that told them why was this announcement. Every exit below used to return
    quietly, so the league had no indication the explanation was missing.

    **An unconfigured verdicts channel is reported here**, which is the opposite of the rule
    ``repost_round_results`` keeps for results and standings. Those are optional; a verdicts
    channel is not. The results specification makes it one of the three a division must have
    before its season's placements can be confirmed, so a round reaching a penalty verdict
    without one is an anomaly rather than a league that chose not to configure it.

    *head* is the approval's shared banner poster where the caller built one, so that the
    attendance sanctions posted later in the same approval fall under this run's banner
    rather than raising a second. Absent one, this run heads itself.
    """
    if not applied_penalties:
        return []

    db_path: str = state.db_path
    round_id: int = state.round_id
    faults: list[str] = []

    ctx = await _get_announcement_context(db_path, round_id)
    if not ctx:
        log.warning("post_penalty_announcements: could not load context for round %s", round_id)
        return [
            f"{_round_label(state)} could not be read from the database, so "
            f"{_n_verdicts(len(applied_penalties))} could not be announced."
        ]

    division_name = ctx["division_name"]

    penalty_channel_id_raw = ctx.get("penalty_channel_id")
    if penalty_channel_id_raw is None:
        log.error(
            "post_penalty_announcements: no verdicts channel for round %s", round_id
        )
        return [
            f"**{division_name}** — no verdicts channel is set, so "
            f"{_n_verdicts(len(applied_penalties))} could not be announced."
        ]

    target_channel = bot.get_channel(int(penalty_channel_id_raw))
    if target_channel is None:
        log.error(
            "post_penalty_announcements: verdicts channel %s inaccessible for round %s — skipping",
            penalty_channel_id_raw,
            round_id,
        )
        return [
            missing_channel_fault(division_name, "verdicts", int(penalty_channel_id_raw))
            + f" {_n_verdicts(len(applied_penalties))} could not be announced."
        ]

    season_number = ctx["season_number"]
    KIND = VerdictKind.PENALTY
    head_the_batch = head or _banner_once(bot, target_channel, ctx)

    for record in applied_penalties:
        # Named before the try so a failure below can still say whose verdict it was, but
        # *read* inside it: a record that cannot even be asked for its driver must cost one
        # verdict, not abandon every one still to come.
        driver_discord_id = None
        try:
            driver_discord_id = (
                record.get("driver_user_id") if hasattr(record, "get")
                else getattr(record, "driver_user_id", 0)
            )
            race_result_id = record.get("race_result_id") if hasattr(record, "get") else getattr(record, "race_result_id", None)
            qual_result_id = record.get("qual_result_id") if hasattr(record, "get") else getattr(record, "qual_result_id", None)

            result_ctx = await _get_result_context(db_path, race_result_id, qual_result_id)
            if not result_ctx:
                log.warning("post_penalty_announcements: no result context for record %r", record)
                faults.append(
                    f"**{division_name}** — the penalty verdict for {_driver_label(driver_discord_id)} "
                    f"could not be built: its result could not be read."
                )
                continue

            # The verdict names the driver by the account they use now, whichever the
            # result was recorded under (issue #243).
            async with get_connection(db_path) as db:
                current_of = await current_account_map_for_division(
                    db, result_ctx["division_id"]
                )
            driver_discord_id = current_of.get(int(driver_discord_id), int(driver_discord_id))

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
                division_id=result_ctx["division_id"],
                role_id=record.get("team_role_id")
                if hasattr(record, "get")
                else getattr(record, "team_role_id", None),
            )

            await head_the_batch()

            _sent = await _send_verdict(
                bot,
                target_channel,
                db_path=db_path,
                round_id=result_ctx["round_id"],
                kind=KIND,
                season_number=season_number,
                division_name=division_name,
                round_number=round_number,
                session_label=session_label,
                driver_discord_id=driver_discord_id,
                driver_display_name=test_display_name,
                driver_name=await _graphic_name(
                    bot,
                    getattr(target_channel, "guild", None),
                    driver_discord_id,
                    fallback_display_name=test_display_name,
                ),
                penalty_description=penalty_description,
                description_text=description_text or NOT_PROVIDED,
                justification_text=justification_text or NOT_PROVIDED,
                team_name=team_name,
            )
            await _record_announcement(
                db_path, "penalty_records", _record_id(record), _sent,
                getattr(target_channel, "id", None),
            )

        except Exception as exc:  # noqa: BLE001 — recorded, and the next verdict taken
            log.exception(
                "post_penalty_announcements: error posting announcement for record %r", record
            )
            faults.append(
                f"**{division_name}** — the penalty verdict for "
                f"{_driver_label(driver_discord_id)} was not announced: {exc}"
            )

    return faults


async def post_appeal_announcements(
    bot,
    state,  # PenaltyReviewState
    applied_corrections: list,
    *,
    head=None,
) -> list[str]:
    """Post one announcement per applied appeal correction to the verdicts channel.

    Identical contract to :func:`post_penalty_announcements`, including the faults it
    returns and the reason an unconfigured verdicts channel is one of them (#237). An
    unannounced appeal verdict is the worse of the two to lose: it is the one that tells a
    driver a sanction against them was overturned.
    """
    if not applied_corrections:
        return []

    db_path: str = state.db_path
    round_id: int = state.round_id
    faults: list[str] = []

    ctx = await _get_announcement_context(db_path, round_id)
    if not ctx:
        log.warning("post_appeal_announcements: could not load context for round %s", round_id)
        return [
            f"{_round_label(state)} could not be read from the database, so "
            f"{_n_verdicts(len(applied_corrections))} could not be announced."
        ]

    division_name = ctx["division_name"]

    penalty_channel_id_raw = ctx.get("penalty_channel_id")
    if penalty_channel_id_raw is None:
        log.error("post_appeal_announcements: no verdicts channel for round %s", round_id)
        return [
            f"**{division_name}** — no verdicts channel is set, so "
            f"{_n_verdicts(len(applied_corrections))} could not be announced."
        ]

    target_channel = bot.get_channel(int(penalty_channel_id_raw))
    if target_channel is None:
        log.error(
            "post_appeal_announcements: verdicts channel %s inaccessible for round %s — skipping",
            penalty_channel_id_raw,
            round_id,
        )
        return [
            missing_channel_fault(division_name, "verdicts", int(penalty_channel_id_raw))
            + f" {_n_verdicts(len(applied_corrections))} could not be announced."
        ]

    season_number = ctx["season_number"]
    KIND = VerdictKind.APPEAL
    head_the_batch = head or _banner_once(bot, target_channel, ctx)

    for record in applied_corrections:
        # Named before the try so a failure below can still say whose verdict it was, but
        # *read* inside it: a record that cannot even be asked for its driver must cost one
        # verdict, not abandon every one still to come.
        driver_discord_id = None
        try:
            driver_discord_id = (
                record.get("driver_user_id") if hasattr(record, "get")
                else getattr(record, "driver_user_id", 0)
            )
            race_result_id = record.get("race_result_id") if hasattr(record, "get") else getattr(record, "race_result_id", None)
            qual_result_id = record.get("qual_result_id") if hasattr(record, "get") else getattr(record, "qual_result_id", None)

            result_ctx = await _get_result_context(db_path, race_result_id, qual_result_id)
            if not result_ctx:
                log.warning("post_appeal_announcements: no result context for record %r", record)
                faults.append(
                    f"**{division_name}** — the appeal verdict for {_driver_label(driver_discord_id)} "
                    f"could not be built: its result could not be read."
                )
                continue

            # The verdict names the driver by the account they use now, whichever the
            # result was recorded under (issue #243).
            async with get_connection(db_path) as db:
                current_of = await current_account_map_for_division(
                    db, result_ctx["division_id"]
                )
            driver_discord_id = current_of.get(int(driver_discord_id), int(driver_discord_id))

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
                division_id=result_ctx["division_id"],
                role_id=record.get("team_role_id")
                if hasattr(record, "get")
                else getattr(record, "team_role_id", None),
            )

            await head_the_batch()

            _sent = await _send_verdict(
                bot,
                target_channel,
                db_path=db_path,
                round_id=result_ctx["round_id"],
                kind=KIND,
                season_number=season_number,
                division_name=division_name,
                round_number=round_number,
                session_label=session_label,
                driver_discord_id=driver_discord_id,
                driver_display_name=test_display_name,
                driver_name=await _graphic_name(
                    bot,
                    getattr(target_channel, "guild", None),
                    driver_discord_id,
                    fallback_display_name=test_display_name,
                ),
                penalty_description=penalty_description,
                description_text=description_text or NOT_PROVIDED,
                justification_text=justification_text or NOT_PROVIDED,
                team_name=team_name,
            )
            await _record_announcement(
                db_path, "appeal_records", _record_id(record), _sent,
                getattr(target_channel, "id", None),
            )

        except Exception as exc:  # noqa: BLE001 — recorded, and the next verdict taken
            log.exception(
                "post_appeal_announcements: error posting announcement for record %r", record
            )
            faults.append(
                f"**{division_name}** — the appeal verdict for "
                f"{_driver_label(driver_discord_id)} was not announced: {exc}"
            )

    return faults


async def delete_verdict_announcements(
    bot, db_path: str, round_id: int
) -> list[str]:
    """Take down every verdict announced for *round_id*, so replacements can stand alone.

    What #189 could not offer and the amendment replay needs: a round being replayed announces
    its verdicts afresh, and the announcements of the classification it replaced have to go, or
    the channel carries two decisions for one incident with nothing saying which is current.

    **A verdict announced before the message id was recorded cannot be taken down.** Those rows
    hold no id, nothing else identifies the message, and guessing by adjacency would delete
    whatever happens to sit there. They are named in the returned lines so the replay can say so
    rather than claiming a clean replacement (#345).

    Deletes by the recorded chunk list through :func:`results_post_service._delete_posting`, so
    a verdict that outgrew one message goes entirely. Returns the faults met, as lines a league
    can read, and an empty list where every announcement was removed.
    """
    from services.results_post_service import _delete_posting, _parse_ids

    faults: list[str] = []
    rows: list[dict] = []
    async with get_connection(db_path) as db:
        for table, fk_col, join_table in (
            ("penalty_records", "race_result_id", "race_session_results"),
            ("penalty_records", "qual_result_id", "qualifying_session_results"),
            ("appeal_records", "race_result_id", "race_session_results"),
            ("appeal_records", "qual_result_id", "qualifying_session_results"),
        ):
            cursor = await db.execute(
                f"""
                SELECT v.id AS record_id, v.announcement_message_id AS anchor,
                       v.announcement_message_ids AS chunks,
                       v.announcement_channel_id AS channel_id,
                       r.driver_user_id AS driver_user_id
                FROM {table} v
                JOIN {join_table} r ON r.id = v.{fk_col}
                JOIN session_results sr ON sr.id = r.session_result_id
                WHERE sr.round_id = ?
                ORDER BY v.id
                """,  # noqa: S608 — every name comes from the tuple above
                (round_id,),
            )
            for row in await cursor.fetchall():
                entry = dict(row)
                entry["table"] = table
                rows.append(entry)

    for row in rows:
        if not row["anchor"]:
            faults.append(
                f"the verdict for {_driver_label(row['driver_user_id'])} was announced before "
                f"the bot began recording its message, so the old announcement is still "
                f"standing and has to be removed by hand"
            )
            continue
        channel = bot.get_channel(int(row["channel_id"])) if row["channel_id"] else None
        if channel is None:
            faults.append(
                f"the verdict for {_driver_label(row['driver_user_id'])} could not be taken "
                f"down: its channel is no longer reachable"
            )
            continue
        await _delete_posting(
            channel, int(row["anchor"]), _parse_ids(row["chunks"]), label="verdict",
        )

    # Forget the announcements just removed, and only those. The two tables number their rows
    # independently, so a record id is only meaningful beside the table it came from — clearing
    # by id across both would blank an unrelated verdict that happened to share a number.
    cleared: dict[str, list[int]] = {}
    for row in rows:
        if row["anchor"]:
            cleared.setdefault(row["table"], []).append(row["record_id"])
    if cleared:
        async with get_connection(db_path) as db:
            for table, ids in cleared.items():
                placeholders = ", ".join("?" for _ in ids)
                await db.execute(
                    f"UPDATE {table} SET announcement_message_id = NULL, "  # noqa: S608
                    f"announcement_message_ids = NULL WHERE id IN ({placeholders})",
                    ids,
                )
            await db.commit()

    return faults


async def post_autosanction_announcement(
    bot,
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
    from services.results_post_service import _parse_ids

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


async def _records_for_round(db_path: str, round_id: int, table: str) -> list[dict]:
    """A round's verdict records of one table, oldest first, shaped as the posters expect.

    The announcement functions read ``driver_user_id`` and the two result-id columns off each
    record, none of which ``penalty_records`` and ``appeal_records`` carry together — the
    driver comes from the result row the verdict points at.
    """
    rows: list[dict] = []
    async with get_connection(db_path) as db:
        for fk_col, result_table in (
            ("race_result_id", "race_session_results"),
            ("qual_result_id", "qualifying_session_results"),
        ):
            cursor = await db.execute(
                f"""
                SELECT v.id AS id, v.race_result_id, v.qual_result_id,
                       v.penalty_type, v.time_seconds, v.description, v.justification,
                       r.driver_user_id AS driver_user_id, r.team_role_id AS team_role_id
                FROM {table} v
                JOIN {result_table} r ON r.id = v.{fk_col}
                JOIN session_results sr ON sr.id = r.session_result_id
                WHERE sr.round_id = ?
                ORDER BY v.id
                """,  # noqa: S608 — names come from the tuple above and the caller's literal
                (round_id,),
            )
            rows.extend(dict(row) for row in await cursor.fetchall())
    return sorted(rows, key=lambda r: r["id"])


async def republish_verdicts_from_round(
    bot, db_path: str, division_id: int, from_round_id: int, state_factory
) -> list[str]:
    """Announce every verdict of every round from *from_round_id* forward, in order.

    **The whole of a round's verdicts, not only those that changed** — a decision taken with
    the replay (#345). A round's verdicts are a contiguous run in the channel, and re-announcing
    a subset would interleave new decisions with old ones, leaving the run in an order that
    matches neither the classification nor the sequence it was decided in.

    **Produced before the originals are destroyed** (Constitution XIV.8). Every replacement for
    every round goes up first; only then are the announcements they replace taken down. A
    failure part-way therefore leaves the league the verdicts it already had.

    *state_factory* builds the ``PenaltyReviewState`` each round's announcement needs, the
    posters reading the round and division from it.

    Returns the faults met, as lines a league can read — including a named line for every
    verdict announced before the bot recorded message ids, which cannot be taken down and is
    left standing beside its replacement rather than silently doubled.
    """
    faults: list[str] = []
    rounds = await _rounds_from(db_path, division_id, from_round_id)

    # **Captured before a single replacement is posted.** Announcing overwrites
    # `announcement_message_id` on the very rows the superseded messages are identified by, so
    # reading them afterwards would return the new announcements and delete what had just been
    # put up. The old ones are noted here and taken down at the end.
    superseded: list[tuple[object, int, list[int] | None, int]] = []
    async with get_connection(db_path) as db:
        for rnd in rounds:
            for table in ("penalty_records", "appeal_records"):
                for fk_col, result_table in (
                    ("race_result_id", "race_session_results"),
                    ("qual_result_id", "qualifying_session_results"),
                ):
                    cursor = await db.execute(
                        f"""
                        SELECT v.announcement_message_id AS anchor,
                               v.announcement_message_ids AS chunks,
                               v.announcement_channel_id AS channel_id,
                               r.driver_user_id AS driver_user_id
                        FROM {table} v
                        JOIN {result_table} r ON r.id = v.{fk_col}
                        JOIN session_results sr ON sr.id = r.session_result_id
                        WHERE sr.round_id = ?
                        ORDER BY v.id
                        """,  # noqa: S608 — names come from the two tuples above
                        (rnd["round_id"],),
                    )
                    for row in await cursor.fetchall():
                        if not row["anchor"]:
                            faults.append(
                                f"the verdict for {_driver_label(row['driver_user_id'])} was "
                                f"announced before the bot began recording its message, so "
                                f"the superseded announcement is still standing and has to be "
                                f"removed by hand"
                            )
                            continue
                        superseded.append(
                            (
                                row["channel_id"],
                                int(row["anchor"]),
                                _parse_chunk_ids(row["chunks"]),
                                row["driver_user_id"],
                            )
                        )

    # ── Produce ───────────────────────────────────────────────────────────
    for rnd in rounds:
        round_id = rnd["round_id"]
        penalties = await _records_for_round(db_path, round_id, "penalty_records")
        appeals = await _records_for_round(db_path, round_id, "appeal_records")
        if not penalties and not appeals:
            continue

        state = state_factory(round_id)
        head = banner_for_round(bot, db_path, round_id)

        if penalties:
            faults.extend(
                await post_penalty_announcements(bot, state, penalties, head=head)
            )
        if appeals:
            faults.extend(await post_appeal_announcements(bot, state, appeals))

    # ── Then destroy ──────────────────────────────────────────────────────
    from services.results_post_service import _delete_posting

    for channel_id, anchor, chunk_ids, driver_user_id in superseded:
        channel = bot.get_channel(int(channel_id)) if channel_id else None
        if channel is None:
            faults.append(
                f"the superseded verdict for {_driver_label(driver_user_id)} could not be "
                f"taken down: its channel is no longer reachable"
            )
            continue
        await _delete_posting(channel, anchor, chunk_ids, label="verdict")

    return faults
