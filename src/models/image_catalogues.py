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
from typing import Iterable, Mapping

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
class NestedSpec:
    """A collection inside a member of another, discriminated by an ordinal (XIV.11).

    ``team_<x>_driver_<y>_name`` is one: the seats of a team. The parent supplies the
    stem — ``team_red_bull`` or ``reserve`` — and this supplies ``_driver_<y>_<field>``.

    **Capacity has two sources**, as :class:`RowSpec`'s has. The seats of a team are fixed
    by the *data* (a team's configured seat count); the seats of a reserve team are fixed
    by the *template*, no configuration bounding them. ``capacity`` is None either way and
    the parent decides which reading applies, because only the parent knows whether a
    binding can supply the number.
    """

    #: ``driver`` for the seats of a team.
    prefix: str = "driver"

    #: Fixed slot count, or None to take it from the data or the template.
    capacity: int | None = None

    #: Per-member field suffixes.
    fields: frozenset[str] = frozenset()

    #: Those of ``fields`` without which a member cannot be drawn.
    mandatory_fields: frozenset[str] = frozenset()

    #: Where True, ``mandatory_fields`` binds member **1 only** and every member beyond it
    #: is optional (XIV.3, v4.3.0 — a classification varying by member, declared by a rule
    #: rather than an enumeration). The reserve block is the case: the template must
    #: declare the block at all without being obliged to declare a fixed number of it.
    first_member_mandatory_only: bool = False

    #: Field suffix → asset class.
    assets: dict[str, str] = field(default_factory=dict)

    def member_id(self, stem: str, index: int) -> str:
        """``team_red_bull_driver_2`` — the member itself."""
        return f"{stem}_{self.prefix}_{index}"

    def field_id(self, stem: str, index: int, suffix: str) -> str:
        """``team_red_bull_driver_2_name`` — one field of one member."""
        return f"{stem}_{self.prefix}_{index}_{suffix}"

    def group_id(self, stem: str, index: int) -> str:
        """``team_red_bull_driver_2_group`` (XIV.2)."""
        return f"{stem}_{self.prefix}_{index}_group"

    def mandatory_ids(self, stem: str, count: int) -> set[str]:
        """Every mandatory field id across *count* members of this nest."""
        ids: set[str] = set()
        for index in range(1, count + 1):
            if self.first_member_mandatory_only and index > 1:
                continue
            ids.update(self.field_id(stem, index, suffix) for suffix in self.mandatory_fields)
        return ids

    def all_ids(self, stem: str, count: int) -> set[str]:
        """Every field id, mandatory or optional, across *count* members."""
        return {
            self.field_id(stem, index, suffix)
            for index in range(1, count + 1)
            for suffix in self.fields
        }

    def declared_capacity(self, stem: str, declared: Iterable[str]) -> int:
        """Count the members *declared* holds under *stem*, requiring contiguity from 1.

        Used for the reserve block, whose slot count the template alone fixes. A gap is a
        fault of the template (XIV.11) and is fatal wherever it is met, so the same
        :class:`CapacityError` serves all three verification moments.
        """
        pattern = re.compile(
            rf"^{re.escape(stem)}_{re.escape(self.prefix)}_(\d+)(?:_.*)?$"
        )
        ordinals = {
            int(match.group(1))
            for match in (pattern.match(name) for name in declared)
            if match is not None
        }
        if not ordinals:
            return 0

        highest = max(ordinals)
        missing = sorted(set(range(1, highest + 1)) - ordinals)
        if missing:
            shown = ", ".join(str(n) for n in missing[:8])
            if len(missing) > 8:
                shown += f", and {len(missing) - 8} more"
            raise CapacityError(
                f"the numbering of `{stem}_{self.prefix}` has a gap: it runs to {highest} "
                f"but declares no {self.prefix} {shown}. Numbering must be contiguous "
                f"from 1."
            )
        return highest


