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

from dataclasses import dataclass, field

from models.image_constants import TEMPLATE_COLUMNS


@dataclass(frozen=True)
class RowSpec:
    """A repeating collection drawn against a fixed number of slots (XIV.12).

    Expressed as prefix + capacity + field suffixes, never as an enumerated list of
    ``row_1_position, row_2_position, …`` (XIV.11).
    """

    #: Always ``row`` under the current convention. Kept explicit so a template holding
    #: two tables is expressible later without changing the shape of a catalogue.
    prefix: str = "row"

    #: Slots the template provides. Data exceeding this is a problem, not a truncation.
    capacity: int = 0

    #: The per-row field suffixes, from which ``row_<x>_<field>`` is built.
    fields: frozenset[str] = frozenset()

    #: Those of ``fields`` without which a row cannot be drawn.
    mandatory_fields: frozenset[str] = frozenset()

    def row_id(self, index: int) -> str:
        """The id of row *index* — ``row_3``. Indexed from 1, unpadded (XIV.11)."""
        return f"{self.prefix}_{index}"

    def field_id(self, index: int, suffix: str) -> str:
        """The id of one field of one row — ``row_3_points``."""
        return f"{self.prefix}_{index}_{suffix}"

    def group_id(self, index: int) -> str:
        """The id of a row's removable group — ``row_3_group`` (XIV.2)."""
        return f"{self.prefix}_{index}_group"

    def all_field_ids(self) -> set[str]:
        """Every per-row field id across the declared capacity."""
        return {
            self.field_id(index, suffix)
            for index in range(1, self.capacity + 1)
            for suffix in self.fields
        }

    def mandatory_field_ids(self) -> set[str]:
        """Every per-row field id a row cannot be drawn without."""
        return {
            self.field_id(index, suffix)
            for index in range(1, self.capacity + 1)
            for suffix in self.mandatory_fields
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

    def all_mandatory_ids(self) -> set[str]:
        """Mandatory singular fields plus every mandatory per-row field."""
        ids = set(self.mandatory)
        if self.rows is not None:
            ids |= self.rows.mandatory_field_ids()
        return ids

    def all_known_ids(self) -> set[str]:
        """Every field name this type may address, mandatory or optional."""
        ids = set(self.mandatory) | set(self.optional)
        if self.rows is not None:
            ids |= self.rows.all_field_ids()
        return ids

    def capacity(self) -> int | None:
        """The declared row capacity, or None where this type draws no varying list."""
        return self.rows.capacity if self.rows is not None else None


#: Template column → its catalogue. Fifteen entries, one per image type, all empty.
CATALOGUES: dict[str, FieldCatalogue] = {
    column: FieldCatalogue() for column in TEMPLATE_COLUMNS
}


def catalogue_for(template_key: str) -> FieldCatalogue:
    """The catalogue for *template_key*, or an empty one for a type not yet known."""
    return CATALOGUES.get(template_key, FieldCatalogue())


def declared_capacities() -> dict[str, int]:
    """Template key → declared row capacity, for every type that declares one.

    Empty while no image type is specified, which is what makes the division-capacity
    guard (Constitution XIV.12) inert rather than absent.
    """
    return {
        key: catalogue.rows.capacity
        for key, catalogue in CATALOGUES.items()
        if catalogue.rows is not None and catalogue.rows.capacity > 0
    }
