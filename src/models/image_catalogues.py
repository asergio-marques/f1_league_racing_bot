"""Field catalogues — one per image type (Constitution XIV.10).

A catalogue is the authoritative list of the field names an image type's render addresses,
split by the operation each receives and classified **mandatory** or **optional**
(XIV.3). It is a code constant, not configuration: it describes what an image type *is*,
and a league cannot vary it.

**Every catalogue here is empty.** No image type has a generation specification yet, so
there is nothing to declare. That is deliberate, and it is what the rest of the module is
built to tolerate:

* the mandatory-field check at all three verification moments passes vacuously;
* validity Layer 2 *skips* rather than passes, so a template is never reported as checked
  more deeply than it was (XIV.9, invariants 3 and 4);
* the division-capacity guard finds no capacity and admits every division.

Populating one entry is the whole of what a later image-type session must do to bring all
four to life for that type. Nothing else changes — no cog, no command signature, no report
renderer (XIV.10).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from models.image_constants import TEMPLATE_COLUMNS


class CapacityError(Exception):
    """A template's repeating collection cannot be counted.

    Raised for the two shapes XIV.11 forbids: no member at all, and a gap in the
    numbering. Both are fatal wherever they are met — at the moment a template is named,
    at season review, and before a render — so the same exception serves all three.
    """


@dataclass(frozen=True)
class RowSpec:
    """A repeating collection drawn against a number of slots (XIV.12).

    Expressed as prefix + capacity + field suffixes, never as an enumerated list of
    ``row_1_position, row_2_position, …`` (XIV.11).

    **Capacity has two sources.** Where the slot count is a property of the image type,
    it is the integer declared here. Where it is a property of the *template* — the
    calendar, whose rounds a league draws to suit its own season — ``capacity`` is None
    and the count is read from the file being checked or filled. Either way XIV.12 is
    satisfied: the capacity is declared and matched, the declaration simply says *how to
    count* rather than *what the number is*.
    """

    #: ``row`` for a table's rows, ``round`` for a calendar's rounds, ``session`` for a
    #: forecast's sessions. Named for the thing it repeats (XIV.11).
    prefix: str = "row"

    #: Slots the template provides, or None to count what the template declares. Data
    #: exceeding it is a problem, not a truncation.
    capacity: int | None = 0

    #: The per-row field suffixes, from which ``<prefix>_<x>_<field>`` is built.
    fields: frozenset[str] = frozenset()

    #: Those of ``fields`` without which a row cannot be drawn.
    mandatory_fields: frozenset[str] = frozenset()

    #: Those of ``fields`` the template must declare but which receive **no value** —
    #: geometry the render reads rather than text it writes. A calendar's
    #: ``vertical_crop_point`` is one: XIV.3 makes a mandatory field fatal when absent
    #: *or* when its value cannot be determined, and the second half cannot apply to a
    #: field that never carries a value. Without this distinction every crop point would
    #: be reported undeterminable on every render.
    valueless_fields: frozenset[str] = frozenset()

    #: Per-member field suffix -> asset class, naming the configured directory it
    #: resolves in (XIV.13). ``{"image": "track"}`` for a calendar's round image.
    assets: dict[str, str] = field(default_factory=dict)

    @property
    def is_derived(self) -> bool:
        """True where the slot count comes from the template rather than from here."""
        return self.capacity is None

    def row_id(self, index: int) -> str:
        """The id of row *index* — ``row_3``. Indexed from 1, unpadded (XIV.11)."""
        return f"{self.prefix}_{index}"

    def field_id(self, index: int, suffix: str) -> str:
        """The id of one field of one row — ``row_3_points``."""
        return f"{self.prefix}_{index}_{suffix}"

    def group_id(self, index: int) -> str:
        """The id of a row's removable group — ``row_3_group`` (XIV.2)."""
        return f"{self.prefix}_{index}_group"

    def declared_capacity(self, root) -> int:
        """Count the members *root* declares, requiring contiguity from 1.

        Reads through :class:`FieldIndex`, so a member addressed by a layer label counts
        exactly as one addressed by an ``@id`` (XIV.2) — a league manager sets the label
        and never sees the identifier their editor generated.
        """
        from utils.svg_document import FieldIndex

        pattern = re.compile(rf"^{re.escape(self.prefix)}_(\d+)(?:_.*)?$")
        ordinals: set[int] = set()
        for name in FieldIndex(root).declared():
            match = pattern.match(name)
            if match is not None:
                ordinals.add(int(match.group(1)))

        if not ordinals:
            raise CapacityError(
                f"the template declares no `{self.prefix}` at all — it must declare at "
                f"least one, numbered from 1"
            )

        highest = max(ordinals)
        missing = sorted(set(range(1, highest + 1)) - ordinals)
        if missing:
            shown = ", ".join(str(n) for n in missing[:8])
            if len(missing) > 8:
                shown += f", and {len(missing) - 8} more"
            raise CapacityError(
                f"the numbering of `{self.prefix}` has a gap: it runs to {highest} but "
                f"declares no {self.prefix} {shown}. Numbering must be contiguous from 1."
            )
        return highest

    def capacity_for(self, root=None) -> int | None:
        """The slot count: the fixed one, or what *root* declares. None if unknowable."""
        if not self.is_derived:
            return self.capacity
        if root is None:
            return None
        return self.declared_capacity(root)

    def all_field_ids(self, root=None) -> set[str]:
        """Every per-row field id across the capacity."""
        capacity = self.capacity_for(root) or 0
        return {
            self.field_id(index, suffix)
            for index in range(1, capacity + 1)
            for suffix in self.fields
        }

    def mandatory_field_ids(self, root=None) -> set[str]:
        """Every per-row field id a row cannot be drawn without."""
        capacity = self.capacity_for(root) or 0
        return {
            self.field_id(index, suffix)
            for index in range(1, capacity + 1)
            for suffix in self.mandatory_fields
        }

    def valueless_field_ids(self, root=None) -> set[str]:
        """Every per-row field id that must be present but receives no value."""
        capacity = self.capacity_for(root) or 0
        return {
            self.field_id(index, suffix)
            for index in range(1, capacity + 1)
            for suffix in self.valueless_fields
        }


