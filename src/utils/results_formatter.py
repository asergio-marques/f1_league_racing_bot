"""results_formatter.py — Format results tables and standings for Discord output."""
from __future__ import annotations

from models.points_config import SessionType
from models.session_result import DriverSessionResult
from models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot

_SESSION_LABELS: dict[SessionType, str] = {
    SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
    SessionType.SPRINT_RACE: "Sprint Race",
    SessionType.FEATURE_QUALIFYING: "Feature Qualifying",
    SessionType.FEATURE_RACE: "Feature Race",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collapse_trailing_zeros(
    rows: list[tuple[int, int]],
) -> list[tuple[str, int]]:
    """Collapse trailing zero-point positions into a single sentinel row.

    Example: [(1,25),(2,18),(3,0),(4,0)] → [("1",25),("2",18),("3+",0)]

    Returns all rows up to and including the last non-zero position as
    ``("{pos}", pts)`` tuples. If any trailing zeros remain, appends a
    ``("{n}+", 0)`` sentinel using the next position after the last non-zero.
    If all rows are zero, returns a single ``("1+", 0)`` sentinel.
    """
    if not rows:
        return []

    last_nonzero = -1
    for i, (_, pts) in enumerate(rows):
        if pts > 0:
            last_nonzero = i

    if last_nonzero == -1:
        # All zeros
        return [(f"{rows[0][0]}+", 0)]

    result: list[tuple[str, int]] = [
        (str(pos), pts) for pos, pts in rows[: last_nonzero + 1]
    ]

    if last_nonzero < len(rows) - 1:
        next_pos = rows[last_nonzero + 1][0]
        result.append((f"{next_pos}+", 0))

    return result


def format_session_label(session_type: SessionType, *, is_sprint: bool = True) -> str:
    """Return the human-readable label for a session type.

    When ``is_sprint=False`` the "Feature " prefix is dropped so that
    FEATURE_QUALIFYING → "Qualifying" and FEATURE_RACE → "Race".
    """
    label = _SESSION_LABELS.get(session_type, session_type.value.replace("_", " ").title())
    if not is_sprint:
        label = label.removeprefix("Feature ").strip()
    return label


# ---------------------------------------------------------------------------
# Session result tables
# ---------------------------------------------------------------------------

def format_qualifying_table(
    driver_rows: list[DriverSessionResult],
    points_by_driver: dict[int, int],
    member_display: dict[int, str] | None = None,
    team_display: dict[int, str] | None = None,
) -> str:
    """Render a qualifying result as a fixed-width code-block table.

    Header: Pos | Driver | Team | Tyre | Best Lap | Gap | Points
    Uses display names when member_display/team_display are provided,
    otherwise falls back to Discord mention strings.
    """
    sorted_rows = sorted(driver_rows, key=lambda r: r.finishing_position)
    header = f"{'Pos':<4} {'Driver':<20} {'Team':<15} {'Tyre':<6} {'Best Lap':<10} {'Gap':<10} {'Points'}"
    separator = "-" * len(header)
    lines: list[str] = [header, separator]
    for row in sorted_rows:
        driver_name = (member_display or {}).get(row.driver_user_id, f"<@{row.driver_user_id}>")
        team_name = (team_display or {}).get(row.team_role_id, f"<@&{row.team_role_id}>")
        tyre = row.tyre or "—"
        best_lap = row.best_lap or row.outcome.value
        gap = row.gap or "—"
        pts = points_by_driver.get(row.driver_user_id, 0)
        lines.append(
            f"{row.finishing_position:<4} {driver_name:<20} {team_name:<15} {tyre:<6} {best_lap:<10} {gap:<10} {pts} pts"
        )
    return "```\n" + "\n".join(lines) + "\n```"


def format_race_table(
    driver_rows: list[DriverSessionResult],
    points_by_driver: dict[int, int],
    member_display: dict[int, str] | None = None,
    team_display: dict[int, str] | None = None,
) -> str:
    """Render a race result as a fixed-width code-block table.

    Header: Pos | Driver | Team | Total Time | Fastest Lap | Time Penalties | Points
    Uses display names when member_display/team_display are provided,
    otherwise falls back to Discord mention strings.
    """
    sorted_rows = sorted(driver_rows, key=lambda r: r.finishing_position)
    header = f"{'Pos':<4} {'Driver':<20} {'Team':<15} {'Total Time':<14} {'Fastest Lap':<13} {'Time Penalties':<16} {'Points'}"
    separator = "-" * len(header)
    lines: list[str] = [header, separator]
    for row in sorted_rows:
        driver_name = (member_display or {}).get(row.driver_user_id, f"<@{row.driver_user_id}>")
        team_name = (team_display or {}).get(row.team_role_id, f"<@&{row.team_role_id}>")
        total_time = row.total_time or row.outcome.value
        fl = row.fastest_lap or "—"
        tp = row.time_penalties or "—"
        pts = points_by_driver.get(row.driver_user_id, 0)
        lines.append(
            f"{row.finishing_position:<4} {driver_name:<20} {team_name:<15} {total_time:<14} {fl:<13} {tp:<16} {pts} pts"
        )
    return "```\n" + "\n".join(lines) + "\n```"


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

def format_driver_standings(
    snapshots: list[DriverStandingsSnapshot],
    reserve_user_ids: set[int],
    show_reserves: bool,
) -> str:
    """Render driver standings as a ranked mention list.

    Omits reserve drivers when ``show_reserves=False``.
    Format: ``{pos}. @Driver — **{total_points} pts**``
    """
    sorted_snaps = sorted(snapshots, key=lambda s: s.standing_position)
    lines: list[str] = []
    for snap in sorted_snaps:
        if not show_reserves and snap.driver_user_id in reserve_user_ids:
            continue
        lines.append(f"{snap.standing_position}. <@{snap.driver_user_id}> — **{snap.total_points} pts**")
    return "\n".join(lines) if lines else "No standings available."


def format_team_standings(
    snapshots: list[TeamStandingsSnapshot],
) -> str:
    """Render team standings as a ranked mention list.

    Format: ``{pos}. @&Team — **{total_points} pts**``
    """
    sorted_snaps = sorted(snapshots, key=lambda s: s.standing_position)
    lines: list[str] = []
    for snap in sorted_snaps:
        lines.append(f"{snap.standing_position}. <@&{snap.team_role_id}> — **{snap.total_points} pts**")
    return "\n".join(lines) if lines else "No standings available."


# ---------------------------------------------------------------------------
# Config view
# ---------------------------------------------------------------------------

def format_config_view(
    config_name: str,
    entries_by_session: dict[str, list[tuple[str, int]]],
    fl_by_session: dict[str, tuple[int, int | None]],
) -> str:
    """Render a points config as a human-readable summary.

    ``entries_by_session``: maps session label → pre-collapsed [(pos_str, points), ...]
    ``fl_by_session``: maps session label → (fl_points, fl_position_limit | None)
    Callers are responsible for collapsing trailing zeros before passing.
    """
    if not entries_by_session:
        return f"**{config_name}** — no entries configured."

    lines = [f"**{config_name}**"]

    for session_label, point_rows in sorted(entries_by_session.items()):
        lines.append(f"\n*{session_label}*")
        for pos_str, pts in point_rows:
            lines.append(f"  P{pos_str}: {pts} pts")

        # FL bonus
        if session_label in fl_by_session:
            fl_pts, fl_limit = fl_by_session[session_label]
            limit_str = f" (top {fl_limit} eligible)" if fl_limit else ""
            lines.append(f"  FL bonus: {fl_pts} pts{limit_str}")

    return "\n".join(lines)
