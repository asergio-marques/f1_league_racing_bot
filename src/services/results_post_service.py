"""results_post_service.py — Post and edit results/standings in Discord channels."""
from __future__ import annotations

import logging

import discord

from db.database import get_connection
from models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from models.session_result import (
    DriverSessionResult,
    OutcomeModifier,
    QualifyingSessionResult,
    RaceSessionResult,
    SessionResult,
)
from models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot
from services import standings_service
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


async def _send_chunked(channel: discord.TextChannel, content: str) -> discord.Message:
    """Send *content* to *channel*, splitting into multiple messages if needed.

    Returns the first (header) message so its ID can be persisted.
    """
    chunks = _split_content(content)
    first_msg = await channel.send(chunks[0])
    for chunk in chunks[1:]:
        await channel.send(chunk)
    return first_msg


async def _delete_with_continuations(
    channel: discord.TextChannel,
    anchor_msg_id: int,
    label: str = "message",
) -> None:
    """Delete the anchor message and any immediately-following bot continuation messages.

    When a result/standings post exceeds 2000 chars it is split into multiple
    consecutive messages via :func:`_send_chunked`.  Only the first message ID is
    persisted; this helper deletes it *and* any subsequent messages in the same
    channel that were authored by the same user (the bot), stopping as soon as it
    encounters a message from someone else.

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


def _label_from_status(result_status: str) -> str:
    """Map a round result_status value to the user-visible lifecycle label."""
    return {
        "PROVISIONAL": "Provisional Results",
        "POST_RACE_PENALTY": "Post-Race Penalty Results",
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
) -> int:
    """Format and send a single session result. Returns the Discord message ID."""
    session_type = SessionType(session_result.session_type)
    session_label = results_formatter.format_session_label(session_type, is_sprint=is_sprint)

    user_ids = [r.driver_user_id for r in driver_rows]
    test_display = await _build_test_driver_display(db_path, user_ids)

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
    msg = await _send_chunked(results_channel, f"{heading}\n{label}\n{table}")

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE session_results SET results_message_id = ? WHERE id = ?",
            (msg.id, session_result.id),
        )
        await db.commit()

    return msg.id


# ---------------------------------------------------------------------------
# Standings posting
# ---------------------------------------------------------------------------

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
) -> None:
    """Format and post (or edit-in-place) the driver and team standings."""
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
    primary_session_label = await _get_primary_session_label(db_path, round_id)
    heading = f"**{season_prefix}{division_name} Round {round_number} — {primary_session_label}**"
    content = (
        f"{heading}\n{label}\n\n"
        f"**Driver Standings**\n{driver_text}\n\n"
        f"**Team Standings**\n{team_text}"
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

    if sent_msg is None:
        sent_msg = await _send_chunked(standings_channel, content)

    # Persist the message ID on the top-ranked driver snapshot
    if driver_snapshots:
        top_driver = min(driver_snapshots, key=lambda s: s.standing_position)
        async with get_connection(db_path) as db:
            await db.execute(
                """
                UPDATE driver_standings_snapshots
                SET standings_message_id = ?
                WHERE round_id = ? AND division_id = ? AND driver_user_id = ?
                """,
                (sent_msg.id, round_id, division_id, top_driver.driver_user_id),
            )
            await db.commit()


async def _get_standings_message_id(
    db_path: str, division_id: int, round_id: int
) -> int | None:
    """Return the standings_message_id for the given round's snapshot."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT standings_message_id
            FROM driver_standings_snapshots
            WHERE division_id = ? AND round_id = ?
            ORDER BY standing_position ASC
            LIMIT 1
            """,
            (division_id, round_id),
        )
        row = await cursor.fetchone()
    return row["standings_message_id"] if row and row["standings_message_id"] else None


async def _get_reserve_user_ids(db_path: str, division_id: int) -> set[int]:
    """Return discord_user_ids of all drivers seated in a reserve team for this division."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dp.discord_user_id
            FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
            WHERE ti.division_id = ? AND ti.is_reserve = 1
              AND ts.driver_profile_id IS NOT NULL
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
    """

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
                driver_user_id=r["driver_user_id"],
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
            driver_user_id=r["driver_user_id"],
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
            round_number, track_name, label, is_sprint,
        )


async def repost_round_results(
    db_path: str,
    round_id: int,
    division_id: int,
    guild: discord.Guild,
    label: str,
) -> None:
    """Load the division's channels and repost/edit round results and standings."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT d.season_id, drc.results_channel_id, drc.standings_channel_id,
                   r.round_number, r.track_name
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
        return

    results_ch_id: int | None = row["results_channel_id"]
    standings_ch_id: int | None = row["standings_channel_id"]
    round_number: int = row["round_number"]
    track_name: str = row["track_name"] or "Unknown"

    if results_ch_id:
        rc = guild.get_channel(results_ch_id)
        if rc:
            await post_round_results(db_path, round_id, division_id, rc, guild, label)

    if standings_ch_id:
        sc = guild.get_channel(standings_ch_id)
        if sc:
            driver_snaps = await standings_service.compute_driver_standings(
                db_path, division_id, round_id
            )
            team_snaps = await standings_service.compute_team_standings(
                db_path, division_id, round_id
            )
            # Check reserves visibility flag
            show_reserves = await _get_show_reserves(db_path, division_id)
            await post_standings(
                db_path, division_id, round_id, round_number, track_name, sc,
                driver_snaps, team_snaps, guild, show_reserves, label
            )


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
) -> str:
    """Delete and repost all session results messages for every round in the division.

    For each round that has at least one ACTIVE session result:
    - Deletes the existing Discord results message for each session (if any).
    - Reposts a fresh results table and saves the new ``results_message_id``.

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
                   r.result_status
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

    for rnd in round_rows:
        round_id: int = rnd["round_id"]
        round_number: int = rnd["round_number"]
        track_name: str = rnd["track_name"] or "Unknown"
        is_sprint: bool = str(rnd["format"]).upper() == "SPRINT"
        rnd_label: str = _label_from_status(rnd["result_status"] or "PROVISIONAL")

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

        for sr_row in session_rows:
            session_result = _sr_from_row(sr_row)

            # Delete the existing Discord message(s) for this session (if any)
            old_msg_id: int | None = sr_row["results_message_id"]
            if old_msg_id is not None:
                await _delete_with_continuations(
                    rc, old_msg_id, label="results message"
                )
                async with get_connection(db_path) as db:
                    await db.execute(
                        "UPDATE session_results SET results_message_id = NULL WHERE id = ?",
                        (sr_row["id"],),
                    )
                    await db.commit()

            # Load driver rows and repost
            driver_rows = await _load_driver_rows(db_path, sr_row["id"], SessionType(sr_row["session_type"]))
            points_map = {
                r.driver_user_id: r.points_awarded + getattr(r, "fastest_lap_bonus", 0)
                for r in driver_rows
            }

            await post_session_results(
                db_path, session_result, driver_rows, points_map, rc, guild,
                round_number, track_name, rnd_label, is_sprint,
            )

    return "ok"


async def repost_standings_for_division(
    db_path: str,
    division_id: int,
    guild: discord.Guild,
) -> str:
    """Delete and repost standings messages for *every* round in the division that
    has had results posted.

    Returns one of three status strings for the caller to surface to the admin:
    - ``"ok"``         — standings reposted successfully
    - ``"no_rounds"``  — no completed rounds exist for this division
    - ``"no_channel"`` — no standings channel is configured for the division
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT r.id AS round_id, r.round_number, r.track_name,
                   r.result_status, drc.standings_channel_id
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

    for row in rows:
        round_id: int = row["round_id"]
        round_number: int = row["round_number"]
        track_name: str = row["track_name"] or "Unknown"
        rsd_label: str = _label_from_status(row["result_status"] or "PROVISIONAL")

        # Delete the existing standings message(s) for this round (if any)
        existing_msg_id = await _get_standings_message_id(db_path, division_id, round_id)
        if existing_msg_id is not None:
            await _delete_with_continuations(
                sc, existing_msg_id, label="standings message"
            )
            async with get_connection(db_path) as db:
                await db.execute(
                    "UPDATE driver_standings_snapshots SET standings_message_id = NULL "
                    "WHERE round_id = ? AND division_id = ?",
                    (round_id, division_id),
                )
                await db.commit()

        driver_snaps = await standings_service.compute_driver_standings(
            db_path, division_id, round_id
        )
        team_snaps = await standings_service.compute_team_standings(
            db_path, division_id, round_id
        )
        await post_standings(
            db_path, division_id, round_id, round_number, track_name, sc,
            driver_snaps, team_snaps, guild, show_reserves, rsd_label,
        )

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
) -> None:
    """Delete all interim results/standings Discord messages for *round_id* and
    repost the final (post-penalty) versions.

    For each non-cancelled session:
    1. Fetch ``results_message_id`` from ``session_results``.
    2. Delete that Discord message if it still exists.
    3. Post the corrected final results table and store the new ``results_message_id``.

    Then for standings:
    4. Find the current ``standings_message_id`` for this round.
    5. Delete that Discord message if it still exists.
    6. Post fresh final standings and update ``standings_message_id``.
    """
    async with get_connection(db_path) as db:
        ctx_cursor = await db.execute(
            """
            SELECT r.round_number, r.track_name,
                   drc.results_channel_id, drc.standings_channel_id,
                   drc.reserves_in_standings
            FROM rounds r
            LEFT JOIN division_results_config drc ON drc.division_id = r.division_id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        ctx = await ctx_cursor.fetchone()

    if ctx is None:
        log.warning("delete_and_repost_final_results: round %s not found", round_id)
        return

    round_number: int = ctx["round_number"]
    track_name: str = ctx["track_name"] or "Unknown"
    results_ch_id: int | None = ctx["results_channel_id"]
    standings_ch_id: int | None = ctx["standings_channel_id"]
    show_reserves: bool = bool(ctx["reserves_in_standings"]) if ctx["reserves_in_standings"] is not None else True

    # ── Delete interim results messages and re-post final ──────────────────
    if results_ch_id:
        rc = guild.get_channel(results_ch_id)
        if rc is not None:
            # Fetch session rows with their existing message IDs
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT id, round_id, division_id, session_type, status,
                           config_name, submitted_by, submitted_at, results_message_id
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
                    await _delete_with_continuations(
                        rc, old_msg_id, label="interim results message"
                    )

                    # Clear stale message_id so post_session_results inserts a fresh one
                    async with get_connection(db_path) as db:
                        await db.execute(
                            "UPDATE session_results SET results_message_id = NULL WHERE id = ?",
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
                    round_number, track_name, label, is_sprint,
                )

    # ── Delete interim standings message and re-post final ─────────────────
    if standings_ch_id:
        sc = guild.get_channel(standings_ch_id)
        if sc is not None:
            old_standings_msg_id = await _get_standings_message_id(db_path, division_id, round_id)
            if old_standings_msg_id is not None:
                await _delete_with_continuations(
                    sc, old_standings_msg_id, label="interim standings message"
                )

                # Clear obsolete standings_message_id from all snapshots for this round
                async with get_connection(db_path) as db:
                    await db.execute(
                        "UPDATE driver_standings_snapshots SET standings_message_id = NULL "
                        "WHERE round_id = ? AND division_id = ?",
                        (round_id, division_id),
                    )
                    await db.commit()

            driver_snaps = await standings_service.compute_driver_standings(
                db_path, division_id, round_id
            )
            team_snaps = await standings_service.compute_team_standings(
                db_path, division_id, round_id
            )
            await post_standings(
                db_path, division_id, round_id, round_number, track_name,
                sc, driver_snaps, team_snaps, guild, show_reserves, label,
            )


async def repost_subsequent_standings(
    db_path: str,
    division_id: int,
    from_round_id: int,
    guild: discord.Guild,
) -> None:
    """Cascade-recompute standings and repost Discord standings messages for all
    rounds *after* *from_round_id* in the division that have an existing
    ``standings_message_id``.

    This is called after :func:`delete_and_repost_final_results` so that
    subsequent rounds' standings reflect any penalty-driven point changes.
    """
    # Cascade recompute DB snapshots for all subsequent rounds
    await standings_service.cascade_recompute_from_round(db_path, division_id, from_round_id)

    # Find subsequent rounds that have standings messages posted
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.id AS round_id, r.round_number, r.track_name, r.result_status,
                   drc.standings_channel_id, drc.reserves_in_standings
            FROM rounds r
            LEFT JOIN division_results_config drc ON drc.division_id = r.division_id
            WHERE r.division_id = ?
              AND r.round_number > (SELECT round_number FROM rounds WHERE id = ?)
              AND r.status != 'CANCELLED'
            ORDER BY r.round_number
            """,
            (division_id, from_round_id),
        )
        rounds = await cursor.fetchall()

    for rnd in rounds:
        rnd_id: int = rnd["round_id"]
        rnd_number: int = rnd["round_number"]
        rnd_track: str = rnd["track_name"] or "Unknown"
        rnd_label: str = _label_from_status(rnd["result_status"] or "PROVISIONAL")
        standings_ch_id: int | None = rnd["standings_channel_id"]
        show_reserves: bool = bool(rnd["reserves_in_standings"]) if rnd["reserves_in_standings"] is not None else True

        if not standings_ch_id:
            continue

        existing_msg_id = await _get_standings_message_id(db_path, division_id, rnd_id)
        if existing_msg_id is None:
            continue  # No standings message posted for this round — skip

        sc = guild.get_channel(standings_ch_id)
        if sc is None:
            continue

        # Delete old standings message(s)
        await _delete_with_continuations(
            sc, existing_msg_id, label="standings message"
        )

        # Clear obsolete standings_message_id
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE driver_standings_snapshots SET standings_message_id = NULL "
                "WHERE round_id = ? AND division_id = ?",
                (rnd_id, division_id),
            )
            await db.commit()

        # Repost fresh standings
        driver_snaps = await standings_service.compute_driver_standings(
            db_path, division_id, rnd_id
        )
        team_snaps = await standings_service.compute_team_standings(
            db_path, division_id, rnd_id
        )
        await post_standings(
            db_path, division_id, rnd_id, rnd_number, rnd_track,
            sc, driver_snaps, team_snaps, guild, show_reserves, rnd_label,
        )


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