@dataclass(frozen=True)
class FieldCatalogue:
    """What one image type addresses, and how each field is classified."""

    #: Absent from the template, or undeterminable at generation → a problem.
    mandatory: frozenset[str] = frozenset()

    #: Absent → fine. Undeterminable → emptied, or its ``_group`` removed.
    optional: frozenset[str] = frozenset()

    #: Image field name → asset class, naming the configured directory it resolves in.
    assets: dict[str, str] = field(default_factory=dict)

    #: The repeating collection this type draws, if any.
    rows: RowSpec | None = None

    @property
    def is_empty(self) -> bool:
        """True while this type has no generation specification.

        Layer 2 asks this to decide whether it applies at all. An empty catalogue must be
        *skipped*, never passed: passing would report a depth nothing was checked to.
        """
        return not self.mandatory and not self.optional and self.rows is None

    def all_mandatory_ids(self, root=None) -> set[str]:
        """Mandatory singular fields plus every mandatory per-row field.

        *root* is needed only where the row capacity is derived from the template; every
        caller working against a fixed capacity may omit it, as they did before.
        """
        ids = set(self.mandatory)
        if self.rows is not None:
            ids |= self.rows.mandatory_field_ids(root)
        return ids

    def all_known_ids(self, root=None) -> set[str]:
        """Every field name this type may address, mandatory or optional."""
        ids = set(self.mandatory) | set(self.optional)
        if self.rows is not None:
            ids |= self.rows.all_field_ids(root)
        return ids

    def capacity(self, root=None) -> int | None:
        """The row capacity, or None where this type draws no varying list.

        Also None where the capacity is derived and no *root* is supplied — the count is
        unknown rather than zero, and a caller that cannot supply the template must not
        read "no capacity declared" as "capacity nought".
        """
        return self.rows.capacity_for(root) if self.rows is not None else None

    def valueless_ids(self, root=None) -> set[str]:
        """Field names the template must declare but the render never fills."""
        return self.rows.valueless_field_ids(root) if self.rows is not None else set()

    def asset_class_for(self, field_id: str) -> str | None:
        """The asset class of *field_id*, whole-graphic field or per-member alike."""
        direct = self.assets.get(field_id)
        if direct is not None:
            return direct
        if self.rows is None or not self.rows.assets:
            return None
        match = re.match(rf"^{re.escape(self.rows.prefix)}_\d+_(.*)$", field_id)
        if match is None:
            return None
        return self.rows.assets.get(match.group(1))


#: The calendar's catalogue — the first image type to be specified.
#:
#: Its rounds are a **template-derived** collection: a league draws as many as its season
#: runs, so the capacity is counted from the file rather than declared here (XIV.12, and
#: see specs/037-calendar-image-generation/contracts/calendar-catalogue.md).
#:
#: The three mystery-round literals are not in this catalogue: "Mystery GP" and the rest
#: are *values*, and a catalogue classifies fields. They live with the resolution, in
#: ``services/image_calendar_service.py``.
CALENDAR_CATALOGUE = FieldCatalogue(
    mandatory=frozenset({"division_name"}),
    optional=frozenset({"season_number", "division_tier"}),
    rows=RowSpec(
        prefix="round",
        capacity=None,
        fields=frozenset(
            {
                "group",
                "image",
                "number",
                "country_name",
                "race_name",
                "track_name",
                "format",
                "date",
                "time",
                "vertical_crop_point",
            }
        ),
        mandatory_fields=frozenset(
            {"number", "country_name", "race_name", "date", "vertical_crop_point"}
        ),
        valueless_fields=frozenset({"vertical_crop_point"}),
        assets={"image": "track"},
    ),
)


#: Template column → its catalogue. Fifteen entries, one per image type; the calendar is
#: populated and the remaining fourteen are still empty.
CATALOGUES: dict[str, FieldCatalogue] = {
    column: FieldCatalogue() for column in TEMPLATE_COLUMNS
}
CATALOGUES["calendar_template"] = CALENDAR_CATALOGUE


def catalogue_for(template_key: str) -> FieldCatalogue:
    """The catalogue for *template_key*, or an empty one for a type not yet known."""
    return CATALOGUES.get(template_key, FieldCatalogue())


def declared_capacities() -> dict[str, int]:
    """Template key → **fixed** row capacity, for every type that declares one in code.

    A template-derived capacity is deliberately excluded. This feeds
    ``placement_service._guard_image_capacity``, which counts seated **drivers**; a
    calendar's collection is **rounds**, so including it would refuse a driver placement
    because a calendar template was small, and would still miss the round-add that
    actually overflows it. Rounds are guarded on their own command instead
    (see ``round_capacity_problem`` below).
    """
    return {
        key: catalogue.rows.capacity
        for key, catalogue in CATALOGUES.items()
        if catalogue.rows is not None
        and catalogue.rows.capacity is not None
        and catalogue.rows.capacity > 0
    }
