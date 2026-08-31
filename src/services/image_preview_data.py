"""Fabricated outcomes for the `/images test` previews (045).

A preview draws the league's own division, round, teams and drivers. What it cannot draw
from the league's records is the *outcome* — a classification of a session not yet run, a
forecast not yet made, an attendance record not yet kept, a verdict no steward has issued.
This module fabricates exactly those, and nothing else.

Nothing here reads the database and nothing here writes to it. The values are handed to the
same ``resolve_drawing`` each posting path calls, so a preview and a post differ in where
the outcome came from and in nothing else.

The prose constants are inherited from the withdrawn sample-data module. They survive it
because the wrapping of a steward's text is still the verdict graphic's whole difficulty,
and text at five lengths is still the only way to judge it.
"""
from __future__ import annotations

#: A display name of the sort no league controls the length of. Any template field carrying
#: a driver name should be bounded, and this is what proves it. Used for a fabricated driver
#: so that the bound is exercised without waiting for a league to seat someone unlucky.
LONG_DRIVER_NAME = "Bartholomew Fotheringay-Pemberton III"

#: One line of prose, comfortably inside any rectangle a league would draw.
VERDICT_TEXT_SHORT = "Contact at turn four."

#: Enough to fill a six-line box at the size the packaged template declares.
VERDICT_TEXT_FULL = (
    "The stewards reviewed onboard footage from both cars and from the car following. "
    "Car 14 was alongside at the apex and had the corner. The contact was avoidable."
)

#: A little more than the box admits, so the reduction of the font size can be judged.
VERDICT_TEXT_OVER = VERDICT_TEXT_FULL + (
    " The driver was warned for the same manoeuvre in the preceding round, which the panel "
    "took into account when setting the length of the penalty."
)

#: An order of magnitude too much, so the floor, the cut and the notice can be judged. It
#: carries the steward's own paragraph breaks, which the graphic keeps as the message does.
VERDICT_TEXT_HUGE = "\n\n".join([VERDICT_TEXT_OVER] * 12)

#: What the textual announcement carries where the steward entered neither, **without** the
#: channel emphasis that message applies (XIV.16).
VERDICT_TEXT_NOT_PROVIDED = "(not provided)"

#: The five lengths a verdict preview draws its free text at, shortest first. The fifth is
#: the case where the steward entered nothing at all.
VERDICT_TEXT_CASES: tuple[str, ...] = (
    VERDICT_TEXT_SHORT,
    VERDICT_TEXT_FULL,
    VERDICT_TEXT_OVER,
    VERDICT_TEXT_HUGE,
    VERDICT_TEXT_NOT_PROVIDED,
)


# ── Fabricated outcomes ───────────────────────────────────────────────────
#
# What a league cannot configure in advance, and a preview must therefore invent: a
# classification of a session not yet run, a forecast not yet made, an attendance record
# not yet kept, a verdict no steward has issued.
#
# Everything here is deterministic in its inputs. A manager judging a drawing does not need
# different numbers on every invocation, and a reproducible picture is far easier to compare
# against a template than a shifting one.

from models.session import MAX_SLOTS, SESSIONS_BY_FORMAT, SessionType  # noqa: E402
from models.session_result import (  # noqa: E402
    OutcomeModifier,
    QualifyingSessionResult,
    RaceSessionResult,
)

#: The three types a phase 2 forecast draws for a session.
PHASE2_TYPES = ("sunny", "mixed", "rain")

#: The five concrete conditions a phase 3 slot draws. All five must appear across a round so
#: that every icon can be judged in one picture (FR-031).
PHASE3_SLOTS = ("Clear", "Light Cloud", "Overcast", "Wet", "Very Wet")

#: The sanctions the module can record and issue, and no others (FR-034). "No further
#: action", a qualifying ban and a race ban are deliberately absent: the steward and results
#: modules cannot record them, and a preview must never draw what the bot cannot issue.
VERDICT_SANCTIONS = (
    ("TIME", 5),
    ("TIME", 10),
    ("TIME", -3),
    ("DSQ", None),
)


def sessions_for(round_format) -> list[SessionType]:
    """The sessions a round of *round_format* is run over, read and never restated."""
    from models.round import RoundFormat

    raw = str(getattr(round_format, "value", round_format) or "NORMAL")
    try:
        key = RoundFormat(raw)
    except ValueError:
        key = RoundFormat.NORMAL
    return list(SESSIONS_BY_FORMAT.get(key, []))


# ── Classifications (FR-023, FR-024) ──────────────────────────────────────


