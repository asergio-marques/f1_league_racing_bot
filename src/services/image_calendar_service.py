"""Resolve a division's calendar and project it onto a template (037).

Two steps, deliberately separate:

1. :func:`resolve_drawing` turns a division, its rounds and the track registry into a
   :class:`CalendarDrawing` — every value decided, nothing drawn. The fatal checks live
   here, so they fail before the expensive work and without a template in hand.
2. :func:`build_fill_spec` projects that drawing onto a parsed template, deciding the
   crop and which rounds leave by their group.

The split is what lets the crop arithmetic be tested without a division and the data
resolution without a template.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from models.image_catalogues import CapacityError, catalogue_for
from models.image_constants import DATE_FORMATS, TIME_FORMATS
from utils.svg_document import FieldIndex
from utils.svg_fill import FillSpec

log = logging.getLogger(__name__)

TEMPLATE_KEY = "calendar_template"

#: The values a round of the mystery format carries. Its track is concealed until the
#: round is run, so the record holds none — but the round is drawn all the same and
#: marked as such, and every mandatory field takes a real value (Constitution XIV.3, and
#: the wip-spec's "A round of the mystery format").
MYSTERY_COUNTRY = "Mystery"
MYSTERY_RACE_NAME = "Mystery GP"
MYSTERY_TRACK_NAME = "Mystery"
MYSTERY_DATUM = "Mystery"

#: Round format -> the label placed on ``round_<x>_format``. A normal round is emptied,
#: so a template author decides by the chrome they draw whether it is marked at all.
FORMAT_LABELS = {
    "SPRINT": "Sprint",
    "ENDURANCE": "Endurance",
    "MYSTERY": "Mystery",
    "NORMAL": "",
}

_ROUND_PREFIX = "round"


class CalendarDataError(Exception):
    """A fatal disagreement between a division and what a calendar needs.

    Raised before anything is drawn. The caller turns it into a Problem, which aborts the
    render and — for an uncommanded posting — falls back to the textual calendar.
    """


@dataclass(frozen=True)
class CalendarRound:
    """One round, every value already decided."""

    ordinal: int
    number: str
    format_label: str
    date_text: str
    time_text: str
    country_name: str
    race_name: str
    track_name: str
    image_datum: str


@dataclass(frozen=True)
class CalendarDrawing:
    """One division's calendar, resolved and ready to project onto a template."""

    division_name: str
    division_tier: str | None
    season_number: str | None
    rounds: list[CalendarRound] = field(default_factory=list)

    @property
    def round_count(self) -> int:
        return len(self.rounds)


# ── 1. Resolution ─────────────────────────────────────────────────────────


def _zone(name: str | None):
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("calendar: unknown time zone %r, falling back to UTC", name)
        return ZoneInfo("UTC")


def _format_moment(
    moment: datetime, date_format: str | None, time_format: str | None, zone_name: str | None
) -> tuple[str, str]:
    """Render *moment* as (date, time) in the configured zone.

    A graphic cannot carry a Discord timestamp, so it carries one zone for every reader
    and says which (Constitution XIV.15).
    """
    zone = _zone(zone_name)
    local = moment.astimezone(zone) if moment.tzinfo is not None else moment.replace(
        tzinfo=ZoneInfo("UTC")
    ).astimezone(zone)

    date_pattern = DATE_FORMATS.get(date_format or "", DATE_FORMATS["DDD_DD_MON_YYYY"])[0]
    time_pattern = TIME_FORMATS.get(time_format or "", TIME_FORMATS["24H"])

    date_text = local.strftime(date_pattern)
    time_text = local.strftime(time_pattern)
    abbreviation = local.strftime("%Z")
    if abbreviation:
        time_text = f"{time_text} {abbreviation}"
    return date_text, time_text


def resolve_drawing(
    *,
    division_name: str,
    division_tier: str | int | None,
    season_number: str | int | None,
    rounds: Sequence,
    tracks: Mapping[str, object],
    date_format: str | None = None,
    time_format: str | None = None,
    time_zone: str | None = None,
) -> CalendarDrawing:
    """Resolve every value a calendar draws, or raise :class:`CalendarDataError`.

    *rounds* are objects carrying ``round_number``, ``format``, ``track_name`` and
    ``scheduled_at``. *tracks* maps a track name to a record carrying ``country``,
    ``gp_name`` and ``name``.

    The track registry is joined **by name**: rounds record a track's name, not an id
    (see specs/037-calendar-image-generation/research.md § R7). A name matching no record
    leaves the mandatory country and grand prix name undeterminable, which is fatal.
    """
    ordered = sorted(rounds, key=lambda r: r.round_number)
    if not ordered:
        raise CalendarDataError(
            f"the division `{division_name}` holds no round at all, so there is no "
            f"calendar to draw"
        )

    resolved: list[CalendarRound] = []
    for entry in ordered:
        format_name = getattr(entry.format, "value", entry.format)
        is_mystery = format_name == "MYSTERY"

        if is_mystery:
            country, race_name, track_name = (
                MYSTERY_COUNTRY,
                MYSTERY_RACE_NAME,
                MYSTERY_TRACK_NAME,
            )
            datum = MYSTERY_DATUM
        else:
            name = entry.track_name
            record = tracks.get(name) if name else None
            if record is None:
                raise CalendarDataError(
                    f"round {entry.round_number} of `{division_name}` names the track "
                    f"`{name}`, which matches no track on this server. Its country and "
                    f"grand prix name cannot be determined."
                )
            country = getattr(record, "country", None)
            race_name = getattr(record, "gp_name", None)
            track_name = name
            datum = name
            if not country or not race_name:
                raise CalendarDataError(
                    f"the track `{name}` records no "
                    f"{'country' if not country else 'grand prix name'}, which round "
                    f"{entry.round_number} of `{division_name}` needs."
                )

        date_text, time_text = _format_moment(
            entry.scheduled_at, date_format, time_format, time_zone
        )

        resolved.append(
            CalendarRound(
                ordinal=entry.round_number,
                number=str(entry.round_number),
                format_label=FORMAT_LABELS.get(format_name, ""),
                date_text=date_text,
                time_text=time_text,
                country_name=country,
                race_name=race_name,
                track_name=track_name,
                image_datum=datum,
            )
        )

    return CalendarDrawing(
        division_name=division_name,
        division_tier=None if division_tier is None else str(division_tier),
        season_number=None if season_number is None else str(season_number),
        rounds=resolved,
    )


