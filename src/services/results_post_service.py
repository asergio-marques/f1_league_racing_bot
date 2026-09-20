"""results_post_service.py — Post and edit results/standings in Discord channels."""
from __future__ import annotations

import asyncio
import json
import logging

import discord

from db.database import get_connection
from services.driver_service import current_account_map_for_division
from models.classification_occasion import ClassificationOccasion
from models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from models.season import SeasonStage
from models.session_result import (
    DriverSessionResult,
    OutcomeModifier,
    QualifyingSessionResult,
    RaceSessionResult,
    SessionResult,
)
from models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot
from services import standings_service
from services.channel_registry_service import (
    SETTING_LABELS,
    missing_channel_fault,
    unpostable_channel_fault,
)
from utils import results_formatter

log = logging.getLogger(__name__)

_MSG_MAX = 1990  # Leave a small margin under Discord's 2000-char limit


def _split_content(text: str) -> list[str]:
    """Split *text* into chunks ≤ _MSG_MAX chars, breaking on newlines where possible."""
    if len(text) <= _MSG_MAX:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > _MSG_MAX and current:
            chunks.append("".join(current).rstrip("\n"))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        chunks.append("".join(current).rstrip("\n"))
    return chunks or [text[:_MSG_MAX]]


#: Seconds to wait between one posting and the next when rebuilding a division's channels.
#:
#: Discord's practical limit is around five messages to a channel in five seconds, and a
#: whole-division replay posts every round of every channel in one go — some tens of messages
#: where the ordinary path posts two or three. discord.py waits out a 429 by itself, so this is
#: not what makes the replay correct; it is what stops it spending its time in rate-limit
#: backoff, which is slower and far less predictable than simply going at a walking pace.
#:
#: A second is comfortably inside the limit and keeps a forty-message rebuild under a minute.
#: Tests patch it to zero — see ``tests/unit/test_replay_throttle.py``.
POSTING_THROTTLE_SECONDS: float = 1.0


async def throttle() -> None:
    """Wait ``POSTING_THROTTLE_SECONDS`` before the next posting of a division-wide replay.

    A function rather than a bare ``sleep`` at each call site so the pacing is named, is read
    from one place, and can be stood down in a test without patching :mod:`asyncio`.
    """
    if POSTING_THROTTLE_SECONDS > 0:
        await asyncio.sleep(POSTING_THROTTLE_SECONDS)


async def _send_chunked(
    channel: discord.TextChannel, content: str
) -> list[discord.Message]:
    """Send *content* to *channel*, splitting into multiple messages if needed.

    Returns **every** message sent, the anchor first, so that all of them can be recorded.

    It used to return the anchor alone, and only the anchor was persisted; the rest were found
    again by walking forward from it over bot-authored messages. That walk cannot tell this
    posting's continuation from the next posting down, so it was wrong wherever two of the
    bot's own postings sit together — which the amendment replay makes ordinary, posting a
    replacement before destroying the original (#345). Recording what was sent removes the
    guess entirely.
    """
    chunks = _split_content(content)
    return [await channel.send(chunk) for chunk in chunks]


def _ids_json(messages: list[discord.Message]) -> str:
    """The chunk list as it is stored: a JSON array of message ids, the anchor first."""
    return json.dumps([m.id for m in messages])


def _parse_ids(raw: str | None) -> list[int] | None:
    """The stored chunk list as message ids, or None where nothing usable was recorded.

    A malformed value is treated as nothing recorded rather than raising: the column is written
    by this module alone, so a bad one means a bug elsewhere, and refusing to delete anything is
    a great deal safer than deleting whatever a half-parsed list happened to yield.
    """
    if not raw:
        return None
    try:
        ids = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("_parse_ids: could not read a stored chunk list: %r", raw)
        return None
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        log.warning("_parse_ids: stored chunk list was not a list of ids: %r", raw)
        return None
    return ids or None


async def _delete_posting(
    channel: discord.TextChannel,
    anchor_msg_id: int,
    message_ids: list[int] | None,
    label: str = "message",
) -> None:
    """Delete a posting: exactly the messages it recorded, or the walk where it recorded none.

    **The recorded list is the truth where there is one** (#345). It is written at send time, so
    it names this posting's own messages and cannot reach a neighbour's — which matters because
    the amendment replay posts a replacement directly beneath the original before destroying it,
    and the two are both the bot's.

    Where no list was recorded the posting predates the column, and
    :func:`_delete_with_continuations` guesses the rest by adjacency, as it always did. That
    path is wrong in exactly the case above; it survives only for rows nothing else can serve.
    """
    if message_ids:
        # The anchor is included whether or not the stored list happens to name it. Every list
        # this module writes puts it first (`_ids_json` is handed the messages `_send_chunked`
        # returned, anchor included), but the anchor and the list are cleared and written through
        # separate paths — so a list that ever omitted it would leak that message for good, and
        # the one thing certain about the anchor is that it belongs to this posting (#345).
        if anchor_msg_id not in message_ids:
            message_ids = [anchor_msg_id, *message_ids]
        for message_id in message_ids:
            try:
                message = await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                log.warning("_delete_posting: could not fetch %s %s: %s", label, message_id, exc)
                continue
            try:
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                log.warning("_delete_posting: could not delete %s %s: %s", label, message_id, exc)
        return
    await _delete_with_continuations(channel, anchor_msg_id, label=label)


async def _delete_with_continuations(
    channel: discord.TextChannel,
    anchor_msg_id: int,
    label: str = "message",
) -> None:
    """Delete the anchor message and any immediately-following bot continuation messages.

    **The fallback, not the route.** Prefer :func:`_delete_posting`, which deletes the messages
    a posting actually recorded. This one guesses them: it takes every message following the
    anchor that the bot authored, stopping at someone else's. That cannot distinguish this
    posting's continuation from the *next posting down*, so it is wrong wherever two of the
    bot's own postings sit together — which the amendment replay makes ordinary, posting a
    replacement before destroying the original (#345).

    It survives for rows written before `results_message_ids` and its siblings existed, which
    recorded an anchor and nothing else. Do not reach for it in new code.

    Args:
        channel:       The Discord text channel to operate on.
        anchor_msg_id: The stored message ID of the first (anchor) chunk.
        label:         Log context string for warning messages.
    """
    try:
        anchor = await channel.fetch_message(anchor_msg_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
        log.warning("_delete_with_continuations: could not fetch %s %s: %s", label, anchor_msg_id, exc)
        return

    bot_user_id: int = anchor.author.id

    # Collect continuation messages (up to 5; we realistically only need 1-2)
    continuations: list[discord.Message] = []
    try:
        async for msg in channel.history(after=anchor, limit=5, oldest_first=True):
            if msg.author.id != bot_user_id:
                break  # Non-bot message — stop; don't delete anything beyond here
            continuations.append(msg)
    except discord.HTTPException as exc:
        log.warning("_delete_with_continuations: history fetch failed for %s: %s", label, exc)

    # Delete continuations first (oldest → newest), then the anchor
    for msg in continuations:
        try:
            await msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            log.warning("_delete_with_continuations: could not delete continuation %s: %s", msg.id, exc)

    try:
        await anchor.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
        log.warning("_delete_with_continuations: could not delete %s %s: %s", label, anchor_msg_id, exc)


# ---------------------------------------------------------------------------
# Display-name helpers
# ---------------------------------------------------------------------------

async def _build_test_driver_display(
    db_path: str,
    user_ids: list[int],
) -> dict[int, str]:
    """Return {user_id: '<@uid> (name)'} for any test drivers in *user_ids*."""
    if not user_ids:
        return {}
    async with get_connection(db_path) as db:
        placeholders = ",".join("?" * len(user_ids))
        cursor = await db.execute(
            f"SELECT discord_user_id, test_display_name FROM driver_profiles"
            f" WHERE is_test_driver = 1 AND discord_user_id IN ({placeholders})",
            [str(uid) for uid in user_ids],
        )
        rows = await cursor.fetchall()
    result: dict[int, str] = {}
    for r in rows:
        uid = int(r["discord_user_id"])
        name = r["test_display_name"]
        if name:
            result[uid] = f"<@{uid}> ({name})"
    return result


async def _build_member_display(
    guild: discord.Guild,
    user_ids: list[int],
) -> dict[int, str]:
    """Return {user_id: display_name} for the given IDs."""
    result: dict[int, str] = {}
    for uid in user_ids:
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except (discord.NotFound, discord.HTTPException):
                member = None
        result[uid] = member.display_name if member else f"User {uid}"
    return result


async def driver_standings_for_display(
    db_path: str,
    division_id: int,
    round_id: int,
    guild: discord.Guild | None,
    bot=None,
) -> list[DriverStandingsSnapshot]:
    """The driver standings, ordered on the names they will be posted under.

    Computed twice, deliberately, and for the reason the opening classification already is:
    the final tiebreak orders two entries level on everything alphabetically by driver, the
    names are resolved from Discord by user id, so the roster has to be known before it can
    be ordered. The first pass is read only for who is in it; the second is the one that
    counts.

    Where no bot or guild is in scope there is nothing to resolve a name from, and the single
    pass falls back to ordering a full tie by user id.

    The recomputation that persists a snapshot resolves the same names, through
    :func:`recompute_standings_from_round`, so the stored order and the drawn order agree.

    The name is whatever resolves at the moment of computing, and no earlier one is kept. A
    driver renamed mid-season therefore moves among the entries they are tied with, and a
    round reposted after the rename can order such a pair the other way about than when it
    was first published. That is accepted rather than worked around (decided 2026-09-15):
    preserving it would mean the standings reading a name nobody is called any more.
    """
    snaps = await standings_service.compute_driver_standings(db_path, division_id, round_id)
    if bot is None or guild is None or not snaps:
        return snaps

    from services.image_results_post import _driver_names

    names = await _driver_names(
        bot, guild, [s.driver_user_id for s in snaps], division_id=division_id
    )
    return await standings_service.compute_driver_standings(
        db_path, division_id, round_id, names
    )


async def standings_display_names(
    db_path: str,
    division_id: int,
    guild: discord.Guild | None,
    bot=None,
) -> dict[int, str] | None:
    """The names every driver the division's standings can mention would be drawn under.

    Resolved across the division rather than for one round, so a cascade can order every
    round it rewrites on one resolution. The roster only grows as a season runs, so the
    division's last round holds every driver an earlier one could.

    ``None`` where there is nothing to resolve a name from, which leaves the caller ordering
    a full tie by user id.
    """
    if bot is None or guild is None:
        return None

    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                """
                SELECT id FROM rounds
                WHERE division_id = ? AND status != 'CANCELLED'
                ORDER BY round_number DESC LIMIT 1
                """,
                (division_id,),
            )
        ).fetchone()
    if row is None:
        return None

    snaps = await standings_service.compute_driver_standings(db_path, division_id, row["id"])
    if not snaps:
        return None

    from services.image_results_post import _driver_names

    return await _driver_names(
        bot, guild, [s.driver_user_id for s in snaps], division_id=division_id
    )


