"""Resolve one session's classification and project it onto a template (039).

Two steps, deliberately separate, as 037's calendar service is:

1. :func:`resolve_drawing` turns a session's persisted classification into a
   :class:`ResultsDrawing` — every value decided, nothing drawn. It takes already-resolved
   display names, so it needs no Discord and no database and is testable on its own.
2. :func:`build_fill_spec` projects that drawing onto a parsed template, deciding which rows
   leave, which column groups leave, and which single row is recoloured.

**This module renders nothing.** Every value the graphic and the textual table both draw is
produced by ``utils.results_formatter``'s row builders and merely *placed* here — that is
Constitution XIV.7 as amended at v4.4.0, and it is why no lap time, gap, interval, lap count
or penalty is formatted anywhere below. See
specs/039-results-image-generation/contracts/shared-rendering.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from models.image_catalogues import CapacityError, catalogue_for
from models.points_config import SessionType
from utils.results_formatter import (
    NOT_APPLICABLE,
    build_qualifying_rows,
    build_race_rows,
    fastest_lap_holder,
    format_session_label,
)
from utils.svg_document import FieldIndex
from utils.svg_fill import FillSpec
from utils.country_data import country_for_nationality

log = logging.getLogger(__name__)

QUALIFYING_TEMPLATE_KEY = "results_qualifying_template"
RACE_TEMPLATE_KEY = "results_race_template"

_ROW_PREFIX = "row"

#: The stages at which each sanction phase has closed. Before it closes, the column carries
#: no value the generation can reach and every cell of it is emptied.
_PENALTY_CLOSED_AT = frozenset({"POST_RACE_PENALTY", "FINAL"})
_APPEAL_CLOSED_AT = frozenset({"FINAL"})

#: Round result_status -> the lifecycle label. The same mapping the message text carries, so
#: the label drawn on the graphic and the label beside it cannot disagree.
_STATUS_LABELS = {
    "PROVISIONAL": "Provisional Results",
    "POST_RACE_PENALTY": "Post-Race Penalty Results",
    "FINAL": "Final Results",
}


class ResultsDataError(Exception):
    """A fatal disagreement between a session and what a results graphic needs.

    Raised before anything is drawn. The caller turns it into a Problem, which aborts the
    render and — for an uncommanded posting — falls back to the textual table.
    """


@dataclass(frozen=True)
class ResultsEntry:
    """One row of the classification, every cell already the string that will be drawn.

    ``None`` on a text cell means the value does not apply and the field is emptied quietly:
    it was *determined* to be nothing, so no mandatory field is offended and no notice is
    raised (Constitution XIV.3).
    """

    #: The row's discriminator, and the value placed on ``row_<x>_position``. Filled from
    #: the ordinal itself; no comparison is made between it and anything else (XIV.11).
    ordinal: int
    driver_name: str
    team_name: str
    points: str
    postrace_penalty: str | None
    appeal_penalty: str | None
    #: The datum behind ``row_<x>_driver_flag``. None where the driver recorded none.
    nationality: str | None = None
    #: Qualifying only.
    tyre: str | None = None
    best_lap: str | None = None
    gap: str | None = None
    #: Race only.
    time: str | None = None
    fastest_lap: str | None = None
    ingame_penalty: str | None = None
    holds_fastest_lap: bool = False


@dataclass(frozen=True)
class FastestLapBlock:
    """The whole-graphic fastest-lap block of a race graphic."""

    driver_name: str
    lap_time: str


@dataclass(frozen=True)
class ResultsDrawing:
    """One session's results, resolved and ready to project onto a template."""

    template_key: str
    division_name: str
    round_number: str
    race_name: str
    session_name: str
    result_status_label: str
    penalty_phase_closed: bool
    appeal_phase_closed: bool
    division_tier: str | None = None
    season_number: str | None = None
    fastest_lap: FastestLapBlock | None = None
    fastest_lap_colour: str | None = None
    #: True where the league collects a driver's nationality at all. Where it does not, an
    #: empty flag field is exactly what was configured and raises nothing (XIV.4).
    nationality_collected: bool = True
    entries: list[ResultsEntry] = field(default_factory=list)

    @property
    def is_qualifying(self) -> bool:
        return self.template_key == QUALIFYING_TEMPLATE_KEY

    @property
    def entry_count(self) -> int:
        return len(self.entries)


# ── 1. Resolution ─────────────────────────────────────────────────────────


