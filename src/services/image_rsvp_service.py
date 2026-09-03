"""Resolve and project a check-in call graphic — one round of one division.

**This is the module's first static graphic** (Constitution XIV.17, v4.6.0). The call it rides
on carries three buttons armed against its message and cannot be reposted, so the embed is
edited in place on every press and the attachment rides through untouched. The graphic is
generated once, at the moment the call is posted, and never again while that call stands.

What makes that safe is what this module cannot reach. It reads the round, its sessions, its
date and the moment its check-in locks — and **no driver, no team, no RSVP status, no
attendance point and no roster**. Everything the three buttons alter lives in the embed, which
is edited, and stays off the picture, which is not.

Adding any of those here is an amendment of the static declaration and not a change to a
utility. Nothing in the module can detect the breach: the result is a stale picture under a
current message, which reports nothing and looks correct.

The graphic **displaces nothing** (XIV.7). The role mention, the embed, its roster, its status
indicators and its three buttons all remain exactly as the textual flow composes them; the
picture is added beside them, and its fallback is the message posted without it.

See specs/041-attendance-image-generation/contracts/attendance-catalogues.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from models.image_catalogues import (
    DIVISION_LOGO_ASSET,
    DIVISION_LOGO_FIELD,
    CapacityError,
    catalogue_for,
)
from models.image_constants import DATE_FORMATS, TIME_FORMATS
from utils.svg_document import FieldIndex
from utils.svg_fill import FillSpec

log = logging.getLogger(__name__)

RSVP_TEMPLATE_KEY = "rsvp_template"

_SESSION_PREFIX = "session"

#: The round-format labels, which are the text the embed carries (FR-022).
_FORMAT_LABELS = {
    "NORMAL": "Normal",
    "SPRINT": "Sprint",
    "ENDURANCE": "Endurance",
    "MYSTERY": "Mystery",
}

#: The sessions each format is run over, in the order they are run. The names are the weather
#: graphic's, carrying no qualifier of a session's length — so the short qualifying and long
#: race of a mystery round are named as any other round's are (FR-024).
_SPRINT_SESSIONS = (
    "Sprint Qualifying",
    "Sprint Race",
    "Feature Qualifying",
    "Feature Race",
)
_ORDINARY_SESSIONS = ("Qualifying", "Race")

#: What a round of the mystery format draws, its track being concealed until it is run. These
#: are values, not exemptions: no mandatory field is emptied for want of a track (FR-029).
MYSTERY_RACE_NAME = "Mystery Grand Prix"
MYSTERY_LITERAL = "Mystery"


class RsvpDataError(Exception):
    """A fatal disagreement between a round and what a check-in graphic needs.

    Raised before anything is drawn. The caller posts the call **without** an attachment —
    there is no text to restore, the graphic having displaced nothing (XIV.7).
    """


@dataclass(frozen=True)
class SessionName:
    """One session of the round, named in the order the sessions are run."""

    ordinal: int
    name: str


@dataclass(frozen=True)
class RsvpDrawing:
    """One check-in call, resolved and ready to project onto a template."""

    division_name: str
    round_number: str
    race_name: str
    round_format: str
    round_date: str
    round_time: str
    template_key: str = RSVP_TEMPLATE_KEY
    division_tier: str | None = None
    season_number: str | None = None
    track_name: str | None = None
    country_name: str | None = None
    track_datum: str | None = None
    deadline_date: str | None = None
    deadline_time: str | None = None
    sessions: list[SessionName] = field(default_factory=list)

    @property
    def session_count(self) -> int:
        return len(self.sessions)


# ── 1. Resolution ─────────────────────────────────────────────────────────


def session_names(round_format: str | None) -> tuple[str, ...]:
    """The sessions a round of *round_format* is run over, in the order they are run."""
    return _SPRINT_SESSIONS if _format_key(round_format) == "SPRINT" else _ORDINARY_SESSIONS


def format_label(round_format: str | None) -> str:
    """The format's label — the text the embed carries."""
    return _FORMAT_LABELS.get(_format_key(round_format), "Normal")


