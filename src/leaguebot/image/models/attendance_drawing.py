"""The attendance sheet's drawing, and the resolution of it from the rows its caller composes.

The models a caller hands the image module to draw a division's attendance sheet — the drivers'
records (`DriverRecord`) and the rounds of the grid (`RoundHeading`) — and `resolve_drawing`,
which turns them into the `AttendanceDrawing` the image module projects onto its template
(`leaguebot.image.services.aspects.image_attendance_service`). Declared here, among image's
models, so that the attendance module composes its sheet through them and the drawing it is
handed, and never reaches into image's aspect services (architecture.md, "How modules and core
fit together").

**An empty round cell means zero.** Six situations produce one — the round conferred none, its
attendance is not yet finalised, it is yet to be run, it is recorded as cancelled, the driver
holds no record for it, or a pardon waived every point it would have conferred. None of them is
a value that could not be *determined*, so none raises a notice: the sheet lists the points a
round conferred and never the reason (XIV.3).

**The rows carry a floor** (XIV.12): a division holding no driver has no sheet to draw, and that
is fatal rather than an empty canvas, raised by `resolve_drawing` against the data before any
template is measured.

Pure: no Discord, no database, no rasteriser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.image.models.image_constants import MYSTERY_DATUM, MYSTERY_GRAND_PRIX
from leaguebot.image.models.image_module import DrawingRefused

ATTENDANCE_TEMPLATE_KEY = "attendance_template"


#: The annotation a driver sanctioned upon this posting carries. The textual sheet appends
#: " *(reached point limit)*"; the graphic draws the plain literal, the emphasis being message
#: formatting rather than a value. It reads the same for the autoreserve and the autosack
#: sanctions: the sheet is not where they are told apart, and the verdict announced for the
#: driver names which was enforced.
SANCTION_ANNOTATION = "Reached point limit"


#: The two labels the one limit plate can carry. The sheet draws a single plate because a
#: league can only have a single limit: `/attendance config autoreserve` and
#: `/attendance config autosack` refuse each other, so a template declaring one block for each
#: would always draw one and delete the other, leaving a hole beside the survivor. The label
#: is therefore a field the projection fills, as `SANCTION_ANNOTATION` is a literal it draws.
LIMIT_LABEL_RESERVE = "RESERVE AT"
LIMIT_LABEL_SACK = "SACKED AT"

#: The two marks a driver's total may be drawn against, and the class they are artwork of.
#: `marker` is shared with the standings result chips and the position-change arrows: three
#: vocabularies of the module's own, closed all the way down, and one folder for a league to
#: redraw them in.
MARK_NEAR = "attendance_limit_near"
MARK_REACHED = "attendance_limit_reached"
MARK_ASSET_CLASS = "marker"

#: Every datum the projection can hand to the asset resolver, named so a test can hold the
#: packaged folder to it — a mark added here without a file would resolve to the fallback and
#: draw the wrong picture rather than fail.
MARK_DATA: tuple[str, ...] = (MARK_NEAR, MARK_REACHED)

#: How many points below the limit the near mark reaches. A driver on the limit less one or
#: less two is two points away or fewer; one on the limit less three is not marked at all.
MARK_NEAR_BAND = 2


class AttendanceDataError(DrawingRefused):
    """A fatal disagreement between a division and what an attendance sheet needs.

    Raised before anything is drawn. The caller turns it into a Problem, which abandons this
    graphic — and this division's graphic alone, the other divisions answering for themselves
    (XIV.4).
    """


@dataclass(frozen=True)
class DriverRecord:
    """One driver's attendance in one division, as the module persisted it.

    *key* identifies the driver for the name, nationality and team lookups — the Discord user
    id, matching what the textual sheet resolves by.

    *round_points* maps a round's **ordinal** to the points that round conferred. A missing
    ordinal and a stored ``None`` are the same as a stored ``0``: the round counted nothing.
    """

    key: int
    total: int
    round_points: Mapping[int, int | None] = field(default_factory=dict)
    sanctioned: bool = False


@dataclass(frozen=True)
class SheetEntry:
    """One row of the sheet, resolved and ready to be projected."""

    ordinal: int
    driver_name: str
    points: str
    #: What is drawn: the team's full name (#381).
    team_name: str = ""
    nationality: str | None = None
    sanction: str = ""
    #: The mark drawn beneath the total, or None where the driver has earned none. It travels
    #: with the row rather than being derived at projection time for the reason the standings
    #: ``CellValue`` carries its highlight beside its text: they are two readings of one
    #: number, and deriving them apart is how a mark comes to disagree with the total above it.
    mark: str | None = None
    #: Round ordinal → the cell's text. An empty string is zero and is drawn empty.
    round_points: Mapping[int, str] = field(default_factory=dict)
    #: What the team's artwork is found by: its shorthand (#381). The drawn name where None.
    team_key: str | None = None


@dataclass(frozen=True)
class RoundHeading:
    """One column of the grid: a round the division holds, run or not."""

    ordinal: int
    number: str
    track: str | None = None

    #: The country the round is run in — the datum its flag resolves by (044).
    #: ``track`` is retained: it still names the round and decides the mystery case,
    #: it simply stopped being an asset datum when the heading moved to the flag class.
    country: str | None = None

    #: Whether the round is a mystery round, which the builder says and never spells: the
    #: heading names the module's own datum for it, in place of the track and the country, so
    #: no builder holds the literal.
    mystery: bool = False

    def __post_init__(self) -> None:
        if self.mystery:
            object.__setattr__(self, "track", MYSTERY_DATUM)
            object.__setattr__(self, "country", MYSTERY_DATUM)


@dataclass(frozen=True)
class AttendanceDrawing:
    """A division's attendance record, resolved and ready to project onto a template."""

    division_name: str
    #: The occasion this sheet stands at, already composed into the phrase the heading draws
    #: — `Opening Classification`, `After Round 10`, `Final Classification`. Shared with the
    #: standings sheets so a division's two records cannot describe the same moment
    #: differently.
    classification_label: str
    template_key: str = ATTENDANCE_TEMPLATE_KEY
    #: Whether this sheet is about a round, and so has a grand prix to name. False at both
    #: season boundaries — see `leaguebot.core.models.classification_occasion`.
    names_a_race: bool = True
    division_tier: str | None = None
    season_number: str | None = None
    race_name: str | None = None
    #: The one limit plate: what it is called, and the number on it. Both are None together
    #: where neither functionality is switched on, and the plate leaves the canvas whole.
    limit_label: str | None = None
    limit_value: str | None = None
    #: True where the league collects a driver's nationality at all. Where it does not, an
    #: empty flag field is exactly what was configured and raises nothing (XIV.4).
    nationality_collected: bool = True
    entries: list[SheetEntry] = field(default_factory=list)
    rounds: list[RoundHeading] = field(default_factory=list)

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def round_count(self) -> int:
        return len(self.rounds)