def template_key_for(session_type: SessionType) -> str:
    """Which of the two templates draws *session_type*.

    One aspect, two image types: a sprint and a feature session of the same kind share a
    template and are told apart by the session name alone.
    """
    return (
        QUALIFYING_TEMPLATE_KEY
        if SessionType(session_type).is_qualifying
        else RACE_TEMPLATE_KEY
    )


def status_label(result_status: str | None) -> str:
    """The lifecycle label for a round's ``result_status``."""
    return _STATUS_LABELS.get(result_status or "PROVISIONAL", "Results")


def _sanction(value: str | None, *, phase_closed: bool) -> str | None:
    """The three states of a sanction cell.

    Where the phase has not closed the field is emptied; where it has closed and applied
    nothing the field carries a dash; where it applied something the field carries it.
    This is the one value the graphic carries that the textual table does not, which is why
    it is decided here and not in the shared row builder.
    """
    if not phase_closed:
        return None
    return value or NOT_APPLICABLE


def resolve_drawing(
    *,
    session_type: SessionType,
    is_sprint: bool,
    result_status: str | None,
    division_name: str,
    round_number: str | int,
    race_name: str,
    driver_rows: Sequence,
    points_map: Mapping[int, int],
    driver_names: Mapping[int, str],
    team_names: Mapping[int, str],
    nationalities: Mapping[int, str | None] | None = None,
    dsq_phase_map: Mapping[int, str] | None = None,
    division_tier: str | int | None = None,
    season_number: str | int | None = None,
    fastest_lap_colour: str | None = None,
    nationality_collected: bool = True,
) -> ResultsDrawing:
    """Resolve every value a results graphic draws, or raise :class:`ResultsDataError`.

    *driver_rows* are the persisted ``QualifyingSessionResult`` or ``RaceSessionResult``
    rows of the session. *driver_names* and *team_names* are already resolved by the caller,
    which is what keeps this function free of Discord: a graphic carries no mention, and the
    name that stands in its place is settled before anything reaches here (XIV.16).

    The classification's order — including the renumbering that drops a disqualified driver
    to the bottom — is the results module's and is persisted before this is called. Nothing
    is reordered and no position is recomputed.
    """
    kind = SessionType(session_type)
    template_key = template_key_for(kind)

    if not driver_rows:
        raise ResultsDataError(
            f"the {format_session_label(kind, is_sprint=is_sprint)} of round "
            f"{round_number} in `{division_name}` records no entry at all, so there is no "
            f"classification to draw"
        )

    penalty_closed = (result_status or "PROVISIONAL") in _PENALTY_CLOSED_AT
    appeal_closed = (result_status or "PROVISIONAL") in _APPEAL_CLOSED_AT

    names = dict(driver_names)
    teams = dict(team_names)
    flags = dict(nationalities or {})

    def named(user_id: int) -> str:
        name = (names.get(user_id) or "").strip()
        if not name:
            # A mandatory field whose value cannot be determined is fatal (XIV.3). The
            # resolution chain ends at the driver's user id, so reaching here means the
            # caller supplied nothing at all for this driver.
            raise ResultsDataError(
                f"no name could be resolved for the driver with id {user_id}, which the "
                f"classification of round {round_number} in `{division_name}` needs"
            )
        return name

    entries: list[ResultsEntry] = []
    block: FastestLapBlock | None = None

    if kind.is_qualifying:
        rows = build_qualifying_rows(
            list(driver_rows), dict(points_map), dsq_phase_map=dict(dsq_phase_map or {})
        )
        for ordinal, row in enumerate(rows, start=1):
            entries.append(
                ResultsEntry(
                    ordinal=ordinal,
                    driver_name=named(row.driver_user_id),
                    team_name=teams.get(row.team_role_id) or f"Role {row.team_role_id}",
                    points=str(row.points),
                    postrace_penalty=_sanction(
                        row.postrace_penalty, phase_closed=penalty_closed
                    ),
                    appeal_penalty=_sanction(
                        row.appeal_penalty, phase_closed=appeal_closed
                    ),
                    nationality=(flags.get(row.driver_user_id) or None),
                    tyre=row.tyre,
                    best_lap=row.best_lap,
                    gap=row.gap,
                )
            )
    else:
        rows = build_race_rows(
            list(driver_rows), dict(points_map), dsq_phase_map=dict(dsq_phase_map or {})
        )
        for ordinal, row in enumerate(rows, start=1):
            entries.append(
                ResultsEntry(
                    ordinal=ordinal,
                    driver_name=named(row.driver_user_id),
                    team_name=teams.get(row.team_role_id) or f"Role {row.team_role_id}",
                    points=str(row.points),
                    postrace_penalty=_sanction(
                        row.postrace_penalty, phase_closed=penalty_closed
                    ),
                    appeal_penalty=_sanction(
                        row.appeal_penalty, phase_closed=appeal_closed
                    ),
                    nationality=(flags.get(row.driver_user_id) or None),
                    time=row.time,
                    fastest_lap=row.fastest_lap,
                    # The in-game penalty belongs to no phase and is never left empty: a
                    # dash where the game applied none.
                    ingame_penalty=row.ingame_penalty or NOT_APPLICABLE,
                    holds_fastest_lap=row.holds_fastest_lap,
                )
            )

        holder = fastest_lap_holder(rows)
        if holder is not None:
            block = FastestLapBlock(
                driver_name=named(holder.driver_user_id),
                lap_time=holder.fastest_lap or NOT_APPLICABLE,
            )

    return ResultsDrawing(
        template_key=template_key,
        division_name=division_name,
        round_number=str(round_number),
        race_name=race_name,
        session_name=format_session_label(kind, is_sprint=is_sprint),
        result_status_label=status_label(result_status),
        penalty_phase_closed=penalty_closed,
        appeal_phase_closed=appeal_closed,
        division_tier=None if division_tier is None else str(division_tier),
        season_number=None if season_number is None else str(season_number),
        fastest_lap=block,
        fastest_lap_colour=fastest_lap_colour,
        nationality_collected=nationality_collected,
        entries=entries,
    )


