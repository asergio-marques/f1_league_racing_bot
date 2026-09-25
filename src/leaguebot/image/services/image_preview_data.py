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

from leaguebot.core.models.session import MAX_SLOTS, SESSIONS_BY_FORMAT, SessionType  # noqa: E402
from leaguebot.core.models.session_result import (  # noqa: E402
    OutcomeModifier,
    QualifyingSessionResult,
    RaceSessionResult,
)
from leaguebot.image.utils.tyre_compound import TYRE_COMPOUNDS  # noqa: E402

#: The three types a phase 2 forecast draws for a session.
PHASE2_TYPES = ("sunny", "mixed", "rain")

#: The five concrete conditions a phase 3 slot draws. All five must appear across a round so
#: that every icon can be judged in one picture (FR-031).
PHASE3_SLOTS = ("Clear", "Light Cloud", "Overcast", "Wet", "Very Wet")

#: The outcomes the module can record and issue, and no others (FR-034): the four sanctions,
#: and no further action, which the results module issues since #138. A qualifying ban and a
#: race ban are deliberately absent: the bot cannot record them, and a preview must never draw
#: what the bot cannot issue.
VERDICT_SANCTIONS = (
    ("TIME", 5),
    ("TIME", 10),
    ("TIME", -3),
    ("DSQ", None),
    ("NFA", None),
)


def sessions_for(round_format) -> list[SessionType]:
    """The sessions a round of *round_format* is run over, read and never restated."""
    from leaguebot.core.models.round import RoundFormat

    raw = str(getattr(round_format, "value", round_format) or "NORMAL")
    try:
        key = RoundFormat(raw)
    except ValueError:
        key = RoundFormat.NORMAL
    return list(SESSIONS_BY_FORMAT.get(key, []))


# ── Classifications (FR-023, FR-024) ──────────────────────────────────────


def fabricate_qualifying_rows(drivers, team_keys, points_map):
    """A believable qualifying classification over *drivers*.

    Every driver appears exactly once, positions run 1..n with no gap, and the best laps
    ascend with position so the gaps the formatter derives agree with the order.

    The compounds are dealt in turn to the rows that record one, so all five appear on a
    field of six rather than only those whose position happens to land on them. Same
    reasoning as PHASE3_SLOTS above: a preview exists to let every icon be judged in one
    picture, and a compound the fabrication never deals is a compound never seen.

    A large enough field also carries a DNS and a DSQ — the outcome literals a session can
    record — so a manager can judge their chips (#144). They stand in the order
    ``validate_submission_block`` requires of a submitted qualifying, the DSQ last of all,
    so the preview never draws a classification the results module would refuse.
    """
    rows = []
    count = len(drivers)
    compounds_dealt = 0
    # A field of five or more ends DNS, DSQ; a field of four ends with the DNS alone.
    dsq_position = count if count >= 5 else None
    dns_position = count - 1 if count >= 5 else (count if count >= 4 else None)
    for position, driver in enumerate(drivers, start=1):
        seconds = 88.400 + (position - 1) * 0.400
        best_lap: str | None = f"1:{int(seconds - 60):02d}.{int(round((seconds % 1) * 1000)):03d}"

        outcome = OutcomeModifier.CLASSIFIED
        if position == dsq_position:
            outcome = OutcomeModifier.DSQ
            best_lap = None
        # A driver of a large enough field set no time, so that case is drawn too.
        elif position == dns_position:
            outcome = OutcomeModifier.DNS
            best_lap = None

        # P2 records no tyre at all, so the absent-datum case is drawn beside the five.
        if position == 2:
            tyre = None
        else:
            tyre = TYRE_COMPOUNDS[compounds_dealt % len(TYRE_COMPOUNDS)]
            compounds_dealt += 1

        rows.append(
            QualifyingSessionResult(
                id=position,
                session_result_id=1,
                driver_user_id=driver.key,
                team_instance_id=team_keys.get(
                    getattr(driver, "team_key", "") or driver.team_name, 0
                ),
                finishing_position=position,
                outcome=outcome,
                tyre=tyre,
                best_lap=best_lap,
                points_awarded=points_map.get(position, 0),
            )
        )
    return rows


