"""Resolve and project a weather graphic — one phase of one round of one division.

**One utility serves all six templates.** The three phases and the mystery notice draw one
subject and differ only in which parts of it they carry, so the resolution is parameterised by
phase rather than split six ways.

Nothing here is computed. The likelihood of rain, the type drawn for each session and the
sequence drawn within it are all read as the weather module persisted them (Principle IV), and
every value the graphic shares with the textual forecast is produced by the renderer that
produces it for the message (Constitution XIV.7) — ``format_rain_probability``,
``format_session_weather_type``, ``session_type_label`` and ``format_slot_sequence``. A change
to how the forecast renders any of them is a change to the graphic by the same stroke.

Two rules of v4.7.0 shape this module:

* the **selecting datum** (XIV.10) — :func:`weather_template_key` is a pure function of the
  phase and the round's format, and reads nothing else;
* **channel markup is not content** (XIV.16) — the summary is drawn from
  ``format_slot_sequence``, which returns the value unadorned, and never by stripping the
  italics out of the forecast message's own rendering.

See specs/042-weather-image-generation/contracts/weather-catalogues.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from models.image_catalogues import CapacityError, catalogue_for
from utils.message_builder import (
    format_rain_probability,
    format_session_weather_type,
    format_slot_sequence,
    session_type_label,
)
from utils.svg_document import FieldIndex
from utils.svg_fill import FillSpec

log = logging.getLogger(__name__)

_SESSION_PREFIX = "session"
_SLOT_PREFIX = "slot"

#: Phase → its template key for a round of every format but the sprint.
_PLAIN_KEYS = {
    1: "weather_p1_template",
    2: "weather_p2_template",
    3: "weather_p3_template",
}

#: Phase → its template key for a round of the sprint format. Phase 1 draws no session, so one
#: template serves every format and the sprint variant does not arise for it.
_SPRINT_KEYS = {
    1: "weather_p1_template",
    2: "weather_p2_sprint_template",
    3: "weather_p3_sprint_template",
}

MYSTERY_TEMPLATE_KEY = "weather_mystery_template"

#: The description of the phase the graphic stands for — fixed text, and the only place the
#: phase is named at all. No weather graphic draws a phase *number* (FR-011, FR-022).
PHASE_DESCRIPTIONS = {
    1: "Initial chance of rain",
    2: "Initial session forecast",
    3: "Final session forecast",
}


class WeatherDataError(Exception):
    """A fatal disagreement between a round and what a weather graphic needs.

    Raised before anything is drawn. A posting no command triggered falls back to the textual
    forecast; a commanded one is rejected and nothing is posted (XIV.7).
    """


def _format_key(round_format: str | None) -> str:
    if round_format is None:
        return "NORMAL"
    raw = getattr(round_format, "value", round_format)
    return str(raw).rsplit(".", 1)[-1].upper()


def weather_template_key(phase: int, round_format: str | None) -> str:
    """The template a phase of a round of *round_format* is drawn from.

    A pure function of its two arguments — Constitution XIV.10 (v4.7.0) requires the catalogue
    to name the datum selecting among an aspect's slots, and the selection to be a function of
    that datum alone.

    Three things it deliberately does **not** do (FR-012):

    * it does not count the sessions the round actually holds. That gives the same answer for
      every format the bot can schedule today and the wrong one the day a format is added: the
      format is the datum, and the session count is a consequence of it;
    * it does not read any configuration beyond the one naming the six templates;
    * it does not fall back to the other slot when the selected one is unconfigured or
      invalid. Drawing a sprint round's four sessions on a canvas authored for two is the
      fault XIV.3's sibling test exists to catch, reached by the module's own hand.
    """
    key = _format_key(round_format)
    if key == "MYSTERY":
        return MYSTERY_TEMPLATE_KEY
    table = _SPRINT_KEYS if key == "SPRINT" else _PLAIN_KEYS
    try:
        return table[int(phase)]
    except (KeyError, TypeError, ValueError):
        raise WeatherDataError(
            f"there is no weather template for phase {phase!r}; the module draws phases 1, 2 "
            f"and 3 and the notice of a mystery round"
        ) from None


@dataclass(frozen=True)
class SlotDrawing:
    """One in-game weather slot of one session, in the order it was drawn."""

    ordinal: int
    label: str


@dataclass(frozen=True)
class SessionDrawing:
    """One session of the round, in the order the sessions are run."""

    ordinal: int
    name: str
    weather_type: str | None = None
    summary: str | None = None
    slots: list[SlotDrawing] = field(default_factory=list)

    @property
    def slot_count(self) -> int:
        return len(self.slots)


@dataclass(frozen=True)
class WeatherDrawing:
    """One weather graphic, resolved and ready to project onto a template."""

    template_key: str
    phase: int
    division_name: str
    round_number: str
    phase_description: str | None = None
    track_name: str | None = None
    race_name: str | None = None
    country_name: str | None = None
    track_datum: str | None = None
    rain_probability: str | None = None
    division_tier: str | None = None
    season_number: str | None = None
    sessions: list[SessionDrawing] = field(default_factory=list)

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @property
    def is_mystery(self) -> bool:
        return self.template_key == MYSTERY_TEMPLATE_KEY


# ── 1. Resolution ─────────────────────────────────────────────────────────


def resolve_drawing(
    *,
    phase: int,
    division_name: str,
    round_number: str | int,
    round_format: str | None = None,
    track_name: str | None = None,
    race_name: str | None = None,
    country_name: str | None = None,
    rain_probability: float | None = None,
    sessions: Sequence[Mapping] | None = None,
    division_tier: str | int | None = None,
    season_number: str | int | None = None,
    template_key: str | None = None,
) -> WeatherDrawing:
    """Resolve every value a weather graphic draws.

    *sessions* is what the weather module persisted, one mapping per session in the order the
    sessions are run: ``session_type`` (the enum value), ``slot_type`` (phase 2's draw) and
    ``slots`` (phase 3's sequence). Phase 1 and the mystery notice take none.

    *rain_probability* is the coefficient phase 1 computed and stored, as a fraction. It is
    rendered here by the renderer the phase 1 message uses, and the **phase 2 and phase 3**
    graphics carry that same stored value though neither of their messages does — XIV.7
    (v4.7.0) admitting a value the text path published in another message of the same flow.
    """
    key = template_key or weather_template_key(phase, round_format)

    # A round of the mystery format runs no phase and computes no forecast. Its notice says a
    # forecast is not coming, and carries the heading fields naming who and when — no track,
    # no session, no likelihood (FR-006).
    if key == MYSTERY_TEMPLATE_KEY:
        return WeatherDrawing(
            template_key=key,
            phase=phase,
            division_name=division_name,
            round_number=str(round_number),
            division_tier=None if division_tier is None else str(division_tier),
            season_number=None if season_number is None else str(season_number),
        )

    drawn: list[SessionDrawing] = []
    if phase in (2, 3):
        for ordinal, entry in enumerate(sessions or (), start=1):
            name = session_type_label(str(entry.get("session_type") or ""))
            slot_type = entry.get("slot_type")
            weather_type = (
                format_session_weather_type(slot_type) if slot_type else None
            )
            labels = list(entry.get("slots") or ()) if phase == 3 else []
            drawn.append(
                SessionDrawing(
                    ordinal=ordinal,
                    name=name,
                    weather_type=weather_type,
                    # The sequence as a **value**: the emphasis the phase 3 message applies is
                    # the channel's instruction and no part of what the picture draws (XIV.16).
                    summary=format_slot_sequence(labels) if labels else None,
                    slots=[
                        SlotDrawing(ordinal=i, label=str(label))
                        for i, label in enumerate(labels, start=1)
                    ],
                )
            )

    return WeatherDrawing(
        template_key=key,
        phase=phase,
        division_name=division_name,
        round_number=str(round_number),
        phase_description=PHASE_DESCRIPTIONS.get(phase),
        track_name=track_name or None,
        race_name=race_name or None,
        country_name=country_name or None,
        track_datum=track_name or None,
        rain_probability=(
            None if rain_probability is None else format_rain_probability(rain_probability)
        ),
        division_tier=None if division_tier is None else str(division_tier),
        season_number=None if season_number is None else str(season_number),
        sessions=drawn,
    )


# ── 2. Projection ─────────────────────────────────────────────────────────


def _ids_bearing(declared, stem: str) -> list[str]:
    return sorted(
        name for name in declared if name == stem or name.startswith(f"{stem}_")
    )


def build_fill_spec(
    drawing: WeatherDrawing,
    root,
    *,
    asset_directories: Mapping[str, Path] | None = None,
) -> FillSpec:
    """Project *drawing* onto *root*, deciding what leaves the canvas beside it.

    Raises :class:`WeatherDataError` where the template's sessions or slots cannot be counted
    — a gap in either numbering, or a declaration below the floor its slot requires (XIV.12,
    v4.7.0). Both are structural faults of the file and are refused at every validity moment;
    meeting one here means the template was changed since it was named.
    """
    catalogue = catalogue_for(drawing.template_key)
    declared = FieldIndex(root).declared()

    try:
        capacity = catalogue.capacity(root) or 0
    except CapacityError as exc:
        raise WeatherDataError(str(exc)) from exc

    text: dict[str, str] = {}
    empty: list[str] = []
    empty_quietly: list[str] = []
    remove: list[str] = []
    image_data: dict[str, tuple[str, str]] = {}
    off_canvas: set[str] = set()

    def put(field_id: str, value: str | None) -> None:
        """A value the graphic must carry; emptied quietly where the data hold none."""
        if field_id not in declared:
            return
        if value:
            text[field_id] = value
        else:
            empty_quietly.append(field_id)

    def put_optional(field_id: str, value: str | None) -> None:
        """An optional value, whose chrome leaves with it where a group is declared.

        A template composing a fixed label around the tier, or a separator between the grand
        prix name and the country, declares the group so that neither is left standing empty
        under a label naming what is not there (FR-032, XIV.2).
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
    put("round_number", drawing.round_number)

    if not drawing.is_mystery:
        put("phase_description", drawing.phase_description)
        put("race_name", drawing.race_name)
        put_optional("track_name", drawing.track_name)
        put_optional("country_name", drawing.country_name)
        put_optional("rain_probability", drawing.rain_probability)

    put_optional("season_number", drawing.season_number)
    put_optional("division_tier", drawing.division_tier)

    # The round's **country flag**, resolved from the country by the module's slug rule. A
    # forecast heads a round rather than picturing it, so it draws no circuit map: XIV.13
    # admits a track-class field on the calendar and the check-in graphic alone (044). The
    # mystery notice declares neither.
    if "track_flag" in declared and not drawing.is_mystery:
        if drawing.country_name:
            image_data["track_flag"] = ("flag", drawing.country_name)
        else:
            group_id = "track_flag_group"
            if group_id in declared:
                off_canvas.update(_ids_bearing(declared, group_id))
                remove.append(group_id)
            else:
                remove.append("track_flag")

    drawn = drawing.sessions[:capacity] if capacity else []
    nested = catalogue.rows.nested if catalogue.rows is not None else None

    for session in drawn:
        stem = f"{_SESSION_PREFIX}_{session.ordinal}"
        put(f"{stem}_name", session.name)
        put(f"{stem}_slot_type", session.weather_type)
        put_optional(f"{stem}_summary", session.summary)

        if f"{stem}_slot_type_icon" in declared:
            if session.weather_type:
                image_data[f"{stem}_slot_type_icon"] = ("weather", session.weather_type)
            else:
                remove.append(f"{stem}_slot_type_icon")

        if nested is None:
            continue

        slot_capacity = nested.declared_capacity(stem, declared)
        for slot in session.slots[:slot_capacity]:
            slot_stem = f"{stem}_{_SLOT_PREFIX}_{slot.ordinal}"
            put(f"{slot_stem}_label", slot.label)
            if f"{slot_stem}_icon" in declared:
                image_data[f"{slot_stem}_icon"] = ("weather", slot.label)

        # Slots the template declares beyond those drawn for this session. Each leaves by its
        # group, and no error is reported: the floor is the greatest the slot's formats can
        # demand, so a shorter session reaching it by removal is the ordinary case and not a
        # degradation (FR-038, FR-017).
        for ordinal in range(session.slot_count + 1, slot_capacity + 1):
            slot_stem = f"{stem}_{_SLOT_PREFIX}_{ordinal}"
            group_id = f"{slot_stem}_group"
            off_canvas.update(_ids_bearing(declared, slot_stem))
            if group_id in declared:
                remove.append(group_id)
            else:
                remove.extend(
                    name
                    for name in _ids_bearing(declared, slot_stem)
                    if name not in remove
                )

    # Sessions the template declares beyond the round's own — a plain template drawing a
    # normal round where an endurance round would fill it. Each leaves by its group, taking
    # every field of the session and of its slots with it (FR-036, FR-040).
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
        row_count=drawing.session_count,
        image_data=image_data,
        catalogue=catalogue,
    )
    if asset_directories:
        spec.asset_directories = dict(asset_directories)
    return spec
