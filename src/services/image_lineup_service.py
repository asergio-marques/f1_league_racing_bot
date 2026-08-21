"""Resolve a division's lineup and project it onto a template (038).

Two steps, deliberately separate, as ``image_calendar_service`` is:

1. :func:`resolve_drawing` turns a division's teams, seats and drivers into a
   :class:`LineupDrawing` — every value decided, nothing drawn. The fatal checks that need
   no template live here, so they fail before the expensive work.
2. :func:`build_fill_spec` projects that drawing onto a parsed template, deciding what is
   filled, what is emptied and what leaves by its group.

The split is what lets the name-resolution chain be tested without a template and the
projection without a division.

**This module holds no Discord and no database.** The display names a guild supplies are
passed in already resolved (see :func:`resolve_drawing`'s ``display_names``), which is what
keeps the resolution unit-testable without a bot.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from models.image_catalogues import RESERVE_KEY, catalogue_for
from utils.asset_resolver import normalise
from utils.country_data import country_for_nationality
from utils.svg_document import FieldIndex
from utils.svg_fill import FillSpec

log = logging.getLogger(__name__)

TEMPLATE_KEY = "lineup_template"

#: The prefix of the ordinal team collection and of the nested seat collection. Read from
#: the catalogue rather than repeated, so the ids the fill builds and the ids validity
#: checks cannot drift apart (Constitution XIV.10).
_TEAM_PREFIX = "team"
_SEAT_PREFIX = "driver"

#: The nationality recorded for a driver who stated none. It resolves an asset by the
#: ordinary slug rule like any other value — it is a *value*, not an absence.
NATIONALITY_OTHER = "Other"


class LineupDataError(Exception):
    """A fatal disagreement between a division and what a lineup needs.

    Raised before anything is drawn. The caller turns it into a Problem, which aborts the
    render and — for an uncommanded posting — falls back to the textual lineup.
    """


@dataclass(frozen=True)
class LineupSeat:
    """One seat of one team, occupied or not."""

    seat_number: int
    driver_name: str = ""
    flag_datum: str | None = None
    portrait_datum: str | None = None
    occupied: bool = False

    #: True where the seat is occupied but the driver records no nationality *and* the
    #: league does collect it. Drives the notice; a league that has switched nationality
    #: collection off raises nothing (Constitution XIV.4, v4.3.0).
    flag_missing: bool = False


@dataclass(frozen=True)
class LineupTeam:
    """One team of the division, with its seats in ascending seat number.

    The **ordinal** is the team's 1-based position in the division's team list, the
    reserve excepted, and is a place in the layout rather than anything about the team
    (XIV.11). It is 0 for the reserve, which is a singleton and occupies no ordinal.
    """

    ordinal: int
    display_name: str
    image_datum: str
    seats: list[LineupSeat] = field(default_factory=list)
    is_reserve: bool = False

    @property
    def occupied_count(self) -> int:
        return sum(1 for seat in self.seats if seat.occupied)

    @property
    def occupied_seats(self) -> list[LineupSeat]:
        return [seat for seat in self.seats if seat.occupied]


@dataclass(frozen=True)
class LineupDrawing:
    """One division's lineup, resolved and ready to project onto a template."""

    division_name: str
    division_tier: str | None = None
    season_number: str | None = None
    teams: list[LineupTeam] = field(default_factory=list)

    #: None where the division fields no reserve driver, which is what removes
    #: ``reserve_group`` in its entirety (FR-004).
    reserve: LineupTeam | None = None

    #: False where the league has switched nationality collection off at its source. A
    #: lineup with no flags at all is then exactly what was configured, and raises nothing.
    nationality_collected: bool = True


# ── 1. Resolution ─────────────────────────────────────────────────────────