def fabricate_race_rows(drivers, team_keys, points_map, *, fastest_lap_position=2):
    """A believable race classification over *drivers*.

    The leader carries a total race time; everyone else an interval growing with position.
    A driver who did not finish is placed behind every finisher, as the results module
    renumbers them, so the outcome literal is drawn where a league would actually see it.

    *fastest_lap_position* is where the bonus falls. It is a parameter because a standings
    grid draws many races at once: pinned to one place, the fastest-lap highlight would
    only ever be seen over the same chip, and a manager judging their template would never
    see it over a winner or over a midfield points finish. A single classification has no
    such need and keeps the second place it always had.

    A large enough field also carries a DSQ, beside the DNF and the lapped finish, so every
    outcome literal a league's results module can record is exercised (#144). The tail
    stands in the order ``validate_submission_block`` requires of a submitted race —
    lapped, then DNF, then DSQ last of all — so the preview never draws a classification
    the results module would refuse.
    """
    rows = []
    count = len(drivers)
    # Six or more end lapped, DNF, DSQ; five end lapped, DNF; four end on the lapped finish.
    # The lapped car always stands directly ahead of the non-finishers and behind every
    # lead-lap finisher — on four it was once third with a lead-lap car behind it.
    dsq_position = count if count >= 6 else None
    dnf_position = count - 1 if count >= 6 else (count if count >= 5 else None)
    non_finishers = (dsq_position is not None) + (dnf_position is not None)
    lapped_position = count - non_finishers if count >= 4 else None
    for position, driver in enumerate(drivers, start=1):
        outcome = OutcomeModifier.CLASSIFIED
        base_time_ms: int | None = 3_723_000 + (position - 1) * 1_800
        laps_behind = None

        if position == dsq_position:
            outcome = OutcomeModifier.DSQ
            base_time_ms = None
        elif position == dnf_position:
            outcome = OutcomeModifier.DNF
            base_time_ms = None
        elif position == lapped_position:
            laps_behind = 1
            base_time_ms = None

        rows.append(
            RaceSessionResult(
                id=position,
                session_result_id=1,
                driver_user_id=driver.key,
                team_instance_id=team_keys.get(
                    getattr(driver, "team_key", "") or driver.team_name, 0
                ),
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


def fabricate_standings_totals(count: int, *, leader: int) -> list[int]:
    """The points total for each position of a fabricated standings, 1st first.

    Descending and scaled to *count* so the ramp never runs off its own floor by accident —
    the defect this replaces was a fixed step that reached zero from position 15 on the
    drivers' ramp and 13 on the constructors', so a normal-sized field never showed either
    of the two cases below at all (#144).

    Two cases are **placed**, not left to arithmetic to produce or fail to produce:

    - **The tie.** 2nd and 3rd are set level, the leader kept clear of it, whenever the
      field holds at least three. *Which* of the two stands higher is not decided here: the
      preview orders its classification through the standings service's own rule over the
      grid it draws, so the pair is separated by the countback a reader can check beneath
      them. Leaving it to the order of the list once drew a driver with a win beneath one
      without.
    - **The nought.** The last entry is set to zero, unless it is the leader (a field of
      one) or already part of the tie (a field of three) — a tie *on* nought is the
      accidental case #144 reported, not the deliberate one this places.
    """
    if count <= 0:
        return []
    step = max(1, leader // count)
    totals = [step * (count - position) for position in range(count)]
    if count >= 3:
        totals[1] = totals[2]
    if count != 3 and count > 1:
        totals[-1] = 0
    return totals


def fabricate_standings_previous_positions(
    keys: list[int], *, newcomers: frozenset[int] = frozenset()
) -> dict[int, int]:
    """A fictitious reference round's positions, keyed as the current classification is.

    ``build_standings_preview`` stands against no real reference round, so nothing about a
    previous classification is on hand to read — but it fabricates the *current* round
    wholesale already, and a fabricated previous one is no different. Handed to
    ``standings_service.derive_movement`` alongside the current positions, it is what lets
    the preview draw the three movement markers the spec requires, rather than omitting the
    column because no real history exists (corrected from the "deliberate" omission this
    replaced, whose reasoning did not hold once the current round was already invented).

    *keys* is every entry's key (``driver_user_id`` or ``team_instance_id``), in the order
    of its **current** standing position, 1st first. The previous positions returned put
    the **third**-placed entry ahead of the **second** — one gained, one lost, and the rest
    unchanged — wherever the field is large enough to hold the swap. A field of fewer than
    three holds every entry unchanged; there is nothing to swap.

    *newcomers* are left out of the previous round altogether, so ``derive_movement`` finds
    no record for them — the spec's "a driver whom the standings of the preceding round do
    not hold", whose movement columns a template must be able to empty.
    """
    previous = {key: position for position, key in enumerate(keys, start=1)}
    if len(keys) >= 3:
        previous[keys[1]], previous[keys[2]] = previous[keys[2]], previous[keys[1]]
    for key in newcomers:
        previous.pop(key, None)
    return previous


def fabricate_standings_round_results(
    run_ordinals, round_formats, drivers, team_keys, *, reserve_driver=None
):
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

    *reserve_driver*, where given, draws three of the spec's cases for one run round, over
    two *different* regular teams, so that a stand-in filling the very seat it vacates does
    not paper over the empty-car case with it:

    - the **last** regular is dropped from the round's field entirely, so its own team's
      last car goes undriven for it (FR-026's "a car nobody drove") — with no reserve to
      fill the seat, unlike the case below;
    - the last regular of **any other** team is dropped and the reserve credited to that
      team instead (a reserve standing in) — which is also what makes that regular's own
      absence visible (a driver absent from one round);
    - whatever remains of the first team is classified at the back of every session of
      that round, so it is a team conferred no points in one of the rounds run. Placed and
      not left to the scatter, which on a small field puts every finisher in the points.

    The second is sought by team and not simply taken as the regular before the first:
    on the commonest field of all, every team seating two, the two last regulars are
    teammates, and taking them both would have skipped the substitution on exactly the
    division a league most often runs. Skipped only where no second regular team exists,
    per the spec's own qualifier that none of its cases is fabricated into existence beyond
    what the field allows (#144).
    """
    from types import SimpleNamespace
    from typing import Any

    from leaguebot.core.models.round import RoundFormat
    from leaguebot.results.services.result_submission_service import get_sessions_for_format

    def team_of(driver) -> str:
        return driver.team_key or driver.team_name

    # Bundled as one Optional rather than several separate ones, so that a check of the one
    # narrows the rest together — the round substituted and who it touches all exist for
    # the same reason and never independently of it.
    substitution: tuple[int, Any, Any, Any] | None = None
    if reserve_driver is not None and run_ordinals:
        regulars = [d for d in drivers if team_of(d) != team_of(reserve_driver)]
        if regulars:
            undriven_driver = regulars[-1]
            absent_driver = next(
                (d for d in reversed(regulars) if team_of(d) != team_of(undriven_driver)),
                None,
            )
            if absent_driver is not None:
                standin = SimpleNamespace(
                    key=reserve_driver.key,
                    display_name=reserve_driver.display_name,
                    team_name=absent_driver.team_name,
                    team_key=absent_driver.team_key or absent_driver.team_name,
                    seat_number=absent_driver.seat_number,
                    nationality=reserve_driver.nationality,
                )
                substitution = (min(run_ordinals), undriven_driver, absent_driver, standin)

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
            active = (
                substitution
                if substitution is not None and ordinal == substitution[0]
                else None
            )
            round_drivers = drivers
            if active is not None:
                _, undriven_driver, absent_driver, standin = active
                dropped = {undriven_driver.key, absent_driver.key}
                round_drivers = [d for d in drivers if d.key not in dropped]
                round_drivers = [
                    standin if d.key == reserve_driver.key else d for d in round_drivers
                ]
            field = _scattered(round_drivers, ordinal, index)
            if active is not None:
                # The team short of a car finishes at the back as well, so the round it
                # scores nothing in is placed rather than left to the scatter — which on a
                # field of five teams or fewer, every finisher in the points, never
                # produced one. Last is the DNF of a race and the DNS of a qualifying.
                short_team = team_of(active[1])
                field = [d for d in field if team_of(d) != short_team] + [
                    d for d in field if team_of(d) == short_team
                ]
            rows = (
                fabricate_qualifying_rows(field, team_keys, points_map)
                if session_type.is_qualifying
                else fabricate_race_rows(
                    field,
                    team_keys,
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


#: The most a single round can confer on one driver. The module's own defaults are a point
#: for failing to check in and a point for being absent, and nothing stacks a third onto a
#: round, so a fabricated cell never shows a number a real round could not produce.
MAX_ROUND_PENALTY = 2

#: The sack threshold a preview seeds where the calendar is long enough to reach it. A round
#: of ten is what a league most often sets, and it leaves the near band well clear of zero.
NOMINAL_ATTENDANCE_LIMIT = 10


def fabricate_attendance_limit(round_ordinals) -> int:
    """The point limit a preview seeds, for the rounds run so far.

    The limit and the totals must be fabricated together: the marks are the whole of what an
    attendance sheet says beyond its numbers, and a limit of ten over two rounds run — four
    points between every driver on the sheet — leaves every row unmarked and the graphic
    unjudged. So where the rounds run cannot confer ten points on anyone, the limit falls to
    what they can, and the tiers of the mark come down with it.
    """
    ceiling = MAX_ROUND_PENALTY * max(1, len(list(round_ordinals)))
    return max(1, min(NOMINAL_ATTENDANCE_LIMIT, ceiling))


def _attendance_totals(count: int, limit: int) -> list[int]:
    """The total each fabricated driver carries, in driver order.

    The last four drivers are placed deliberately: past the limit, on it, and at both values
    inside the near band, so that a sheet drawn from these exercises the reached mark, the
    near mark and their absence in one picture rather than painting one mark down the column.
    The rest descend through the band below, cycling so that no two adjacent drivers share a
    total — a field where everyone holds the same number tells a manager nothing about how
    the sheet orders or draws it.
    """
    from leaguebot.image.services.image_attendance_service import MARK_NEAR_BAND

    if count <= 0:
        return []

    totals = [0] * count
    marked = set()
    # Worst first, from the end of the field: the sanctioned driver is the last of them.
    for offset, value in enumerate((limit + 1, limit, limit - 1, limit - 2)):
        index = count - 1 - offset
        if index <= 0:  # never the driver drawn holding nothing at all
            break
        totals[index] = max(1, value)
        marked.add(index)

    # Everyone else sits below the near band, where a real field mostly does, descending so
    # that adjacent rows differ and the sheet's ordering can be read off the column.
    band = list(range(max(1, limit - MARK_NEAR_BAND - 1), 0, -1))
    unmarked = [index for index in range(1, count) if index not in marked]
    for position, index in enumerate(unmarked):
        totals[index] = band[position % len(band)]

    return totals


def _scatter_penalties(
    total: int, ordinals, offset: int, doubled: bool = False
) -> dict[int, int | None]:
    """*total* points spread over *ordinals*, at most `MAX_ROUND_PENALTY` in any one round.

    The round the spreading starts from rotates with the driver, so the penalties fall across
    the grid rather than stacking into the same early columns for every row. *doubled* fills
    each round to the maximum before moving to the next, which is how a cell showing two ever
    reaches a long calendar: spread thin over twenty rounds, no driver's total would ever
    double up, and the sheet would draw one number in every filled cell.

    A total larger than the rounds can carry is simply cut short — the record's total is read
    back off the cells afterwards, so the number above a row can never disagree with the row.
    """
    ordinals = list(ordinals)
    counts: dict[int, int] = {ordinal: 0 for ordinal in ordinals}
    remaining = total
    for step in range(MAX_ROUND_PENALTY * len(ordinals)):
        if remaining <= 0:
            break
        column = step // MAX_ROUND_PENALTY if doubled else step
        counts[ordinals[(offset + column) % len(ordinals)]] += 1
        remaining -= 1
    points: dict[int, int | None] = dict(counts)
    return points


def fabricate_attendance_records(drivers, round_ordinals, limit: int | None = None):
    """One record per driver over *round_ordinals*, covering the range a sheet carries.

    The states drawn, as far as the driver count allows: a driver holding nothing at all, one
    absent from a round entirely, one carrying the sanction annotation, and — through
    `_attendance_totals` — a driver at each tier of the limit mark and several at none of them.

    *limit* is the threshold the sheet is drawn against, and defaults to the one
    `fabricate_attendance_limit` seeds for the same rounds. A caller passing its own must pass
    that same number to ``resolve_drawing``, or the marks will answer to a limit the plate
    above them does not name.
    """
    from leaguebot.image.services.image_attendance_service import DriverRecord

    ordinals = list(round_ordinals)
    limit = fabricate_attendance_limit(ordinals) if limit is None else limit
    totals = _attendance_totals(len(drivers), limit)

    records = []
    for index, driver in enumerate(drivers):
        if index == 0:
            points: dict[int, int | None] = {}  # holds nothing at all: every cell empty
        else:
            # The second driver took no part in the first round run, which is not the same
            # as a round that conferred nothing on them: that column carries no cell at all.
            # Not where one round has been run, though — that would leave them holding no
            # cell at all, which is the first driver's case and not this one.
            absent = index == 1 and len(ordinals) > 1
            available = ordinals[1:] if absent else ordinals
            points = _scatter_penalties(
                totals[index], available, index, doubled=index % 2 == 1
            )

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
    result: list[dict[str, object]] = []
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