@dataclass(frozen=True)
class KeyedSpec:
    """A collection whose members are discriminated by a **key** (XIV.11, v4.3.0).

    The lineup's teams are the first of these: ``team_red_bull_name``, not
    ``team_1_name``. A key exists so that a member may be hand-designed *as itself* — a
    team's block carries that team's own livery, which an ordinal cannot address because
    it does not say which team it is. The cost is that such a template is authored against
    one league's data, and XIV.11 forbids choosing a key where an ordinal would serve.

    The capacity is fixed **by the data** (XIV.12): the division decides the member set,
    and the template must declare exactly those members — a divergence in *either*
    direction is fatal, both sides being declared and knowable.
    """

    #: ``team``. Named for the thing it repeats.
    prefix: str = "team"

    fields: frozenset[str] = frozenset()
    mandatory_fields: frozenset[str] = frozenset()

    #: Field suffix → asset class.
    assets: dict[str, str] = field(default_factory=dict)

    #: The collection nested inside each member — a team's seats.
    nested: NestedSpec | None = None

    #: True where the division fixes the member set rather than the template.
    capacity_from_data: bool = True

    def member_id(self, key: str) -> str:
        """``team_red_bull``."""
        return f"{self.prefix}_{key}"

    def field_id(self, key: str, suffix: str) -> str:
        """``team_red_bull_name``."""
        return f"{self.prefix}_{key}_{suffix}"

    def group_id(self, key: str) -> str:
        """``team_red_bull_group`` (XIV.2)."""
        return f"{self.prefix}_{key}_group"

    def declared_keys(self, declared: Iterable[str], known: Iterable[str] = ()) -> set[str]:
        """The keys the template declares, read back out of its ids.

        A key may itself hold underscores, so reading one back is ambiguous in principle:
        ``team_red_bull_name`` is team ``red_bull`` with suffix ``name``, but a team
        actually named "Red Bull Name" would declare ``team_red_bull_name_name``. The
        ambiguity is resolved by matching *known* keys first — the binding supplies them
        — and only parsing what is left over, which is exactly the divergent set this
        method exists to find.
        """
        names = set(declared)
        found: set[str] = set()

        for key in known:
            stem = f"{self.prefix}_{key}_"
            if any(name.startswith(stem) for name in names):
                found.add(key)
                names = {name for name in names if not name.startswith(stem)}

        suffixes = "|".join(sorted(map(re.escape, self.fields))) or "name"
        simple = re.compile(rf"^{re.escape(self.prefix)}_(.+?)_(?:{suffixes})$")
        nested = (
            re.compile(
                rf"^{re.escape(self.prefix)}_(.+?)_{re.escape(self.nested.prefix)}_\d+_.*$"
            )
            if self.nested is not None
            else None
        )

        for name in names:
            match = nested.match(name) if nested is not None else None
            if match is None:
                match = simple.match(name)
            if match is not None:
                found.add(match.group(1))
        return found


@dataclass(frozen=True)
class SingletonSpec:
    """A collection of exactly one member, named, bearing no discriminator (XIV.11).

    The lineup's reserve block. Its name is **reserved**: no keyed member of a sibling
    collection may normalise to it, which is why ``reserve`` is refused as a team name at
    the command that would set it (Principle IX).

    Its ``_group`` is **mandatory** — the first mandatory group in the module (XIV.2,
    v4.3.0). Every division holds a reserve team, so a template omitting the block would
    always omit a team the division fields; but many divisions field no reserve driver, so
    the block leaves whole when there is nothing to put in it. Declaring it is obligatory;
    drawing it is not.
    """

    #: ``reserve``.
    name: str = "reserve"

    fields: frozenset[str] = frozenset()
    mandatory_fields: frozenset[str] = frozenset()

    #: Field suffix → asset class.
    assets: dict[str, str] = field(default_factory=dict)

    #: The reserve seats. Their count is fixed by the **template**: a division's reserve
    #: population varies over a season and cannot be known when the template is drawn.
    nested: NestedSpec | None = None

    def field_id(self, suffix: str) -> str:
        """``reserve_name``."""
        return f"{self.name}_{suffix}"

    def group_id(self) -> str:
        """``reserve_group``."""
        return f"{self.name}_group"

    def declared_capacity(self, declared: Iterable[str]) -> int:
        """The reserve slot count the template declares, contiguous from 1."""
        if self.nested is None:
            return 0
        return self.nested.declared_capacity(self.name, declared)