# ── 2. Projection onto a template ─────────────────────────────────────────


def _round_fields_declared(declared: Iterable[str], ordinal: int) -> list[str]:
    """Every id the template declares bearing *ordinal*, group included."""
    pattern = re.compile(rf"^{_ROUND_PREFIX}_{ordinal}(?:_.*)?$")
    return sorted(name for name in declared if pattern.match(name))


def build_fill_spec(
    drawing: CalendarDrawing,
    root,
    *,
    track_directory: Path | None = None,
) -> FillSpec:
    """Project *drawing* onto *root*, deciding the crop and what leaves beside it.

    Raises :class:`CalendarDataError` where the template cannot be counted. Overflow is
    **not** raised here: it is reported through ``row_count`` so the render service issues
    the capacity problem in one place, with the count, capacity and template named.
    """
    catalogue = catalogue_for(TEMPLATE_KEY)
    index = FieldIndex(root)
    declared = index.declared()

    try:
        capacity = catalogue.capacity(root) or 0
    except CapacityError as exc:
        raise CalendarDataError(str(exc)) from exc

    drawn = drawing.rounds[:capacity]
    final_ordinal = drawn[-1].ordinal if drawn else 0

    text: dict[str, str] = {}
    empty: list[str] = []
    image_data: dict[str, tuple[str, str]] = {}

    def put(field_id: str, value: str) -> None:
        """Fill where declared; empty rather than dash where the value does not apply."""
        if field_id not in declared:
            return
        if value:
            text[field_id] = value
        else:
            empty.append(field_id)

    put("division_name", drawing.division_name)
    put("season_number", drawing.season_number or "")
    put("division_tier", drawing.division_tier or "")

    for entry in drawn:
        prefix = f"{_ROUND_PREFIX}_{entry.ordinal}"
        put(f"{prefix}_number", entry.number)
        put(f"{prefix}_country_name", entry.country_name)
        put(f"{prefix}_race_name", entry.race_name)
        put(f"{prefix}_track_name", entry.track_name)
        put(f"{prefix}_format", entry.format_label)
        put(f"{prefix}_date", entry.date_text)
        put(f"{prefix}_time", entry.time_text)
        if f"{prefix}_image" in declared:
            image_data[f"{prefix}_image"] = ("track", entry.image_datum)

    # Rounds the template declares beyond the division's last. The crop removes whatever
    # is drawn *below* the cut; whatever stands *beside* the final round is above it and
    # must leave by its group. The two divide the work between them.
    remove: list[str] = []
    off_canvas: set[str] = set()
    crop_id = f"{_ROUND_PREFIX}_{final_ordinal}_vertical_crop_point"
    crop_y = _y_of(index, crop_id)

    for ordinal in range(final_ordinal + 1, capacity + 1):
        group_id = f"{_ROUND_PREFIX}_{ordinal}_group"
        # Every field of a round the division does not hold leaves the canvas one way or
        # the other, so none of them is an unresolved field (Constitution XIV.3).
        off_canvas.update(_round_fields_declared(declared, ordinal))
        if not _any_field_above(index, ordinal, crop_y):
            continue  # below the cut — the crop removes it
        if group_id in declared:
            remove.append(group_id)
        else:
            remove.extend(
                name
                for name in _round_fields_declared(declared, ordinal)
                if name not in remove
            )

    spec = FillSpec(
        root=root,
        image_type=TEMPLATE_KEY,
        text=text,
        empty=empty,
        remove=remove,
        off_canvas=off_canvas,
        crop=crop_id if crop_id in declared else None,
        # Only the template's *last* declared round is expected to crop at the canvas
        # height. A division drawn shorter crops higher by design, and must raise nothing.
        crop_is_final=final_ordinal == capacity,
        row_count=drawing.round_count,
        image_data=image_data,
        catalogue=catalogue,
    )
    if track_directory is not None:
        spec.asset_directories = {"track": track_directory}
    return spec


def _y_of(index: FieldIndex, field_id: str) -> float | None:
    # Reuses the fill pipeline's own geometry so the crop decision here and the cut there
    # cannot disagree about where a node sits.
    from utils.svg_fill import _element_y

    element = index.resolve(field_id)
    return None if element is None else _element_y(element)


def _any_field_above(index: FieldIndex, ordinal: int, crop_y: float | None) -> bool:
    """True where any field of *ordinal* sits at or above the cut.

    With no crop point resolved there is nothing to be above, so the round is treated as
    standing beside the final one and is removed — the safer reading, since leaving it
    would draw a round the division does not hold.
    """
    if crop_y is None:
        return True
    for name in _round_fields_declared(index.declared(), ordinal):
        element = index.resolve(name)
        if element is None:
            continue
        y = _y_of(index, name)
        if y is not None and y < crop_y:
            return True
    return False