async def recompute_standings_from_round(
    db_path: str,
    division_id: int,
    from_round_id: int,
    guild: discord.Guild | None,
    bot=None,
) -> None:
    """Cascade the standings from *from_round_id*, ordered as the postings are.

    The snapshot a round stores is the order a league was shown, so the recomputation reads
    the same names the posting does. Without it the two paths agree on everything except the
    entries tied on every criterion, which the persisted order would settle by user id and
    the posted order by name — and the next round's movement arrows are derived from the
    stored order, so the disagreement would surface as an arrow against a driver who had not
    moved (decided 2026-09-15, reversing the narrower call taken earlier the same day).
    """
    names = await standings_display_names(db_path, division_id, guild, bot)
    await standings_service.cascade_recompute_from_round(
        db_path, division_id, from_round_id, names
    )


async def _build_team_display(
    guild: discord.Guild,
    role_ids: list[int],
) -> dict[int, str]:
    """Return {role_id: role_name} for the given IDs."""
    result: dict[int, str] = {}
    for rid in role_ids:
        role = guild.get_role(rid)
        result[rid] = role.name if role else f"Role {rid}"
    return result


async def _get_heading_context(
    db_path: str, round_id: int
) -> tuple[int | None, str]:
    """Return (season_number, division_name) for building result headings."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT s.season_number, d.name AS division_name
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None, "Unknown"
    return row["season_number"], row["division_name"]


async def _get_primary_session_label(db_path: str, round_id: int) -> str:
    """Return the formatted label for the most recently posted session of *round_id*."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT sr.session_type, r.format
            FROM session_results sr
            JOIN rounds r ON r.id = sr.round_id
            WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
            ORDER BY sr.id DESC
            LIMIT 1
            """,
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return "Results"
    st = SessionType(row["session_type"])
    is_sprint = str(row["format"]).upper() == "SPRINT"
    return results_formatter.format_session_label(st, is_sprint=is_sprint)


async def _get_result_status(db_path: str, round_id: int) -> str:
    """The round's results lifecycle stage, from which both phase closures follow (039)."""
    async with get_connection(db_path) as db:
        row = await (
            await db.execute("SELECT status FROM rounds WHERE id = ?", (round_id,))
        ).fetchone()
    if row is None or not row["status"]:
        return ""
    return row["status"]


async def _get_division_tier(db_path: str, division_id: int) -> int | None:
    """The division's tier, an optional field of the results graphic."""
    async with get_connection(db_path) as db:
        row = await (
            await db.execute("SELECT tier FROM divisions WHERE id = ?", (division_id,))
        ).fetchone()
    return None if row is None else row["tier"]


def _label_from_status(result_status: str) -> str:
    """Map a round result_status value to the user-visible lifecycle label."""
    return {
        "AWAITING_REPORT_VERDICTS": "Provisional Results",
        "AWAITING_APPEAL_VERDICTS": "Post-Race Penalty Results",
        "FINAL": "Final Results",
    }.get(result_status, "Results")


# ---------------------------------------------------------------------------
# Session-level posting
# ---------------------------------------------------------------------------

async def _load_dsq_phase_map(
    db_path: str,
    result_ids: list[int],
    *,
    is_qualifying: bool,
) -> dict[int, str]:
    """Return a mapping of result-row id -> 'PENALTY' or 'APPEAL' for DSQ entries.

    APPEAL overrides PENALTY when both exist for the same row (appeals are applied last).
    """
    if not result_ids:
        return {}
    ph = ",".join("?" * len(result_ids))
    fk_col = "qual_result_id" if is_qualifying else "race_result_id"
    phase_map: dict[int, str] = {}
    async with get_connection(db_path) as db:
        # Penalty phase DSQs
        cursor = await db.execute(
            f"SELECT {fk_col} AS rid FROM penalty_records "
            f"WHERE {fk_col} IN ({ph}) AND penalty_type = 'DSQ'",
            result_ids,
        )
        for row in await cursor.fetchall():
            phase_map[row["rid"]] = "PENALTY"
        # Appeal phase DSQs (override)
        cursor = await db.execute(
            f"SELECT {fk_col} AS rid FROM appeal_records "
            f"WHERE {fk_col} IN ({ph}) AND penalty_type = 'DSQ'",
            result_ids,
        )
        for row in await cursor.fetchall():
            phase_map[row["rid"]] = "APPEAL"
    return phase_map


async def post_session_results(
    db_path: str,
    session_result: SessionResult,
    driver_rows: list,  # list[QualifyingSessionResult] | list[RaceSessionResult] | list[DriverSessionResult]
    points_map: dict[int, int],
    results_channel: discord.TextChannel,
    guild: discord.Guild,
    round_number: int,
    track_name: str,
    label: str,
    is_sprint: bool = True,
    *,
    bot=None,
    result_status: str | None = None,
    dsq_phase_map: dict[int, str] | None = None,
) -> int:
    """Format and send a single session result. Returns the Discord message ID.

    *dsq_phase_map* lets a caller state which rows were disqualified, and in which phase,
    instead of having it read from the verdict tables. The amendment replay posts what it has
    computed **before** committing any of it (#345), so the tables still hold the round it is
    replacing; reading them there would draw the old classification's marks onto the new one.
    Omitted — every caller but that one — it is loaded from the database exactly as before.

    **The image path is a guard clause in front of an untouched body** (039). Where the
    images module is enabled, the `results` aspect is on and this session's template is
    valid, ``image_results_post.try_post`` produces the PNG, posts it under the heading and
    the lifecycle label, replaces the previous message and returns its id.

    Where it is not — no bot in scope, the module off, the aspect off, an invalid template,
    or a render that failed an *uncommanded* posting — everything below runs exactly as it
    did before 039. The graphic is an alternative output beside the text, not a reform of it
    (Constitution XIV.7).

    This function is the single funnel every reposting occasion reaches, which is why the
    hook is here and not at its three call sites.
    """
    session_type = SessionType(session_result.session_type)
    session_label = results_formatter.format_session_label(session_type, is_sprint=is_sprint)

    user_ids = [r.driver_user_id for r in driver_rows]
    test_display = await _build_test_driver_display(db_path, user_ids)

    if dsq_phase_map is None:
        result_ids = [r.id for r in driver_rows]
        dsq_phase_map = await _load_dsq_phase_map(
            db_path, result_ids, is_qualifying=session_type.is_qualifying
        )

    if session_type.is_qualifying:
        table = results_formatter.format_qualifying_table(
            driver_rows, points_map, member_display=test_display or None,
            dsq_phase_map=dsq_phase_map,
        )
    else:
        table = results_formatter.format_race_table(
            driver_rows, points_map, member_display=test_display or None,
            dsq_phase_map=dsq_phase_map,
        )

    season_number, division_name = await _get_heading_context(db_path, session_result.round_id)
    season_prefix = f"Season {season_number} " if season_number is not None else ""
    heading = f"**{season_prefix}{division_name} Round {round_number} — {session_label}**"

    # ── The image path (039) ──────────────────────────────────────────────
    if bot is not None:
        try:
            from services.image_results_post import try_post

            outcome = await try_post(
                bot,
                guild,
                results_channel,
                heading=heading,
                label=label,
                session_result=session_result,
                driver_rows=driver_rows,
                points_map=points_map,
                round_number=round_number,
                race_name=track_name,
                is_sprint=is_sprint,
                result_status=result_status
                or await _get_result_status(db_path, session_result.round_id),
                division_name=division_name,
                season_number=season_number,
                division_tier=await _get_division_tier(db_path, session_result.division_id),
                dsq_phase_map=dsq_phase_map,
            )
            if outcome.applicable and outcome.message_id is not None:
                return outcome.message_id
        except Exception as exc:  # noqa: BLE001 — never block a posting on the image path
            log.error("results: image path failed for session %s: %s", session_result.id, exc)

    sent = await _send_chunked(results_channel, f"{heading}\n{label}\n{table}")

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE session_results SET results_message_id = ?, results_message_ids = ? "
            "WHERE id = ?",
            (sent[0].id, _ids_json(sent), session_result.id),
        )
        await db.commit()

    return sent[0].id