@dataclass(frozen=True)
class LineupBinding:
    """The division's shape, which is what makes a keyed catalogue answerable.

    **Absence is meaningful.** ``binding=None`` means *no division is in view* — the
    moment a template is named — and the catalogue answers with its team-independent ids
    only. That is the correct answer for that moment, not a degraded one. An **empty**
    binding means a division fielding no team at all, which is a different thing.
    """

    #: Normalised team names, in the order the division holds them. Excludes the reserve
    #: team, which is a singleton and never addressed through ``team_<x>_`` fields.
    team_keys: tuple[str, ...] = ()

    #: Key → the seat count configured for that team.
    seats: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(set(self.team_keys)) != len(self.team_keys):
            raise ValueError(
                "two teams of the division normalise to the same key; a binding cannot "
                "be built from them"
            )
        if RESERVE_KEY in self.team_keys:
            raise ValueError(
                f"`{RESERVE_KEY}` is reserved for the reserve team and may not be a team key"
            )
        unknown = set(self.seats) - set(self.team_keys)
        if unknown:
            raise ValueError(
                f"seat counts given for teams not in the binding: {sorted(unknown)}"
            )

    def seats_for(self, key: str) -> int:
        return self.seats.get(key, 0)


#: The singleton name the reserve block owns, and the team key no league may claim.
RESERVE_KEY = "reserve"


