"""Compose one session's results picture from its classification (039).

A results picture is drawn by the image module and composed here, by the module that owns the
classification (docs/design/architecture.md, "How modules and core fit together"):
:func:`resolve_drawing` turns a session's persisted classification into the plain
:class:`ResultsDrawing` image declares, every value decided and nothing drawn, and
:func:`build_drawing` reads the names it needs through core's readers and hands it back for
``ImageDrawing.draw_results`` to place on a template.

**This module renders nothing, and neither does image.** Every value the graphic and the textual
table both draw is produced by ``results_formatter``'s row builders and merely placed, which is
Constitution XIV.7 as amended at v4.4.0, and why no lap time, gap, interval, lap count or penalty
is formatted anywhere else. The one colour a drawing carries is image's own configuration, applied
where the drawing is rendered, never here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from leaguebot.core.services.drivers.driver_names import (
    read_driver_names,
    read_driver_nationalities,
    read_nationality_collected,
)
from leaguebot.core.services.drivers.team_service import (
    team_artwork_keys_for_instances,
    team_names_for_instances,
)
from leaguebot.image.models.results_drawing import (
    QUALIFYING_TEMPLATE_KEY,
    RACE_TEMPLATE_KEY,
    FastestLapBlock,
    ResultsDataError,
    ResultsDrawing,
    ResultsEntry,
)
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.utils.results_formatter import (
    NOT_APPLICABLE,
    UNKNOWN_TEAM,
    build_qualifying_rows,
    build_race_rows,
    fastest_lap_holder,
    format_session_label,
)

if TYPE_CHECKING:
    from leaguebot.framework.utils.hooks import AwaitedHook

#: The stages at which each sanction phase has closed. Before it closes, the column carries
#: no value the generation can reach and every cell of it is emptied.
_PENALTY_CLOSED_AT = frozenset({"AWAITING_APPEAL_VERDICTS", "FINAL"})
_APPEAL_CLOSED_AT = frozenset({"FINAL"})

#: Round result_status -> the lifecycle label. The same mapping the message text carries, so
#: the label drawn on the graphic and the label beside it cannot disagree.
_STATUS_LABELS = {
    "AWAITING_REPORT_VERDICTS": "Provisional Results",
    "AWAITING_APPEAL_VERDICTS": "Post-Race Penalty Results",
    "FINAL": "Final Results",
}


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
    return _STATUS_LABELS.get(result_status or "", "Results")


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
    team_keys: Mapping[int, str] | None = None,
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

    *team_keys* holds each team's shorthand, which its artwork is found by, apart from the
    full name drawn (#381). A team absent from it is found by the name drawn.

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

    penalty_closed = (result_status or "") in _PENALTY_CLOSED_AT
    appeal_closed = (result_status or "") in _APPEAL_CLOSED_AT

    names = dict(driver_names)
    teams = dict(team_names)
    keys = dict(team_keys or {})
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
                    team_name=teams.get(row.team_instance_id) or UNKNOWN_TEAM,
                    team_key=keys.get(row.team_instance_id),
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
        race_rows = build_race_rows(
            list(driver_rows), dict(points_map), dsq_phase_map=dict(dsq_phase_map or {})
        )
        for ordinal, race_row in enumerate(race_rows, start=1):
            entries.append(
                ResultsEntry(
                    ordinal=ordinal,
                    driver_name=named(race_row.driver_user_id),
                    team_name=teams.get(race_row.team_instance_id) or UNKNOWN_TEAM,
                    team_key=keys.get(race_row.team_instance_id),
                    points=str(race_row.points),
                    postrace_penalty=_sanction(
                        race_row.postrace_penalty, phase_closed=penalty_closed
                    ),
                    appeal_penalty=_sanction(
                        race_row.appeal_penalty, phase_closed=appeal_closed
                    ),
                    nationality=(flags.get(race_row.driver_user_id) or None),
                    time=race_row.time,
                    fastest_lap=race_row.fastest_lap,
                    # The in-game penalty belongs to no phase and is never left empty: a
                    # dash where the game applied none.
                    ingame_penalty=race_row.ingame_penalty or NOT_APPLICABLE,
                    holds_fastest_lap=race_row.holds_fastest_lap,
                )
            )

        holder = fastest_lap_holder(race_rows)
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


async def build_drawing(
    db_path: str,
    guild,
    *,
    driver_names_read: AwaitedHook,
    session_result,
    driver_rows,
    points_map,
    round_number,
    race_name: str,
    is_sprint: bool,
    result_status: str,
    division_name: str,
    division_tier=None,
    season_number=None,
    dsq_phase_map=None,
) -> ResultsDrawing:
    """Resolve one session into a ResultsDrawing, or raise ResultsDataError.

    A team is named by the team itself, never by its Discord role (#375), and found by its
    shorthand (#381): nothing here asks Discord for a team. A driver's name and nationality are
    read through core's readers, handed the hook signup answers.
    """
    user_ids = [row.driver_user_id for row in driver_rows]
    team_ids = [row.team_instance_id for row in driver_rows]
    division_id = session_result.division_id

    return resolve_drawing(
        session_type=session_result.session_type,
        is_sprint=is_sprint,
        result_status=result_status,
        division_name=division_name,
        division_tier=division_tier,
        season_number=season_number,
        round_number=round_number,
        race_name=race_name,
        driver_rows=driver_rows,
        points_map=points_map,
        driver_names=await read_driver_names(
            db_path, driver_names_read, guild, user_ids, division_id=division_id
        ),
        team_names=await team_names_for_instances(db_path, team_ids),
        team_keys=await team_artwork_keys_for_instances(db_path, team_ids),
        nationalities=await read_driver_nationalities(
            db_path, driver_names_read, user_ids, division_id=division_id
        ),
        dsq_phase_map=dsq_phase_map or {},
        nationality_collected=await read_nationality_collected(db_path, driver_names_read),
    )