# ---------------------------------------------------------------------------
# Standings posting
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Standings composition
#
# The textual flow posts ONE message carrying both championships; the image flow posts TWO.
# Composition is kept apart from posting so that a fallback can carry the failed
# championship's section **alone** — Constitution XIV.7 as amended at v4.5.0 puts a fallback
# at the grain of the graphic that failed, and never re-posts what a surviving graphic
# already drew. With the two welded together, one failing championship could only fall back
# by reposting both.
#
# See specs/040-standings-image-generation/contracts/standings-posting.md.
# ---------------------------------------------------------------------------

#: The two championships, in the order they are posted.
STANDINGS_DRIVERS = "drivers"
STANDINGS_CONSTRUCTORS = "constructors"

_STANDINGS_SUBHEADINGS = {
    STANDINGS_DRIVERS: "**Driver Standings**",
    STANDINGS_CONSTRUCTORS: "**Team Standings**",
}


def standings_section(championship: str, body: str) -> str:
    """One championship's section, sub-heading included.

    The sub-heading belongs to the section rather than to the message, so that a section
    posted alone still says which championship it is.
    """
    return f"{_STANDINGS_SUBHEADINGS[championship]}\n{body}"


def compose_standings_message(heading: str, label: str, sections: list[str]) -> str:
    """The heading, the lifecycle label, and one or more championship sections.

    Passing both sections reproduces the message the textual flow has always posted, byte
    for byte. Passing one is what a per-championship fallback posts.

    A season-boundary posting passes an empty *label* and the occasion's own phrase as the
    *heading*. The graphic it falls back from carries no text at all, but a bare table names
    nothing — so where the sheet is written out rather than drawn, the phrase is what heads
    it.
    """
    top = f"{heading}\n{label}" if label else heading
    return "\n\n".join([top, *sections])


async def post_standings(
    db_path: str,
    division_id: int,
    round_id: int,
    round_number: int,
    track_name: str,
    standings_channel: discord.TextChannel,
    driver_snapshots: list[DriverStandingsSnapshot],
    team_snapshots: list[TeamStandingsSnapshot],
    guild: discord.Guild,
    show_reserves: bool,
    label: str,
    *,
    bot=None,
    occasion: ClassificationOccasion = ClassificationOccasion.AFTER_ROUND,
) -> None:
    """Format and post (or edit-in-place) the driver and team standings.

    **The image path is a guard clause in front of an untouched body** (040). Where the
    images module is enabled, the `standings` aspect is on and a template is valid,
    ``image_standings_post.try_post`` produces the two PNGs, posts each under the heading
    and the lifecycle label, replaces the previous messages and persists their ids. The
    textual message below is then composed of whichever championships did *not* draw — one
    section, or neither, or both.

    Where the image flow does not run at all — no bot in scope, the module off, the aspect
    off, neither template valid — everything below runs exactly as it did before 040, byte
    for byte. The graphic is an alternative output beside the text, not a reform of it
    (Constitution XIV.7).

    This function is the single funnel all seven reposting occasions reach over its five
    call sites, which is why the hook is here and not at any of them.
    """
    # Determine reserve user IDs from the DB (is_reserve team instances)
    reserve_user_ids: set[int] = await _get_reserve_user_ids(db_path, division_id)

    driver_uids = [s.driver_user_id for s in driver_snapshots]
    test_display = await _build_test_driver_display(db_path, driver_uids)

    driver_text = results_formatter.format_driver_standings(
        driver_snapshots, reserve_user_ids, show_reserves, driver_display=test_display or None
    )
    team_text = results_formatter.format_team_standings(team_snapshots)

    season_number, division_name = await _get_heading_context(db_path, round_id)
    season_prefix = f"Season {season_number} " if season_number is not None else ""
    if occasion.names_a_round:
        primary_session_label = await _get_primary_session_label(db_path, round_id)
        heading = (
            f"**{season_prefix}{division_name} Round {round_number} "
            f"— {primary_session_label}**"
        )
    else:
        # No round to head it with and no phase to label it. The graphic goes out bare; the
        # textual fallback below is headed by the occasion's own phrase.
        heading = f"**{season_prefix}{division_name} — {occasion.label()}**"
        label = ""

    sections_by_championship = {
        STANDINGS_DRIVERS: driver_text,
        STANDINGS_CONSTRUCTORS: team_text,
    }
    championships = [STANDINGS_DRIVERS, STANDINGS_CONSTRUCTORS]

    # ── The image path (040) ──────────────────────────────────────────────
    # A round recorded as cancelled produces no standings posting. A season-boundary sheet
    # is not about a round and no round can cancel it, so the guard stands down there.
    cancelled = occasion.names_a_round and await _round_is_cancelled(db_path, round_id)
    if bot is not None and not cancelled:
        try:
            from services.image_standings_post import try_post

            outcome = await try_post(
                bot,
                guild,
                standings_channel,
                db_path=db_path,
                division_id=division_id,
                round_id=round_id,
                round_number=round_number,
                heading=heading,
                label=label,
                driver_snapshots=driver_snapshots,
                team_snapshots=team_snapshots,
                reserve_user_ids=reserve_user_ids,
                show_reserves=show_reserves,
                result_status=await _get_result_status(db_path, round_id),
                division_name=division_name,
                division_tier=await _get_division_tier(db_path, division_id),
                season_number=season_number,
                race_name=track_name,
                occasion=occasion,
            )
        except Exception:  # noqa: BLE001 — a graphic never gates the standings
            log.exception(
                "post_standings: the image path failed for round %s; "
                "the textual standings stand",
                round_id,
            )
        else:
            if outcome.rejects:
                # A commanded posting that would not draw posts nothing at all (XIV.7).
                return
            if outcome.applicable:
                championships = outcome.fallback_championships
                if not championships:
                    return
                return await _post_standings_sections(
                    db_path,
                    division_id,
                    round_id,
                    standings_channel,
                    driver_snapshots,
                    heading=heading,
                    label=label,
                    sections={
                        name: sections_by_championship[name] for name in championships
                    },
                )

    content = compose_standings_message(
        heading,
        label,
        [standings_section(name, sections_by_championship[name]) for name in championships],
    )

    # Look for existing standings message (stored in the top-ranked driver snapshot)
    existing_msg_id = await _get_standings_message_id(db_path, division_id, round_id)

    sent_msg: discord.Message | None = None
    if existing_msg_id is not None:
        try:
            existing_msg = await standings_channel.fetch_message(existing_msg_id)
            # Only edit in-place when the content fits in a single message; otherwise
            # fall through to delete-and-resend so we can chunk across multiple messages.
            if len(content) <= _MSG_MAX:
                await existing_msg.edit(content=content)
                sent_msg = existing_msg
            else:
                await existing_msg.delete()
        except (discord.NotFound, discord.HTTPException):
            sent_msg = None

    sent_ids: str | None = None
    if sent_msg is None:
        sent = await _send_chunked(standings_channel, content)
        sent_msg = sent[0]
        sent_ids = _ids_json(sent)
    else:
        # Edited in place, so it is the one message it always was and occupies no others.
        sent_ids = _ids_json([sent_msg])

    # Persist the message ID on the top-ranked driver snapshot. One message carries both
    # championships here, so the constructor column is left null — which is exactly what
    # distinguishes a textual posting from an image one when the next posting reads them.
    if driver_snapshots:
        await _set_standings_message_id(
            db_path, division_id, round_id, sent_msg.id, STANDINGS_DRIVERS,
            message_ids=sent_ids,
        )


async def _forget_standings_messages(
    db_path: str, division_id: int, round_id: int
) -> list[tuple[int, list[int] | None]]:
    """Forget a round's standings messages and hand them back to be deleted later.

    The reading half of :func:`_clear_standings_messages`, split out for the produce-then-
    destroy rebuild (#345): the ids have to leave the database *before* the replacement is
    posted, so :func:`post_standings` inserts rather than editing the message it replaces —
    but the messages themselves must not be destroyed until every replacement is up.

    Returns ``(anchor id, chunk list)`` per championship that had one, in a stable order.
    """
    found: list[tuple[int, list[int] | None]] = []
    for championship in (STANDINGS_DRIVERS, STANDINGS_CONSTRUCTORS):
        existing_id = await _get_standings_message_id(
            db_path, division_id, round_id, championship
        )
        if existing_id is None:
            continue
        found.append(
            (
                existing_id,
                await _get_standings_message_ids(
                    db_path, division_id, round_id, championship
                ),
            )
        )
        await _set_standings_message_id(
            db_path, division_id, round_id, None, championship
        )
    return found


async def _clear_standings_messages(
    db_path: str,
    division_id: int,
    round_id: int,
    channel: discord.TextChannel | None,
) -> None:
    """Delete both championships' standings messages and forget their ids.

    The textual flow posts one message and leaves the constructor column null; the image
    flow posts two and fills both. A caller clearing only the drivers column would strand
    whatever the constructors column still names — the next posting would neither replace
    it nor delete it, and the league would read a stale constructors table beside a current
    one indefinitely.
    """
    for championship in (STANDINGS_DRIVERS, STANDINGS_CONSTRUCTORS):
        existing_id = await _get_standings_message_id(
            db_path, division_id, round_id, championship
        )
        if existing_id is None:
            continue
        if channel is not None:
            existing_ids = await _get_standings_message_ids(
                db_path, division_id, round_id, championship
            )
            await _delete_posting(
                channel, existing_id, existing_ids, label="standings message"
            )
        await _set_standings_message_id(
            db_path, division_id, round_id, None, championship
        )