def resolve_driver_name(
    *,
    discord_user_id: str | int | None,
    display_name: str | None = None,
    signup_display_name: str | None = None,
    signup_username: str | None = None,
    test_display_name: str | None = None,
) -> str:
    """The first of the five links that yields a non-empty value (FR-005).

    An image cannot carry a Discord mention as the textual lineup does, so the chain ends
    at the user id rather than at nothing: every driver is named, always.
    """
    for candidate in (
        display_name,
        signup_display_name,
        signup_username,
        test_display_name,
        None if discord_user_id is None else str(discord_user_id),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return ""


def _seat_of(
    entry,
    *,
    display_names: Mapping[str, str],
    nationality_collected: bool,
) -> LineupSeat:
    """One seat, from a record carrying ``seat_number`` and possibly a driver."""
    seat_number = int(entry.seat_number)
    user_id = getattr(entry, "discord_user_id", None)

    if user_id is None:
        # Configured but unoccupied: the name is emptied and the images removed, rather
        # than the seat being omitted as the textual lineup omits it (FR-008).
        return LineupSeat(seat_number=seat_number, occupied=False)

    key = str(user_id)
    name = resolve_driver_name(
        discord_user_id=key,
        display_name=display_names.get(key),
        signup_display_name=getattr(entry, "server_display_name", None),
        signup_username=getattr(entry, "discord_username", None),
        test_display_name=getattr(entry, "test_display_name", None),
    )

    nationality = getattr(entry, "nationality", None)
    has_nationality = bool(nationality and str(nationality).strip())

    return LineupSeat(
        seat_number=seat_number,
        driver_name=name,
        # Resolved from the *datum*; the file is found, fallen back on, or fatal, by the
        # ordinary rule of Constitution XIV.13. The datum is the driver's **country**,
        # not their nationality: one flag directory serves a driver and a round alike.
        flag_datum=country_for_nationality(nationality),
        portrait_datum=key,
        occupied=True,
        flag_missing=not has_nationality and nationality_collected,
    )


def resolve_drawing(
    *,
    division_name: str,
    division_tier: str | int | None = None,
    season_number: str | int | None = None,
    teams: Sequence,
    display_names: Mapping[str, str] | None = None,
    nationality_collected: bool = True,
) -> LineupDrawing:
    """Resolve every value a lineup draws, or raise :class:`LineupDataError`.

    *teams* are records carrying ``name``, ``is_reserve`` and ``seats``; each seat carries
    ``seat_number`` and, where occupied, ``discord_user_id`` plus the signup fields the
    name chain reads.

    *display_names* maps a Discord user id to that account's display name **on the server
    at the moment of generation** — the first link of the chain, and the only one this
    module cannot resolve for itself (research R9).
    """
    names = dict(display_names or {})
    resolved: list[LineupTeam] = []
    reserve: LineupTeam | None = None

    # The ordinal is the team's position in the division's list, counted over the
    # non-reserve teams alone. Three checks stood here until v6.0.0 — a name normalising
    # to nothing, to `reserve`, or to the same word as another team — because the
    # normalised name *was* the template field. It names a file now and nothing else, so a
    # bad name can no longer collide with a field. The names are constrained at the command
    # that sets them and reported at `season review` (Principle IX); a render is not the
    # place to discover it, and refusing the graphic would punish the wrong moment.
    for record in teams:
        display = (getattr(record, "name", "") or "").strip()
        is_reserve = bool(getattr(record, "is_reserve", False))

        seats = sorted(
            (
                _seat_of(
                    entry,
                    display_names=names,
                    nationality_collected=nationality_collected,
                )
                for entry in getattr(record, "seats", [])
            ),
            key=lambda seat: seat.seat_number,
        )

        team = LineupTeam(
            ordinal=0 if is_reserve else len(resolved) + 1,
            display_name=display,
            image_datum=display,
            seats=seats,
            is_reserve=is_reserve,
        )
        if is_reserve:
            # A reserve team fielding nobody takes the whole block off the graphic.
            reserve = team if team.occupied_count else None
        else:
            resolved.append(team)

    return LineupDrawing(
        division_name=division_name,
        division_tier=None if division_tier is None else str(division_tier),
        season_number=None if season_number is None else str(season_number),
        teams=resolved,
        reserve=reserve,
        nationality_collected=nationality_collected,
    )


# ── 2. Projection onto a template ─────────────────────────────────────────


def build_fill_spec(
    drawing: LineupDrawing,
    root,
    *,
    asset_directories: Mapping[str, Path] | None = None,
) -> FillSpec:
    """Project *drawing* onto *root*, deciding what is filled, emptied and removed.

    Raises :class:`LineupDataError` on each of the three overflows this graphic can meet:
    teams beyond the template's blocks, drivers beyond a block's seat slots, and reserve
    drivers beyond the reserve block. None is routed through ``row_count`` as the
    calendar's rounds are: the generic message speaks of "rows" and "slots", and XIV.9.2
    requires a fault to name *what* is at fault — here the teams or the drivers that would
    be dropped. ``row_count`` is still set, as a backstop behind the named checks.
    """
    catalogue = catalogue_for(TEMPLATE_KEY)
    index = FieldIndex(root)
    declared = index.declared()

    text: dict[str, str] = {}
    empty: list[str] = []
    empty_quietly: list[str] = []
    remove: list[str] = []
    image_data: dict[str, tuple[str, str]] = {}

    def put(field_id: str, value: str) -> None:
        """Fill where declared; empty rather than dash where the value does not apply."""
        if field_id not in declared:
            return
        if value:
            text[field_id] = value
        else:
            empty.append(field_id)

    def put_seat_name(field_id: str, value: str) -> None:
        """A seat's name. An unoccupied seat is *determined* to be empty, not undetermined.

        The template's layout is fixed, so the seat is drawn with its name cleared rather
        than omitted as the textual lineup omits it (FR-008). Nothing has gone wrong, so
        no notice is raised and the mandatory classification is not offended: XIV.3 makes
        a mandatory field fatal when its value cannot be *determined*, and this one was.
        """
        if field_id not in declared:
            return
        if value:
            text[field_id] = value
        else:
            empty_quietly.append(field_id)

    def put_asset(field_id: str, asset_class: str, datum: str | None) -> None:
        if field_id not in declared:
            return
        if datum:
            image_data[field_id] = (asset_class, datum)
        else:
            # No datum means no asset is sought at all — Rule 13's "absent datum", which
            # is governed by the field's classification and not by asset resolution.
            remove.append(field_id)

    put("division_name", drawing.division_name)
    put("season_number", drawing.season_number or "")
    put("division_tier", drawing.division_tier or "")

    # ── The team blocks, addressed by ordinal ────────────────────────────
    #
    # The block count is the template's (XIV.12): a division fielding fewer teams than the
    # file declares draws the surplus blocks away in silence, and one fielding more is
    # fatal. The overflow is reported through `row_count` below, which names the teams.
    blocks = catalogue.capacity(root) or 0
    seats_nest = catalogue.rows.nested if catalogue.rows is not None else None

    if len(drawing.teams) > blocks:
        dropped = ", ".join(
            f"`{team.display_name}`" for team in drawing.teams[blocks:][:8]
        )
        raise LineupDataError(
            f"the division fields {len(drawing.teams)} teams but the template declares "
            f"{blocks} team {'block' if blocks == 1 else 'blocks'}. {dropped} would be "
            f"dropped. Enlarge the template."
        )

    def remove_block(ordinal: int) -> None:
        """Take an ordinal the division fields no team at off the graphic (FR-013)."""
        stem = f"{_TEAM_PREFIX}_{ordinal}"
        group_id = f"{stem}_group"
        if group_id in declared:
            remove.append(group_id)
        else:
            remove.extend(
                name
                for name in declared
                if name == stem or name.startswith(f"{stem}_")
            )

    by_ordinal = {team.ordinal: team for team in drawing.teams}

    for ordinal in range(1, blocks + 1):
        team = by_ordinal.get(ordinal)
        if team is None:
            remove_block(ordinal)
            continue

        stem = f"{_TEAM_PREFIX}_{ordinal}"
        put(f"{stem}_name", team.display_name)
        put_asset(f"{stem}_image", "team", team.image_datum)

        slots = 0
        if seats_nest is not None:
            slots = seats_nest.declared_capacity(stem, declared)

        # The seats are a **ceiling** (XIV.12, 047 FR-018), exactly as the constructors
        # grid's cars are. A slot beyond what the team is configured with is removed and
        # nothing is reported; a slot within them that nobody occupies is drawn empty, a
        # vacancy being worth showing. Only a *driver* who would be dropped is fatal.
        occupied = team.occupied_seats
        if len(occupied) > slots:
            dropped = ", ".join(f"`{seat.driver_name}`" for seat in occupied[slots:][:8])
            raise LineupDataError(
                f"`{team.display_name}` seats {len(occupied)} drivers but the template "
                f"declares {slots} seat {'slot' if slots == 1 else 'slots'} at block "
                f"{ordinal}. {dropped} would be dropped. Enlarge the template."
            )

        drawn = list(team.seats)
        # Shed configured-but-empty seats, latest first, until the block can hold the rest.
        while len(drawn) > slots:
            for index in range(len(drawn) - 1, -1, -1):
                if not drawn[index].occupied:
                    del drawn[index]
                    break
            else:  # pragma: no cover — guarded by the overflow check above
                break

        for position in range(1, slots + 1):
            seat = drawn[position - 1] if position <= len(drawn) else None
            base = f"{stem}_{_SEAT_PREFIX}_{position}"
            if seat is None:
                # Declared beyond the team's configured seats: removed, not drawn empty.
                remove.extend(
                    name
                    for name in declared
                    if name == base or name.startswith(f"{base}_")
                )
                continue
            put_seat_name(f"{base}_name", seat.driver_name)
            put_asset(f"{base}_flag", "flag", seat.flag_datum)
            put_asset(f"{base}_image", "driver", seat.portrait_datum)

    # The reserve block. Its slots are fixed by the template, so this is the one lineup
    # collection to which overflow applies.
    reserve_slots = catalogue.singleton_capacity(root) or 0
    if drawing.reserve is None:
        # Removed in its entirety, taking every other `reserve_` field with it (FR-004).
        # Where the template declares no group, each field goes one by one.
        group_id = f"{RESERVE_KEY}_group"
        if group_id in declared:
            remove.append(group_id)
        else:
            remove.extend(
                name
                for name in declared
                if name == RESERVE_KEY or name.startswith(f"{RESERVE_KEY}_")
            )
    else:
        occupied = [seat for seat in drawing.reserve.seats if seat.occupied]
        if len(occupied) > reserve_slots:
            dropped = ", ".join(
                f"`{seat.driver_name}`" for seat in occupied[reserve_slots:][:8]
            )
            raise LineupDataError(
                f"the division fields {len(occupied)} reserve drivers but the template "
                f"declares {reserve_slots} reserve "
                f"{'slot' if reserve_slots == 1 else 'slots'}. "
                f"{dropped} would be dropped. Enlarge the template."
            )

        put(f"{RESERVE_KEY}_name", drawing.reserve.display_name)
        put_asset(f"{RESERVE_KEY}_image", "team", drawing.reserve.image_datum)
        # Slots declared beyond the division's reserve drivers are treated exactly as an
        # unoccupied seat is: the name emptied, the images removed.
        padded = list(occupied) + [
            LineupSeat(seat_number=index_)
            for index_ in range(len(occupied) + 1, reserve_slots + 1)
        ]
        for position, seat in enumerate(padded, start=1):
            base = f"{RESERVE_KEY}_{_SEAT_PREFIX}_{position}"
            put_seat_name(f"{base}_name", seat.driver_name)
            put_asset(f"{base}_flag", "flag", seat.flag_datum)
            put_asset(f"{base}_image", "driver", seat.portrait_datum)

    spec = FillSpec(
        root=root,
        image_type=TEMPLATE_KEY,
        text=text,
        empty=empty,
        empty_quietly=empty_quietly,
        remove=remove,
        image_data=image_data,
        catalogue=catalogue,
    )
    # The division's team count, measured against the block count by the generic capacity
    # guard in `image_render_service` — the same route a calendar's rounds take.
    spec.row_count = len(drawing.teams)
    if asset_directories:
        spec.asset_directories = dict(asset_directories)
    return spec


# ── 3. Verification against a division, and against a stand-in ────────────


def suppressed_flag_fields(drawing: LineupDrawing) -> set[str]:
    """Flag fields removed for an absence the league configured (FR-009).

    Where nationality collection is switched off at its source, the removal is exactly
    what the league asked for and raises no notice — reporting a setting back to the
    person who chose it, once per driver, on every render, would bury the notices that
    mean something (Constitution XIV.4, v4.3.0).
    """
    if drawing.nationality_collected:
        return set()

    fields: set[str] = set()
    for team in drawing.teams:
        # Positional, as the fill draws them. A block smaller than the team's configured
        # seats sheds the *unoccupied* ones, so an occupied seat can sit at a lower
        # position than its seat number — the enumeration below, and never the number.
        for position, seat in enumerate(team.seats, start=1):
            if seat.occupied and seat.flag_datum is None:
                fields.add(
                    f"{_TEAM_PREFIX}_{team.ordinal}_{_SEAT_PREFIX}_{position}_flag"
                )
    if drawing.reserve is not None:
        occupied = [seat for seat in drawing.reserve.seats if seat.occupied]
        for position, seat in enumerate(occupied, start=1):
            if seat.flag_datum is None:
                fields.add(f"{RESERVE_KEY}_{_SEAT_PREFIX}_{position}_flag")
    return fields