# ── 1. Resolution ─────────────────────────────────────────────────────────


def cell_text(points: int | None) -> str:
    """The text of one round cell.

    Zero and absent are the same picture and the same meaning. This is the whole of the
    six-case collapse: the sheet reports what a round conferred, and nothing conferred is
    nothing drawn.
    """
    return "" if not points else str(points)


def active_limit(
    *, autoreserve_threshold: int | None, autosack_threshold: int | None
) -> tuple[str, int] | None:
    """The one limit the sheet answers to — its label and its value — or None where neither is on.

    A league can only have one. `/attendance config autoreserve` and `/attendance config
    autosack` each refuse to be set while the other is active, so exactly one of the two is
    ever a number and the sheet has one plate and one set of marks rather than two of each.

    Auto-sack wins where a database somehow holds both, because that is what the enforcement
    does: `owed_sanctions` tests the sack threshold first and moves on to the
    next driver, so a sheet marking against the reserve limit would warn of a sanction that
    could no longer be the one applied.

    0 and None are the same thing here as everywhere else in the module: switched off.
    """
    if autosack_threshold:
        return LIMIT_LABEL_SACK, autosack_threshold
    if autoreserve_threshold:
        return LIMIT_LABEL_RESERVE, autoreserve_threshold
    return None


def mark_for(total: int | None, limit: int | None) -> str | None:
    """The mark a driver's *total* earns against *limit*, or None where it earns none.

    Two tiers, and *limit* is whichever of the two thresholds is live — the mark says a driver
    is close to losing their seat and not which of the two ways they would lose it, exactly as
    ``SANCTION_ANNOTATION`` does once they have.

    ``>=`` at the limit, matching `owed_sanctions`: the sheet must not draw a
    driver as merely close when the same number has already sanctioned them.

    **A total of zero never marks.** The near band is otherwise anchored below zero for a limit
    of one or two, which would paint the mark across every driver on a clean sheet — a warning
    of nothing, drawn over almost the whole column.
    """
    if not limit or not total:
        return None
    if total >= limit:
        return MARK_REACHED
    if total >= max(1, limit - MARK_NEAR_BAND):
        return MARK_NEAR
    return None