async def _round_is_cancelled(db_path: str, round_id: int) -> bool:
    """True where the round is recorded as cancelled.

    Its standings are not posted as graphics, the `standings` toggle notwithstanding
    (FR-050). The textual path's own behaviour for a cancelled round is untouched — this
    only decides whether the image branch is entered.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT status FROM rounds WHERE id = ?", (round_id,)
        )
        row = await cursor.fetchone()
    if row is None:
        return False
    try:
        return (row["status"] or "").upper() == "CANCELLED"
    except (IndexError, KeyError, TypeError):
        return False


async def _post_standings_sections(
    db_path: str,
    division_id: int,
    round_id: int,
    standings_channel: discord.TextChannel,
    driver_snapshots: list[DriverStandingsSnapshot],
    *,
    heading: str,
    label: str,
    sections: dict[str, str],
) -> None:
    """Post a textual section for each championship whose graphic did not draw.

    One message per championship rather than one carrying both, so that the message ids
    stay one-to-one with the championships and a surviving graphic is never accompanied by
    a text table repeating what it already drew (FR-052).

    **Produce before destroying** (FR-048): the replacement is sent before the message it
    replaces is deleted, so the channel is never without that championship's standings.
    """
    if not sections:
        return

    for championship, body in sections.items():
        content = compose_standings_message(
            heading, label, [standings_section(championship, body)]
        )
        previous_id = await _get_standings_message_id(
            db_path, division_id, round_id, championship
        )

        previous_ids = await _get_standings_message_ids(
            db_path, division_id, round_id, championship
        )

        sent = await _send_chunked(standings_channel, content)

        if previous_id is not None:
            await _delete_posting(
                standings_channel, previous_id, previous_ids, label="standings message"
            )

        if driver_snapshots:
            await _set_standings_message_id(
                db_path, division_id, round_id, sent[0].id, championship,
                message_ids=_ids_json(sent),
            )


#: Championship → the column naming the message that carries it. Both live on the row of the
#: top-ranked driver, as the first of them always has.
_STANDINGS_ID_COLUMNS = {
    STANDINGS_DRIVERS: "standings_message_id",
    STANDINGS_CONSTRUCTORS: "constructor_standings_message_id",
}

#: The same two, for the chunk list each posting occupies (#345).
_STANDINGS_IDS_COLUMNS = {
    STANDINGS_DRIVERS: "standings_message_ids",
    STANDINGS_CONSTRUCTORS: "constructor_standings_message_ids",
}


async def _get_standings_message_id(
    db_path: str,
    division_id: int,
    round_id: int,
    championship: str = STANDINGS_DRIVERS,
) -> int | None:
    """Return the message id carrying *championship* for the given round's snapshot.

    Defaults to the driver standings, which is the message the textual flow posts for both
    championships together — so every caller written before the image flow keeps its meaning
    unchanged.
    """
    column = _STANDINGS_ID_COLUMNS[championship]
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"""
            SELECT {column} AS message_id
            FROM driver_standings_snapshots
            WHERE division_id = ? AND round_id = ?
            ORDER BY standing_position ASC
            LIMIT 1
            """,
            (division_id, round_id),
        )
        row = await cursor.fetchone()
    return row["message_id"] if row and row["message_id"] else None


async def _get_standings_message_ids(
    db_path: str,
    division_id: int,
    round_id: int,
    championship: str = STANDINGS_DRIVERS,
) -> list[int] | None:
    """Return every message id *championship*'s posting occupies, or None where none was recorded.

    None is not an empty list. A posting written before the chunk list existed recorded only its
    anchor, and has to fall back to the adjacency walk; a posting that recorded ``[]`` would be
    claiming to occupy no messages at all. Keeping the two apart is what stops the fallback
    being reached by accident.
    """
    column = _STANDINGS_IDS_COLUMNS[championship]
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"""
            SELECT {column} AS message_ids
            FROM driver_standings_snapshots
            WHERE division_id = ? AND round_id = ?
            ORDER BY standing_position ASC
            LIMIT 1
            """,  # noqa: S608 — column comes from the constant map above
            (division_id, round_id),
        )
        row = await cursor.fetchone()
    return _parse_ids(row["message_ids"] if row else None)


async def _set_standings_message_id(
    db_path: str,
    division_id: int,
    round_id: int,
    message_id: int | None,
    championship: str = STANDINGS_DRIVERS,
    *,
    message_ids: str | None = None,
) -> None:
    """Persist *message_id* for *championship* on the top-ranked driver's row.

    Written on every posting, textual or graphic, so the two flows never disagree about
    which message is which. The textual flow leaves the constructor column null.

    *message_ids* is the JSON chunk list of the same posting where the caller has one, so that
    deleting it later removes every message it occupies rather than guessing at the rest
    (#345). Clearing an id clears the list with it: a stale list outliving the message it
    described would send a delete at somebody else's posting.
    """
    column = _STANDINGS_ID_COLUMNS[championship]
    list_column = _STANDINGS_IDS_COLUMNS[championship]
    async with get_connection(db_path) as db:
        await db.execute(
            f"""
            UPDATE driver_standings_snapshots
            SET {column} = ?, {list_column} = ?
            WHERE round_id = ? AND division_id = ?
              AND driver_user_id = (
                  SELECT driver_user_id FROM driver_standings_snapshots
                  WHERE round_id = ? AND division_id = ?
                  ORDER BY standing_position ASC LIMIT 1
              )
            """,
            (message_id, message_ids, round_id, division_id, round_id, division_id),
        )
        await db.commit()


async def _get_reserve_user_ids(db_path: str, division_id: int) -> set[int]:
    """Return discord_user_ids of all drivers seated in a reserve team for this division."""
    from services.season_lifecycle_service import uncommitted_seat_excluded

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"""
            SELECT dp.discord_user_id
            FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
            WHERE ti.division_id = ? AND ti.is_reserve = 1
              AND ts.driver_profile_id IS NOT NULL
              AND {uncommitted_seat_excluded("ts")}
            """,
            (division_id,),
        )
        rows = await cursor.fetchall()
    return {int(r["discord_user_id"]) for r in rows if r["discord_user_id"]}


# ---------------------------------------------------------------------------
# Driver-row loading helpers
# ---------------------------------------------------------------------------

async def _load_driver_rows(
    db_path: str,
    session_result_id: int,
    session_type: SessionType,
) -> list:
    """Load driver rows for a session from new tables.

    Returns list[QualifyingSessionResult] for qualifying, list[RaceSessionResult]
    for race.

    Each row names the driver by the account they use **now**, not the one it was recorded
    under (issue #243): the rows are read for drawing and posting only, and everything drawn
    names a driver by their current account — a completed season's results drawn again
    included. Nothing stored is altered. A caller writing results back must read them itself.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT division_id FROM session_results WHERE id = ?", (session_result_id,)
        )
        header = await cursor.fetchone()
        current_of = (
            await current_account_map_for_division(db, header["division_id"]) if header else {}
        )

    if session_type.is_qualifying:
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT id, session_result_id, driver_user_id, team_role_id, finishing_position, "
                "outcome, tyre, best_lap, points_awarded, driver_profile_id "
                "FROM qualifying_session_results WHERE session_result_id = ? "
                "ORDER BY finishing_position",
                (session_result_id,),
            )
            rows = await cursor.fetchall()
        return [
            QualifyingSessionResult(
                id=r["id"],
                session_result_id=r["session_result_id"],
                driver_user_id=current_of.get(r["driver_user_id"], r["driver_user_id"]),
                team_role_id=r["team_role_id"],
                finishing_position=r["finishing_position"],
                outcome=OutcomeModifier(r["outcome"]),
                tyre=r["tyre"],
                best_lap=r["best_lap"],
                points_awarded=r["points_awarded"] or 0,
                driver_profile_id=r["driver_profile_id"],
            )
            for r in rows
        ]

    # Race session
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, base_time_ms, laps_behind, ingame_time_penalties_ms, "
            "postrace_time_penalties_ms, appeal_time_penalties_ms, fastest_lap, "
            "fastest_lap_bonus, points_awarded, driver_profile_id "
            "FROM race_session_results WHERE session_result_id = ? "
            "ORDER BY finishing_position",
            (session_result_id,),
        )
        rows = await cursor.fetchall()
    return [
        RaceSessionResult(
            id=r["id"],
            session_result_id=r["session_result_id"],
            driver_user_id=current_of.get(r["driver_user_id"], r["driver_user_id"]),
            team_role_id=r["team_role_id"],
            finishing_position=r["finishing_position"],
            outcome=OutcomeModifier(r["outcome"]),
            base_time_ms=r["base_time_ms"],
            laps_behind=r["laps_behind"],
            ingame_time_penalties_ms=r["ingame_time_penalties_ms"] or 0,
            postrace_time_penalties_ms=r["postrace_time_penalties_ms"] or 0,
            appeal_time_penalties_ms=r["appeal_time_penalties_ms"] or 0,
            fastest_lap=r["fastest_lap"],
            fastest_lap_bonus=r["fastest_lap_bonus"] or 0,
            points_awarded=r["points_awarded"] or 0,
            driver_profile_id=r["driver_profile_id"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Round-level posting
# ---------------------------------------------------------------------------

async def post_round_results(
    db_path: str,
    round_id: int,
    division_id: int,
    results_channel: discord.TextChannel,
    guild: discord.Guild,
    label: str,
    *,
    bot=None,
) -> None:
    """Post results for all non-cancelled sessions of a round in session order."""
    from services.result_submission_service import SESSION_ORDER_SPRINT, SESSION_ORDER_NORMAL
    from models.round import RoundFormat

    # Load round context (round_number, track_name, format) for message headers
    async with get_connection(db_path) as db:
        rnd_cursor = await db.execute(
            "SELECT round_number, track_name, format FROM rounds WHERE id = ?",
            (round_id,),
        )
        rnd_row = await rnd_cursor.fetchone()

    if rnd_row is None:
        log.warning("post_round_results: round %s not found", round_id)
        return

    round_number: int = rnd_row["round_number"]
    track_name: str = rnd_row["track_name"] or "Unknown"
    is_sprint: bool = str(rnd_row["format"]).upper() == "SPRINT"

    # Load session results for this round
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT id, round_id, division_id, session_type, status, config_name,
                   submitted_by, submitted_at, results_message_id
            FROM session_results
            WHERE round_id = ? AND status = 'ACTIVE'
            ORDER BY id
            """,
            (round_id,),
        )
        session_rows = await cursor.fetchall()

    if not session_rows:
        log.debug("post_round_results: no ACTIVE sessions for round %s", round_id)
        return

    for sr_row in session_rows:
        session_result = SessionResult(
            id=sr_row["id"],
            round_id=sr_row["round_id"],
            division_id=sr_row["division_id"],
            session_type=sr_row["session_type"],
            status=sr_row["status"],
            config_name=sr_row["config_name"],
            submitted_by=sr_row["submitted_by"],
            submitted_at=sr_row["submitted_at"],
            results_message_id=sr_row["results_message_id"],
        )

        # Load driver rows from new tables (QualifyingSessionResult / RaceSessionResult)
        session_type = SessionType(session_result.session_type)
        driver_rows = await _load_driver_rows(db_path, session_result.id, session_type)

        points_map = {
            r.driver_user_id: r.points_awarded + getattr(r, "fastest_lap_bonus", 0)
            for r in driver_rows
        }

        await post_session_results(
            db_path, session_result, driver_rows, points_map, results_channel, guild,
            round_number, track_name, label, is_sprint, bot=bot,
        )


#: What the repost needs of a channel before it starts, by the names Discord's own
#: interface uses. ``view_channel`` is listed even though a bot that cannot see a channel
#: usually cannot send to it either: the two are separate permissions and Discord will
#: report ``send_messages`` as granted on a channel the bot cannot read, so checking only
#: the latter would pass a channel nothing can be posted to.
#:
#: ``read_message_history`` is here because a repost *replaces* rather than appends — it
#: finds what it posted last with ``fetch_message``, which needs it. ``manage_messages`` is
#: deliberately absent: the bot only ever deletes its own messages, which needs no
#: permission at all.
_REPOST_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("view_channel", "View Channel"),
    ("send_messages", "Send Messages"),
    ("read_message_history", "Read Message History"),
)

#: Asked of a channel that may receive a graphic, in addition to the above.
_ATTACHMENT_PERMISSION: tuple[str, str] = ("attach_files", "Attach Files")


def _bot_member(guild: "discord.Guild", bot):
    """The bot's own member object in *guild*, or ``None`` where it cannot be resolved.

    **``guild.me`` is deliberately not used here** (#187). It is a property reading
    ``self._state.user.id``, so where the client has no user yet it raises
    ``AttributeError`` rather than returning ``None`` — an exception thrown out of a
    pre-flight check whose whole purpose is to refuse cleanly, which is the one thing it
    must not do. ``signup_cog`` already takes this guarded form before it does permission
    arithmetic (``src/cogs/signup_cog.py:870``), and it is the form this check follows.

    Resolving it is not optional: without a member there is no permission arithmetic to do,
    and both callers treat ``None`` as a fault rather than as leave to assume.
    """
    bot_user = getattr(bot, "user", None) if bot is not None else None
    if bot_user is None:
        return None
    return guild.get_member(bot_user.id)


def _channel_fault(
    guild: discord.Guild,
    bot_member,
    division_name: str,
    setting: str,
    channel_id: int,
    *,
    needs_attachment: bool,
) -> str | None:
    """The fault standing between the bot and posting to *channel_id*, or None.

    Reads the gateway cache rather than calling Discord, which is what makes it usable as a
    gate: it is the same reading ``/division results-channel`` already takes before it
    accepts a channel.
    """
    channel = guild.get_channel(channel_id)
    if channel is None:
        return missing_channel_fault(division_name, setting, channel_id)

    if not isinstance(channel, discord.TextChannel):
        label = SETTING_LABELS.get(setting, setting)
        return (
            f"**{division_name}** — the {label} channel <#{channel_id}> is not a text "
            f"channel, so nothing can be posted in it."
        )

    permissions = channel.permissions_for(bot_member)
    wanted = list(_REPOST_PERMISSIONS)
    if needs_attachment:
        wanted.append(_ATTACHMENT_PERMISSION)
    missing = [name for attr, name in wanted if not getattr(permissions, attr, False)]
    if missing:
        return unpostable_channel_fault(division_name, setting, channel_id, missing)
    return None


async def repost_channel_faults(
    db_path: str,
    season_id: int,
    guild: "discord.Guild | None",
    bot=None,
) -> list[str]:
    """What stands between this season and reposting every division's results (#187).

    Returns the faults as lines a league can read, and an empty list where every division's
    configured channels are there and can be posted to.

    **Read before anything is written, so that an amendment is refused entire rather than
    half-made.** The approval of a mid-season amendment overwrites the season's points and
    then reposts every round of every division against the new numbers. A channel that has
    been deleted, or one the bot's Send Messages has been revoked on, used to be discovered
    only once the points were already overwritten — and then swallowed, so the league was
    told the championship had been republished when none of it had. Establishing it first
    turns that into a refusal that changes nothing.

    **A channel a division never configured is not a fault.** It has nothing posted for it
    and the cascade is right to skip it in silence; reporting it would refuse leagues that
    are correctly configured. Only a channel the league *did* configure and the server no
    longer holds, or holds and will not accept a posting in, is reported here.

    **The bot's own member object is required, not assumed.** Without it there is no
    permission arithmetic to do, and guessing would defeat the point of the gate — so an
    unresolvable bot member is itself a fault rather than a reason to wave the season
    through.
    """
    bot_member, faults = _repost_gate(guild, bot)
    if faults:
        return faults

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT d.id, d.name,
                   drc.results_channel_id, drc.standings_channel_id
            FROM divisions d
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            WHERE d.season_id = ? AND d.status != 'CANCELLED'
            ORDER BY d.tier, d.id
            """,
            (season_id,),
        )
        division_rows = await cursor.fetchall()

    return await _channel_faults_for_rows(division_rows, guild, bot_member, bot)


def _repost_gate(guild: "discord.Guild | None", bot):
    """The bot member the permission arithmetic needs, or the fault standing in its way.

    Shared by the season-wide and division-wide gates so that the two cannot drift: both
    refuse a guild they cannot reach, and both treat an unresolvable bot member as a fault
    rather than as leave to assume (#187).
    """
    if guild is None:
        return None, [
            "The bot is not in this server, so nothing can be reposted. "
            "Check that it is still a member and try again."
        ]

    bot_member = _bot_member(guild, bot)
    if bot_member is None:
        return None, [
            "The bot cannot read its own permissions in this server, so it cannot tell "
            "whether the reposting would succeed. Try again in a moment."
        ]

    return bot_member, []


def _cascade_channel_fault(
    guild, bot_member, division_name: str, setting: str, channel_id: int,
    *, needs_attachment: bool,
) -> str | None:
    """The fault standing between the cascade and *channel_id*, or None (#237).

    **Deliberately more permissive than the pre-flight**, because it runs where the posting
    cannot be refused. ``repost_channel_faults`` gates an amendment *before* a row is
    overwritten, so it is right to refuse whenever it cannot satisfy itself — an
    unresolvable bot member, a channel that is not a ``TextChannel``. Here the penalty is
    already applied and the round has already moved on, so every such refusal would itself
    become "the results were not reposted", which is the outcome this issue exists to
    prevent. It reports only what is *positively* wrong.

    Two differences follow. Without a bot member there is no permission arithmetic to do,
    and its absence is not itself a fault. And the pre-flight's ``isinstance`` check is
    dropped: a channel of an unexpected type is left to the posting to reject, where the
    failure surfaces as an answered command (#156) rather than as a repost refused on
    suspicion.

    What is checked in every case is that the channel still *exists* — the silent case this
    issue is about, and the one that cost a league its posted results.

    **Attach Files is still asked for, and the over-approximation behind it is kept on
    purpose** (raised in review of #237, decided 2026-09-20). ``aspect_attaches_files``
    answers "could this channel ever be sent a file" from the module switch and the aspect
    toggle, not from template validity, so it asks for the permission even where a broken
    template would have fallen back to text and posted fine. Dropping it here would fix that
    narrow false refusal and open a far worse one: a league with the aspect on and a
    *valid* template, missing Attach Files, would have its posted results deleted and the
    replacement rejected by Discord — which is the data loss this whole issue is about. The
    two errors are not symmetrical, so the check errs the way the rest of this function
    does: toward the league keeping what it already has.
    """
    channel = guild.get_channel(channel_id)
    if channel is None:
        return missing_channel_fault(division_name, setting, channel_id)

    if bot_member is None:
        return None

    permissions = channel.permissions_for(bot_member)
    wanted = list(_REPOST_PERMISSIONS)
    if needs_attachment:
        wanted.append(_ATTACHMENT_PERMISSION)
    missing = [name for attr, name in wanted if not getattr(permissions, attr, False)]
    if missing:
        return unpostable_channel_fault(division_name, setting, channel_id, missing)
    return None


async def _channel_faults_for_rows(division_rows, guild, bot_member, bot) -> list[str]:
    """The faults across *division_rows*, each row carrying a division's two channel ids."""
    # Asked of the image module rather than read out of its configuration here: a posting
    # service hands the image module an occasion and acts on what comes back, and does not
    # read its settings (#187, and the layering
    # `tests/integration/test_image_module_flow.py` holds).
    from services.image_validity_service import aspect_attaches_files

    results_graphics = await aspect_attaches_files(bot, "results")
    standings_graphics = await aspect_attaches_files(bot, "standings")

    faults: list[str] = []
    for row in division_rows:
        division_name = row["name"] or f"division {row['id']}"
        for setting, channel_id, needs_attachment in (
            ("results", row["results_channel_id"], results_graphics),
            ("standings", row["standings_channel_id"], standings_graphics),
        ):
            if not channel_id:
                continue
            fault = _channel_fault(
                guild,
                bot_member,
                division_name,
                setting,
                int(channel_id),
                needs_attachment=needs_attachment,
            )
            if fault is not None:
                faults.append(fault)
    return faults


def merge_faults(*fault_lists: list[str]) -> list[str]:
    """The faults of several reposts as one list, in order, without repeating a line.

    ``delete_and_repost_final_results`` and ``repost_subsequent_standings`` run one after
    the other over the *same* division, so a standings channel that has gone missing is
    found by both and worded identically by both (raised in review of #237). Concatenating
    handed the manager the same bullet twice, reading as two separate problems to repair.
    Each function already refuses to repeat itself internally; this keeps that true across
    the pair.
    """
    merged: list[str] = []
    for faults in fault_lists:
        for line in faults:
            if line not in merged:
                merged.append(line)
    return merged


async def results_sync_hint(db_path: str, division_id: int) -> str:
    """The line telling a manager how to finish a repost that did not land (#237).

    Names both sync commands, because the cascade posts two things a league reads
    separately — the round's own results and the division's standings — and each has its
    own command. Modelled on ``attendance_service.sync_hint``, which does the same for a
    run of sanctions that did not all apply (#239).

    **Except once the season is pending completion**, where both sync commands are refused
    (issue #224) and naming them would send a manager to a door that will not open. The one
    repost still available there is `/round results amend` itself, which is also the only
    thing that can have failed: it replaces the round's own results *and* every later
    round's standings, so re-running it after the channel is repaired recovers the whole of
    what was lost. The stage is read here rather than passed in, so that none of the three
    callers has to know the rule.
    """
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT d.name AS name, s.stage AS stage FROM divisions d "
                "JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
        ).fetchone()
    name = (row["name"] if row is not None else None) or f"division {division_id}"
    stage = row["stage"] if row is not None else None
    if stage == SeasonStage.PENDING_COMPLETION.value:
        return (
            f"Repair the cause, then amend the round again with `/round results amend "
            f"division_name:{name}` — every division of this season is done, so the sync "
            f"commands are closed and the amendment is what reposts."
        )
    return (
        f"Repair the cause, then run `/results rounds sync division:{name}` and "
        f"`/results standings sync division:{name}`."
    )


async def repost_round_results(
    db_path: str,
    round_id: int,
    division_id: int,
    guild: discord.Guild,
    label: str | None = None,
    *,
    bot=None,
) -> list[str]:
    """Load the division's channels and repost/edit round results and standings.

    Returns the faults it met, as lines a league can read, and an empty list where
    everything it was asked to do was done.

    **The label is derived from the round, not demanded of the caller** (#130). Every
    caller reposts rounds it does not choose — the amendment cascade walks a whole
    season at once, and its rounds sit at different lifecycle stages — so no single
    label a caller could pass would be right for all of them. Omitting *label* takes
    the round's own status through ``_label_from_status``, which is what the two sync
    commands do. An explicit *label* still wins, for a caller that means to override it.

    **A round with no ACTIVE session results is skipped entirely.** Nothing was posted
    for it, so there is nothing to repost. ``post_round_results`` already guards itself
    this way but ``post_standings`` does not, and the amendment cascade walks every
    non-cancelled round of the division — future ones included. Without this guard,
    approving an amendment would post standings for rounds that have not been raced.

    **A configured channel that has gone missing is a fault, not a silence** (#187). The
    two channel guards below used to be bare truthiness tests, which conflated a channel
    the league never configured with one it configured and has since deleted. The first
    is no business of this function's — a division with no standings channel has nothing
    posted for it and is right to be skipped without a word. The second is a fault, and
    swallowing it is what let a failed amendment cascade report success: no exception is
    raised by a channel that simply is not there, so the caller's ``except`` never ran
    and not even the host's log file recorded anything. ``repost_results_for_division``
    already warns in exactly this case; this function was the outlier.

    The missing channel is reported this way rather than raised because a repost runs after
    the thing it reports on has already happened: making this raise would turn a stale
    channel into a failed penalty. Every caller now has somewhere to put the return —
    ``penalty_service`` posts it to the log channel, and the two review approvals in
    ``result_submission_service`` tell the approving manager as well (#237). One caller
    still discards it — ``amendment_service.approve_amendment`` — and is left alone
    deliberately: the amendment path is gated by ``repost_channel_faults`` before it writes
    anything, so a fault there has already been reported and refused upstream.
    """
    faults: list[str] = []
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT d.season_id, d.name AS division_name,
                   drc.results_channel_id, drc.standings_channel_id,
                   r.round_number, r.track_name, r.status
            FROM divisions d
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            JOIN rounds r ON r.id = ?
            WHERE d.id = ?
            """,
            (round_id, division_id),
        )
        row = await cursor.fetchone()

    if row is None:
        log.warning("repost_round_results: division %s not found", division_id)
        return faults

    division_name: str = row["division_name"] or f"division {division_id}"
    results_ch_id: int | None = row["results_channel_id"]
    standings_ch_id: int | None = row["standings_channel_id"]
    round_number: int = row["round_number"]
    track_name: str = row["track_name"] or "Unknown"

    if label is None:
        label = _label_from_status(row["status"] or "")

    if not await _round_has_posted_results(db_path, round_id):
        log.debug(
            "repost_round_results: round %s has no ACTIVE session results — nothing to repost",
            round_id,
        )
        return faults

    if results_ch_id:
        rc = guild.get_channel(results_ch_id)
        if rc is None:
            log.warning(
                "repost_round_results: results channel %s not found in guild", results_ch_id
            )
            faults.append(missing_channel_fault(division_name, "results", results_ch_id))
        else:
            await post_round_results(db_path, round_id, division_id, rc, guild, label, bot=bot)

    if standings_ch_id:
        sc = guild.get_channel(standings_ch_id)
        if sc is None:
            log.warning(
                "repost_round_results: standings channel %s not found in guild", standings_ch_id
            )
            faults.append(missing_channel_fault(division_name, "standings", standings_ch_id))
        else:
            driver_snaps = await driver_standings_for_display(
                db_path, division_id, round_id, guild, bot
            )
            team_snaps = await standings_service.compute_team_standings(
                db_path, division_id, round_id
            )
            # Check reserves visibility flag
            show_reserves = await _get_show_reserves(db_path, division_id)
            await post_standings(
                db_path, division_id, round_id, round_number, track_name, sc,
                driver_snaps, team_snaps, guild, show_reserves, label, bot=bot,
            )

    return faults


async def _round_has_posted_results(db_path: str, round_id: int) -> bool:
    """Whether *round_id* has any ACTIVE session results — i.e. whether it has been raced.

    The same condition the two sync commands select on, so a repost covers exactly the
    rounds a resynchronisation would.
    """
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM session_results WHERE round_id = ? AND status = 'ACTIVE' LIMIT 1",
                (round_id,),
            )
        ).fetchone()
    return row is not None


async def _get_show_reserves(db_path: str, division_id: int) -> bool:
    """Return the reserves_in_standings flag for a division (default True)."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT reserves_in_standings FROM division_results_config WHERE division_id = ?",
            (division_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return True
    val = row["reserves_in_standings"]
    return bool(val) if val is not None else True


async def repost_results_for_division(
    db_path: str,
    division_id: int,
    guild: discord.Guild,
    *,
    bot=None,
) -> str:
    """Repost every round's session results, in round order, then take the old ones down.

    **The replacement is produced before the original is destroyed** (Constitution XIV.8, and
    #345). Every round's new messages are posted first, in round order, and only once all of
    them are up are the messages they replace deleted. It used to go the other way, one session
    at a time, which meant a failure part-way left the division's channel holding the rounds it
    had reached and nothing for the rest — with the originals already gone.

    Two things follow. The channel briefly carries **both** copies, the new set beneath the old,
    for as long as the rebuild takes; that is accepted, and it resolves itself. And a failure
    before the deletions raises with the originals untouched, so the caller can take the new
    messages down again and leave the league exactly what it had.

    Returns one of three status strings:
    - ``"ok"``         — results reposted successfully
    - ``"no_rounds"``  — no completed rounds exist for this division
    - ``"no_channel"`` — no results channel is configured for the division
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT d.id, drc.results_channel_id
            FROM divisions d
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            WHERE d.id = ?
            """,
            (division_id,),
        )
        div_row = await cursor.fetchone()

    if div_row is None:
        return "no_rounds"

    results_ch_id: int | None = div_row["results_channel_id"]
    if not results_ch_id:
        return "no_channel"

    rc = guild.get_channel(results_ch_id)
    if rc is None:
        log.warning(
            "repost_results_for_division: results channel %s not found in guild",
            results_ch_id,
        )
        return "no_channel"

    # Fetch all rounds with at least one ACTIVE session, ordered by round_number
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT r.id AS round_id, r.round_number, r.track_name, r.format,
                   r.status
            FROM rounds r
            JOIN session_results sr ON sr.round_id = r.id
            WHERE r.division_id = ? AND sr.status = 'ACTIVE'
            ORDER BY r.round_number
            """,
            (division_id,),
        )
        round_rows = await cursor.fetchall()

    if not round_rows:
        return "no_rounds"

    # ── Produce ───────────────────────────────────────────────────────────
    # Each session's superseded posting, remembered while the replacement goes up. Nothing is
    # deleted until every round has been reposted, so a failure part-way leaves the league the
    # results it already had rather than a channel half rebuilt.
    superseded: list[tuple[int, int, list[int] | None]] = []

    for rnd in round_rows:
        round_id: int = rnd["round_id"]
        round_number: int = rnd["round_number"]
        track_name: str = rnd["track_name"] or "Unknown"
        is_sprint: bool = str(rnd["format"]).upper() == "SPRINT"
        rnd_label: str = _label_from_status(rnd["status"] or "")

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, round_id, division_id, session_type, status, config_name,
                       submitted_by, submitted_at, results_message_id,
                       results_message_ids
                FROM session_results
                WHERE round_id = ? AND status = 'ACTIVE'
                ORDER BY id
                """,
                (round_id,),
            )
            session_rows = await cursor.fetchall()

        for sr_row in session_rows:
            session_result = _sr_from_row(sr_row)

            old_msg_id: int | None = sr_row["results_message_id"]
            if old_msg_id is not None:
                superseded.append(
                    (sr_row["id"], old_msg_id, _parse_ids(sr_row["results_message_ids"]))
                )

            # The stored id is cleared **before** the repost, so `post_session_results` writes
            # a new one rather than believing it is editing the message it is replacing. The
            # id itself is already held in `superseded`, so clearing it loses nothing.
            if old_msg_id is not None:
                async with get_connection(db_path) as db:
                    await db.execute(
                        "UPDATE session_results SET results_message_id = NULL, "
                        "results_message_ids = NULL WHERE id = ?",
                        (sr_row["id"],),
                    )
                    await db.commit()

            driver_rows = await _load_driver_rows(db_path, sr_row["id"], SessionType(sr_row["session_type"]))
            points_map = {
                r.driver_user_id: r.points_awarded + getattr(r, "fastest_lap_bonus", 0)
                for r in driver_rows
            }

            await post_session_results(
                db_path, session_result, driver_rows, points_map, rc, guild,
                round_number, track_name, rnd_label, is_sprint, bot=bot,
            )
            await throttle()

    # ── Then destroy ──────────────────────────────────────────────────────
    # Every replacement is up. The originals go now, oldest first, so the channel reads in
    # round order throughout rather than shuffling as it empties.
    for _session_id, old_msg_id, old_ids in superseded:
        await _delete_posting(rc, old_msg_id, old_ids, label="results message")

    return "ok"


async def repost_standings_for_division(
    db_path: str,
    division_id: int,
    guild: discord.Guild,
    *,
    bot=None,
) -> str:
    """Repost every round's standings, in round order, then take the old ones down.

    **The replacement is produced before the original is destroyed** (Constitution XIV.8,
    #345), as :func:`repost_results_for_division` now does for results — and for the same
    reason: a rebuild that deleted as it went left a failure part-way through with the rounds
    it had reached rebuilt and the rest destroyed with nothing put back.

    The stored ids are forgotten before each repost so that :func:`post_standings` inserts a
    new message rather than editing the one it is replacing, and the ids themselves are held
    until the deletion pass at the end.

    Returns one of three status strings for the caller to surface to the admin:
    - ``"ok"``         — standings reposted successfully
    - ``"no_rounds"``  — no completed rounds exist for this division
    - ``"no_channel"`` — no standings channel is configured for the division
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT r.id AS round_id, r.round_number, r.track_name,
                   r.status, drc.standings_channel_id
            FROM rounds r
            JOIN session_results sr ON sr.round_id = r.id
            LEFT JOIN division_results_config drc ON drc.division_id = r.division_id
            WHERE r.division_id = ?
              AND sr.status = 'ACTIVE'
            ORDER BY r.round_number
            """,
            (division_id,),
        )
        rows = await cursor.fetchall()

    if not rows:
        return "no_rounds"

    standings_ch_id: int | None = rows[0]["standings_channel_id"]
    if not standings_ch_id:
        return "no_channel"

    sc = guild.get_channel(standings_ch_id)
    if sc is None:
        log.warning(
            "repost_standings_for_division: standings channel %s not found in guild",
            standings_ch_id,
        )
        return "no_channel"

    show_reserves = await _get_show_reserves(db_path, division_id)

    # ── Produce ───────────────────────────────────────────────────────────
    # Each round's superseded standings, remembered while the replacements go up.
    superseded: list[tuple[int, list[int] | None]] = []

    for row in rows:
        round_id: int = row["round_id"]
        round_number: int = row["round_number"]
        track_name: str = row["track_name"] or "Unknown"
        rsd_label: str = _label_from_status(row["status"] or "")

        superseded.extend(
            await _forget_standings_messages(db_path, division_id, round_id)
        )

        driver_snaps = await driver_standings_for_display(
            db_path, division_id, round_id, guild, bot
        )
        team_snaps = await standings_service.compute_team_standings(
            db_path, division_id, round_id
        )
        await post_standings(
            db_path, division_id, round_id, round_number, track_name, sc,
            driver_snaps, team_snaps, guild, show_reserves, rsd_label, bot=bot,
        )
        await throttle()

    # ── Then destroy ──────────────────────────────────────────────────────
    for anchor, chunk_ids in superseded:
        await _delete_posting(sc, anchor, chunk_ids, label="standings message")

    return "ok"