def fabricate_qualifying_rows(drivers, team_role_ids, points_map):
    """A believable qualifying classification over *drivers*.

    Every driver appears exactly once, positions run 1..n with no gap, and the best laps
    ascend with position so the gaps the formatter derives agree with the order.
    """
    rows = []
    count = len(drivers)
    for position, driver in enumerate(drivers, start=1):
        seconds = 88.400 + (position - 1) * 0.400
        best_lap = f"1:{int(seconds - 60):02d}.{int(round((seconds % 1) * 1000)):03d}"

        outcome = OutcomeModifier.CLASSIFIED
        # The last driver of a large enough field set no time, so that case is drawn too.
        if count >= 4 and position == count:
            outcome = OutcomeModifier.DNS
            best_lap = None

        rows.append(
            QualifyingSessionResult(
                id=position,
                session_result_id=1,
                driver_user_id=driver.key,
                team_role_id=team_role_ids.get(driver.team_name, 0),
                finishing_position=position,
                outcome=outcome,
                # One driver with no tyre recorded, so the absent-datum case is drawn.
                tyre=None if position == 2 else ("Soft" if position % 2 else "Medium"),
                best_lap=best_lap,
                points_awarded=points_map.get(position, 0),
            )
        )
    return rows


def fabricate_race_rows(drivers, team_role_ids, points_map, *, fastest_lap_position=2):
    """A believable race classification over *drivers*.

    The leader carries a total race time; everyone else an interval growing with position.
    A driver who did not finish is placed last, as the results module renumbers them, so
    the outcome literal is drawn where a league would actually see it.

    *fastest_lap_position* is where the bonus falls. It is a parameter because a standings
    grid draws many races at once: pinned to one place, the fastest-lap highlight would
    only ever be seen over the same chip, and a manager judging their template would never
    see it over a winner or over a midfield points finish. A single classification has no
    such need and keeps the second place it always had.
    """
    rows = []
    count = len(drivers)
    for position, driver in enumerate(drivers, start=1):
        outcome = OutcomeModifier.CLASSIFIED
        base_time_ms: int | None = 3_723_000 + (position - 1) * 1_800
        laps_behind = None

        if count >= 5 and position == count:
            outcome = OutcomeModifier.DNF
            base_time_ms = None
        elif count >= 4 and position == count - 1:
            laps_behind = 1
            base_time_ms = None

        rows.append(
            RaceSessionResult(
                id=position,
                session_result_id=1,
                driver_user_id=driver.key,
                team_role_id=team_role_ids.get(driver.team_name, 0),
                finishing_position=position,
                outcome=outcome,
                base_time_ms=base_time_ms,
                laps_behind=laps_behind,
                # One entry carrying a penalty the game applied, and the rest none.
                ingame_time_penalties_ms=5_000 if position == 3 else 0,
                postrace_time_penalties_ms=0,
                appeal_time_penalties_ms=0,
                fastest_lap="1:29.145" if position == fastest_lap_position else None,
                fastest_lap_bonus=1 if position == fastest_lap_position else 0,
                points_awarded=points_map.get(position, 0),
            )
        )
    return rows


# ── Standings grid (FR-025, FR-026) ───────────────────────────────────────


#: Where the fastest lap falls, cycled by round. Chosen to put the overlay over each ground
#: it can land on within a few rounds of any grid: a win, the rest of the podium, a midfield
#: points finish, and a finish outside the points where it stands alone.
_FASTEST_LAP_PLACES = (2, 6, 1, 11, 3)


def _scattered(drivers, ordinal: int, session_index: int):
    """*drivers* in the order they finished this session — a different order each round.

    Without this every round of a preview grid would hold the same classification, the
    builders below numbering the field in the order they are handed it. The first driver
    would then be first in every round, and a manager judging a template would see one flat
    column: a solid stripe of winner's chips down the top row and nothing anywhere else.

    **Derived, never random.** This module's contract is stated at the head of the file — a
    preview must draw the same picture on every invocation, or two renders of one round
    cannot be compared against each other. So the order is a pure function of the round and
    the session.

    An affine map ``i -> (step*i + offset) mod n`` is a permutation whenever *step* is
    coprime to *n*, so every driver appears exactly once by construction rather than by
    luck. The arithmetic is written out here rather than left to `random.shuffle` so that
    no change of Python can move it.

    **The multiplier varies by round, and must.** Holding it fixed and moving only the
    offset shifts every driver by the same amount from one round to the next — a rotation
    in all but name, which preserves who follows whom and draws the grid as a set of
    diagonal stripes. Changing the multiplier reorders the field against itself, which is
    what scatters it.
    """
    count = len(drivers)
    if count < 3:
        return list(drivers)

    steps = [s for s in range(2, count) if _coprime(s, count)] or [1]
    step = steps[(ordinal * 3 + session_index) % len(steps)]
    offset = (ordinal * 7 + session_index * 3) % count
    return [drivers[(step * i + offset) % count] for i in range(count)]


def _coprime(a: int, b: int) -> bool:
    while b:
        a, b = b, a % b
    return a == 1