@dataclass(frozen=True)
class FieldCatalogue:
    """What one image type addresses, and how each field is classified."""

    #: Absent from the template, or undeterminable at generation → a problem.
    mandatory: frozenset[str] = frozenset()

    #: Absent → fine. Undeterminable → emptied, or its ``_group`` removed.
    optional: frozenset[str] = frozenset()

    #: Image field name → asset class, naming the configured directory it resolves in.
    assets: dict[str, str] = field(default_factory=dict)

    #: The repeating collection this type draws, if any — ordinal-discriminated.
    rows: RowSpec | None = None

    #: The key-discriminated collection this type draws, if any (XIV.11, v4.3.0).
    keyed: KeyedSpec | None = None

    #: The single named member this type draws, if any (XIV.11, v4.3.0).
    singleton: SingletonSpec | None = None

    @property
    def is_empty(self) -> bool:
        """True while this type has no generation specification.

        Layer 2 asks this to decide whether it applies at all. An empty catalogue must be
        *skipped*, never passed: passing would report a depth nothing was checked to.
        """
        return (
            not self.mandatory
            and not self.optional
            and self.rows is None
            and self.keyed is None
            and self.singleton is None
        )

    @staticmethod
    def _declared(root) -> set[str]:
        from utils.svg_document import FieldIndex

        return set(FieldIndex(root).declared())

    def all_mandatory_ids(self, root=None, binding=None) -> set[str]:
        """Mandatory singular fields, plus every mandatory per-member field.

        *root* is needed where a capacity is counted from the template. *binding* is
        needed where a capacity is fixed by the data (XIV.12) — the lineup's teams and
        seats. **With no binding the keyed collection contributes nothing**, which is the
        correct answer at the two moments that hold no division, and is what
        ``/images template <kind>`` and validity Layer 2 both read.
        """
        ids = set(self.mandatory)
        if self.rows is not None:
            ids |= self.rows.mandatory_field_ids(root)

        if self.keyed is not None and binding is not None:
            for key in binding.team_keys:
                stem = self.keyed.member_id(key)
                ids.update(
                    self.keyed.field_id(key, suffix)
                    for suffix in self.keyed.mandatory_fields
                )
                if self.keyed.nested is not None:
                    ids |= self.keyed.nested.mandatory_ids(stem, binding.seats_for(key))

        if self.singleton is not None:
            ids.update(
                self.singleton.field_id(suffix)
                for suffix in self.singleton.mandatory_fields
            )
            # The reserve block's own seats are counted from the template, so they are
            # checkable with no division in view — which is what makes the singleton the
            # part of a lineup that a naming command can reject on (research R4).
            if self.singleton.nested is not None and root is not None:
                count = self.singleton.declared_capacity(self._declared(root))
                ids |= self.singleton.nested.mandatory_ids(
                    self.singleton.name, max(count, 1)
                )

        return ids

    def all_known_ids(self, root=None, binding=None) -> set[str]:
        """Every field name this type may address, mandatory or optional."""
        ids = set(self.mandatory) | set(self.optional)
        if self.rows is not None:
            ids |= self.rows.all_field_ids(root)

        if self.keyed is not None and binding is not None:
            for key in binding.team_keys:
                stem = self.keyed.member_id(key)
                ids.update(
                    self.keyed.field_id(key, suffix) for suffix in self.keyed.fields
                )
                if self.keyed.nested is not None:
                    ids |= self.keyed.nested.all_ids(stem, binding.seats_for(key))

        if self.singleton is not None:
            ids.update(
                self.singleton.field_id(suffix) for suffix in self.singleton.fields
            )
            if self.singleton.nested is not None and root is not None:
                count = self.singleton.declared_capacity(self._declared(root))
                ids |= self.singleton.nested.all_ids(self.singleton.name, count)

        return ids

    def divergent_members(self, root, binding) -> list[str]:
        """Where a **data-fixed** collection and the template disagree, in either direction.

        A member the data hold and the template does not declare, and a member the
        template declares and the data do not hold, are one fault seen from its two sides
        (XIV.12, v4.3.0): both are declared and both are knowable, so neither may be
        quietly absorbed. Returns one human-readable line per divergence, naming the team
        or the seat — never a count.

        Empty where this type declares no keyed collection, or where no binding is in
        view. A stand-in caller may pass a binding it knows to be approximate; the
        *severity* of what comes back is the caller's to decide (XIV.9).
        """
        if self.keyed is None or binding is None:
            return []

        declared = self._declared(root)
        bound = set(binding.team_keys)
        found = self.keyed.declared_keys(declared, known=bound)
        problems: list[str] = []

        for key in sorted(found - bound):
            problems.append(
                f"the template declares `{self.keyed.member_id(key)}` fields, but the "
                f"division fields no team normalising to `{key}`"
            )

        for key in sorted(bound - found):
            problems.append(
                f"the division fields a team normalising to `{key}`, but the template "
                f"declares no `{self.keyed.field_id(key, 'name')}`"
            )

        nested = self.keyed.nested
        if nested is None:
            return problems

        for key in sorted(found & bound):
            stem = self.keyed.member_id(key)
            seats = binding.seats_for(key)
            try:
                drawn = nested.declared_capacity(stem, declared)
            except CapacityError as exc:
                problems.append(str(exc))
                continue
            for index in range(seats + 1, drawn + 1):
                problems.append(
                    f"the template declares `{nested.field_id(stem, index, 'name')}`, but "
                    f"`{key}` is configured with {seats} "
                    f"{'seat' if seats == 1 else 'seats'}"
                )
            for index in range(drawn + 1, seats + 1):
                problems.append(
                    f"`{key}` is configured with seat {index}, but the template declares "
                    f"no `{nested.field_id(stem, index, 'name')}`"
                )

        return problems

    def capacity(self, root=None) -> int | None:
        """The capacity of the collection whose slots the **template** fixes.

        The calendar's rounds and a classification's rows; for the lineup, the reserve
        seats — the one lineup collection a template bounds, a division's reserve
        population varying over a season. The team and seat collections are fixed by the
        data and are not capacities in this sense: they diverge rather than overflow.

        None where this type draws no such list, and None where the count is derived but
        no *root* is supplied — unknown rather than nought.
        """
        if self.rows is not None:
            return self.rows.capacity_for(root)
        if self.singleton is not None and self.singleton.nested is not None:
            if root is None:
                return None
            return self.singleton.declared_capacity(self._declared(root))
        return None

    def valueless_ids(self, root=None, binding=None) -> set[str]:
        """Field names the template must declare but the render never fills.

        Two kinds. A calendar's ``vertical_crop_point`` is geometry the render *reads*.
        A **mandatory group** — the lineup's ``reserve_group`` — is a container the render
        either leaves alone or removes whole; it never carries text. XIV.3 makes a
        mandatory field fatal when absent *or* when its value cannot be determined, and
        the second half cannot apply to a field that never carries a value. Without this
        every reserve block would be reported undeterminable on every render.
        """
        ids = self.rows.valueless_field_ids(root) if self.rows is not None else set()

        if self.singleton is not None and "group" in self.singleton.mandatory_fields:
            ids.add(self.singleton.group_id())

        if (
            self.keyed is not None
            and binding is not None
            and "group" in self.keyed.mandatory_fields
        ):
            ids.update(self.keyed.group_id(key) for key in binding.team_keys)

        return ids

    def asset_class_for(self, field_id: str) -> str | None:
        """The asset class of *field_id*, whole-graphic field or per-member alike."""
        direct = self.assets.get(field_id)
        if direct is not None:
            return direct

        if self.rows is not None and self.rows.assets:
            match = re.match(rf"^{re.escape(self.rows.prefix)}_\d+_(.*)$", field_id)
            if match is not None:
                return self.rows.assets.get(match.group(1))

        # The singleton is tried before the keyed collection: `reserve_driver_1_image` is
        # unambiguous, while a keyed pattern would happily read `reserve` as a team key.
        if self.singleton is not None:
            found = self._singleton_asset(field_id)
            if found is not None:
                return found

        if self.keyed is not None:
            return self._keyed_asset(field_id)
        return None

    def _singleton_asset(self, field_id: str) -> str | None:
        spec = self.singleton
        assert spec is not None
        if spec.nested is not None and spec.nested.assets:
            match = re.match(
                rf"^{re.escape(spec.name)}_{re.escape(spec.nested.prefix)}_\d+_(.*)$",
                field_id,
            )
            if match is not None:
                return spec.nested.assets.get(match.group(1))
        if spec.assets:
            match = re.match(rf"^{re.escape(spec.name)}_(.*)$", field_id)
            if match is not None:
                return spec.assets.get(match.group(1))
        return None

    def _keyed_asset(self, field_id: str) -> str | None:
        spec = self.keyed
        assert spec is not None
        if spec.nested is not None and spec.nested.assets:
            match = re.match(
                rf"^{re.escape(spec.prefix)}_.+?_{re.escape(spec.nested.prefix)}_\d+_(.*)$",
                field_id,
            )
            if match is not None:
                return spec.nested.assets.get(match.group(1))
        if spec.assets:
            suffixes = "|".join(sorted(map(re.escape, spec.assets)))
            match = re.match(rf"^{re.escape(spec.prefix)}_.+?_({suffixes})$", field_id)
            if match is not None:
                return spec.assets.get(match.group(1))
        return None


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