# ---------------------------------------------------------------------------
# Finalization helpers (T021, T021b)
# ---------------------------------------------------------------------------

async def delete_and_repost_final_results(
    db_path: str,
    round_id: int,
    division_id: int,
    guild: discord.Guild,
    label: str,
    *,
    bot=None,
) -> list[str]:
    """Delete all interim results/standings Discord messages for *round_id* and
    repost the final (post-penalty) versions.

    Returns the faults it met, as lines a league can read, and an empty list where
    everything it was asked to do was done — the same contract
    :func:`repost_round_results` keeps.

    For each non-cancelled session:
    1. Fetch ``results_message_id`` from ``session_results``.
    2. Delete that Discord message if it still exists.
    3. Post the corrected final results table and store the new ``results_message_id``.

    Then for standings:
    4. Find the current ``standings_message_id`` for this round.
    5. Delete that Discord message if it still exists.
    6. Post fresh final standings and update ``standings_message_id``.

    **A channel is gated before anything of its is deleted** (#237). The order above
    destroys the league's copy before it has established that a new one can be put in its
    place, so a channel that has been deleted, or one the bot's Send Messages has since been
    revoked on, used to leave the round with no results posted at all — silently, because
    ``guild.get_channel`` returning ``None`` raises nothing and the caller went on to log the
    approval as a success. Reading the channel first turns the worst case back into "the
    league keeps what it already had", which is why the gate is here rather than in the
    caller: every route into this function has the same window.

    The gate is ``_cascade_channel_fault``, which is **not** the pre-flight's
    ``_channel_fault`` — see its docstring for why the two differ. A channel that is merely
    unconfigured stays silent, while one that is configured and unreachable is reported
    (#187's rule against over-reporting, kept).
    """
    faults: list[str] = []

    async with get_connection(db_path) as db:
        ctx_cursor = await db.execute(
            """
            SELECT r.round_number, r.track_name, d.name AS division_name,
                   drc.results_channel_id, drc.standings_channel_id,
                   drc.reserves_in_standings
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            LEFT JOIN division_results_config drc ON drc.division_id = r.division_id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        ctx = await ctx_cursor.fetchone()

    if ctx is None:
        log.warning("delete_and_repost_final_results: round %s not found", round_id)
        return [
            "The round could not be read from the database, so its results and standings "
            "were not reposted."
        ]

    round_number: int = ctx["round_number"]
    track_name: str = ctx["track_name"] or "Unknown"
    division_name: str = ctx["division_name"] or f"division {division_id}"
    results_ch_id: int | None = ctx["results_channel_id"]
    standings_ch_id: int | None = ctx["standings_channel_id"]
    show_reserves: bool = bool(ctx["reserves_in_standings"]) if ctx["reserves_in_standings"] is not None else True

    # A round with no ACTIVE session results had nothing posted for it, so no channel
    # fault is owed — the guard ``repost_round_results`` already applies, kept here so the
    # two functions agree on what counts as a fault (raised in review of #237).
    if not await _round_has_posted_results(db_path, round_id):
        log.debug(
            "delete_and_repost_final_results: round %s has no ACTIVE session results",
            round_id,
        )
        return faults

    bot_member = _bot_member(guild, bot)
    results_graphics = standings_graphics = False
    if bot_member is not None:
        from services.image_validity_service import aspect_attaches_files

        results_graphics = await aspect_attaches_files(bot, "results")
        standings_graphics = await aspect_attaches_files(bot, "standings")

    # ── Delete interim results messages and re-post final ──────────────────
    if results_ch_id:
        results_fault = _cascade_channel_fault(
            guild, bot_member, division_name, "results", int(results_ch_id),
            needs_attachment=results_graphics,
        )
        if results_fault is not None:
            faults.append(results_fault)
        else:
            rc = guild.get_channel(results_ch_id)
            # Fetch session rows with their existing message IDs
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT id, round_id, division_id, session_type, status,
                           config_name, submitted_by, submitted_at, results_message_id,
                           results_message_ids
                    FROM session_results
                    WHERE round_id = ? AND status = 'ACTIVE'
                    ORDER BY id
                    """,
                    (round_id,),
                )
                session_rows = await cursor.fetchall()

            is_sprint = await _is_sprint_round(db_path, round_id)

            for sr_row in session_rows:
                session_result = _sr_from_row(sr_row)

                # Delete old interim Discord message
                old_msg_id: int | None = sr_row["results_message_id"]
                if old_msg_id is not None:
                    await _delete_posting(
                        rc, old_msg_id, _parse_ids(sr_row["results_message_ids"]),
                        label="interim results message",
                    )

                    # Clear stale message_id so post_session_results inserts a fresh one
                    async with get_connection(db_path) as db:
                        await db.execute(
                            "UPDATE session_results SET results_message_id = NULL, "
                            "results_message_ids = NULL WHERE id = ?",
                            (sr_row["id"],),
                        )
                        await db.commit()

                # Load updated driver rows
                driver_rows = await _load_driver_rows(db_path, sr_row["id"], SessionType(sr_row["session_type"]))
                points_map = {
                    r.driver_user_id: r.points_awarded + getattr(r, "fastest_lap_bonus", 0)
                    for r in driver_rows
                }

                await post_session_results(
                    db_path, session_result, driver_rows, points_map, rc, guild,
                    round_number, track_name, label, is_sprint, bot=bot,
                )

    # ── Delete interim standings message and re-post final ─────────────────
    if standings_ch_id:
        standings_fault = _cascade_channel_fault(
            guild, bot_member, division_name, "standings", int(standings_ch_id),
            needs_attachment=standings_graphics,
        )
        if standings_fault is not None:
            faults.append(standings_fault)
        else:
            sc = guild.get_channel(standings_ch_id)
            # Both championships' interim messages go, whichever flow posted them.
            await _clear_standings_messages(db_path, division_id, round_id, sc)

            driver_snaps = await driver_standings_for_display(
                db_path, division_id, round_id, guild, bot
            )
            team_snaps = await standings_service.compute_team_standings(
                db_path, division_id, round_id
            )
            await post_standings(
                db_path, division_id, round_id, round_number, track_name,
                sc, driver_snaps, team_snaps, guild, show_reserves, label, bot=bot,
            )

    return faults


