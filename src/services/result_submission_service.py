"""result_submission_service.py — Round result submission wizard and channel management."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import discord

from db.database import get_connection
from models.points_config import SessionType
from models.round import RoundFormat
from models.session_result import OutcomeModifier

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session ordering
# ---------------------------------------------------------------------------

SESSION_ORDER_NORMAL: list[SessionType] = [
    SessionType.FEATURE_QUALIFYING,
    SessionType.FEATURE_RACE,
]

SESSION_ORDER_SPRINT: list[SessionType] = [
    SessionType.SPRINT_QUALIFYING,
    SessionType.SPRINT_RACE,
    SessionType.FEATURE_QUALIFYING,
    SessionType.FEATURE_RACE,
]

_SESSION_LABELS: dict[SessionType, str] = {
    SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
    SessionType.SPRINT_RACE: "Sprint Race",
    SessionType.FEATURE_QUALIFYING: "Feature Qualifying",
    SessionType.FEATURE_RACE: "Feature Race",
}


def get_sessions_for_format(round_format: RoundFormat) -> list[SessionType]:
    """Return the ordered list of session types for a given round format.

    SPRINT rounds include all four sessions.
    NORMAL and ENDURANCE rounds use Feature sessions only (spec §8).
    """
    if round_format is RoundFormat.SPRINT:
        return SESSION_ORDER_SPRINT
    return SESSION_ORDER_NORMAL


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def create_submission_channel(
    guild: discord.Guild,
    results_channel: discord.TextChannel,
    division_name_slug: str,
    round_number: int,
    round_id: int,
    db_path: str,
) -> discord.TextChannel:
    """Create a transient text channel for result submission adjacent to the results channel."""
    name = f"results-sub-{division_name_slug}-r{round_number}"
    channel = await guild.create_text_channel(
        name=name,
        category=results_channel.category,
        reason="Results submission channel created by bot",
    )
    created_at = datetime.now(timezone.utc).isoformat()
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, closed) "
            "VALUES (?, ?, ?, 0)",
            (round_id, channel.id, created_at),
        )
        await db.commit()
    return channel


async def close_submission_channel(
    channel_id: int,
    round_id: int,
    guild: discord.Guild,
    db_path: str,
) -> None:
    """Mark the submission channel closed in the DB then delete it from Discord."""
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1 WHERE round_id = ?",
            (round_id,),
        )
        await db.commit()
    channel = guild.get_channel(channel_id)
    if channel is not None:
        try:
            await channel.delete(reason="Results submission complete")
        except discord.NotFound:
            pass
        except discord.HTTPException as exc:
            log.warning(
                "close_submission_channel: failed to delete channel %s: %s",
                channel_id,
                exc,
            )


async def is_submission_open(db_path: str, round_id: int) -> bool:
    """Return True if a submission channel for this round exists and is not yet closed."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT closed FROM round_submission_channels WHERE round_id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    return row is not None and row["closed"] == 0


async def save_session_result(
    db_path: str,
    round_id: int,
    division_id: int,
    session_type: SessionType,
    status: str,
    config_name: str | None,
    submitted_by: int | None,
    driver_rows: list[dict],
) -> int:
    """INSERT session_results + driver_session_results; return session_result_id."""
    submitted_at = datetime.now(timezone.utc).isoformat()
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO session_results
                (round_id, division_id, session_type, status, config_name,
                 submitted_by, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                round_id,
                division_id,
                session_type.value,
                status,
                config_name,
                submitted_by,
                submitted_at,
            ),
        )
        session_result_id = cursor.lastrowid
        for row in driver_rows:
            await db.execute(
                """
                INSERT INTO driver_session_results
                    (session_result_id, driver_user_id, team_role_id, finishing_position,
                     outcome, tyre, best_lap, gap, total_time, fastest_lap, time_penalties,
                     post_steward_total_time, post_race_time_penalties,
                     points_awarded, fastest_lap_bonus, is_superseded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, 0)
                """,
                (
                    session_result_id,
                    row["driver_user_id"],
                    row["team_role_id"],
                    row["finishing_position"],
                    row.get("outcome", OutcomeModifier.CLASSIFIED.value),
                    row.get("tyre"),
                    row.get("best_lap"),
                    row.get("gap"),
                    row.get("total_time"),
                    row.get("fastest_lap"),
                    row.get("time_penalties"),
                    row.get("points_awarded", 0),
                    row.get("fastest_lap_bonus", 0),
                ),
            )
        await db.commit()
    return session_result_id