#: The lineup's catalogue — the second image type to be specified, and the first whose
#: fields are named after the league's own data.
#:
#: Three collections, each of a different shape, which is why v4.3.0 had to admit all
#: three before this could be written:
#:
#: * **teams** — keyed by the normalised team name, capacity fixed by the *division*;
#: * **seats** — nested inside a team, capacity fixed by the team's configuration;
#: * **reserve** — a singleton with a mandatory group, its seats fixed by the *template*.
#:
#: The reserve team's display name ("Reserve") and the driver-name resolution chain are
#: **values**, not fields, and live with the resolution in
#: ``services/image_lineup_service.py``. See
#: specs/038-lineup-image-generation/contracts/lineup-catalogue.md.
LINEUP_CATALOGUE = FieldCatalogue(
    mandatory=frozenset({"division_name"}),
    optional=frozenset({"season_number", "division_tier"}),
    keyed=KeyedSpec(
        prefix="team",
        fields=frozenset({"name", "image", "group"}),
        mandatory_fields=frozenset({"name"}),
        assets={"image": "team"},
        capacity_from_data=True,
        nested=NestedSpec(
            prefix="driver",
            capacity=None,
            fields=frozenset({"name", "flag", "image", "group"}),
            mandatory_fields=frozenset({"name"}),
            assets={"flag": "flag", "image": "driver"},
        ),
    ),
    singleton=SingletonSpec(
        name=RESERVE_KEY,
        fields=frozenset({"name", "image", "group"}),
        # The group, not the name: every division holds a reserve team, so the template
        # must declare the block; but the block's *name* is chrome the author may omit.
        mandatory_fields=frozenset({"group"}),
        assets={"image": "team"},
        nested=NestedSpec(
            prefix="driver",
            capacity=None,
            fields=frozenset({"name", "flag", "image", "group"}),
            mandatory_fields=frozenset({"name"}),
            # Seat 1 mandatory, every seat beyond it optional (XIV.3, v4.3.0).
            first_member_mandatory_only=True,
            assets={"flag": "flag", "image": "driver"},
        ),
    ),
)


