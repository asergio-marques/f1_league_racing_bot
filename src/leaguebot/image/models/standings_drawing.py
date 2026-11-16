"""What a standings picture draws: one championship's classification and its season grid.

Plain data, declared by the image module and composed by whoever has the classification
(docs/design/architecture.md, "How modules and core fit together"): the drawing places these
values on a template and works none of them out. Pure: no Discord, no database, no rasteriser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from leaguebot.image.models.image_constants import MYSTERY_DATUM

DRIVERS_TEMPLATE_KEY = "standings_drivers_template"
CONSTRUCTORS_TEMPLATE_KEY = "standings_constructors_template"


class StandingsDataError(Exception):
    """A fatal disagreement between a classification and what a standings graphic needs.

    Raised before anything is drawn. The caller turns it into a Problem, which abandons this
    graphic — and that graphic alone, the other championship answering for itself (XIV.4).
    """


class PositionMovement(Protocol):
    """One entry's change of position against the reference round, as the drawing reads it.

    ``change`` is unsigned; ``direction`` carries the sense. Whoever composes the drawing
    derives it, and the drawing carries it through untouched.
    """

    @property
    def previous_position(self) -> int: ...

    @property
    def change(self) -> int: ...

    @property
    def direction(self) -> str: ...


#: The four backgrounds a cell may be given, in precedence order. A podium place takes
#: priority over a points finish — a winner is in the points too, and the gold says more.
#:
#: Each value doubles as the **stem of the selector** the template is asked for, so
#: ``f"highlight_{kind}"`` composes without a second table to keep in step.
#:
#: A kind is **not** a datum. The datum names the session as well — ``race_p1`` against
#: ``qualifying_p1`` — because one folder now holds the marks of both, and a bare ``p1.svg``
#: sitting beside ``qualifying_p1.svg`` said which session it belonged to only by omission.
#: The kind is what the stylesheet selector is built from, and stays as it was, so a template
#: written against ``.highlight_p1_text`` is untouched by the datum naming.
HIGHLIGHT_P1 = "p1"
HIGHLIGHT_P2 = "p2"
HIGHLIGHT_P3 = "p3"
HIGHLIGHT_POINTS = "points"

#: The four together, for the places that need to walk them.
HIGHLIGHT_KINDS: tuple[str, ...] = (
    HIGHLIGHT_P1,
    HIGHLIGHT_P2,
    HIGHLIGHT_P3,
    HIGHLIGHT_POINTS,
)

#: The fastest-lap overlay. Not a member of the four above: it is an independent layer and
#: may stand at the same time as any of them.
#:
#: This is the **layer's** name, not the datum. It composes the slot id the template declares —
#: ``row_<x>_round_<z>_feature_race_fastest_lap`` — where the ``race`` is already the session,
#: so prefixing it here would name the session twice. The file it draws is
#: :data:`FASTEST_LAP_DATUM`.
HIGHLIGHT_FASTEST_LAP = "fastest_lap"

#: The asset class every chip draws. Which chip is drawn is the **datum** — one of the four
#: kinds above, or the fastest lap — never a class of its own, so a league keeps one folder.
#: That folder is the **marker** directory, shared with the position-change arrows and with
#: the attendance sheet's own marks: three vocabularies of the module's own, closed all the
#: way down, and one folder for a league to draw them in.
HIGHLIGHT_ASSET_CLASS = "marker"

#: What each session's datum is prefixed with. The four kinds are the same four either
#: session earns, but the mark is a different picture: a race result takes the plate, and a
#: qualifying result a small corner triangle drawn a step darker than the plate of its own
#: placing. The prefixes are what tell the two sets of files apart in one folder.
RACE_DATUM_PREFIX = "race_"
QUALIFYING_DATUM_PREFIX = "qualifying_"

#: The file the fastest-lap overlay draws. A race datum like any other, so it carries the race
#: prefix: only a race confers a fastest lap, and a reader of the folder should not have to
#: know that to place the file.
FASTEST_LAP_DATUM = f"{RACE_DATUM_PREFIX}{HIGHLIGHT_FASTEST_LAP}"

#: Every datum the projection can hand to the asset resolver. Named so that a test can hold the
#: packaged folder to it — a kind added here without a file would resolve to the fallback and
#: draw the wrong mark rather than fail.
HIGHLIGHT_DATA: tuple[str, ...] = (
    *(f"{RACE_DATUM_PREFIX}{kind}" for kind in HIGHLIGHT_KINDS),
    FASTEST_LAP_DATUM,
    *(f"{QUALIFYING_DATUM_PREFIX}{kind}" for kind in HIGHLIGHT_KINDS),
)



@dataclass(frozen=True)
class CellValue:
    """One cell of the season grid: what it says, and what it is worth calling out.

    Text and highlight travel together deliberately. They are two readings of one session
    result, and a projection that derived them apart would eventually draw a gold chip under
    an outcome literal — which is exactly the case ``highlight_for`` exists to refuse.
    """

    #: The finishing position or outcome literal drawn in the cell. An **empty string** means
    #: the data determined it to be nothing — no session of that type, a round unrun or
    #: cancelled, or a driver who took no part — and is emptied quietly rather than dashed
    #: (XIV.3).
    text: str = ""
    #: One of the four background kinds, or None where the cell earns no background.
    highlight: str | None = None
    #: Whether the fastest-lap overlay stands on this cell.
    fastest_lap: bool = False



@dataclass(frozen=True)
class RoundCells:
    """The cells of one round on one row, part of the season grid."""

    #: Session key → cell, for a drivers row.
    sessions: dict[str, CellValue] = field(default_factory=dict)
    #: Car ordinal → (driver name or None, session key → cell), for a constructors row.
    cars: dict[int, tuple[str | None, dict[str, CellValue]]] = field(default_factory=dict)


@dataclass(frozen=True)
class StandingsEntry:
    """One row of the classification, every cell already the string that will be drawn."""

    #: The row's discriminator — the drawing order, contiguous from 1.
    ordinal: int
    #: The value placed on ``row_<x>_position``: the position the standings **recorded**.
    #:
    #: Usually the ordinal, and deliberately not filled from it. A reserve driver who raced
    #: holds a standing position but is drawn only where the division's reserves toggle is
    #: on, so with the toggle off the recorded positions run 1, 2, 4 while the rows run
    #: 1, 2, 3. The textual standings print the recorded position, and XIV.7 forbids the
    #: graphic and the table disagreeing about a value both draw — so the recorded position
    #: is what is drawn, and the ordinal addresses the row and nothing else.
    #:
    #: XIV.11's "fill from the ordinal" governs a collection whose ordinal genuinely
    #: coincides with the datum, as a results row's does; here the two can part company.
    position: str
    #: The team drawn on this row. On the drivers graphic that is the team of the division
    #: **seating the driver at the moment of generation** — the reserve team for a reserve —
    #: and never the team whose car they drove in any one round (FR-020). Its full name (#381).
    team_name: str
    points: str
    #: Drivers graphic only; None on the constructors graphic, which names no driver here.
    driver_name: str | None = None
    #: The datum behind ``row_<x>_driver_flag``. None where the driver recorded none.
    nationality: str | None = None
    #: The leader's points less this entry's. Always available: it is arithmetic over the
    #: classification being drawn alone, so it survives a first round with no reference.
    gap_to_leader: int | None = None
    #: The change against the reference round, or None where it cannot be determined — the
    #: first round of a division, or an entry the reference round does not hold. Absent
    #: entirely rather than partly filled, and not a failure (FR-017).
    movement: PositionMovement | None = None
    #: Round ordinal → its cells. Empty on a template declaring no round.
    cells: dict[int, RoundCells] = field(default_factory=dict)
    #: What the team's artwork is found by: its shorthand (#381). The drawn name where None.
    team_key: str | None = None


@dataclass(frozen=True)
class RoundHeading:
    """One column of the grid."""

    ordinal: int
    number: str
    track: str | None = None

    #: The country the round is run in — the datum its flag resolves by (044).
    #: ``track`` is retained: it still names the round and decides the mystery case,
    #: it simply stopped being an asset datum when the heading moved to the flag class.
    country: str | None = None

    #: Whether the round is a mystery round, which the builder says and never spells: its flag
    #: is the module's own datum in place of the country, and no track is named.
    mystery: bool = False

    def __post_init__(self) -> None:
        if self.mystery:
            object.__setattr__(self, "country", MYSTERY_DATUM)


@dataclass(frozen=True)
class StandingsDrawing:
    """One championship's classification, resolved and ready to project onto a template."""

    template_key: str
    division_name: str
    #: The occasion this sheet stands at, already composed into the phrase the heading draws
    #: — `Opening Classification`, `After Round 10`, `Final Classification`. Composed once, by
    #: `ClassificationOccasion.label`, so the graphic and the message text cannot disagree.
    classification_label: str
    #: The round's results lifecycle phase. Empty at the season's opening, where no session
    #: has run and there is no phase to stand between.
    result_status_label: str
    #: Whether this sheet is about a round, and so has a grand prix to name beneath its
    #: title. False at both season boundaries — see `leaguebot.core.models.classification_occasion`.
    names_a_race: bool = True
    division_tier: str | None = None
    season_number: str | None = None
    race_name: str | None = None
    #: True where the league collects a driver's nationality at all. Where it does not, an
    #: empty flag field is exactly what was configured and raises nothing (XIV.4).
    nationality_collected: bool = True
    entries: list[StandingsEntry] = field(default_factory=list)
    rounds: list[RoundHeading] = field(default_factory=list)
    #: Constructors only: team name -> the team's currently configured seat count. A car
    #: beyond it is removed silently regardless of whether a driver was allocated to it —
    #: the ceiling `build_fill_spec` trims cars against is the team's own seats, never the
    #: template's declared room, which only bounds the fatal case. Empty on the drivers
    #: graphic.
    team_seat_counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_drivers(self) -> bool:
        return self.template_key == DRIVERS_TEMPLATE_KEY

    @property
    def entry_count(self) -> int:
        return len(self.entries)
