"""results_post_service.py — Post and edit results/standings in Discord channels."""
from __future__ import annotations

import logging

import discord

from db.database import get_connection
from models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from models.session_result import DriverSessionResult, SessionResult
from models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot
from services import standings_service
from utils import results_formatter

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Display-name helpers
# ---------------------------------------------------------------------------

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


async def _get_season_number(db_path: str, round_id: int) -> int | None:
    """Return the season_number for the season that contains this round."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT s.season_number
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cursor.fetchone()
    return row["season_number"] if row else None


# ---------------------------------------------------------------------------
# Session-level posting
# ---------------------------------------------------------------------------

async def post_session_results(
    db_path: str,
    session_result: SessionResult,
    driver_rows: list[DriverSessionResult],
    points_map: dict[int, int],
    results_channel: discord.TextChannel,
    guild: discord.Guild,
    round_number: int,
    track_name: str,
    is_sprint: bool = True,
) -> int:
    """Format and send a single session result. Returns the Discord message ID."""
    session_type = SessionType(session_result.session_type)
    label = results_formatter.format_session_label(session_type, is_sprint=is_sprint)

    if session_type.is_qualifying:
        table = results_formatter.format_qualifying_table(driver_rows, points_map)
    else:
        table = results_formatter.format_race_table(driver_rows, points_map)

    season_number = await _get_season_number(db_path, session_result.round_id)
    season_prefix = f"S{season_number} — " if season_number is not None else ""
    msg = await results_channel.send(
        f"**{season_prefix}Round {round_number} — {track_name} | {label} Results**\n{table}"
    )

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
) -> None:
    """Format and post (or edit-in-place) the driver and team standings."""
    # Determine reserve user IDs from the DB (is_reserve team instances)
    reserve_user_ids: set[int] = await _get_reserve_user_ids(db_path, division_id)

    driver_text = results_formatter.format_driver_standings(
        driver_snapshots, reserve_user_ids, show_reserves
    )
    team_text = results_formatter.format_team_standings(team_snapshots)

    season_number = await _get_season_number(db_path, round_id)
    season_suffix = f" | S{season_number}" if season_number is not None else ""
    content = (
        f"**Driver Standings — after Round {round_number} ({track_name}){season_suffix}**\n{driver_text}\n\n"
        f"**Team Standings — after Round {round_number} ({track_name}){season_suffix}**\n{team_text}"
    )

    # Look for existing standings message (stored in the top-ranked driver snapshot)
    existing_msg_id = await _get_standings_message_id(db_path, division_id, round_id)

    sent_msg: discord.Message | None = None
    if existing_msg_id is not None:
        try:
            existing_msg = await standings_channel.fetch_message(existing_msg_id)
            await existing_msg.edit(content=content)
            sent_msg = existing_msg
        except (discord.NotFound, discord.HTTPException):
            sent_msg = None

    if sent_msg is None:
        sent_msg = await standings_channel.send(content)

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
    """Return the standings_message_id from the latest snapshot for this division."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT standings_message_id
            FROM driver_standings_snapshots
            WHERE division_id = ?
            ORDER BY round_id DESC, standing_position ASC
            LIMIT 1
            """,
            (division_id,),
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
# Round-level posting
# ---------------------------------------------------------------------------

async def post_round_results(
    db_path: str,
    round_id: int,
    division_id: int,
    results_channel: discord.TextChannel,
    guild: discord.Guild,
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

        # Load driver rows
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, session_result_id, driver_user_id, team_role_id, finishing_position,
                       outcome, tyre, best_lap, gap, total_time, fastest_lap, time_penalties,
                       post_steward_total_time, post_race_time_penalties,
                       points_awarded, fastest_lap_bonus, is_superseded
                FROM driver_session_results
                WHERE session_result_id = ? AND is_superseded = 0
                ORDER BY finishing_position
                """,
                (session_result.id,),
            )
            driver_rows_raw = await cursor.fetchall()

        from models.session_result import OutcomeModifier as _OM
        driver_rows = [
            DriverSessionResult(
                id=r["id"],
                session_result_id=r["session_result_id"],
                driver_user_id=r["driver_user_id"],
                team_role_id=r["team_role_id"],
                finishing_position=r["finishing_position"],
                outcome=_OM(r["outcome"]),
                tyre=r["tyre"],
                best_lap=r["best_lap"],
                gap=r["gap"],
                total_time=r["total_time"],
                fastest_lap=r["fastest_lap"],
                time_penalties=r["time_penalties"],
                post_steward_total_time=r["post_steward_total_time"],
                post_race_time_penalties=r["post_race_time_penalties"],
                points_awarded=r["points_awarded"] or 0,
                fastest_lap_bonus=r["fastest_lap_bonus"] or 0,
                is_superseded=bool(r["is_superseded"]),
            )
            for r in driver_rows_raw
        ]

        points_map = {
            r.driver_user_id: r.points_awarded + r.fastest_lap_bonus
            for r in driver_rows
        }

        await post_session_results(
            db_path, session_result, driver_rows, points_map, results_channel, guild,
            round_number, track_name, is_sprint,
        )