async def repost_subsequent_standings(
    db_path: str,
    division_id: int,
    from_round_id: int,
    guild: discord.Guild,
    *,
    bot=None,
) -> list[str]:
    """Cascade-recompute standings and repost Discord standings messages for all
    rounds *after* *from_round_id* in the division that have an existing
    ``standings_message_id``.

    This is called after :func:`delete_and_repost_final_results` so that
    subsequent rounds' standings reflect any penalty-driven point changes.

    Returns the faults it met, as lines a league can read, and an empty list where
    everything it was asked to do was done.

    **The channel is gated before any round's standings are cleared** (#237), for the
    reason given on :func:`delete_and_repost_final_results`: this function deletes each
    round's standings before reposting them, and an unreachable channel used to be skipped
    by a bare ``continue`` — leaving every later round of the division with its standings
    deleted and nothing put back, with nobody told.

    **The fault is reported once, not once per round.** The standings channel is a division
    setting, so every round in the loop would raise the identical line; the rounds that went
    unreposted because of it are named together in a line of their own instead.
    """
    faults: list[str] = []

    # Cascade recompute DB snapshots for all subsequent rounds, ordered on the names the
    # reposts below will draw.
    await recompute_standings_from_round(db_path, division_id, from_round_id, guild, bot)

    # Find subsequent rounds that have standings messages posted
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.id AS round_id, r.round_number, r.track_name, r.status,
                   d.name AS division_name,
                   drc.standings_channel_id, drc.reserves_in_standings
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            LEFT JOIN division_results_config drc ON drc.division_id = r.division_id
            WHERE r.division_id = ?
              AND r.round_number > (SELECT round_number FROM rounds WHERE id = ?)
              AND r.status != 'CANCELLED'
            ORDER BY r.round_number
            """,
            (division_id, from_round_id),
        )
        rounds = await cursor.fetchall()

    bot_member = _bot_member(guild, bot)
    standings_graphics = False
    if bot_member is not None:
        from services.image_validity_service import aspect_attaches_files

        standings_graphics = await aspect_attaches_files(bot, "standings")

    gated: dict[int, str | None] = {}
    skipped_rounds: list[int] = []

    for rnd in rounds:
        rnd_id: int = rnd["round_id"]
        rnd_number: int = rnd["round_number"]
        rnd_track: str = rnd["track_name"] or "Unknown"
        rnd_label: str = _label_from_status(rnd["status"] or "")
        standings_ch_id: int | None = rnd["standings_channel_id"]
        show_reserves: bool = bool(rnd["reserves_in_standings"]) if rnd["reserves_in_standings"] is not None else True

        if not standings_ch_id:
            continue

        # "Has this round been posted?" is answered by *either* championship holding a
        # message, not by the drivers column alone: the image flow can leave the two in
        # different states, and a round whose constructors graphic stands would otherwise
        # never be recomputed.
        posted = [
            await _get_standings_message_id(db_path, division_id, rnd_id, championship)
            for championship in (STANDINGS_DRIVERS, STANDINGS_CONSTRUCTORS)
        ]
        if not any(msg_id is not None for msg_id in posted):
            continue  # No standings message posted for this round — skip

        if standings_ch_id not in gated:
            gated[standings_ch_id] = _cascade_channel_fault(
                guild, bot_member, rnd["division_name"] or f"division {division_id}",
                "standings", int(standings_ch_id),
                needs_attachment=standings_graphics,
            )
        channel_fault = gated[standings_ch_id]
        if channel_fault is not None:
            if channel_fault not in faults:
                faults.append(channel_fault)
            skipped_rounds.append(rnd_number)
            continue

        sc = guild.get_channel(standings_ch_id)

        # Delete old standings message(s) for both championships and forget their ids
        await _clear_standings_messages(db_path, division_id, rnd_id, sc)

        # Repost fresh standings
        driver_snaps = await driver_standings_for_display(
            db_path, division_id, rnd_id, guild, bot
        )
        team_snaps = await standings_service.compute_team_standings(
            db_path, division_id, rnd_id
        )
        await post_standings(
            db_path, division_id, rnd_id, rnd_number, rnd_track,
            sc, driver_snaps, team_snaps, guild, show_reserves, rnd_label, bot=bot,
        )

    if skipped_rounds:
        listed = ", ".join(f"round {n}" for n in skipped_rounds)
        faults.append(
            f"The standings of {listed} were not reposted, so they still show the points "
            f"as they stood before."
        )

    return faults


# ---------------------------------------------------------------------------
# Private helpers for finalization
# ---------------------------------------------------------------------------

async def _is_sprint_round(db_path: str, round_id: int) -> bool:
    """Return True if *round_id* is a SPRINT-format round."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT format FROM rounds WHERE id = ?", (round_id,)
        )
        row = await cursor.fetchone()
    return row is not None and str(row["format"]).upper() == "SPRINT"