#: Template column → its catalogue. Fifteen entries, one per image type; the calendar and
#: the lineup are populated and the remaining thirteen are still empty.
CATALOGUES: dict[str, FieldCatalogue] = {
    column: FieldCatalogue() for column in TEMPLATE_COLUMNS
}
CATALOGUES["calendar_template"] = CALENDAR_CATALOGUE
CATALOGUES["lineup_template"] = LINEUP_CATALOGUE


def reserve_capacity_problem(root, would_hold: int) -> str | None:
    """Why *would_hold* reserve drivers outgrow the lineup template, or None.

    The reserve block is the **only** lineup collection a template bounds (XIV.12): a
    division's reserve population varies over a season and cannot be known when the
    template is drawn, so its slots are counted from the file. The team and seat
    collections are fixed by the data and diverge rather than overflow.

    Kept out of :func:`declared_capacities`, which feeds the seated-**driver** guard: a
    reserve slot count compared against every seated driver would refuse a placement for
    the wrong reason and still miss the reserve assignment that actually overflows.
    """
    catalogue = catalogue_for("lineup_template")
    try:
        capacity = catalogue.capacity(root)
    except CapacityError as exc:
        return str(exc)
    if not capacity or would_hold <= capacity:
        return None
    return (
        f"that would give the division {would_hold} reserve drivers, but the configured "
        f"lineup template declares {capacity} reserve "
        f"{'slot' if capacity == 1 else 'slots'}"
    )


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

    The **lineup** is excluded for the same reason and returns nothing here: it declares
    ``rows=None``. Its team and seat collections are fixed by the data and diverge rather
    than overflow (XIV.12), and its one template-fixed collection is the *reserve* seats,
    which are guarded at driver assignment by ``reserve_capacity_problem`` below — a
    count of reserve drivers, not of every seated driver.
    """
    return {
        key: catalogue.rows.capacity
        for key, catalogue in CATALOGUES.items()
        if catalogue.rows is not None
        and catalogue.rows.capacity is not None
        and catalogue.rows.capacity > 0
    }