def _format_key(round_format: str | None) -> str:
    if round_format is None:
        return "NORMAL"
    raw = getattr(round_format, "value", round_format)
    return str(raw).rsplit(".", 1)[-1].upper()


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("rsvp: unknown time zone %r, falling back to UTC", name)
        return ZoneInfo("UTC")


def format_moment(
    moment: datetime,
    date_format: str | None,
    time_format: str | None,
    zone_name: str | None,
) -> tuple[str, str]:
    """Render *moment* as (date, time) in the configured zone, as the calendar does.

    A graphic cannot carry a Discord timestamp, so it carries one zone for every reader and
    says which (XIV.15). The embed renders this same instant as a per-reader timestamp; this
    is the one respect in which the picture tells a reader less.
    """
    zone = _zone(zone_name)
    local = (
        moment.astimezone(zone)
        if moment.tzinfo is not None
        else moment.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    )

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
    round_number: str | int,
    round_format: str | None,
    scheduled_at: datetime,
    deadline_at: datetime | None = None,
    track_name: str | None = None,
    race_name: str | None = None,
    country_name: str | None = None,
    is_mystery: bool = False,
    division_tier: str | int | None = None,
    season_number: str | int | None = None,
    date_format: str | None = None,
    time_format: str | None = None,
    time_zone: str | None = None,
) -> RsvpDrawing:
    """Resolve every value a check-in graphic draws.

    *deadline_at* is the moment the check-in locks, and arrives **already derived** from
    ``attendance_service.derive_checkin_deadline``. No arithmetic over times happens in this
    module: XIV.7 admits the deadline as a derived presentation only on the condition that the
    derivation lives in the service owning the figures.

    A round of the mystery format conceals its track and records none. It is drawn all the
    same, and its concealment is a *value* rather than an exemption (FR-029).
    """
    mystery = is_mystery or _format_key(round_format) == "MYSTERY"

    if mystery:
        drawn_race = MYSTERY_RACE_NAME
        drawn_track = MYSTERY_LITERAL
        drawn_country = MYSTERY_LITERAL
        track_datum: str | None = MYSTERY_LITERAL
    else:
        drawn_race = race_name or ""
        drawn_track = track_name or ""
        drawn_country = country_name or ""
        track_datum = track_name or None

    date_text, time_text = format_moment(
        scheduled_at, date_format, time_format, time_zone
    )

    deadline_date = deadline_time = None
    if deadline_at is not None:
        deadline_date, deadline_time = format_moment(
            deadline_at, date_format, time_format, time_zone
        )

    names = session_names(round_format)
    sessions = [SessionName(ordinal=i, name=n) for i, n in enumerate(names, start=1)]

    return RsvpDrawing(
        division_name=division_name,
        round_number=str(round_number),
        race_name=drawn_race,
        round_format=format_label(round_format),
        round_date=date_text,
        round_time=time_text,
        division_tier=None if division_tier is None else str(division_tier),
        season_number=None if season_number is None else str(season_number),
        track_name=drawn_track or None,
        country_name=drawn_country or None,
        track_datum=track_datum,
        deadline_date=deadline_date,
        deadline_time=deadline_time,
        sessions=sessions,
    )


# ── 2. Projection ─────────────────────────────────────────────────────────


def _ids_bearing(declared, stem: str) -> list[str]:
    return sorted(
        name for name in declared if name == stem or name.startswith(f"{stem}_")
    )