def _sr_from_row(sr_row) -> "SessionResult":
    """Construct a :class:`SessionResult` from a DB row dict."""
    from models.session_result import SessionResult
    return SessionResult(
        id=sr_row["id"],
        round_id=sr_row["round_id"],
        division_id=sr_row["division_id"],
        session_type=sr_row["session_type"],
        status=sr_row["status"],
        config_name=sr_row["config_name"],
        submitted_by=sr_row["submitted_by"],
        submitted_at=sr_row["submitted_at"],
        results_message_id=sr_row["results_message_id"],
    )




async def replay_division_channels(
    db_path: str,
    division_id: int,
    from_round_id: int,
    guild: discord.Guild,
    *,
    bot=None,
    verdict_state_factory=None,
) -> list[str]:
    """Rebuild everything a division's channels show, in the order a league reads them.

    What the amendment replay calls once its corrected round has been computed (#345). The
    five stages run in the order the specification states — **results, standings, the
    attendance sheet, the report verdicts, then the appeal verdicts** — and each rebuilds the
    *whole* division rather than the amended round alone.

    **Why the whole division.** Amending round 1 of five reposts round 1, and a repost is a new
    message at the bottom of the channel: the results channel would then read 2, 3, 4, 5, 1.
    Reposting every round in round order is what keeps the sequence a league reads matching the
    sequence it raced.

    **Every stage produces before it destroys** (Constitution XIV.8). Within each channel the
    replacements go up first and the superseded messages come down afterwards, so a failure
    leaves the league the board it had rather than half of two.

    **The attendance sheet is not a sequence.** A division keeps one live sheet in one slot, so
    it is reposted once, against the round the running totals now stand at — which is the
    latest round, not the amended one. The caller does that, this function does not touch it;
    it is named here so the order is readable in one place.

    Returns the faults met across every stage, merged, as lines a league can read.
    """
    faults: list[str] = []

    results_status = await repost_results_for_division(
        db_path, division_id, guild, bot=bot
    )
    if results_status == "no_channel":
        faults.append(
            "the division's results channel could not be reached, so its results were "
            "not reposted"
        )

    standings_status = await repost_standings_for_division(
        db_path, division_id, guild, bot=bot
    )
    if standings_status == "no_channel":
        faults.append(
            "the division's standings channel could not be reached, so its standings were "
            "not reposted"
        )

    if bot is not None and verdict_state_factory is not None:
        from services.verdict_announcement_service import republish_verdicts_from_round

        faults.extend(
            await republish_verdicts_from_round(
                bot, db_path, division_id, from_round_id, verdict_state_factory
            )
        )

    return merge_faults(faults, [])
