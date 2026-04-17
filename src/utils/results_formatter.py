"""results_formatter.py — Format results tables and standings for Discord output."""
from __future__ import annotations

import re

from models.points_config import SessionType
from models.session_result import (
    DriverSessionResult,
    QualifyingSessionResult,
    RaceSessionResult,
)
from models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot

_SESSION_LABELS: dict[SessionType, str] = {
    SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
    SessionType.SPRINT_RACE: "Sprint Race",
    SessionType.FEATURE_QUALIFYING: "Feature Qualifying",
    SessionType.FEATURE_RACE: "Feature Race",
}

_LAP_TIME_RE = re.compile(
    r"^(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d+)(?:\.(?P<ms>\d+))?$"
)


def _best_lap_to_ms(s: str) -> int | None:
    """Parse an absolute lap-time string to ms. Returns None on failure."""
    m = _LAP_TIME_RE.match((s or "").strip())
    if not m:
        return None
    h = int(m.group("h") or 0)
    mins = int(m.group("m") or 0)
    secs = int(m.group("s") or 0)
    ms_raw = m.group("ms") or "0"
    ms = int(ms_raw.ljust(3, "0")[:3])
    return (h * 3600 + mins * 60 + secs) * 1000 + ms


def _ms_to_lap_time(ms: int) -> str:
    """Format ms as M:SS.mmm or H:MM:SS.mmm."""
    total_s, ms_part = divmod(ms, 1000)
    total_m, secs = divmod(total_s, 60)
    hours, mins = divmod(total_m, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}.{ms_part:03d}"
    return f"{mins}:{secs:02d}.{ms_part:03d}"


def _ms_to_gap(gap_ms: int) -> str:
    """Format a gap in ms as +SS.mmm or +M:SS.mmm etc."""
    total_s, ms_part = divmod(gap_ms, 1000)
    total_m, secs = divmod(total_s, 60)
    hours, mins = divmod(total_m, 60)
    if hours:
        return f"+{hours}:{mins:02d}:{secs:02d}.{ms_part:03d}"
    if mins:
        return f"+{mins}:{secs:02d}.{ms_part:03d}"
    return f"+{secs}.{ms_part:03d}"


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
    driver_rows: list[QualifyingSessionResult],
    points_by_driver: dict[int, int],
    member_display: dict[int, str] | None = None,
    team_display: dict[int, str] | None = None,
) -> str:
    """Render a qualifying result as a plain-text mention list.

    Format per line: {pos}. @Driver (@&Team) — {tyre} — {best_lap} — {gap} — {pts} pts

    Gap is computed on the fly as (driver_best_lap_ms - P1_best_lap_ms).  P1 shows "—"
    for gap.  Non-classified drivers show their outcome in the best_lap field and "—"
    for gap.
    """
    sorted_rows = sorted(driver_rows, key=lambda r: r.finishing_position)

    # Find P1's best_lap_ms for gap computation
    p1_ms: int | None = None
    for row in sorted_rows:
        ms = _best_lap_to_ms(row.best_lap or "")
        if ms is not None:
            p1_ms = ms
            break

    lines: list[str] = []
    for row in sorted_rows:
        driver_ref = (member_display or {}).get(row.driver_user_id) or f"<@{row.driver_user_id}>"
        team_ref = (team_display or {}).get(row.team_role_id) or f"<@&{row.team_role_id}>"
        tyre = row.tyre or "—"
        best_lap_display = row.best_lap or row.outcome.value

        # Gap: compute for P2+; "—" for P1 and non-classified
        gap_display = "—"
        if row.finishing_position != 1 and row.outcome.is_points_eligible:
            driver_ms = _best_lap_to_ms(row.best_lap or "")
            if driver_ms is not None and p1_ms is not None:
                gap_display = _ms_to_gap(driver_ms - p1_ms)

        pts = points_by_driver.get(row.driver_user_id, 0)
        lines.append(
            f"**{row.finishing_position}.** {driver_ref} ({team_ref})"
            f" — {tyre} — {best_lap_display} — {gap_display} — **{pts} pts**"
        )
    return "\n".join(lines)