def build_fill_spec(
    drawing: RsvpDrawing,
    root,
    *,
    asset_directories: Mapping[str, Path] | None = None,
) -> FillSpec:
    """Project *drawing* onto *root*, deciding what leaves the canvas beside it.

    Raises :class:`RsvpDataError` where the template's sessions cannot be counted — a gap in
    the numbering. Declaring **no** session at all is not a fault: the session list is an
    optional unit, and a template declaring none of it names no session (FR-004).
    """
    catalogue = catalogue_for(drawing.template_key)
    declared = FieldIndex(root).declared()

    try:
        capacity = catalogue.capacity(root) or 0
    except CapacityError as exc:
        raise RsvpDataError(str(exc)) from exc

    drawn = drawing.sessions[:capacity]

    text: dict[str, str] = {}
    empty: list[str] = []
    empty_quietly: list[str] = []
    remove: list[str] = []
    image_data: dict[str, tuple[str, str]] = {}
    off_canvas: set[str] = set()

    def put(field_id: str, value: str | None) -> None:
        if field_id not in declared:
            return
        if value:
            text[field_id] = value
        else:
            empty_quietly.append(field_id)

    def put_optional(field_id: str, value: str | None) -> None:
        """An optional whose absence is worth a notice — unless a group takes it away.

        A template giving the country a card of its own, or the track image a plate, declares
        the group so that a round carrying no track leaves neither standing empty under a
        label naming what is not there.
        """
        if field_id not in declared:
            return
        if value:
            text[field_id] = value
            return
        group_id = f"{field_id}_group"
        if group_id in declared:
            off_canvas.update(_ids_bearing(declared, group_id))
            remove.append(group_id)
        else:
            empty.append(field_id)

    put("division_name", drawing.division_name)

    # The division's logo, where a league's own template declares the slot (2026-09-02).
    if DIVISION_LOGO_FIELD in declared:
        image_data[DIVISION_LOGO_FIELD] = (DIVISION_LOGO_ASSET, drawing.division_name)
    put("round_number", drawing.round_number)
    put("race_name", drawing.race_name)
    put("round_format", drawing.round_format)
    put("round_date", drawing.round_date)
    put("round_time", drawing.round_time)

    put_optional("season_number", drawing.season_number)
    put_optional("division_tier", drawing.division_tier)
    put_optional("track_name", drawing.track_name)
    put_optional("country_name", drawing.country_name)
    put_optional("deadline_date", drawing.deadline_date)
    put_optional("deadline_time", drawing.deadline_time)

    # The round's country flag. The check-in graphic is one of the two types that may draw
    # a circuit map as well (044, XIV.13); each class is optional and independent, so a
    # template declares either, both, or neither. A mystery round conceals its country with
    # its track, and both data are already the "Mystery" literal above, so each class
    # resolves its own directory's mystery.svg with no special case here.
    if "track_flag" in declared:
        if drawing.country_name:
            image_data["track_flag"] = ("flag", drawing.country_name)
        else:
            group_id = "track_flag_group"
            if group_id in declared:
                off_canvas.update(_ids_bearing(declared, group_id))
                remove.append(group_id)
            else:
                remove.append("track_flag")

    # The track image. A mystery round resolves it from the datum "Mystery", drawing the
    # packaged mystery.svg — a league decides by the file it places there how a concealed
    # track is depicted (FR-029).
    if "track_image" in declared:
        if drawing.track_datum:
            image_data["track_image"] = ("track", drawing.track_datum)
        else:
            group_id = "track_image_group"
            if group_id in declared:
                off_canvas.update(_ids_bearing(declared, group_id))
                remove.append(group_id)
            else:
                remove.append("track_image")

    for session in drawn:
        put(f"{_SESSION_PREFIX}_{session.ordinal}_name", session.name)

    # Sessions the template declares beyond the round's own. Each leaves by its group, which
    # this type makes mandatory, and no error is reported (FR-040).
    for ordinal in range(len(drawn) + 1, capacity + 1):
        stem = f"{_SESSION_PREFIX}_{ordinal}"
        group_id = f"{stem}_group"
        off_canvas.update(_ids_bearing(declared, stem))
        if group_id in declared:
            remove.append(group_id)
        else:
            remove.extend(
                name for name in _ids_bearing(declared, stem) if name not in remove
            )

    spec = FillSpec(
        root=root,
        image_type=drawing.template_key,
        text=text,
        empty=empty,
        empty_quietly=empty_quietly,
        remove=remove,
        off_canvas=off_canvas,
        # A template declaring **no** session at all is not overflowing — it has opted out of
        # the session list entirely, the collection being an optional unit (XIV.3). Reporting
        # the round's session count there would refuse a legitimate template. A template
        # declaring *some* sessions and too few is a different matter and overflows as any
        # other collection does (FR-040).
        row_count=0 if capacity == 0 else drawing.session_count,
        image_data=image_data,
        catalogue=catalogue,
    )
    if asset_directories:
        spec.asset_directories = dict(asset_directories)
    return spec