# ── 2. Projection onto a template ─────────────────────────────────────────


def _row_fields_declared(declared, ordinal: int) -> list[str]:
    """Every id the template declares bearing *ordinal*, group included."""
    stem = f"{_ROW_PREFIX}_{ordinal}"
    return sorted(
        name for name in declared if name == stem or name.startswith(f"{stem}_")
    )


def build_fill_spec(
    drawing: ResultsDrawing,
    root,
    *,
    asset_directories: Mapping[str, Path] | None = None,
) -> FillSpec:
    """Project *drawing* onto *root*, deciding what leaves the canvas beside it.

    Raises :class:`ResultsDataError` where the template's rows cannot be counted — no row at
    all, or a gap in the numbering. Overflow is **not** raised here: it is reported through
    ``row_count`` so the render service issues the capacity problem in one place, naming the
    count, the capacity and the template.
    """
    catalogue = catalogue_for(drawing.template_key)
    index = FieldIndex(root)
    declared = index.declared()

    try:
        capacity = catalogue.capacity(root) or 0
    except CapacityError as exc:
        raise ResultsDataError(str(exc)) from exc

    drawn = drawing.entries[:capacity]

    text: dict[str, str] = {}
    empty: list[str] = []
    empty_quietly: list[str] = []
    remove: list[str] = []
    recolour: dict[str, str] = {}
    image_data: dict[str, tuple[str, str]] = {}
    off_canvas: set[str] = set()

    def put(field_id: str, value: str | None) -> None:
        """Fill where declared; empty rather than dash where the value does not apply.

        Every emptying here is of a value the data **determined** to be nothing — a
        sanction column of a phase not yet closed, the gap of the entry holding the
        reference lap — so it is quiet and offends no mandatory field (XIV.3).
        """
        if field_id not in declared:
            return
        if value:
            text[field_id] = value
        else:
            empty_quietly.append(field_id)

    def put_optional(field_id: str, value: str | None) -> None:
        """A whole-graphic optional whose absence is worth a notice."""
        if field_id not in declared:
            return
        if value:
            text[field_id] = value
        else:
            empty.append(field_id)

    put("division_name", drawing.division_name)
    put("round_number", drawing.round_number)
    put("race_name", drawing.race_name)
    put("session_name", drawing.session_name)
    put("result_status", drawing.result_status_label)
    put_optional("season_number", drawing.season_number)
    put_optional("division_tier", drawing.division_tier)

    # Column groups (XIV.2, v4.4.0). Each wraps its column's heading and no cell of any
    # row, so it leaves whole while that phase stands open — a heading over a column of
    # empty cells being a heading the generation cannot otherwise reach. A template
    # declaring neither carries its heading over an emptied column, which is meant.
    if not drawing.penalty_phase_closed and "postrace_penalty_group" in declared:
        remove.append("postrace_penalty_group")
    if not drawing.appeal_phase_closed and "appeal_penalty_group" in declared:
        remove.append("appeal_penalty_group")

    for entry in drawn:
        stem = f"{_ROW_PREFIX}_{entry.ordinal}"
        # Filled from the ordinal itself — the renumbering is already persisted, and a
        # comparison here could only disagree with a fact (XIV.11, v4.4.0).
        put(f"{stem}_position", str(entry.ordinal))
        put(f"{stem}_driver_name", entry.driver_name)
        put(f"{stem}_team_name", entry.team_name)
        put(f"{stem}_points", entry.points)
        put(f"{stem}_postrace_penalty", entry.postrace_penalty)
        put(f"{stem}_appeal_penalty", entry.appeal_penalty)

        if f"{stem}_team_image" in declared:
            image_data[f"{stem}_team_image"] = ("team", entry.team_name)

        # The flag. A nationality the league collects but this driver did not state is an
        # ordinary emptied optional and reports as one; a league that switched collection
        # off at its source has configured a graphic with no flags, and that raises nothing.
        flag_id = f"{stem}_driver_flag"
        if flag_id in declared:
            # The switch is read **first**: a driver who stated a nationality before the
            # league turned collection off still holds one, and testing the value first
            # would draw their flag alone among a table of blanks.
            if not drawing.nationality_collected:
                empty_quietly.append(flag_id)
            elif entry.nationality:
                image_data[flag_id] = ("flag", country_for_nationality(entry.nationality))
            else:
                empty.append(flag_id)

        if drawing.is_qualifying:
            put(f"{stem}_best_lap", entry.best_lap)
            put(f"{stem}_gap", entry.gap)
            # Always offered to the resolver: an absent compound draws the tyre class's
            # fallback and reports nothing, which the catalogue declares (XIV.13, v4.4.0).
            if f"{stem}_tyre" in declared:
                image_data[f"{stem}_tyre"] = ("tyre", entry.tyre or "")
        else:
            put(f"{stem}_time", entry.time)
            put(f"{stem}_fastest_lap", entry.fastest_lap)
            put(f"{stem}_ingame_penalty", entry.ingame_penalty)
            # The module's one data-driven recolour. It does not consume the field: the
            # fastest lap is filled above exactly as any other cell is (XIV.2).
            if (
                entry.holds_fastest_lap
                and drawing.fastest_lap_colour
                and f"{stem}_fastest_lap" in declared
            ):
                recolour[f"{stem}_fastest_lap"] = drawing.fastest_lap_colour

    # The fastest-lap block: a block group (XIV.2, v4.4.0) wrapping fields that stand or
    # fall together. Removed whole where the session conferred no bonus; where the template
    # declares no group, the fields alone are emptied.
    if not drawing.is_qualifying:
        if drawing.fastest_lap is not None:
            put("fastest_lap_driver_name", drawing.fastest_lap.driver_name)
            put("fastest_lap_time", drawing.fastest_lap.lap_time)
        elif "fastest_lap_group" in declared:
            remove.append("fastest_lap_group")
            off_canvas.update(
                name
                for name in declared
                if name.startswith("fastest_lap_") and name != "fastest_lap_group"
            )
        else:
            put("fastest_lap_driver_name", None)
            put("fastest_lap_time", None)

    # Rows the template declares beyond the session's entries. Each leaves by its group,
    # which this type makes mandatory, and every field it takes with it is off the canvas
    # and therefore not unresolved (XIV.3).
    for ordinal in range(len(drawn) + 1, capacity + 1):
        group_id = f"{_ROW_PREFIX}_{ordinal}_group"
        off_canvas.update(_row_fields_declared(declared, ordinal))
        if group_id in declared:
            remove.append(group_id)
        else:
            remove.extend(
                name
                for name in _row_fields_declared(declared, ordinal)
                if name not in remove
            )

    spec = FillSpec(
        root=root,
        image_type=drawing.template_key,
        text=text,
        empty=empty,
        empty_quietly=empty_quietly,
        remove=remove,
        recolour=recolour,
        off_canvas=off_canvas,
        row_count=drawing.entry_count,
        image_data=image_data,
        catalogue=catalogue,
    )
    if asset_directories:
        spec.asset_directories = dict(asset_directories)
    return spec