async def amend_session_result(
    db_path: str,
    round_id: int,
    division_id: int,
    session_type: SessionType,
    new_driver_rows: list,
    config_name: str,
    amended_by: int,
    bot,
) -> None:
    """Supersede the existing session results and insert new driver rows.

    ``new_driver_rows`` may be ``ParsedQualifyingRow`` / ``ParsedRaceRow`` dataclasses
    or plain dicts; both are accessed via ``getattr`` with a ``get`` fallback.

    Cascades standings recomputation from this round forward and reposts results.
    """

    def _get(obj, key, default=None):
        if hasattr(obj, key):
            return getattr(obj, key)
        if hasattr(obj, "get"):
            return obj.get(key, default)
        return default

    submitted_at = datetime.now(timezone.utc).isoformat()
    async with get_connection(db_path) as db:
        # Mark all current rows as superseded
        await db.execute(
            """
            UPDATE driver_session_results SET is_superseded = 1
            WHERE session_result_id = (
                SELECT id FROM session_results
                WHERE round_id = ? AND session_type = ?
            )
            """,
            (round_id, session_type.value),
        )
        # Update the session_results header
        await db.execute(
            """
            UPDATE session_results
            SET submitted_by = ?, submitted_at = ?, config_name = ?
            WHERE round_id = ? AND session_type = ?
            """,
            (amended_by, submitted_at, config_name, round_id, session_type.value),
        )
        # Fetch session_result_id
        cursor = await db.execute(
            "SELECT id FROM session_results WHERE round_id = ? AND session_type = ?",
            (round_id, session_type.value),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.rollback()
            raise ValueError(
                f"No session_results row found for round={round_id} session={session_type.value}"
            )
        session_result_id = row["id"]
        # Insert new rows
        for i, dr in enumerate(new_driver_rows, start=1):
            await db.execute(
                """
                INSERT INTO driver_session_results
                    (session_result_id, driver_user_id, team_role_id, finishing_position,
                     outcome, tyre, best_lap, gap, total_time, fastest_lap, time_penalties,
                     post_steward_total_time, post_race_time_penalties,
                     points_awarded, fastest_lap_bonus, is_superseded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, 0)
                """,
                (
                    session_result_id,
                    _get(dr, "driver_user_id"),
                    _get(dr, "team_role_id"),
                    _get(dr, "position") or _get(dr, "finishing_position") or i,
                    _get(dr, "outcome", OutcomeModifier.CLASSIFIED.value),
                    _get(dr, "tyre"),
                    _get(dr, "best_lap"),
                    _get(dr, "gap"),
                    _get(dr, "total_time"),
                    _get(dr, "fastest_lap"),
                    _get(dr, "time_penalties"),
                    _get(dr, "points_awarded", 0),
                    _get(dr, "fastest_lap_bonus", 0),
                ),
            )
        await db.commit()

    # Cascade standings and repost
    from services import standings_service, results_post_service  # lazy imports

    rctx = await _get_round_context(db_path, round_id)
    server_id = rctx["server_id"]
    guild = bot.get_guild(server_id)
    await standings_service.cascade_recompute_from_round(db_path, division_id, round_id)
    if guild is not None:
        await results_post_service.repost_round_results(db_path, round_id, division_id, guild)

    await bot.output_router.post_log(
        server_id,
        f"\U0001f4cb RESULT_AMENDED | season={rctx['season_number']} "
        f"division={rctx['division_name']!r} round={rctx['round_number']} "
        f"session={session_type.value} by=<@{amended_by}>",
    )


# ---------------------------------------------------------------------------
# Validation — regex constants
# ---------------------------------------------------------------------------

# Absolute lap time:  M:SS.mmm  |  SS.mmm  |  H:MM:SS.mmm
_ABS_TIME_RE = re.compile(
    r"^\d+:\d{2}\.\d{3}$"
    r"|^\d+\.\d{3}$"
    r"|^\d+:\d{2}:\d{2}\.\d{3}$"
)

# Delta time:  +M:SS.mmm  |  +SS.mmm  |  +H:MM:SS.mmm
_DELTA_TIME_RE = re.compile(
    r"^\+\d+:\d{2}\.\d{3}$"
    r"|^\+\d+\.\d{3}$"
    r"|^\+\d+:\d{2}:\d{2}\.\d{3}$"
)

# Lap gap:  "x Laps"  |  "+x Laps"  (case-insensitive)
_LAP_GAP_RE = re.compile(r"^\+?\d+ Laps?$", re.IGNORECASE)

# Discord member mention:  <@123>  or  <@!123>
_MEMBER_MENTION_RE = re.compile(r"^<@!?(\d+)>$")

# Discord role mention:  <@&123>
_ROLE_MENTION_RE = re.compile(r"^<@&(\d+)>$")

_OUTCOME_LITERALS: frozenset[str] = frozenset({"DNS", "DNF", "DSQ"})


def _parse_mention(text: str) -> int | None:
    """Extract a member user ID from '<@123>' or '<@!123>'. Returns None if no match."""
    m = _MEMBER_MENTION_RE.match(text.strip())
    return int(m.group(1)) if m else None


def _parse_role_mention(text: str) -> int | None:
    """Extract a role ID from '<@&123>'. Returns None if no match."""
    m = _ROLE_MENTION_RE.match(text.strip())
    return int(m.group(1)) if m else None


def _parse_outcome(time_field: str) -> OutcomeModifier:
    """Infer OutcomeModifier from a race/qualifying time field value."""
    val = time_field.strip().upper()
    if val == "DNS":
        return OutcomeModifier.DNS
    if val == "DNF":
        return OutcomeModifier.DNF
    if val == "DSQ":
        return OutcomeModifier.DSQ
    return OutcomeModifier.CLASSIFIED


# ---------------------------------------------------------------------------
# Validation — parsed row types
# ---------------------------------------------------------------------------

@dataclass
class ParsedQualifyingRow:
    position: int
    driver_user_id: int
    team_role_id: int
    tyre: str
    best_lap: str   # time string or DNS/DNF/DSQ
    gap: str        # delta string or "N/A"
    outcome: OutcomeModifier


@dataclass
class ParsedRaceRow:
    position: int
    driver_user_id: int
    team_role_id: int
    total_time: str       # absolute time, delta, lap-gap, or outcome literal
    fastest_lap: str      # time string or "N/A"
    time_penalties: str   # time string or "N/A"
    outcome: OutcomeModifier


# ---------------------------------------------------------------------------
# Validation — per-row functions
# ---------------------------------------------------------------------------

def validate_qualifying_row(line: str) -> ParsedQualifyingRow | str:
    """Parse and validate a single qualifying-result line (6 comma-separated fields).

    Fields: Position, Driver mention, Team role mention, Tyre, Best Lap, Gap
    Returns a ParsedQualifyingRow on success or an error string on failure.
    """
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) != 6:
        return f"Expected 6 comma-separated fields, got {len(parts)}: `{line.strip()}`"

    pos_str, driver_str, team_str, tyre, best_lap, gap = parts

    if not pos_str.isdigit():
        return f"Position must be a positive integer, got `{pos_str}`"
    position = int(pos_str)

    driver_user_id = _parse_mention(driver_str)
    if driver_user_id is None:
        return f"Driver must be a Discord member mention (<@user_id>), got `{driver_str}`"

    team_role_id = _parse_role_mention(team_str)
    if team_role_id is None:
        return f"Team must be a Discord role mention (<@&role_id>), got `{team_str}`"

    best_lap_upper = best_lap.upper()
    if best_lap_upper not in _OUTCOME_LITERALS and not _ABS_TIME_RE.match(best_lap):
        return (
            f"Best Lap must be a time (e.g. 1:23.456) or DNS/DNF/DSQ, got `{best_lap}`"
        )

    # For 1st position the Gap input is ignored entirely (spec)
    if position != 1:
        gap_upper = gap.upper()
        if (
            gap_upper != "N/A"
            and not _DELTA_TIME_RE.match(gap)
            and not _ABS_TIME_RE.match(gap)
        ):
            return f"Gap must be a delta time (e.g. +1:23.456), an absolute time, or N/A, got `{gap}`"

    outcome = _parse_outcome(best_lap)
    return ParsedQualifyingRow(
        position=position,
        driver_user_id=driver_user_id,
        team_role_id=team_role_id,
        tyre=tyre,
        best_lap=best_lap,
        gap=gap,
        outcome=outcome,
    )


