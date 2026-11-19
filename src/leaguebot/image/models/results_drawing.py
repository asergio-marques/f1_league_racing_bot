"""What a results picture draws: one session's classification, every value already decided.

Plain data, declared by the image module and composed by whoever has the classification
(docs/design/architecture.md, "How modules and core fit together"): the drawing places these
values on a template and works none of them out. Pure: no Discord, no database, no rasteriser.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from leaguebot.image.models.image_module import DrawingRefused

#: The aspect a results picture is switched by, which a caller asks the drawing's gate about.
RESULTS_ASPECT = "results"

QUALIFYING_TEMPLATE_KEY = "results_qualifying_template"
RACE_TEMPLATE_KEY = "results_race_template"


class ResultsDataError(DrawingRefused):
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
    #: What is drawn: the team's full name (#381).
    team_name: str
    points: str
    postrace_penalty: str | None
    appeal_penalty: str | None
    #: What the team's artwork is found by: its shorthand (#381). The drawn name where None.
    team_key: str | None = None
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