def fabricate_standings_round_results(run_ordinals, round_formats, drivers, team_role_ids):
    """Session results for every round already run, over the division's own drivers.

    Reuses ``fabricate_qualifying_rows``/``fabricate_race_rows`` — the same builders the
    results preview already calls — once per session per round, so the standings grid's
    outcome data and the results preview's come from one code path. *round_formats* maps a
    round's ordinal to its format string; a round absent from *run_ordinals* is simply not
    represented in the result, which the grid reads as "not yet run".

    The **results** module's own session vocabulary is used here (Sprint/Feature Qualifying
    and Race), read through ``result_submission_service.get_sessions_for_format`` — not the
    schedule's Short/Long/Full vocabulary ``sessions_for`` reads for the weather previews,
    which answers a different question (how many weather slots a session carries).
    """
    from models.round import RoundFormat
    from services.result_submission_service import get_sessions_for_format

    out: dict[int, dict[str, list]] = {}
    for ordinal in run_ordinals:
        try:
            round_format = RoundFormat(round_formats.get(ordinal, "NORMAL"))
        except ValueError:
            round_format = RoundFormat.NORMAL
        session_map: dict[str, list] = {}
        for index, session_type in enumerate(get_sessions_for_format(round_format)):
            points_map = (
                {1: 3, 2: 2, 3: 1}
                if session_type.is_qualifying
                else {n: max(0, 26 - 2 * (n - 1)) for n in range(1, 14)}
            )
            field = _scattered(drivers, ordinal, index)
            rows = (
                fabricate_qualifying_rows(field, team_role_ids, points_map)
                if session_type.is_qualifying
                else fabricate_race_rows(
                    field,
                    team_role_ids,
                    points_map,
                    fastest_lap_position=_FASTEST_LAP_PLACES[
                        ordinal % len(_FASTEST_LAP_PLACES)
                    ],
                )
            )
            session_map[session_type.value] = rows
        out[ordinal] = session_map
    return out


# ── Attendance (FR-027) ───────────────────────────────────────────────────


def fabricate_attendance_records(drivers, round_ordinals):
    """One record per driver over *round_ordinals*, covering the range a sheet carries.

    The states drawn, as far as the driver count allows: a driver holding nothing at all, one
    absent from a round entirely, and one carrying the sanction annotation.
    """
    from services.image_attendance_service import DriverRecord

    records = []
    for index, driver in enumerate(drivers):
        points: dict[int, int | None] = {}
        for offset, ordinal in enumerate(round_ordinals):
            if index == 0:
                continue  # holds nothing at all: every cell of the row empty
            if index == 1 and offset == 0:
                continue  # took no part in one of the rounds run
            points[ordinal] = (index + offset) % 3

        records.append(
            DriverRecord(
                key=driver.key,
                total=sum(value or 0 for value in points.values()),
                round_points=points,
                sanctioned=bool(points) and index == len(drivers) - 1,
            )
        )
    return records


# ── Forecasts (FR-029 to FR-031) ──────────────────────────────────────────


def fabricate_rain_probability() -> float:
    """A likelihood in [0, 1], deliberately not a whole percentage (FR-029).

    A whole number would leave the rounding of the rendered figure unjudged, which is the
    one thing about this value worth looking at.
    """
    return 0.374


def fabricate_phase2_sessions(round_format):
    """One weather type per session, covering what the format admits (FR-030).

    A sprint round runs four sessions and shows all three types; a two-session round shows
    two. Nothing is fabricated into existence to reach a case the format cannot hold.
    """
    return [
        {
            "session_type": session.value,
            "slot_type": PHASE2_TYPES[index % len(PHASE2_TYPES)],
        }
        for index, session in enumerate(sessions_for(round_format))
    ]


def fabricate_phase3_sessions(round_format):
    """A slot sequence per session, with all five conditions across the round (FR-031).

    Each session is given the slots its own type admits and no more, and the five conditions
    are dealt round-robin across the round's slots so every icon appears. Every non-mystery
    format holds at least five slots in total, so the requirement is always reachable — the
    normal format reaches it exactly, with nothing to spare.
    """
    result = []
    slot_index = 0
    for session in sessions_for(round_format):
        slots = []
        for _ in range(MAX_SLOTS.get(session, 1)):
            slots.append(PHASE3_SLOTS[slot_index % len(PHASE3_SLOTS)])
            slot_index += 1
        result.append(
            {
                "session_type": session.value,
                "slot_type": PHASE2_TYPES[len(result) % len(PHASE2_TYPES)],
                "slots": slots,
            }
        )
    return result


# ── Verdicts (FR-032 to FR-034) ───────────────────────────────────────────


def fabricate_verdict_cases(driver, session_names):
    """The verdicts a preview draws, one per case.

    Each pairs a sanction the module can issue with free text at one of five lengths, so the
    wrapping — the whole of this type's difficulty — can be judged at every extent the field
    admits.
    """
    cases = []
    for index, (penalty_type, seconds) in enumerate(VERDICT_SANCTIONS):
        cases.append(
            {
                "penalty_type": penalty_type,
                "time_seconds": seconds,
                "description": VERDICT_TEXT_CASES[index % len(VERDICT_TEXT_CASES)],
                "justification": VERDICT_TEXT_CASES[
                    (index + 1) % len(VERDICT_TEXT_CASES)
                ],
                "session_name": (
                    session_names[index % len(session_names)] if session_names else None
                ),
                "driver": driver,
            }
        )
    return cases