def format_race_table(
    driver_rows: list[RaceSessionResult],
    points_by_driver: dict[int, int],
    member_display: dict[int, str] | None = None,
    team_display: dict[int, str] | None = None,
) -> str:
    """Render a race result as a plain-text mention list.

    Format per line:
      {pos}. @Driver (@&Team) — {total_time_or_interval} — {fastest_lap} — {penalty} — {pts} pts

    Display rules:
    - P1: total_time = base_time_ms + ingame + postrace + appeal, formatted as M:SS.mmm
    - P2+ classified non-lapped: interval = driver_total_ms - P1_total_ms, as +SS.mmm
    - Lapped: shows "+N Lap(s)"
    - DNS/DNF/DSQ: shows the outcome literal
    - penalty column: postrace + appeal in seconds (shown as e.g. "+5s" or "—" when 0)

    A fastest-lap footnote is appended when any driver has fastest_lap_bonus > 0.
    """
    sorted_rows = sorted(driver_rows, key=lambda r: r.finishing_position)

    # Find P1's total_time_ms for interval computation
    p1_total_ms: int | None = None
    for row in sorted_rows:
        if row.total_time_ms is not None:
            p1_total_ms = row.total_time_ms
            break

    lines: list[str] = []
    fl_driver_id: int | None = None
    fl_time: str | None = None

    for row in sorted_rows:
        driver_ref = (member_display or {}).get(row.driver_user_id) or f"<@{row.driver_user_id}>"
        team_ref = (team_display or {}).get(row.team_role_id) or f"<@&{row.team_role_id}>"

        # Time / interval column
        if row.outcome in (row.outcome.DNF, row.outcome.DNS, row.outcome.DSQ):
            time_display = row.outcome.value
        elif row.laps_behind is not None:
            lap_word = "Lap" if row.laps_behind == 1 else "Laps"
            time_display = f"+{row.laps_behind} {lap_word}"
        elif row.total_time_ms is not None:
            if row.finishing_position == 1 or p1_total_ms is None:
                time_display = _ms_to_lap_time(row.total_time_ms)
            else:
                time_display = _ms_to_gap(row.total_time_ms - p1_total_ms)
        else:
            time_display = "—"

        # Fastest lap column
        fl = (row.fastest_lap or "").strip()
        fl_display = fl if fl else "—"

        # Post-race penalty column: postrace + appeal in whole seconds
        total_penalty_ms = row.postrace_time_penalties_ms + row.appeal_time_penalties_ms
        if total_penalty_ms != 0:
            sign = "+" if total_penalty_ms > 0 else ""
            pen_display = f"{sign}{total_penalty_ms // 1000}s"
        else:
            pen_display = "—"

        pts = points_by_driver.get(row.driver_user_id, 0)
        lines.append(
            f"**{row.finishing_position}.** {driver_ref} ({team_ref})"
            f" — {time_display} — {fl_display} — {pen_display} — **{pts} pts**"
        )
        if row.fastest_lap_bonus > 0:
            fl_driver_id = row.driver_user_id
            fl_time = row.fastest_lap

    result = "\n".join(lines)
    if fl_driver_id is not None:
        fl_driver_ref = (member_display or {}).get(fl_driver_id) or f"<@{fl_driver_id}>"
        result += f"\n🏎 **Fastest lap** — {fl_driver_ref} — {fl_time or '—'}"
    return result


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

def format_driver_standings(
    snapshots: list[DriverStandingsSnapshot],
    reserve_user_ids: set[int],
    show_reserves: bool,
    driver_display: dict[int, str] | None = None,
) -> str:
    """Render driver standings as a ranked mention list.

    Non-reserve drivers are always shown (even at 0 points).
    Reserve drivers are shown only when they have points AND ``show_reserves=True``.
    Format: ``{pos}. @Driver — **{total_points} pts**``
    """
    sorted_snaps = sorted(snapshots, key=lambda s: s.standing_position)
    lines: list[str] = []
    for snap in sorted_snaps:
        if snap.driver_user_id in reserve_user_ids:
            if (snap.total_points == 0 and not snap.race_participant) or not show_reserves:
                continue
        driver_ref = (driver_display or {}).get(snap.driver_user_id) or f"<@{snap.driver_user_id}>"
        lines.append(f"{snap.standing_position}. {driver_ref} — **{snap.total_points} pts**")
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