def resolve_drawing(
    *,
    division_name: str,
    records: Sequence[DriverRecord],
    occasion: ClassificationOccasion = ClassificationOccasion.AFTER_ROUND,
    round_number: str | int | None = None,
    display_names: Mapping[int, str],
    team_names: Mapping[int, str] | None = None,
    nationalities: Mapping[int, str | None] | None = None,
    rounds: Sequence[RoundHeading] | None = None,
    team_keys: Mapping[int, str] | None = None,
    autoreserve_threshold: int | None = None,
    autosack_threshold: int | None = None,
    division_tier: str | int | None = None,
    season_number: str | int | None = None,
    race_name: str | None = None,
    nationality_collected: bool = True,
    mystery: bool = False,
) -> AttendanceDrawing:
    """Resolve every value an attendance sheet draws.

    *records* are the drivers the textual sheet composes — every non-reserve driver of the
    division, every reserve distributed into a seat for the round, and every driver sanctioned
    upon this posting. Composing them is the caller's, as it is the textual sheet's; a driver
    sacked or moved to Reserve at an earlier round keeps their row (#220), their team being the
    seat they hold now: the reserve team, or none.

    The rows are drawn in the order *records* are handed, ordinals ``1..n``. That order is
    attendance's, the textual sheet's for the occasion, and the drawing never re-orders it: an
    order is a rule, and a graphic works out no rule (XIV.7).

    Raises :class:`AttendanceDataError` where the division holds no driver at all — the floor
    of XIV.12, raised here against the data and before any template is in view.

    *team_keys* holds the shorthand of each driver's team, keyed as *team_names* is, which the
    team's artwork is found by apart from the full name drawn (#381).

    *mystery* says the sheet stands after a round of the mystery format, which is named as the
    module's own grand prix for it in place of *race_name*, so no builder holds the literal.
    """
    if not records:
        raise AttendanceDataError(
            f"the division `{division_name}` holds no driver at all, so there is no "
            f"attendance sheet to draw"
        )

    team_map = team_names or {}
    key_map = team_keys or {}
    nationality_map = nationalities or {}

    # The one limit the plate names and the marks are measured against, decided once so a
    # row can never be marked against a limit other than the one drawn above it.
    limit = active_limit(
        autoreserve_threshold=autoreserve_threshold,
        autosack_threshold=autosack_threshold,
    )

    entries: list[SheetEntry] = []
    for record in records:
        entries.append(
            SheetEntry(
                ordinal=len(entries) + 1,
                driver_name=display_names.get(record.key, str(record.key)),
                points=str(record.total or 0),
                team_name=team_map.get(record.key, ""),
                team_key=key_map.get(record.key),
                nationality=nationality_map.get(record.key),
                sanction=SANCTION_ANNOTATION if record.sanctioned else "",
                mark=mark_for(record.total, limit[1] if limit else None),
                round_points={
                    ordinal: cell_text(value)
                    for ordinal, value in record.round_points.items()
                },
            )
        )

    return AttendanceDrawing(
        division_name=division_name,
        classification_label=occasion.label(round_number),
        names_a_race=occasion.names_a_round,
        division_tier=None if division_tier is None else str(division_tier),
        season_number=None if season_number is None else str(season_number),
        race_name=MYSTERY_GRAND_PRIX if mystery else race_name,
        # Both thresholds off is the functionality switched off, which is a configured
        # absence: the block leaves whole and nothing is reported (XIV.4).
        limit_label=limit[0] if limit else None,
        limit_value=str(limit[1]) if limit else None,
        nationality_collected=nationality_collected,
        entries=entries,
        rounds=list(rounds or []),
    )
