"""results_formatter.py — Format results tables and standings for Discord output.

**One rendering, two presentations** (Constitution XIV.7, v4.4.0). A value the textual table
and the results *graphic* both draw is produced here, once, and each presenter places it. The
row builders below — :func:`build_qualifying_rows` and :func:`build_race_rows` — own every
such derivation: the reference-lap search, the interval rule, the laps-behind wording, the
displacement of a time by an outcome literal, and the rendering of a time penalty. Neither
presenter may restate any of them.

A cell of ``None`` means **this value does not apply to this entry**. It is not "missing" and
not "undeterminable". The textual table renders it as :data:`NOT_APPLICABLE`; the graphic
empties the field, quietly, and no mandatory field is thereby offended (XIV.3).

Three things deliberately stay with each presenter, and no row carries them:

* the **mention substitution** — the table draws ``<@id>``, the graphic draws names (XIV.16);
* the **sanction phase rule** — the wip-spec makes the emptying of a sanction field for a
  phase not yet closed the one value the graphic carries that the table does not;
* the **placeholder** for ``None``.

See specs/039-results-image-generation/contracts/shared-rendering.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from models.points_config import SessionType
from models.session_result import (
    DriverSessionResult,
    OutcomeModifier,
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

#: What a presenter draws where a cell is ``None``. The textual table writes it; the graphic
#: empties the field instead and never draws it (FR-013).
NOT_APPLICABLE = "—"


def parse_lap_time(s: str) -> int | None:
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


def render_lap_time(ms: int) -> str:
    """Format ms as M:SS.mmm or H:MM:SS.mmm — the hours shown only where there are any."""
    total_s, ms_part = divmod(ms, 1000)
    total_m, secs = divmod(total_s, 60)
    hours, mins = divmod(total_m, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}.{ms_part:03d}"
    return f"{mins}:{secs:02d}.{ms_part:03d}"


def render_gap(gap_ms: int) -> str:
    """Format a gap in ms as +SS.mmm, or +M:SS.mmm and +H:MM:SS.mmm where they are needed."""
    total_s, ms_part = divmod(gap_ms, 1000)
    total_m, secs = divmod(total_s, 60)
    hours, mins = divmod(total_m, 60)
    if hours:
        return f"+{hours}:{mins:02d}:{secs:02d}.{ms_part:03d}"
    if mins:
        return f"+{mins}:{secs:02d}.{ms_part:03d}"
    return f"+{secs}.{ms_part:03d}"


def render_time_penalty(ms: int) -> str | None:
    """A time penalty in signed seconds, to the precision it was recorded with.

    A whole number of seconds carries no decimal part and a fraction is rendered to three
    decimal places: five seconds is ``+5s`` and five and a half ``+5.500s``. A penalty is
    **never** rounded to a whole second for display — the in-game penalty column carries a
    fraction more often than not.

    Returns None where no penalty was applied, which each presenter renders its own way.
    """
    if ms == 0:
        return None
    sign = "+" if ms > 0 else "-"
    whole, fraction = divmod(abs(ms), 1000)
    if fraction:
        return f"{sign}{whole}.{fraction:03d}s"
    return f"{sign}{whole}s"


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

@dataclass(frozen=True)
class QualifyingRow:
    """One row of a qualifying classification, every cell already rendered.

    ``None`` means the value does not apply to this entry (see the module docstring).
    """

    position: int
    driver_user_id: int
    team_role_id: int
    tyre: str | None
    best_lap: str | None
    gap: str | None
    #: The sanction the penalty phase applied, before any phase-closure rule.
    postrace_penalty: str | None
    #: The sanction the appeal phase applied, before any phase-closure rule.
    appeal_penalty: str | None
    points: int


@dataclass(frozen=True)
class RaceRow:
    """One row of a race classification, every cell already rendered."""

    position: int
    driver_user_id: int
    team_role_id: int
    #: Total race time for the first-placed entry, an interval, a lap count, or an outcome.
    time: str | None
    fastest_lap: str | None
    ingame_penalty: str | None
    postrace_penalty: str | None
    appeal_penalty: str | None
    points: int
    holds_fastest_lap: bool


def build_qualifying_rows(
    driver_rows: list[QualifyingSessionResult],
    points_by_driver: dict[int, int],
    *,
    dsq_phase_map: dict[int, str] | None = None,
) -> list[QualifyingRow]:
    """Resolve every cell of a qualifying classification, ordered by finishing position.

    The **reference lap** is the best lap of the first-placed entry, or — where that entry
    holds none — the best lap of the first entry of the classification that does. The entry
    holding it carries no gap, and where no entry of the session holds a lap at all every gap
    is ``None``.
    """
    sorted_rows = sorted(driver_rows, key=lambda r: r.finishing_position)

    reference_ms: int | None = None
    for row in sorted_rows:
        ms = parse_lap_time(row.best_lap or "")
        if ms is not None:
            reference_ms = ms
            break

    built: list[QualifyingRow] = []
    for row in sorted_rows:
        # An entry that did not finish, did not start or was disqualified carries that
        # outcome as the text of its best-lap field, **whatever time may have been recorded
        # for it** — the outcome displaces the lap rather than standing in for a missing one.
        # A classified entry holding no lap carries nothing, and is drawn as a cell that does
        # not apply.
        best_lap = (
            row.best_lap or None
            if row.outcome is OutcomeModifier.CLASSIFIED
            else row.outcome.value
        )

        gap: str | None = None
        if row.finishing_position != 1 and row.outcome.is_points_eligible:
            own_ms = parse_lap_time(row.best_lap or "")
            if own_ms is not None and reference_ms is not None:
                gap = render_gap(own_ms - reference_ms)

        phase = (dsq_phase_map or {}).get(row.id)
        built.append(
            QualifyingRow(
                position=row.finishing_position,
                driver_user_id=row.driver_user_id,
                team_role_id=row.team_role_id,
                tyre=row.tyre or None,
                best_lap=best_lap,
                gap=gap,
                # Qualifying accepts no time penalty, only a disqualification.
                postrace_penalty="DSQ" if phase == "PENALTY" else None,
                appeal_penalty="DSQ" if phase == "APPEAL" else None,
                points=points_by_driver.get(row.driver_user_id, 0),
            )
        )
    return built


def build_race_rows(
    driver_rows: list[RaceSessionResult],
    points_by_driver: dict[int, int],
    *,
    dsq_phase_map: dict[int, str] | None = None,
) -> list[RaceRow]:
    """Resolve every cell of a race classification, ordered by finishing position.

    The time column carries, in this order of precedence: the outcome literal of an entry
    that did not finish, did not start or was disqualified; the count of laps an entry
    finished behind, singular for one and plural beyond; the total race time for the
    first-placed entry; and the interval to that entry for anyone else. Where no time is
    recorded for the first-placed entry, every entry carries its own total race time.
    """
    sorted_rows = sorted(driver_rows, key=lambda r: r.finishing_position)

    # The reference is the **first-placed entry's** total time and no one else's. Where that
    # entry records none, there is no interval to state and every entry carries its own total
    # race time instead. This is deliberately unlike the qualifying reference lap, which does
    # fall through to the first entry of the classification that holds one.
    leader_total_ms = sorted_rows[0].total_time_ms if sorted_rows else None

    built: list[RaceRow] = []
    for row in sorted_rows:
        if row.outcome in (row.outcome.DNF, row.outcome.DNS, row.outcome.DSQ):
            time_cell: str | None = row.outcome.value
        elif row.laps_behind is not None:
            lap_word = "Lap" if row.laps_behind == 1 else "Laps"
            time_cell = f"+{row.laps_behind} {lap_word}"
        elif row.total_time_ms is not None:
            if row.finishing_position == 1 or leader_total_ms is None:
                time_cell = render_lap_time(row.total_time_ms)
            else:
                time_cell = render_gap(row.total_time_ms - leader_total_ms)
        else:
            time_cell = None

        phase = (dsq_phase_map or {}).get(row.id)
        built.append(
            RaceRow(
                position=row.finishing_position,
                driver_user_id=row.driver_user_id,
                team_role_id=row.team_role_id,
                time=time_cell,
                fastest_lap=(row.fastest_lap or "").strip() or None,
                ingame_penalty=render_time_penalty(row.ingame_time_penalties_ms),
                postrace_penalty=(
                    "DSQ"
                    if phase == "PENALTY"
                    else render_time_penalty(row.postrace_time_penalties_ms)
                ),
                appeal_penalty=(
                    "DSQ"
                    if phase == "APPEAL"
                    else render_time_penalty(row.appeal_time_penalties_ms)
                ),
                points=points_by_driver.get(row.driver_user_id, 0),
                holds_fastest_lap=row.fastest_lap_bonus > 0,
            )
        )
    return built


def fastest_lap_holder(rows: list[RaceRow]) -> RaceRow | None:
    """The entry the session conferred the fastest-lap bonus on, or None where none did."""
    for row in rows:
        if row.holds_fastest_lap:
            return row
    return None


def format_qualifying_table(
    driver_rows: list[QualifyingSessionResult],
    points_by_driver: dict[int, int],
    member_display: dict[int, str] | None = None,
    team_display: dict[int, str] | None = None,
    dsq_phase_map: dict[int, str] | None = None,
) -> str:
    """Render a qualifying result as a plain-text mention list.

    Format per line:
      {pos}. @Driver (@&Team) — {tyre} — {best_lap} — {gap} — {postrace_pen} — {appeal_pen} — {pts} pts

    Every value is resolved by :func:`build_qualifying_rows`; this function places them and
    computes nothing of its own (Constitution XIV.7). A cell the builder returns as ``None``
    is drawn as :data:`NOT_APPLICABLE` here and emptied on the graphic.
    """
    rows = build_qualifying_rows(
        driver_rows, points_by_driver, dsq_phase_map=dsq_phase_map
    )

    lines: list[str] = []
    for row in rows:
        driver_ref = (member_display or {}).get(row.driver_user_id) or f"<@{row.driver_user_id}>"
        team_ref = (team_display or {}).get(row.team_role_id) or f"<@&{row.team_role_id}>"
        lines.append(
            f"**{row.position}.** {driver_ref} ({team_ref})"
            f" — {row.tyre or NOT_APPLICABLE}"
            f" — {row.best_lap or NOT_APPLICABLE}"
            f" — {row.gap or NOT_APPLICABLE}"
            f" — {row.postrace_penalty or NOT_APPLICABLE}"
            f" — {row.appeal_penalty or NOT_APPLICABLE}"
            f" — **{row.points} pts**"
        )
    return "\n".join(lines)


def format_race_table(
    driver_rows: list[RaceSessionResult],
    points_by_driver: dict[int, int],
    member_display: dict[int, str] | None = None,
    team_display: dict[int, str] | None = None,
    dsq_phase_map: dict[int, str] | None = None,
) -> str:
    """Render a race result as a plain-text mention list.

    Format per line:
      {pos}. @Driver (@&Team) — {total_time_or_interval} — {fastest_lap} — {ingame_pen} — {postrace_pen} — {appeal_pen} — {pts} pts

    Every value is resolved by :func:`build_race_rows`; this function places them and computes
    nothing of its own (Constitution XIV.7). A cell the builder returns as ``None`` is drawn as
    :data:`NOT_APPLICABLE` here and emptied on the graphic.

    A fastest-lap footnote is appended when the session conferred the bonus.
    """
    rows = build_race_rows(driver_rows, points_by_driver, dsq_phase_map=dsq_phase_map)

    lines: list[str] = []
    for row in rows:
        driver_ref = (member_display or {}).get(row.driver_user_id) or f"<@{row.driver_user_id}>"
        team_ref = (team_display or {}).get(row.team_role_id) or f"<@&{row.team_role_id}>"
        lines.append(
            f"**{row.position}.** {driver_ref} ({team_ref})"
            f" — {row.time or NOT_APPLICABLE}"
            f" — {row.fastest_lap or NOT_APPLICABLE}"
            f" — {row.ingame_penalty or NOT_APPLICABLE}"
            f" — {row.postrace_penalty or NOT_APPLICABLE}"
            f" — {row.appeal_penalty or NOT_APPLICABLE}"
            f" — **{row.points} pts**"
        )

    result = "\n".join(lines)
    holder = fastest_lap_holder(rows)
    if holder is not None:
        holder_ref = (
            (member_display or {}).get(holder.driver_user_id)
            or f"<@{holder.driver_user_id}>"
        )
        result += (
            f"\n🏎 **Fastest lap** — {holder_ref} — "
            f"{holder.fastest_lap or NOT_APPLICABLE}"
        )
    return result


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The three movement columns
#
# The textual standings draw none of these today. They live here all the same, beside the
# renderers they would sit with, so that adopting a column into the text path is a call and
# not a reimplementation — which is the shared-rendering half of Constitution XIV.7.
#
# The *values* are derived in ``services/standings_service.derive_movement``; these turn one
# into the string a field carries. See
# specs/040-standings-image-generation/contracts/derived-columns.md.
# ---------------------------------------------------------------------------


def format_gap_to_leader(gap: int, *, is_leader: bool) -> str:
    """The points separating an entry from the leader, with a leading minus.

    Empty for the leader itself: there is no gap to draw, and XIV.3 has the graphic empty a
    field rather than draw a placeholder where a value does not apply.
    """
    if is_leader:
        return ""
    return f"-{gap}"


def format_position_change(change: int) -> str:
    """The number of positions gained or lost, **without** a sign.

    The direction is carried by the marker image beside it, not by the number, so a template
    drawing the number alone still reads correctly. "0" where the entry neither gained nor
    lost — a determined value, not an absent one.
    """
    return str(change)


def format_previous_position(position: int) -> str:
    """The position an entry held in the reference round."""
    return str(position)


def format_grid_cell(row: QualifyingSessionResult | RaceSessionResult) -> str:
    """One cell of a standings grid: a finishing position, or the recorded outcome literal.

    Reads ``outcome`` and never ``finishing_position`` for a non-classified entry — a driver
    dropped to the bottom by a disqualification carries "DSQ", never the position the drop
    gave them. Shared with the image standings grid so the literal is rendered in one place
    (Constitution XIV.7).
    """
    if row.outcome is OutcomeModifier.CLASSIFIED:
        return str(row.finishing_position)
    return row.outcome.value


def driver_is_drawn(
    snapshot: DriverStandingsSnapshot,
    reserve_user_ids: set[int],
    show_reserves: bool,
) -> bool:
    """Whether *snapshot* belongs in the driver classification.

    Non-reserve drivers are always drawn, at zero points as at any other. A reserve driver is
    drawn only where the division's reserves toggle is on **and** they hold points or have
    taken part in a race.

    Extracted so the graphic composes its classification by calling the same rule the textual
    standings compose theirs by, rather than restating it (Constitution XIV.7). The two
    cannot disagree about who is in the championship.
    """
    if snapshot.driver_user_id in reserve_user_ids:
        if not show_reserves:
            return False
        return snapshot.total_points != 0 or snapshot.race_participant
    return True


def format_driver_standings(
    snapshots: list[DriverStandingsSnapshot],
    reserve_user_ids: set[int],
    show_reserves: bool,
    driver_display: dict[int, str] | None = None,
) -> str:
    """Render driver standings as a ranked mention list.

    Composition is :func:`driver_is_drawn`. Format:
    ``{pos}. @Driver — **{total_points} pts**``
    """
    sorted_snaps = sorted(snapshots, key=lambda s: s.standing_position)
    lines: list[str] = []
    for snap in sorted_snaps:
        if not driver_is_drawn(snap, reserve_user_ids, show_reserves):
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