async def repost_round_results(
    db_path: str,
    round_id: int,
    division_id: int,
    guild: discord.Guild,
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
            await post_round_results(db_path, round_id, division_id, rc, guild)

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
                driver_snaps, team_snaps, guild, show_reserves
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


async def repost_standings_for_division(
    db_path: str,
    division_id: int,
    guild: discord.Guild,
) -> str:
    """Repost the current standings for a division to its configured standings channel.

    Returns one of three status strings for the caller to surface to the admin:
    - ``"ok"``         — standings reposted successfully
    - ``"no_rounds"``  — no completed rounds exist for this division
    - ``"no_channel"`` — no standings channel is configured for the division
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.id AS round_id, r.round_number, r.track_name, drc.standings_channel_id
            FROM rounds r
            JOIN session_results sr ON sr.round_id = r.id
            LEFT JOIN division_results_config drc ON drc.division_id = r.division_id
            WHERE r.division_id = ?
              AND sr.status = 'ACTIVE'
            ORDER BY r.round_number DESC
            LIMIT 1
            """,
            (division_id,),
        )
        row = await cursor.fetchone()

    if row is None:
        return "no_rounds"

    round_id: int = row["round_id"]
    round_number: int = row["round_number"]
    track_name: str = row["track_name"] or "Unknown"
    standings_ch_id: int | None = row["standings_channel_id"]

    if not standings_ch_id:
        return "no_channel"

    sc = guild.get_channel(standings_ch_id)
    if sc is None:
        log.warning(
            "repost_standings_for_division: standings channel %s not found in guild",
            standings_ch_id,
        )
        return "no_channel"

    driver_snaps = await standings_service.compute_driver_standings(db_path, division_id, round_id)
    team_snaps = await standings_service.compute_team_standings(db_path, division_id, round_id)
    show_reserves = await _get_show_reserves(db_path, division_id)
    await post_standings(
        db_path, division_id, round_id, round_number, track_name, sc,
        driver_snaps, team_snaps, guild, show_reserves,
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
                    try:
                        old_msg = await rc.fetch_message(old_msg_id)
                        await old_msg.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                        log.warning(
                            "delete_and_repost_final_results: could not delete interim "
                            "results message %s: %s",
                            old_msg_id,
                            exc,
                        )

                    # Clear stale message_id so post_session_results inserts a fresh one
                    async with get_connection(db_path) as db:
                        await db.execute(
                            "UPDATE session_results SET results_message_id = NULL WHERE id = ?",
                            (sr_row["id"],),
                        )
                        await db.commit()

                # Load updated driver rows
                async with get_connection(db_path) as db:
                    cursor = await db.execute(
                        """
                        SELECT id, session_result_id, driver_user_id, team_role_id,
                               finishing_position, outcome, tyre, best_lap, gap, total_time,
                               fastest_lap, time_penalties, post_steward_total_time,
                               post_race_time_penalties, points_awarded, fastest_lap_bonus,
                               is_superseded
                        FROM driver_session_results
                        WHERE session_result_id = ? AND is_superseded = 0
                        ORDER BY finishing_position
                        """,
                        (sr_row["id"],),
                    )
                    driver_rows_raw = await cursor.fetchall()

                from models.session_result import OutcomeModifier as _OM
                driver_rows = [
                    _dr_from_row(r)
                    for r in driver_rows_raw
                ]
                points_map = {
                    r.driver_user_id: r.points_awarded + r.fastest_lap_bonus
                    for r in driver_rows
                }

                await post_session_results(
                    db_path, session_result, driver_rows, points_map, rc, guild,
                    round_number, track_name, is_sprint,
                )

    # ── Delete interim standings message and re-post final ─────────────────
    if standings_ch_id:
        sc = guild.get_channel(standings_ch_id)
        if sc is not None:
            old_standings_msg_id = await _get_standings_message_id(db_path, division_id, round_id)
            if old_standings_msg_id is not None:
                try:
                    old_sm = await sc.fetch_message(old_standings_msg_id)
                    await old_sm.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                    log.warning(
                        "delete_and_repost_final_results: could not delete interim "
                        "standings message %s: %s",
                        old_standings_msg_id,
                        exc,
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
                sc, driver_snaps, team_snaps, guild, show_reserves,
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
            SELECT r.id AS round_id, r.round_number, r.track_name,
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

        # Delete old standings message
        try:
            old_sm = await sc.fetch_message(existing_msg_id)
            await old_sm.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "repost_subsequent_standings: could not delete standings message %s: %s",
                existing_msg_id,
                exc,
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
            sc, driver_snaps, team_snaps, guild, show_reserves,
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


def _dr_from_row(r) -> "DriverSessionResult":
    """Construct a :class:`DriverSessionResult` from a DB row dict."""
    from models.session_result import DriverSessionResult, OutcomeModifier
    return DriverSessionResult(
        id=r["id"],
        session_result_id=r["session_result_id"],
        driver_user_id=r["driver_user_id"],
        team_role_id=r["team_role_id"],
        finishing_position=r["finishing_position"],
        outcome=OutcomeModifier(r["outcome"]),
        tyre=r["tyre"],
        best_lap=r["best_lap"],
        gap=r["gap"],
        total_time=r["total_time"],
        fastest_lap=r["fastest_lap"],
        time_penalties=r["time_penalties"],
        post_steward_total_time=r["post_steward_total_time"],
        post_race_time_penalties=r["post_race_time_penalties"],
        points_awarded=r["points_awarded"] or 0,
        fastest_lap_bonus=r["fastest_lap_bonus"] or 0,
        is_superseded=bool(r["is_superseded"]),
    )