def validate_race_row(line: str, is_first: bool) -> ParsedRaceRow | str:
    """Parse and validate a single race-result line (6 comma-separated fields).

    Fields: Position, Driver mention, Team role mention, Total Time, Fastest Lap, Time Penalties
    For the 1st-place driver, Total Time must be an absolute time.
    For other positions, Total Time may be absolute, delta, lap-gap, or outcome literal.
    Returns a ParsedRaceRow on success or an error string on failure.
    """
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) != 6:
        return f"Expected 6 comma-separated fields, got {len(parts)}: `{line.strip()}`"

    pos_str, driver_str, team_str, total_time, fastest_lap, time_penalties = parts

    if not pos_str.isdigit():
        return f"Position must be a positive integer, got `{pos_str}`"
    position = int(pos_str)

    driver_user_id = _parse_mention(driver_str)
    if driver_user_id is None:
        return f"Driver must be a Discord member mention (<@user_id>), got `{driver_str}`"

    team_role_id = _parse_role_mention(team_str)
    if team_role_id is None:
        return f"Team must be a Discord role mention (<@&role_id>), got `{team_str}`"

    total_upper = total_time.upper()
    if is_first:
        if not _ABS_TIME_RE.match(total_time):
            return (
                f"1st-place Total Time must be an absolute time (e.g. 1:23:45.678), "
                f"got `{total_time}`"
            )
    else:
        valid = (
            total_upper in _OUTCOME_LITERALS
            or _ABS_TIME_RE.match(total_time)
            or _DELTA_TIME_RE.match(total_time)
            or _LAP_GAP_RE.match(total_time)
        )
        if not valid:
            return (
                f"Total Time must be a time, delta (+M:SS.mmm), lap gap (x Laps), "
                f"or DNS/DNF/DSQ, got `{total_time}`"
            )

    fl_upper = fastest_lap.upper()
    # When Total Time is an outcome literal, Fastest Lap validation is skipped (spec)
    if total_upper not in _OUTCOME_LITERALS:
        if fl_upper != "N/A" and not _ABS_TIME_RE.match(fastest_lap):
            return f"Fastest Lap must be a time (e.g. 1:23.456) or N/A, got `{fastest_lap}`"

    tp_upper = time_penalties.upper()
    if tp_upper != "N/A" and not _ABS_TIME_RE.match(time_penalties):
        return (
            f"Time Penalties must be a time (e.g. 0:05.000) or N/A, got `{time_penalties}`"
        )

    outcome = _parse_outcome(total_time)
    return ParsedRaceRow(
        position=position,
        driver_user_id=driver_user_id,
        team_role_id=team_role_id,
        total_time=total_time,
        fastest_lap=fastest_lap,
        time_penalties=time_penalties,
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Validation — block-level function
# ---------------------------------------------------------------------------

def validate_submission_block(
    lines: list[str],
    session_type: SessionType,
    division_driver_ids: set[int],
    team_role_ids: set[int],
    reserve_team_role_id: int | None,
    driver_team_map: dict[int, int],
) -> list[ParsedQualifyingRow | ParsedRaceRow] | list[str]:
    """Validate all result lines for a session.

    Returns a list of parsed rows on success, or a list of error strings on failure.
    Checks: field formats; sequential positions from 1; drivers in division; valid team
    roles; each driver assigned to their own team (or the reserve team).
    """
    is_qualifying = session_type.is_qualifying
    non_empty = [ln for ln in lines if ln.strip()]

    if not non_empty:
        return ["No result lines found — please submit at least one driver line."]

    errors: list[str] = []
    parsed_rows: list[ParsedQualifyingRow | ParsedRaceRow] = []

    for i, line in enumerate(non_empty, start=1):
        if is_qualifying:
            result = validate_qualifying_row(line)
        else:
            result = validate_race_row(line, is_first=(i == 1))

        if isinstance(result, str):
            errors.append(f"Row {i}: {result}")
        else:
            parsed_rows.append(result)

    if errors:
        return errors

    # Positions must be contiguous from 1
    positions = sorted(r.position for r in parsed_rows)
    for expected, actual in enumerate(positions, start=1):
        if actual != expected:
            errors.append(
                f"Position gap: expected position {expected}, got {actual}. "
                "All positions must be sequential starting from 1."
            )
            break

    if errors:
        return errors

    # Each driver must be in the division
    for row in parsed_rows:
        if row.driver_user_id not in division_driver_ids:
            errors.append(
                f"Row {row.position}: driver <@{row.driver_user_id}> "
                "is not registered in this division."
            )

    # Each team role must be valid
    all_valid_team_roles: set[int] = set(team_role_ids)
    if reserve_team_role_id is not None:
        all_valid_team_roles.add(reserve_team_role_id)

    for row in parsed_rows:
        if row.team_role_id not in all_valid_team_roles:
            errors.append(
                f"Row {row.position}: <@&{row.team_role_id}> "
                "is not a valid team role for this division."
            )

    # Driver must be assigned to the stated team or the reserve team
    for row in parsed_rows:
        mapped_team = driver_team_map.get(row.driver_user_id)
        if mapped_team is None:
            continue  # already reported above as "not in division"
        if row.team_role_id != mapped_team and row.team_role_id != reserve_team_role_id:
            errors.append(
                f"Row {row.position}: driver <@{row.driver_user_id}> "
                f"submitted as <@&{row.team_role_id}> "
                f"but is assigned to <@&{mapped_team}>."
            )

    if errors:
        return errors

    # G1: derive Best Lap for qualifying DNF entries that have a valid gap
    if is_qualifying:
        p1_row = next((r for r in parsed_rows if r.position == 1), None)
        if (
            p1_row is not None
            and p1_row.best_lap.upper() not in _OUTCOME_LITERALS
            and _ABS_TIME_RE.match(p1_row.best_lap)
        ):
            try:
                p1_ms = _parse_time_to_ms(p1_row.best_lap)
                for row in parsed_rows:
                    if row.best_lap.upper() == "DNF" and (
                        _DELTA_TIME_RE.match(row.gap) or _ABS_TIME_RE.match(row.gap)
                    ):
                        gap_ms = _parse_time_to_ms(row.gap)
                        row.best_lap = _format_time_ms(p1_ms + gap_ms)
            except ValueError:
                pass  # parsing failed; leave best_lap unchanged

    return parsed_rows


# ---------------------------------------------------------------------------
# Time parsing helpers (for DNF best-lap derivation)
# ---------------------------------------------------------------------------

def _parse_time_to_ms(s: str) -> int:
    """Parse 'H:MM:SS.mmm', 'M:SS.mmm', or 'SS.mmm' (optional leading '+') to ms."""
    s = s.lstrip("+")
    parts = s.split(":")
    try:
        if len(parts) == 1:
            sec_str, ms_str = parts[0].split(".")
            return int(sec_str) * 1000 + int(ms_str)
        if len(parts) == 2:
            sec_str, ms_str = parts[1].split(".")
            return int(parts[0]) * 60_000 + int(sec_str) * 1000 + int(ms_str)
        if len(parts) == 3:
            sec_str, ms_str = parts[2].split(".")
            return (
                int(parts[0]) * 3_600_000
                + int(parts[1]) * 60_000
                + int(sec_str) * 1000
                + int(ms_str)
            )
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Cannot parse time string {s!r}") from exc
    raise ValueError(f"Cannot parse time string {s!r}")


def _format_time_ms(total_ms: int) -> str:
    """Format milliseconds to 'M:SS.mmm' or 'SS.mmm'."""
    ms = total_ms % 1000
    total_s = total_ms // 1000
    mins = total_s // 60
    secs = total_s % 60
    if mins == 0:
        return f"{secs}.{ms:03d}"
    return f"{mins}:{secs:02d}.{ms:03d}"


# ---------------------------------------------------------------------------
# Config selection view
# ---------------------------------------------------------------------------

class _ConfigSelectView(discord.ui.View):
    """Button view for selecting an attached points config."""

    def __init__(self, config_names: list[str]) -> None:
        super().__init__(timeout=None)
        self.selected: str | None = None
        for name in config_names:
            button = discord.ui.Button(
                label=name[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"config_sel_{name[:60]}",
            )

            async def _cb(
                interaction: discord.Interaction,
                _name: str = name,
            ) -> None:
                self.selected = _name
                self.stop()
                await interaction.response.defer()

            button.callback = _cb
            self.add_item(button)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _get_server_id_for_round(db_path: str, round_id: int) -> int:
    """Resolve server_id from a round_id via the division → season chain."""
    ctx = await _get_round_context(db_path, round_id)
    return ctx["server_id"]


async def _get_round_context(db_path: str, round_id: int) -> dict:
    """Return server_id, season_number, round_number, division_name for a round."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT s.server_id, s.season_number, r.round_number, d.name AS division_name
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"Round {round_id} not found or has no season")
    return dict(row)


async def _build_division_validation_data(
    division_id: int,
    server_id: int,
    bot,
) -> tuple[set[int], set[int], int | None, dict[int, int]]:
    """Build validation structures for the given division.

    Returns:
        (division_driver_ids, team_role_ids, reserve_team_role_id, driver_team_map)
    """
    div_teams = await bot.team_service.get_division_teams(division_id)
    teams_with_roles = await bot.team_service.get_teams_with_roles(server_id)

    name_to_role: dict[str, int] = {
        t["name"]: t["role_id"]
        for t in teams_with_roles
        if t["role_id"] is not None
    }

    division_driver_ids: set[int] = set()
    team_role_ids: set[int] = set()
    reserve_team_role_id: int | None = None
    driver_team_map: dict[int, int] = {}

    for team in div_teams:
        role_id = name_to_role.get(team["name"])
        if role_id is None:
            continue
        if team["is_reserve"]:
            reserve_team_role_id = role_id
        else:
            team_role_ids.add(role_id)
        for seat in team["seats"]:
            uid_str = seat.get("discord_user_id")
            if uid_str is not None:
                uid = int(uid_str)
                division_driver_ids.add(uid)
                driver_team_map[uid] = role_id

    return division_driver_ids, team_role_ids, reserve_team_role_id, driver_team_map


def _make_slug(name: str) -> str:
    """Convert a division name to a Discord-channel-safe slug."""
    slug = name.lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return slug[:24]  # keep channel name short


def _row_dict_from_qualifying(row: ParsedQualifyingRow) -> dict:
    return {
        "driver_user_id": row.driver_user_id,
        "team_role_id": row.team_role_id,
        "finishing_position": row.position,
        "outcome": row.outcome.value,
        "tyre": row.tyre,
        "best_lap": row.best_lap,
        "gap": row.gap,
    }


def _row_dict_from_race(row: ParsedRaceRow) -> dict:
    fl = row.fastest_lap if row.fastest_lap.upper() != "N/A" else None
    tp = row.time_penalties if row.time_penalties.upper() != "N/A" else None
    return {
        "driver_user_id": row.driver_user_id,
        "team_role_id": row.team_role_id,
        "finishing_position": row.position,
        "outcome": row.outcome.value,
        "total_time": row.total_time,
        "fastest_lap": fl,
        "time_penalties": tp,
    }


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

async def run_result_submission_job(round_id: int, bot) -> None:
    """APScheduler job entry point — runs the full submission wizard for a round.

    Triggered at each round's scheduled start time. Creates a transient submission
    channel, collects results session by session with validation, prompts for config
    selection, persists everything, and then closes the channel.
    """
    db_path: str = bot.db_path  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # 1. Load round context
    # ------------------------------------------------------------------
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.id           AS round_id,
                   r.division_id,
                   r.round_number,
                   r.format       AS round_format,
                   r.status       AS round_status,
                   d.name         AS division_name,
                   d.mention_role_id,
                   drc.results_channel_id,
                   s.id           AS season_id,
                   s.server_id,
                   s.season_number
            FROM rounds r
            JOIN divisions d   ON d.id = r.division_id
            JOIN seasons s     ON s.id = d.season_id
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        ctx = await cursor.fetchone()

    if ctx is None:
        log.warning(
            "run_result_submission_job: round %s not found — skipping", round_id
        )
        return

    if ctx["round_status"] == "CANCELLED":
        log.info(
            "run_result_submission_job: round %s is CANCELLED — skipping", round_id
        )
        return

    server_id: int = ctx["server_id"]
    division_id: int = ctx["division_id"]
    division_name: str = ctx["division_name"]
    round_number: int = ctx["round_number"]
    season_id: int = ctx["season_id"]
    season_number: int = ctx["season_number"]
    round_format = RoundFormat(ctx["round_format"])
    results_channel_id: int | None = ctx["results_channel_id"]
    mention_role_id: int = ctx["mention_role_id"]

    # ------------------------------------------------------------------
    # 2. Module guard
    # ------------------------------------------------------------------
    if not await bot.module_service.is_results_enabled(server_id):  # type: ignore[attr-defined]
        log.info(
            "run_result_submission_job: results module disabled for server %s — skipping",
            server_id,
        )
        return

    # ------------------------------------------------------------------
    # 3. Get guild + results channel
    # ------------------------------------------------------------------
    guild = bot.get_guild(server_id)  # type: ignore[attr-defined]
    if guild is None:
        log.error(
            "run_result_submission_job: guild %s not in cache for round %s",
            server_id,
            round_id,
        )
        return

    if results_channel_id is None:
        log.error(
            "run_result_submission_job: no results_channel_id for division %s (round %s)",
            division_id,
            round_id,
        )
        return

    results_channel = guild.get_channel(results_channel_id)
    if results_channel is None:
        log.error(
            "run_result_submission_job: results channel %s not found in guild %s (round %s)",
            results_channel_id,
            server_id,
            round_id,
        )
        return

    # ------------------------------------------------------------------
    # 4. Load validation data
    # ------------------------------------------------------------------
    try:
        (
            division_driver_ids,
            team_role_ids,
            reserve_team_role_id,
            driver_team_map,
        ) = await _build_division_validation_data(division_id, server_id, bot)
    except Exception:
        log.exception(
            "run_result_submission_job: failed to build validation data for round %s",
            round_id,
        )
        return

    # ------------------------------------------------------------------
    # 5. Create submission channel
    # ------------------------------------------------------------------
    slug = _make_slug(division_name)
    try:
        sub_channel = await create_submission_channel(
            guild, results_channel, slug, round_number, round_id, db_path
        )
    except discord.HTTPException:
        log.exception(
            "run_result_submission_job: failed to create submission channel for round %s",
            round_id,
        )
        return

    # ------------------------------------------------------------------
    # 6. Opening message
    # ------------------------------------------------------------------
    sessions = get_sessions_for_format(round_format)
    session_list_str = ", ".join(_SESSION_LABELS[s] for s in sessions)
    mention_str = f" <@&{mention_role_id}>" if mention_role_id else ""
    await sub_channel.send(
        f"✅ Results submission open for **Round {round_number}** ({division_name})."
        f" Sessions: {session_list_str}.{mention_str}\n\n"
        "Submit results one driver per line (comma-separated), or type `CANCELLED` to skip a session."
    )

    # ------------------------------------------------------------------
    # 7. Load attached config names for this season
    # ------------------------------------------------------------------
    from services import season_points_service  # lazy import to avoid circular

    config_names = await season_points_service.get_attached_config_names(db_path, season_id)

    # ------------------------------------------------------------------
    # 8. Per-session collection loop
    # ------------------------------------------------------------------
    for session_type in sessions:
        label = _SESSION_LABELS[session_type]

        if session_type.is_qualifying:
            format_hint = "Format: `Position, @Driver, @TeamRole, Tyre, BestLap, Gap`"
        else:
            format_hint = "Format: `Position, @Driver, @TeamRole, TotalTime, FastestLap, TimePenalties`"

        await sub_channel.send(
            f"📋 Submit **{label}** results (one driver per line), or type `CANCELLED`.\n"
            f"{format_hint}"
        )

        while True:
            msg = await bot.wait_for(  # type: ignore[attr-defined]
                "message",
                check=lambda m, ch=sub_channel: (
                    m.channel.id == ch.id and not m.author.bot
                ),
            )

            content = msg.content.strip()

            if content.upper() == "CANCELLED":
                await save_session_result(
                    db_path=db_path,
                    round_id=round_id,
                    division_id=division_id,
                    session_type=session_type,
                    status="CANCELLED",
                    config_name=None,
                    submitted_by=msg.author.id,
                    driver_rows=[],
                )
                await sub_channel.send(f"✅ **{label}** marked as CANCELLED.")
                break

            # Validate the block
            lines = content.splitlines()
            result = validate_submission_block(
                lines,
                session_type,
                division_driver_ids,
                team_role_ids,
                reserve_team_role_id,
                driver_team_map,
            )

            if isinstance(result[0] if result else None, str):
                # Validation failed — these are error strings
                error_list = "\n".join(f"• {e}" for e in result)
                await bot.output_router.post_log(  # type: ignore[attr-defined]
                    server_id,
                    f"RESULT_SUBMISSION_REJECTED | season={season_number} "
                    f"division={division_name!r} round={round_number} "
                    f"session={session_type.value} by=<@{msg.author.id}>\n```\n{content[:500]}\n```",
                )
                await sub_channel.send(
                    f"❌ Validation failed:\n{error_list}\nPlease correct and resubmit."
                )
                continue

            # Valid — parsed_rows
            parsed_rows = result

            # Log accepted input (with raw content for auditability)
            await bot.output_router.post_log(  # type: ignore[attr-defined]
                server_id,
                f"RESULT_SUBMISSION_ACCEPTED | season={season_number} "
                f"division={division_name!r} round={round_number} "
                f"session={session_type.value} by=<@{msg.author.id}>\n```\n{content[:500]}\n```",
            )

            # Config selection
            selected_config: str | None = None
            if len(config_names) == 1:
                selected_config = config_names[0]
                await sub_channel.send(
                    f"✅ Auto-selected config **{selected_config}** (only one attached)."
                )
            elif len(config_names) > 1:
                view = _ConfigSelectView(config_names)
                config_msg = await sub_channel.send(
                    "🔧 Select the points configuration for this session:",
                    view=view,
                )
                await view.wait()
                selected_config = view.selected
                try:
                    await config_msg.edit(
                        content=f"🔧 Config selected: **{selected_config}**", view=None
                    )
                except discord.HTTPException:
                    pass
            else:
                # No configs attached — this should have been caught at approval but handle gracefully
                log.warning(
                    "run_result_submission_job: no configs attached to season %s", season_id
                )
                await sub_channel.send(
                    "⚠️ No points configuration attached to this season. "
                    "Results will be saved without a config."
                )

            # Convert parsed rows to dicts for DB insertion
            if session_type.is_qualifying:
                driver_rows_data = [
                    _row_dict_from_qualifying(r)   # type: ignore[arg-type]
                    for r in parsed_rows
                ]
            else:
                driver_rows_data = [
                    _row_dict_from_race(r)  # type: ignore[arg-type]
                    for r in parsed_rows
                ]

            await save_session_result(
                db_path=db_path,
                round_id=round_id,
                division_id=division_id,
                session_type=session_type,
                status="ACTIVE",
                config_name=selected_config,
                submitted_by=msg.author.id,
                driver_rows=driver_rows_data,
            )
            await sub_channel.send(f"✅ **{label}** results saved.")
            break  # advance to next session

    # ------------------------------------------------------------------
    # 9. Compute standings and post results
    # ------------------------------------------------------------------
    try:
        from services import standings_service, results_post_service  # lazy imports

        await standings_service.compute_and_persist_round(db_path, round_id, division_id)

        div_with_channels = await bot.season_service.get_divisions_with_results_config(season_id)  # type: ignore[attr-defined]
        div_obj = next((d for d in div_with_channels if d.id == division_id), None)
        if div_obj and div_obj.results_channel_id:
            rc = guild.get_channel(div_obj.results_channel_id)
            if rc:
                await results_post_service.post_round_results(
                    db_path, round_id, division_id, rc, guild
                )
        if div_obj and div_obj.standings_channel_id:
            sc = guild.get_channel(div_obj.standings_channel_id)
            if sc:
                from services.standings_service import compute_driver_standings, compute_team_standings
                driver_snaps = await compute_driver_standings(db_path, division_id, round_id)
                team_snaps = await compute_team_standings(db_path, division_id, round_id)
                await results_post_service.post_standings(
                    db_path, division_id, round_id, sc, driver_snaps, team_snaps, guild,
                    show_reserves=True,
                )
    except ImportError:
        log.info(
            "run_result_submission_job: standings/post services not yet available "
            "(round %s) — results saved, posting skipped",
            round_id,
        )
    except Exception:
        log.exception(
            "run_result_submission_job: error during standings/post for round %s",
            round_id,
        )

    # ------------------------------------------------------------------
    # 10. Close submission channel
    # ------------------------------------------------------------------
    await sub_channel.send("✅ All sessions complete. Closing this channel.")
    await close_submission_channel(sub_channel.id, round_id, guild, db_path)
