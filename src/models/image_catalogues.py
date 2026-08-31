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

    #: The least a template filling this **slot** must declare — XIV.12's third capacity,
    #: fixed by the template slot (v4.7.0).
    #:
    #: Set alongside ``capacity=None``: the count still comes from the template, and this
    #: constrains it from below. It exists because the other two readings cannot express a
    #: floor. A fixed ``capacity`` makes over-declaration *fatal*, which would forbid a
    #: template author drawing a fifth block as chrome; a bare ``None`` admits a template
    #: too small to draw a round the league has already scheduled.
    #:
    #: The number is a constant of the **game**, not of the league — a round of the sprint
    #: format holds four sessions and its longest allows three weather slots, whoever is
    #: playing — which is what makes it declarable with no division in view, and what lets
    #: the command naming a template refuse on it (XIV.9, a structural check).
    #:
    #: ``capacity=<int>`` together with a ``minimum`` is **not admitted**: a fixed capacity
    #: is already both bounds.
    minimum: int | None = None

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

    #: Image field suffixes whose **absent datum** draws the class's ``fallback.svg``, and
    #: raises **no** notice for it (XIV.13, v4.4.0). The fallback stands for the absence
    #: itself rather than for a file that should have existed, so nothing has degraded and
    #: there is nothing to report. Where the class carries no fallback the declaration is
    #: inert and the field is removed — an absent datum is never fatal for want of a file.
    #:
    #: Declared per **field**, never per class: one class serves fields that answer absence
    #: differently. A qualifying entry with no tyre recorded draws the tyre fallback; a
    #: configured seat no driver occupies must draw no portrait and no flag at all.
    fallback_when_absent: frozenset[str] = frozenset()

    #: The collection repeating **inside** each member of this one, if any (XIV.11 nesting).
    #: A standings grid's rounds hang here: :class:`NestedSpec` already takes its parent's
    #: id as a ``stem``, so ``row_3`` is a stem like any other and
    #: ``row_3_round_7_feature_race_result`` follows without a second id rule.
    nested: NestedSpec | None = None

    #: Where True, a template declaring **no** member of this collection is not faulty, and
    #: every field of it — and of anything nested inside it — is skipped (XIV.3, v4.5.0). A
    #: field this spec classifies mandatory then binds only on the members a template *does*
    #: declare. The standings results grid is the case: a template drawing no round draws a
    #: classification alone and owes no ``round_<z>_number``.
    #:
    #: This is the *scope* of a classification narrowing, not a third classification.
    optional_unit: bool = False

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

        Declaring none raises, unless this collection is an :attr:`optional_unit`, in which
        case none is a legitimate answer and the count is nought.

        Where a :attr:`minimum` is declared, declaring fewer than it also raises — the floor
        of XIV.12's third capacity. The same :class:`CapacityError` carries it, so it refuses
        at all three verification moments through the path the other two faults already take.
        """
        from utils.svg_document import FieldIndex

        pattern = re.compile(rf"^{re.escape(self.prefix)}_(\d+)(?:_.*)?$")
        ordinals: set[int] = set()
        for name in FieldIndex(root).declared():
            match = pattern.match(name)
            if match is not None:
                ordinals.add(int(match.group(1)))

        if not ordinals:
            if self.optional_unit:
                return 0
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

        if self.minimum is not None and highest < self.minimum:
            raise CapacityError(
                f"the template declares {highest} `{self.prefix}` "
                f"{'member' if highest == 1 else 'members'} but this slot requires at least "
                f"{self.minimum}. Declaring more than {self.minimum} is fine — the surplus is "
                f"removed when the data do not fill it."
            )

        # The nest's own floor and numbering, per member. This has to be raised from here
        # rather than left to id enumeration: ``NestedSpec.ids_under`` answers an uncountable
        # nest with the empty set — deliberately, a fault being no business of enumeration —
        # so a nest declaring too few members would otherwise contribute *nothing* and read as
        # a template with no mandatory slot fields at all, which is silence where XIV.12
        # requires a refusal.
        # Confined to a nest that declares a floor — the slot-fixed form of XIV.12. Every
        # other nest keeps the behaviour it had: a fault there is left to id enumeration and
        # to the checks that already report it, and widening that is not this feature's to do.
        if self.nested is not None and self.nested.minimum is not None:
            declared = set(FieldIndex(root).declared())
            for index in range(1, highest + 1):
                # Raises on the nest's own floor and on a gap in its numbering. The call is
                # made for that effect: the answer is discarded, and only the raise matters.
                self.nested.declared_capacity(self.row_id(index), declared)
        return highest

    def capacity_for(self, root=None) -> int | None:
        """The slot count: the fixed one, or what *root* declares. None if unknowable."""
        if not self.is_derived:
            return self.capacity
        if root is None:
            return None
        return self.declared_capacity(root)

    def _nested_ids(self, root, *, mandatory_only: bool) -> set[str]:
        """Every id contributed by the collection nested inside each member.

        Empty where this collection nests nothing, and empty where no *root* is in view —
        the nest's own capacity is counted from the template, so with no template there is
        nothing to count and nought is the honest answer, as it is for a derived capacity.
        """
        if self.nested is None or root is None:
            return set()

        from utils.svg_document import FieldIndex

        declared = set(FieldIndex(root).declared())
        capacity = self.capacity_for(root) or 0
        ids: set[str] = set()
        for index in range(1, capacity + 1):
            ids |= self.nested.ids_under(
                self.row_id(index), declared, mandatory_only=mandatory_only
            )
        return ids

    def all_field_ids(self, root=None) -> set[str]:
        """Every per-row field id across the capacity, nested collections included."""
        capacity = self.capacity_for(root) or 0
        ids = {
            self.field_id(index, suffix)
            for index in range(1, capacity + 1)
            for suffix in self.fields
        }
        return ids | self._nested_ids(root, mandatory_only=False)

    def mandatory_field_ids(self, root=None) -> set[str]:
        """Every per-row field id a row cannot be drawn without, nests included."""
        capacity = self.capacity_for(root) or 0
        ids = {
            self.field_id(index, suffix)
            for index in range(1, capacity + 1)
            for suffix in self.mandatory_fields
        }
        return ids | self._nested_ids(root, mandatory_only=True)

    def valueless_field_ids(self, root=None) -> set[str]:
        """Every per-row field id that must be present but receives no value.

        Includes the nest's own valueless ids — a weather slot's ``group`` hangs off the
        session that contains it, and is as much a container as the session's own group.
        """
        capacity = self.capacity_for(root) or 0
        ids = {
            self.field_id(index, suffix)
            for index in range(1, capacity + 1)
            for suffix in self.valueless_fields
        }
        if self.nested is None or root is None or not self.nested.valueless_fields:
            return ids

        from utils.svg_document import FieldIndex

        declared = set(FieldIndex(root).declared())
        for index in range(1, capacity + 1):
            stem = self.row_id(index)
            try:
                count = self.nested.declared_capacity(stem, declared)
            except CapacityError:
                continue
            ids |= self.nested.valueless_ids_under(stem, count)
        return ids


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

    #: The least a template filling this slot must declare **under each containing member** —
    #: XIV.12's third capacity, fixed by the template slot (v4.7.0). Mirrors
    #: :attr:`RowSpec.minimum`, and carries the same reasoning: a phase 3 weather template of
    #: the sprint slot must declare three slots for every session it declares, three being the
    #: greatest any session of a sprint round allows.
    minimum: int | None = None

    #: Per-member field suffixes.
    fields: frozenset[str] = frozenset()

    #: Those of ``fields`` without which a member cannot be drawn.
    mandatory_fields: frozenset[str] = frozenset()

    #: Those of ``fields`` the template must declare but which receive **no value** —
    #: mirrors :attr:`RowSpec.valueless_fields` for a nested collection. A weather slot's
    #: ``group`` is the case: XIV.3 makes a mandatory field fatal when absent *or* when its
    #: value cannot be determined, and the second half cannot apply to a container the render
    #: either leaves alone or removes whole. Without this every drawn slot group would be
    #: reported undeterminable on every render.
    valueless_fields: frozenset[str] = frozenset()

    #: Where True, ``mandatory_fields`` binds member **1 only** and every member beyond it
    #: is optional (XIV.3, v4.3.0 — a classification varying by member, declared by a rule
    #: rather than an enumeration). The reserve block is the case: the template must
    #: declare the block at all without being obliged to declare a fixed number of it.
    first_member_mandatory_only: bool = False

    #: Field suffix → asset class.
    assets: dict[str, str] = field(default_factory=dict)

    #: A third level, hanging off each member of this one (XIV.11 nesting). The cars of a
    #: round of a constructors grid are here: the round nest's member id ``row_3_round_7``
    #: is the stem this one builds ``row_3_round_7_driver_2_name`` from.
    nested: NestedSpec | None = None

    #: Where True, a template declaring no member of this collection is not faulty and its
    #: fields are skipped (XIV.3, v4.5.0). Mirrors :attr:`RowSpec.optional_unit`.
    optional_unit: bool = False

    #: Where True, the configured value bounding this collection belongs to the **containing
    #: member** rather than to the graphic — the seats of the team on *this* row, not a
    #: number that could be right for every row (XIV.12, v4.5.0).
    #:
    #: One template draws every containing member, so no declared count can satisfy them
    #: all: the members a template declares are a **ceiling**, those beyond the containing
    #: member's configured value are trimmed for that member alone and report nothing, and
    #: the fatal test is against the data actually drawn. The count is read from the
    #: configuration at each check and is never frozen here, which is why :attr:`capacity`
    #: stays None.
    capacity_per_member: bool = False

    def ids_under(
        self,
        stem: str,
        declared: Iterable[str],
        *,
        mandatory_only: bool = False,
        count: int | None = None,
    ) -> set[str]:
        """Every id this collection and its own nests contribute beneath *stem*.

        *count* overrides the count taken from the template, for a capacity the data fix.
        A gap in the numbering yields the empty set rather than raising: the gap is a fault
        the structural check reports in its own words, and enumerating ids is not the place
        to discover it.
        """
        declared = set(declared)
        if count is None:
            try:
                count = self.declared_capacity(stem, declared)
            except CapacityError:
                return set()

        suffixes = self.mandatory_fields if mandatory_only else self.fields
        ids: set[str] = set()
        for index in range(1, count + 1):
            if mandatory_only and self.first_member_mandatory_only and index > 1:
                continue
            ids.update(self.field_id(stem, index, suffix) for suffix in suffixes)
            if self.nested is not None:
                ids |= self.nested.ids_under(
                    self.member_id(stem, index), declared, mandatory_only=mandatory_only
                )
        return ids

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

    def valueless_ids_under(self, stem: str, count: int) -> set[str]:
        """Every id across *count* members that must be present but carries no value."""
        return {
            self.field_id(stem, index, suffix)
            for index in range(1, count + 1)
            for suffix in self.valueless_fields
        }

    def declared_capacity(self, stem: str, declared: Iterable[str]) -> int:
        """Count the members *declared* holds under *stem*, requiring contiguity from 1.

        Used for the reserve block, whose slot count the template alone fixes. A gap is a
        fault of the template (XIV.11) and is fatal wherever it is met, so the same
        :class:`CapacityError` serves all three verification moments.

        Where a :attr:`minimum` is declared, declaring fewer than it under a member that
        declares **any** also raises — the floor of XIV.12's third capacity. A member
        declaring none of the nest at all is left to the caller, which is what lets a session
        the template does not declare stay silent rather than reporting a missing floor.
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

        if self.minimum is not None and highest < self.minimum:
            raise CapacityError(
                f"`{stem}` declares {highest} `{self.prefix}` "
                f"{'member' if highest == 1 else 'members'} but this slot requires at least "
                f"{self.minimum} for every {stem.rsplit('_', 1)[0] or 'member'} it declares. "
                f"Declaring more than {self.minimum} is fine — the surplus is removed when "
                f"the data do not fill it."
            )
        return highest


@dataclass(frozen=True)
class SingletonSpec:
    """A collection of exactly one member, named, bearing no discriminator (XIV.11).

    The lineup's reserve block. Its name is **reserved**: no member of a sibling
    collection may normalise to it, which is why ``reserve`` is refused as a team name at
    the command that would set it (Principle IX).

    Its ``_group`` is **optional**, and the block with it. Every division holds a reserve
    team, but not every league wants one on its lineup sheet: declaring the block is how a
    template asks for it to be drawn, and a template declaring no slots draws no reserves
    and reports nothing. Where the block *is* declared it still leaves whole when the
    division fields nobody to put in it.

    The cost of letting a template omit the block on purpose is that one omitting it by
    mistake loses its reserves in silence. That is the trade accepted here: the block was
    mandatory until this rule so that the mistake could be caught, and the price of
    catching it was refusing the deliberate case outright.
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

    #: A **second** top-level ordinal collection, where this type draws a grid. The round
    #: headings of a standings graphic are here (XIV.2, the discriminated column group).
    #:
    #: Separate from :attr:`rows` because the two are counted against different data — rows
    #: against the classification, columns against the division's calendar — and diverge
    #: independently and in opposite directions. A column's *cells* are not here: a cell
    #: belongs to its row and its column both and a node has one parent, so cells hang off
    #: ``rows.nested`` and this carries the chrome alone.
    columns: RowSpec | None = None

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
            and self.columns is None
            and self.singleton is None
        )

    @staticmethod
    def _declared(root) -> set[str]:
        from utils.svg_document import FieldIndex

        return set(FieldIndex(root).declared())

    def all_mandatory_ids(self, root=None) -> set[str]:
        """Mandatory singular fields, plus every mandatory per-member field.

        *root* is needed wherever a capacity is counted from the template, which since
        v6.0.0 is everywhere: no collection of any type is fixed by the data, so the file
        alone answers what a template must declare. The ``binding`` this once took was the
        lineup's team list, and there is no longer anything for it to bind.
        """
        ids = set(self.mandatory)
        if self.rows is not None:
            ids |= self.rows.mandatory_field_ids(root)
        if self.columns is not None:
            ids |= self.columns.mandatory_field_ids(root)

        if self.singleton is not None:
            ids.update(
                self.singleton.field_id(suffix)
                for suffix in self.singleton.mandatory_fields
            )
            # The reserve block's own seats are counted from the template, so they are
            # checkable with no division in view — which is what makes the singleton the
            # part of a lineup that a naming command can reject on (research R4).
            #
            # Counted, and no longer floored at one: a template declaring no slots
            # declares no reserve block, and demanding `reserve_driver_1_name` of it would
            # refuse the very file a league authors when it wants no reserves drawn. What
            # a naming command still rejects on is a block half-declared — a gap in the
            # numbering, or a slot declared without its name.
            if self.singleton.nested is not None and root is not None:
                count = self.singleton.declared_capacity(self._declared(root))
                ids |= self.singleton.nested.mandatory_ids(self.singleton.name, count)

        return ids

    def all_known_ids(self, root=None) -> set[str]:
        """Every field name this type may address, mandatory or optional."""
        ids = set(self.mandatory) | set(self.optional)
        if self.rows is not None:
            ids |= self.rows.all_field_ids(root)
        if self.columns is not None:
            ids |= self.columns.all_field_ids(root)

        if self.singleton is not None:
            ids.update(
                self.singleton.field_id(suffix) for suffix in self.singleton.fields
            )
            if self.singleton.nested is not None and root is not None:
                count = self.singleton.declared_capacity(self._declared(root))
                ids |= self.singleton.nested.all_ids(self.singleton.name, count)

        return ids

    def capacity(self, root=None) -> int | None:
        """The capacity of this type's **top-level** collection.

        The calendar's rounds, a classification's rows, a lineup's team blocks.

        None where this type draws no such list, and None where the count is derived but
        no *root* is supplied — unknown rather than nought.

        **This is not the reserve block.** Until v6.0.0 the lineup declared no ``rows``
        and this method fell through to :meth:`singleton_capacity`, which made it *look*
        like the accessor for the reserve seats. Giving the team collection its ordinal
        rows spec took that fall-through away. Read the reserve through
        :meth:`singleton_capacity` and never through this (research R1 of 047).
        """
        if self.rows is not None:
            return self.rows.capacity_for(root)
        return None

    def singleton_capacity(self, root=None) -> int | None:
        """The slot count of the collection nested inside this type's singleton member.

        The lineup's reserve seats, and nothing else at present. Counted from the template
        alone: a division's reserve population varies over a season and cannot be known
        when the file is drawn (XIV.12).

        None where this type declares no such nest, and None where no *root* is supplied.
        """
        if self.singleton is None or self.singleton.nested is None:
            return None
        if root is None:
            return None
        return self.singleton.declared_capacity(self._declared(root))

    def column_capacity(self, root=None) -> int | None:
        """The capacity of the **second** top-level collection — a grid's columns.

        Kept apart from :meth:`capacity`, which feeds the overflow guard counting the rows
        of a classification. A standings grid's columns are counted against the division's
        calendar instead, so conflating the two would refuse a driver placement because a
        template drew few rounds, and would still miss the round that actually overflows it.

        None where this type draws no columns, and None where the count is derived but no
        *root* is supplied — unknown rather than nought.
        """
        if self.columns is None:
            return None
        return self.columns.capacity_for(root)

    def valueless_ids(self, root=None) -> set[str]:
        """Field names the template must declare but the render never fills.

        Two kinds. A calendar's ``vertical_crop_point`` is geometry the render *reads*.
        A **mandatory group** — the lineup's ``reserve_group`` — is a container the render
        either leaves alone or removes whole; it never carries text. XIV.3 makes a
        mandatory field fatal when absent *or* when its value cannot be determined, and
        the second half cannot apply to a field that never carries a value. Without this
        every reserve block would be reported undeterminable on every render.
        """
        ids = self.rows.valueless_field_ids(root) if self.rows is not None else set()
        if self.columns is not None:
            ids |= self.columns.valueless_field_ids(root)

        if self.singleton is not None and "group" in self.singleton.mandatory_fields:
            ids.add(self.singleton.group_id())

        return ids

    def draws_fallback_when_absent(self, field_id: str) -> bool:
        """Whether an **absent datum** on *field_id* draws its class's fallback quietly.

        Declared per field by :attr:`RowSpec.fallback_when_absent` (XIV.13, v4.4.0). False
        for every field of every type that declares none, which leaves the absent datum to
        Rule 3 — removed where the field is optional, fatal where it is mandatory.
        """
        if self.rows is None or not self.rows.fallback_when_absent:
            return False
        match = re.match(rf"^{re.escape(self.rows.prefix)}_\d+_(.*)$", field_id)
        return match is not None and match.group(1) in self.rows.fallback_when_absent

    def asset_class_for(self, field_id: str) -> str | None:
        """The asset class of *field_id*, whole-graphic field or per-member alike."""
        direct = self.assets.get(field_id)
        if direct is not None:
            return direct

        # Each ordinal collection is tried and **fallen through** rather than returned from,
        # because one id may match a prefix without belonging to that level: a grid's
        # `row_1_round_2_feature_race_result` matches the row pattern with a suffix the row
        # knows nothing about, and belongs to the nest below it.
        for spec in (self.rows, self.columns):
            if spec is None:
                continue
            found = self._ordinal_asset(spec, field_id)
            if found is not None:
                return found

        if self.singleton is not None:
            found = self._singleton_asset(field_id)
            if found is not None:
                return found

        return None

    @staticmethod
    def _ordinal_asset(spec: RowSpec, field_id: str) -> str | None:
        """The asset class *field_id* takes from *spec* or from a collection nested in it.

        Descends the nest chain so a grid's deeper levels can carry assets of their own. The
        standings grid carries none — its cells are text — but the shape has to be right
        before an image type needs it, and returning None rather than short-circuiting is
        what lets the caller try the next collection.
        """
        match = re.match(rf"^{re.escape(spec.prefix)}_\d+_(.*)$", field_id)
        if match is None:
            return None
        remainder = match.group(1)
        if spec.assets:
            found = spec.assets.get(remainder)
            if found is not None:
                return found

        nest = spec.nested
        while nest is not None:
            nested_match = re.match(
                rf"^{re.escape(nest.prefix)}_\d+_(.*)$", remainder
            )
            if nested_match is None:
                return None
            remainder = nested_match.group(1)
            if nest.assets:
                found = nest.assets.get(remainder)
                if found is not None:
                    return found
            nest = nest.nested
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
                # The two classes a round may be pictured by (044). Both optional: a
                # template declares either, both, or neither, and chooses per round.
                "flag",
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
        assets={"flag": "flag", "image": "track"},
    ),
)


#: The lineup's catalogue. It was once the one image type whose fields were named after a
#: league's own data; since v6.0.0 it names none, and one shipped file serves every league.
#:
#: Two collections:
#:
#: * **teams** — an ordinal collection, capacity counted from the *template*, with the
#:   **seats** nested inside each block as a ceiling bounded by that team's configuration;
#: * **reserve** — a singleton with a mandatory group, its seats fixed by the *template*.
#:
#: The reserve team's display name ("Reserve") and the driver-name resolution chain are
#: **values**, not fields, and live with the resolution in
#: ``services/image_lineup_service.py``. See
#: specs/038-lineup-image-generation/contracts/lineup-catalogue.md.
LINEUP_CATALOGUE = FieldCatalogue(
    mandatory=frozenset({"division_name"}),
    optional=frozenset({"season_number", "division_tier"}),
    # Ordinal since v6.0.0. The team collection was **keyed** by the normalised team name,
    # which made this the one template of the module authored against a league's own data:
    # no shipped file could draw a league whose teams it did not know, and every division
    # of a season was forced into one composition to keep a single file serving them all.
    # `team_<x>` is a place in the layout; which team fills it is resolved from the
    # division at generation and recorded nowhere (XIV.11, 047 FR-001 and FR-006).
    rows=RowSpec(
        prefix="team",
        # Counted from the template, contiguous from 1. The division is measured against
        # it, and a division fielding fewer blocks than the file declares is ordinary.
        capacity=None,
        fields=frozenset({"name", "image", "group"}),
        mandatory_fields=frozenset({"name"}),
        assets={"image": "team"},
        nested=NestedSpec(
            prefix="driver",
            capacity=None,
            # A **ceiling**, exactly as the constructors grid's cars are (XIV.12, 047
            # FR-018). Teams differ in size and one template draws every block, so slots
            # beyond a team's configured seats are trimmed for that block alone and report
            # nothing. The fatal test is against the drivers actually seated.
            capacity_per_member=True,
            fields=frozenset({"name", "flag", "image", "group"}),
            mandatory_fields=frozenset({"name"}),
            assets={"flag": "flag", "image": "driver"},
        ),
    ),
    singleton=SingletonSpec(
        name=RESERVE_KEY,
        fields=frozenset({"name", "image", "group"}),
        # Nothing is mandatory: a lineup template need not declare the reserve block at
        # all, and one that declares no slots simply draws no reserves. Where the block is
        # declared, its *name* remains chrome the author may omit.
        mandatory_fields=frozenset(),
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


#: The crop point and the footer band, which every image type drawn as a **list of rows** may
#: declare (Constitution XIV.2, v7.1.0) so that a division of twenty drivers is not drawn on a
#: canvas built for fifty.
#:
#: **Optional, deliberately.** A league's own template authored before v7.1.0 declares neither
#: and must go on rendering at full height; making the crop point mandatory would invalidate
#: every one of them at the moment it was named. The calendar is the exception and keeps its own
#: mandatory crop point: it has required one since 037, and no template of it exists that does
#: not declare one.
#:
#: The crop point is `valueless` for the same reason the calendar's is — geometry the render
#: reads, never text it writes — so Rule 3's "its value could not be determined" cannot apply.
_ROW_CROP_FIELD = "vertical_crop_point"

#: The footer band is a whole-graphic field, not a per-row one: there is one of it.
FOOTER_GROUP_FIELD = "footer_group"

#: What both results templates address, whatever the session they draw. Declared once and
#: composed into each of the two catalogues below (XIV.10, v4.4.0: siblings may share the
#: declaration of their common part and must remain separately addressable).
_RESULTS_MANDATORY = frozenset(
    {"division_name", "round_number", "race_name", "session_name", "result_status"}
)
_RESULTS_OPTIONAL = frozenset(
    {
        "season_number",
        FOOTER_GROUP_FIELD,
        "division_tier",
        # Column groups (XIV.2, v4.4.0): each wraps the heading of a sanction column and no
        # cell of any row, and leaves while that phase stands open.
        "postrace_penalty_group",
        "appeal_penalty_group",
    }
)

#: The row fields both kinds of results graphic carry.
_RESULTS_ROW_FIELDS = frozenset(
    {
        "group",
        "position",
        "driver_name",
        "driver_flag",
        "team_name",
        "team_image",
        "postrace_penalty",
        "appeal_penalty",
        "points",
    }
)
_RESULTS_ROW_MANDATORY = frozenset(
    {
        # The group is mandatory and valueless: the template must provide the row, and the
        # render either fills it or removes it whole.
        "group",
        "position",
        "driver_name",
        "team_name",
        "team_image",
        "postrace_penalty",
        "appeal_penalty",
        "points",
    }
)
_RESULTS_ROW_ASSETS = {"driver_flag": "flag", "team_image": "team"}


#: The qualifying results catalogue — the third image type to be specified, and the first
#: whose aspect is drawn by two templates rather than one.
#:
#: Its rows are a **template-derived** collection, as the calendar's rounds are: a league
#: draws as many as its grid needs, so the capacity is counted from the file (XIV.12). The
#: row's ordinal *is* the finishing position drawn upon it, and the position field is filled
#: from that ordinal with no reconciliation attempted (XIV.11, v4.4.0).
#:
#: See specs/039-results-image-generation/contracts/results-catalogue.md.
RESULTS_QUALIFYING_CATALOGUE = FieldCatalogue(
    mandatory=_RESULTS_MANDATORY,
    optional=_RESULTS_OPTIONAL,
    rows=RowSpec(
        prefix="row",
        capacity=None,
        fields=_RESULTS_ROW_FIELDS | {"tyre", "best_lap", "gap", _ROW_CROP_FIELD},
        mandatory_fields=_RESULTS_ROW_MANDATORY | {"best_lap", "gap"},
        valueless_fields=frozenset({"group", _ROW_CROP_FIELD}),
        assets={**_RESULTS_ROW_ASSETS, "tyre": "tyre"},
        # A tyre is a value the submission of a session need not carry, so its absence is a
        # state worth depicting rather than a gap worth reporting (XIV.13, v4.4.0).
        fallback_when_absent=frozenset({"tyre"}),
    ),
)


#: The race results catalogue. Identical to its qualifying sibling but for the columns of its
#: rows, and for the fastest-lap block — a **block group** (XIV.2, v4.4.0) wrapping fields that
#: stand or fall together, removed when the session conferred no bonus.
#:
#: ``row_<x>_fastest_lap`` is the module's only data-driven recolour. A recolour does not
#: consume the field: it is filled as any other (XIV.2).
RESULTS_RACE_CATALOGUE = FieldCatalogue(
    mandatory=_RESULTS_MANDATORY,
    optional=_RESULTS_OPTIONAL
    | {
        "fastest_lap_group",
        # The marking's legend, removed with the plate above. Its own group because it is
        # drawn in the footer band the crop carries up (XIV.2, v7.1.0).
        "fastest_lap_legend_group",
        "fastest_lap_driver_name",
        "fastest_lap_time",
    },
    rows=RowSpec(
        prefix="row",
        capacity=None,
        fields=_RESULTS_ROW_FIELDS
        | {"time", "fastest_lap", "ingame_penalty", _ROW_CROP_FIELD},
        mandatory_fields=_RESULTS_ROW_MANDATORY
        | {"time", "fastest_lap", "ingame_penalty"},
        valueless_fields=frozenset({"group", _ROW_CROP_FIELD}),
        assets=dict(_RESULTS_ROW_ASSETS),
    ),
)


#: The heading fields both standings championships share. The lifecycle label is drawn on
#: the graphic *and* kept as message text: XIV.16 (v4.5.0) makes that split non-exclusive,
#: so a picture forwarded away from its message still says which phase it stands after.
_STANDINGS_MANDATORY = frozenset({"division_name", "round_number", "result_status"})

#: The optional headings, each with the group that may wrap it. XIV.2 requires a declared
#: ``<field>_group`` to leave whole wherever the field would be emptied, so that the label
#: and plate standing around a value go with it; declaring the groups here is what lets the
#: utility reach them. Every shipped template carries both.
_STANDINGS_OPTIONAL = frozenset(
    {
        "season_number",
        FOOTER_GROUP_FIELD,
        "season_number_group",
        "division_tier",
        "division_tier_group",
        "race_name",
        "race_name_group",
    }
)

#: The row fields both championships share. The drivers row adds the driver's name and flag;
#: neither is a field of the constructors row, and declaring one there is a sibling fault.
_STANDINGS_ROW_FIELDS = frozenset(
    {
        "group",
        "position",
        "team_name",
        "team_image",
        "points",
        "gap_to_leader",
        "previous_position",
        "position_change_group",
        "position_change",
        "position_change_marker",
    }
)
_STANDINGS_ROW_MANDATORY = frozenset(
    {"group", "position", "team_name", "team_image", "points"}
)
_STANDINGS_ROW_ASSETS = {
    "team_image": "team",
    "position_change_marker": "marker",
}

#: The four session cells a round of the grid may carry, on a driver's row directly and on a
#: constructor's car. Every one is optional: a template draws the sessions it has room for.
_STANDINGS_CELL_FIELDS = frozenset(
    {
        "sprint_qualifying_result",
        "sprint_race_result",
        "feature_qualifying_result",
        "feature_race_result",
    }
)

#: The highlight layers a cell may carry beneath its text: a **background** taking the podium
#: or points colour, and a **fastest-lap overlay** that may stand over it. Optional, and
#: independently so — a template opts in per cell and per kind by declaring the field and a
#: matching ``.highlight_*`` rule, and one declaring neither renders as it always did.
#:
#: **Asset fields**, of the closed-set `standings_highlight` class. The chip is artwork rather
#: than a colour: a stylesheet can only ever wash the whole cell, where a file can draw a plate,
#: a corner mark, or whatever else a league wants of it. The five data — `p1`, `p2`, `p3`,
#: `points`, `fastest_lap` — are the module's own vocabulary, so a league missing one is given
#: the bot's own file rather than a generic fallback (XIV.13).
#:
#: Their slots **stretch** rather than hold one aspect (XIV.6, v7.4.0): a cell is 52 x 22 on the
#: drivers grid and 52 x 18 on the constructors one, and no single ratio serves two row bands.
#:
#: The two qualifying backgrounds are admitted although the shipped templates decline them.
#: A raised qualifying glyph shares one auto-laid text chunk with the race result beside it
#: and so has no fixed position to put a chip behind; a league that re-lays its own template
#: to give qualifying a column of its own may declare them, and is not refused for it.
_STANDINGS_CELL_HIGHLIGHTS = frozenset(
    {
        "sprint_qualifying_background",
        "sprint_race_background",
        "sprint_race_fastest_lap",
        "feature_qualifying_background",
        "feature_race_background",
        "feature_race_fastest_lap",
    }
)

#: Every highlight field draws the one class; which chip is drawn is the datum, not the class.
_STANDINGS_CELL_HIGHLIGHT_ASSETS = {
    suffix: "standings_highlight" for suffix in _STANDINGS_CELL_HIGHLIGHTS
}

#: The round headings, shared by both standings championships **and by the attendance
#: sheet**. An **optional unit** (XIV.3, v4.5.0): a template declaring no round draws its
#: classification or its totals alone and owes no field here, while one declaring any round
#: owes that round its number — the image standing in addition to the number and never in
#: its place.
#:
#: The image is the round's **country flag** and never a circuit map (044). A round stands
#: here as a column heading, at a size no circuit outline survives, and XIV.13 admits a
#: track-class field on the calendar and the check-in graphic alone.
#:
#: The three types that draw a round grid head it identically, and the wip-spec says so in as
#: many words: the attendance sheet's round group "contains the fields of the round named
#: below and no field of any row, **as it does on the standings graphics**". One function so
#: that a change to the heading cannot reach one type and miss another.
def _round_heading_columns() -> RowSpec:
    return RowSpec(
        prefix="round",
        capacity=None,
        optional_unit=True,
        fields=frozenset({"group", "number", "flag"}),
        mandatory_fields=frozenset({"number"}),
        valueless_fields=frozenset({"group"}),
        assets={"flag": "flag"},
    )


#: The driver standings catalogue — the fourth image type to be specified, and the first to
#: draw a **grid**: its fields are addressed on two ordinals at once, the row and the round.
#:
#: A cell belongs to its row and to its round both, and a node of an SVG file has one parent,
#: so the cells hang off the row (``rows.nested``) and the headings stand at top level
#: (``columns``) carrying chrome alone. Removing a round therefore reaches two id families,
#: not one — XIV.12's "one capacity may govern several id families".
#:
#: See specs/040-standings-image-generation/contracts/standings-catalogue.md.
STANDINGS_DRIVERS_CATALOGUE = FieldCatalogue(
    mandatory=_STANDINGS_MANDATORY,
    optional=_STANDINGS_OPTIONAL,
    columns=_round_heading_columns(),
    rows=RowSpec(
        prefix="row",
        capacity=None,
        fields=_STANDINGS_ROW_FIELDS | {"driver_name", "driver_flag", _ROW_CROP_FIELD},
        mandatory_fields=_STANDINGS_ROW_MANDATORY | {"driver_name"},
        valueless_fields=frozenset(
            {"group", "position_change_group", _ROW_CROP_FIELD}
        ),
        assets={**_STANDINGS_ROW_ASSETS, "driver_flag": "flag"},
        nested=NestedSpec(
            prefix="round",
            capacity=None,
            optional_unit=True,
            fields=_STANDINGS_CELL_FIELDS | _STANDINGS_CELL_HIGHLIGHTS | {"group"},
            mandatory_fields=frozenset(),
            # The group was already valueless in fact and unclassified in the spec, this
            # nest declaring no valueless field at all. Naming it costs nothing and
            # `RowSpec.valueless_field_ids` consults the nest only where the set is
            # non-empty. The highlight fields are **not** valueless: they carry an asset.
            valueless_fields=frozenset({"group"}),
            assets=dict(_STANDINGS_CELL_HIGHLIGHT_ASSETS),
        ),
    ),
)


#: The constructor standings catalogue. Its row carries no driver name and no flag, and its
#: grid reaches a **third** level: the cells of a round stand for the team's cars one by one.
#:
#: That third level is the first collection whose data-fixed capacity varies by containing
#: member (XIV.12, v4.5.0) — the seats configured for the team on *this* row. One template
#: draws every row, so the cars it declares are a ceiling rather than a count.
STANDINGS_CONSTRUCTORS_CATALOGUE = FieldCatalogue(
    mandatory=_STANDINGS_MANDATORY,
    optional=_STANDINGS_OPTIONAL,
    columns=_round_heading_columns(),
    rows=RowSpec(
        prefix="row",
        capacity=None,
        fields=_STANDINGS_ROW_FIELDS | {_ROW_CROP_FIELD},
        mandatory_fields=_STANDINGS_ROW_MANDATORY,
        valueless_fields=frozenset(
            {"group", "position_change_group", _ROW_CROP_FIELD}
        ),
        assets=dict(_STANDINGS_ROW_ASSETS),
        nested=NestedSpec(
            prefix="round",
            capacity=None,
            optional_unit=True,
            # The round level of a constructors grid carries no field of its own: the
            # wip-spec gives it no `row_<x>_round_<z>_group`, the cars below it being what
            # a round of a team's row actually holds.
            fields=frozenset(),
            mandatory_fields=frozenset(),
            nested=NestedSpec(
                prefix="driver",
                capacity=None,
                capacity_per_member=True,
                fields=(
                    _STANDINGS_CELL_FIELDS
                    | _STANDINGS_CELL_HIGHLIGHTS
                    | {"group", "name"}
                ),
                mandatory_fields=frozenset(),
                valueless_fields=frozenset({"group"}),
                assets=dict(_STANDINGS_CELL_HIGHLIGHT_ASSETS),
            ),
        ),
    ),
)


#: The attendance sheet's heading fields. It stands **after** a round and names that round;
#: it carries no lifecycle label, an attendance record having no phases to stand between.
_ATTENDANCE_MANDATORY = frozenset({"division_name", "round_number"})

#: The optional headings, each with the group that may wrap it, plus the two **block groups**
#: (XIV.2) wrapping the point limits. A block group is named for the block and not for a field,
#: so ``autoreserve_group`` is a catalogue entry in its own right where ``season_number_group``
#: is the ordinary ``<field>_group`` form; both are declared here so the utility can reach them.
#:
#: A limit whose functionality is switched off is a **configured absence** (XIV.4): the group
#: leaves whole, and no notice is raised for a setting the league chose itself.
_ATTENDANCE_OPTIONAL = frozenset(
    {
        "season_number",
        FOOTER_GROUP_FIELD,
        "season_number_group",
        "division_tier",
        "division_tier_group",
        "race_name",
        "race_name_group",
        "autoreserve_group",
        "autoreserve_limit",
        "autosack_group",
        "autosack_limit",
    }
)


#: The attendance sheet — the fifth image type to be specified, and the second to draw a grid.
#:
#: Its shape is the drivers standings' with the movement columns removed: rows against the
#: drivers, a round collection at top level carrying the headings, and a cell per round hanging
#: off each **row** (XIV.2 — a cell belongs to its row and its column both, and a node of an SVG
#: file has one parent). Removing a round therefore reaches two id families, not one.
#:
#: Its row ordinal is a **place in the layout and not a datum** (XIV.11, v4.6.0): the sheet is a
#: record and not a classification, two drivers level on totals stand level, and no position is
#: drawn. This is why the row carries no ``position`` field where the standings row does.
#:
#: The rows carry a **floor**: a division holding no driver has no sheet to draw (XIV.12,
#: v4.6.0). The floor is raised by ``image_attendance_service.resolve_drawing`` against the
#: concrete data, as the calendar's is — a statement about the data and not about the template.
#:
#: See specs/041-attendance-image-generation/contracts/attendance-catalogues.md.
ATTENDANCE_CATALOGUE = FieldCatalogue(
    mandatory=_ATTENDANCE_MANDATORY,
    optional=_ATTENDANCE_OPTIONAL,
    columns=_round_heading_columns(),
    rows=RowSpec(
        prefix="row",
        capacity=None,
        fields=frozenset(
            {
                "group",
                "driver_name",
                "driver_flag",
                "team_name",
                "team_image",
                "points",
                "sanction",
                _ROW_CROP_FIELD,
            }
        ),
        mandatory_fields=frozenset({"group", "driver_name", "points"}),
        valueless_fields=frozenset({"group", _ROW_CROP_FIELD}),
        assets={"driver_flag": "flag", "team_image": "team"},
        nested=NestedSpec(
            prefix="round",
            capacity=None,
            optional_unit=True,
            fields=frozenset({"points"}),
            mandatory_fields=frozenset(),
        ),
    ),
)


#: The check-in call's heading fields. The grand prix name is **mandatory** here where it is
#: optional on the sheet: a call announces one round and is meaningless without naming it,
#: while a sheet is a record of a season that happens to stand after one.
_RSVP_MANDATORY = frozenset(
    {
        "division_name",
        "round_number",
        "race_name",
        "round_format",
        "round_date",
        "round_time",
    }
)

#: The optional headings. ``race_name_group`` is here though its field is mandatory — XIV.2
#: lets a group wrap a field of either classification, and declaring it is what lets the
#: utility reach it.
_RSVP_OPTIONAL = frozenset(
    {
        "season_number",
        "season_number_group",
        "division_tier",
        "division_tier_group",
        "race_name_group",
        "track_name",
        "track_name_group",
        "country_name",
        "country_name_group",
        "track_flag",
        "track_flag_group",
        "track_image",
        "track_image_group",
        "deadline_date",
        "deadline_time",
    }
)


#: The check-in call graphic — the first **static** graphic of the module (XIV.17, v4.6.0).
#:
#: It is generated once, at the moment the call is posted, and never again while that call
#: stands: the message carries three buttons armed against it and cannot be reposted, so the
#: embed is edited in place on every press and the attachment rides through untouched.
#:
#: **What is absent here is the substance of that declaration.** There is no driver name, no
#: team, no RSVP status, no attendance point and no roster — everything a button press alters
#: lives in the embed, which is edited, and stays off the picture, which is not. Adding any
#: field whose value can change while the call stands is an amendment of the static
#: declaration and **not** a catalogue edit; nothing in the module can detect the breach, and
#: the result is a stale picture under a current message that reports nothing.
#:
#: See specs/041-attendance-image-generation/contracts/attendance-catalogues.md.
RSVP_CATALOGUE = FieldCatalogue(
    mandatory=_RSVP_MANDATORY,
    optional=_RSVP_OPTIONAL,
    assets={"track_flag": "flag", "track_image": "track"},
    rows=RowSpec(
        prefix="session",
        capacity=None,
        optional_unit=True,
        fields=frozenset({"group", "name"}),
        mandatory_fields=frozenset({"group", "name"}),
        valueless_fields=frozenset({"group"}),
    ),
)


# ── Weather ───────────────────────────────────────────────────────────────
#
# Six templates serve one aspect — the module's most divided. Three phases, two round-format
# variants for each of phases 2 and 3, and a notice for a kind of round that runs no phase at
# all.


def _weather_floors(formats: tuple[str, ...]) -> tuple[int, int]:
    """(sessions, slots) — the greatest *formats* can demand, for a template slot's floor.

    XIV.12's third capacity is fixed by the **template slot**, and the number is a constant of
    the game rather than of the league: a round of the sprint format holds four sessions and
    its longest session allows three weather slots, whoever is playing.

    Derived from the weather module's own constants at import, never written as literals — a
    figure copied here would be a second thing to keep true (FR-015).
    """
    from models.round import RoundFormat
    from models.session import MAX_SLOTS, SESSIONS_BY_FORMAT

    sessions = 0
    slots = 0
    for name in formats:
        types = SESSIONS_BY_FORMAT[RoundFormat(name)]
        sessions = max(sessions, len(types))
        for session_type in types:
            slots = max(slots, MAX_SLOTS[session_type])
    return sessions, slots


#: The sprint slot serves rounds of the sprint format alone; the plain slot serves every other
#: format that runs a phase. A mystery round runs none and reaches neither.
_SPRINT_SESSIONS_FLOOR, _SPRINT_SLOTS_FLOOR = _weather_floors(("SPRINT",))
_PLAIN_SESSIONS_FLOOR, _PLAIN_SLOTS_FLOOR = _weather_floors(("NORMAL", "ENDURANCE"))


#: The heading fields every phase graphic carries. The mystery notice carries four of them and
#: nothing else — it announces that no forecast is coming, and has nothing else to say.
_WEATHER_HEADING_MANDATORY = frozenset(
    {"division_name", "phase_description", "round_number", "track_name"}
)

_WEATHER_HEADING_OPTIONAL = frozenset(
    {
        "season_number",
        "season_number_group",
        "division_tier",
        "division_tier_group",
        "race_name",
        "race_name_group",
        "country_name",
        "country_name_group",
        "track_flag",
        "track_flag_group",
    }
)

#: Phase 2 draws one weather type per session; phase 3 draws the sequence within it. The two
#: meanings of "slot" belong to different phases and are told apart by **this catalogue**,
#: never by parsing an id (XIV.11, v4.7.0): ``session_<x>_slot_type`` is a field of the session
#: and ``session_<x>_slot_<y>_label`` a field of one of its slots.
_P2_SESSION_FIELDS = frozenset({"group", "name", "slot_type", "slot_type_icon"})
_P3_SESSION_FIELDS = frozenset(
    {"group", "name", "slot_type", "slot_type_icon", "summary"}
)


def _p2_sessions(minimum: int) -> RowSpec:
    return RowSpec(
        prefix="session",
        capacity=None,
        minimum=minimum,
        fields=_P2_SESSION_FIELDS,
        mandatory_fields=frozenset({"group", "name", "slot_type"}),
        valueless_fields=frozenset({"group"}),
        assets={"slot_type_icon": "weather"},
    )


def _p3_sessions(minimum: int, slot_minimum: int) -> RowSpec:
    return RowSpec(
        prefix="session",
        capacity=None,
        minimum=minimum,
        fields=_P3_SESSION_FIELDS,
        # The type is **optional** here: phase 3's subject is the sequence, and a template
        # may carry it without restating what phase 2 drew.
        mandatory_fields=frozenset({"group", "name"}),
        valueless_fields=frozenset({"group"}),
        assets={"slot_type_icon": "weather"},
        nested=NestedSpec(
            prefix="slot",
            capacity=None,
            minimum=slot_minimum,
            fields=frozenset({"group", "label", "icon"}),
            mandatory_fields=frozenset({"group", "label"}),
            valueless_fields=frozenset({"group"}),
            assets={"icon": "weather"},
        ),
    )


#: Phase 1 — the likelihood of rain, and the heading. It holds no session, so no floor and no
#: variant arise: one template serves every format.
WEATHER_P1_CATALOGUE = FieldCatalogue(
    mandatory=_WEATHER_HEADING_MANDATORY | {"rain_probability"},
    optional=_WEATHER_HEADING_OPTIONAL,
    assets={"track_flag": "flag"},
)

#: Phase 2 — one weather type per session. ``rain_probability`` is optional from here on: the
#: value is phase 1's, carried forward because XIV.7 (v4.7.0) admits a value the text path
#: published in **another message of the same flow**.
WEATHER_P2_CATALOGUE = FieldCatalogue(
    mandatory=_WEATHER_HEADING_MANDATORY,
    optional=_WEATHER_HEADING_OPTIONAL | {"rain_probability", "rain_probability_group"},
    assets={"track_flag": "flag"},
    rows=_p2_sessions(_PLAIN_SESSIONS_FLOOR),
)

WEATHER_P2_SPRINT_CATALOGUE = FieldCatalogue(
    mandatory=_WEATHER_HEADING_MANDATORY,
    optional=_WEATHER_HEADING_OPTIONAL | {"rain_probability", "rain_probability_group"},
    assets={"track_flag": "flag"},
    rows=_p2_sessions(_SPRINT_SESSIONS_FLOOR),
)

#: Phase 3 — the sequence drawn within each session.
WEATHER_P3_CATALOGUE = FieldCatalogue(
    mandatory=_WEATHER_HEADING_MANDATORY,
    optional=_WEATHER_HEADING_OPTIONAL | {"rain_probability", "rain_probability_group"},
    assets={"track_flag": "flag"},
    rows=_p3_sessions(_PLAIN_SESSIONS_FLOOR, _PLAIN_SLOTS_FLOOR),
)

WEATHER_P3_SPRINT_CATALOGUE = FieldCatalogue(
    mandatory=_WEATHER_HEADING_MANDATORY,
    optional=_WEATHER_HEADING_OPTIONAL | {"rain_probability", "rain_probability_group"},
    assets={"track_flag": "flag"},
    rows=_p3_sessions(_SPRINT_SESSIONS_FLOOR, _SPRINT_SLOTS_FLOOR),
)

#: The notice of a round of the mystery format — the first image type in the module that
#: exists for a **kind of record** rather than for an output aspect (XIV.3, v4.7.0).
#:
#: Such a round conceals its track, runs no session and computes no forecast. The notice says
#: a forecast is not coming, so it shares with a forecast only the heading fields naming who
#: and when. It is not an exemption: it draws every field of its own catalogue in full.
WEATHER_MYSTERY_CATALOGUE = FieldCatalogue(
    mandatory=frozenset({"division_name", "round_number"}),
    optional=frozenset(
        {
            "season_number",
            "season_number_group",
            "division_tier",
            "division_tier_group",
        }
    ),
)


# ── Verdicts ──────────────────────────────────────────────────────────────
#
# One template serves the three kinds of verdict — a post-race penalty, an appeal, and an
# attendance sanction the bot enforced itself — the three being told apart by the text placed
# on `verdict_stage` and `session_name` alone. They differ in no *field*, so they are one
# image type and one slot rather than siblings (XIV.10, v4.8.0).
#
# It declares **no collection**: one decision, upon one driver, at one round. Only
# WEATHER_MYSTERY_CATALOGUE had reached that before it, and the two arrive from opposite
# directions — the notice because it says a forecast is not coming and so has almost nothing
# to draw, the verdict because its subject is singular.
#
# `session_name` is **mandatory and may be drawn empty**. An attendance sanction pertains to
# no session, so the data determine its value to be nothing, which XIV.3 holds is determined
# rather than missing: the field is emptied, its group removed, and no notice arises. The
# template must still declare it, which is what keeps it mandatory. The label "Attendance
# Sanction" stands on `verdict_stage` alone and is never written here as well.
#
# `driver_flag` carries XIV.4's configured-absence suppression, justified per field: a league
# that switched nationality collection off draws no flag and is told nothing, while a league
# that collects it and holds none for this driver is told. The distinction is the lineup's and
# is carried, not re-derived.
_VERDICTS_MANDATORY = frozenset(
    {
        "division_name",
        "round_number",
        "session_name",
        "verdict_stage",
        "driver_name",
        "penalty",
        "description",
        "justification",
    }
)

_VERDICTS_OPTIONAL = frozenset(
    {
        "season_number",
        "season_number_group",
        "division_tier",
        "division_tier_group",
        "race_name",
        "session_name_group",
        "team_name",
        "team_name_group",
        "driver_flag",
        "team_image",
    }
)

VERDICTS_CATALOGUE = FieldCatalogue(
    mandatory=_VERDICTS_MANDATORY,
    optional=_VERDICTS_OPTIONAL,
    assets={"driver_flag": "flag", "team_image": "team"},
)


#: Template column → its catalogue. Fifteen entries, one per image type; all fifteen — the
#: calendar, the lineup, the two results types, the two standings types, the two attendance
#: types, the six weather types and the verdict — are populated.
CATALOGUES: dict[str, FieldCatalogue] = {
    column: FieldCatalogue() for column in TEMPLATE_COLUMNS
}
CATALOGUES["calendar_template"] = CALENDAR_CATALOGUE
CATALOGUES["lineup_template"] = LINEUP_CATALOGUE
CATALOGUES["results_qualifying_template"] = RESULTS_QUALIFYING_CATALOGUE
CATALOGUES["results_race_template"] = RESULTS_RACE_CATALOGUE
CATALOGUES["standings_drivers_template"] = STANDINGS_DRIVERS_CATALOGUE
CATALOGUES["standings_constructors_template"] = STANDINGS_CONSTRUCTORS_CATALOGUE
CATALOGUES["attendance_template"] = ATTENDANCE_CATALOGUE
CATALOGUES["rsvp_template"] = RSVP_CATALOGUE
CATALOGUES["weather_p1_template"] = WEATHER_P1_CATALOGUE
CATALOGUES["weather_p2_template"] = WEATHER_P2_CATALOGUE
CATALOGUES["weather_p2_sprint_template"] = WEATHER_P2_SPRINT_CATALOGUE
CATALOGUES["weather_p3_template"] = WEATHER_P3_CATALOGUE
CATALOGUES["weather_p3_sprint_template"] = WEATHER_P3_SPRINT_CATALOGUE
CATALOGUES["weather_mystery_template"] = WEATHER_MYSTERY_CATALOGUE
CATALOGUES["verdicts_template"] = VERDICTS_CATALOGUE


def sibling_keys(template_key: str) -> list[str]:
    """The template keys that are **siblings** of *template_key* (XIV.3, widened at v4.6.0).

    Two image types are siblings where **either** holds:

    * they draw one **aspect** — qualifying and race results, driver and constructor
      standings, the six forecasts;
    * they are the several graphics of one **source module**, whatever they draw. The
      attendance sheet and the check-in call share not one field and are siblings all the
      same.

    Common content is what makes a swapped file *plausible*; common provenance is what makes
    it *possible*, and only the latter is the test. The fault the rule catches is a file in
    the wrong slot, and the files a league is likeliest to swap are the ones it authors in one
    sitting and configures with two adjacent commands.

    **The union matters.** Restricting the relation to the source module alone would lose
    nothing today but says the wrong thing; taking the aspect alone is what v4.6.0 widened,
    the attendance pair sharing a module and not an aspect.

    An aspect whose source module is ``None`` — the calendar and the lineup, drawn from the
    foundational concepts rather than from an optional module — contributes **no** module
    relation. Without that guard the two would become siblings of each other, which the
    constitution explicitly denies: a calendar template declaring a lineup's field states
    nothing about a calendar.
    """
    from models.image_constants import ASPECT_SOURCE_MODULE, ASPECT_TEMPLATES

    aspect_of = {key: aspect for aspect, keys in ASPECT_TEMPLATES.items() for key in keys}
    own_aspect = aspect_of.get(template_key)
    if own_aspect is None:
        return []

    siblings: set[str] = set(ASPECT_TEMPLATES[own_aspect])

    own_module = ASPECT_SOURCE_MODULE.get(own_aspect)
    if own_module is not None:
        for aspect, keys in ASPECT_TEMPLATES.items():
            if ASPECT_SOURCE_MODULE.get(aspect) == own_module:
                siblings |= set(keys)

    siblings.discard(template_key)
    return sorted(siblings)


def _canonical(name: str) -> str:
    """An id with every all-digit segment replaced by ``#``.

    ``row_1_round_2_points`` becomes ``row_#_round_#_points``, so one ordinal-bearing id
    compares against a catalogue's declaration without the catalogue having to enumerate its
    members (XIV.11 forbids the enumeration). ``round_format`` carries no digit segment and is
    left alone, which is what keeps a top-level field from colliding with a collection whose
    prefix it happens to share.
    """
    return "_".join("#" if part.isdigit() else part for part in name.split("_"))


def _canonical_ids(catalogue: FieldCatalogue) -> set[str]:
    """Every id *catalogue* addresses, in canonical form.

    Covers the **whole** addressable surface and not the rows alone: top-level fields, the
    row collection, the column collection, and anything nested inside either. Two sibling
    catalogues may overlap in their top-level fields and share no collection at all — which is
    exactly the attendance pair — so a comparison restricted to rows would miss the fault it
    exists to catch.

    Keyed and singleton collections are deliberately omitted. Only the lineup declares them,
    and the lineup has no sibling under any relation: it is alone in its aspect and its source
    module is ``None``.
    """
    ids: set[str] = set(catalogue.mandatory) | set(catalogue.optional)

    def walk(spec, stem: str) -> None:
        if spec is None:
            return
        member = f"{stem}{spec.prefix}_#"
        ids.add(member)
        for suffix in spec.fields:
            ids.add(f"{member}_{suffix}")
        walk(getattr(spec, "nested", None), f"{member}_")

    walk(catalogue.rows, "")
    walk(catalogue.columns, "")

    # XIV.2 lets **any** field be wrapped in a group named for it, and the rule is general
    # rather than per catalogue: a template declaring `season_number` may wrap it in
    # `season_number_group` whatever type it is. Deriving the group form here rather than
    # requiring each catalogue to enumerate it is what keeps a legitimate wrapper from
    # reading as a sibling's field — the standings catalogues list their groups explicitly
    # and the results ones do not, and the shipped results templates carry them.
    ids |= {f"{name}_group" for name in set(ids)}
    return ids


def sibling_row_fields(template_key: str) -> set[str]:
    """Row field **suffixes** belonging to a sibling image type and not to this one.

    Retained for reporting and for the per-row half of the check. The whole-surface
    comparison is :func:`sibling_fields_declared`, which this no longer bounds.
    """
    own = CATALOGUES.get(template_key)
    if own is None or own.rows is None:
        return set()

    foreign: set[str] = set()
    for key in sibling_keys(template_key):
        catalogue = CATALOGUES.get(key)
        if catalogue is None or catalogue.rows is None:
            continue
        if catalogue.rows.prefix != own.rows.prefix:
            continue
        foreign |= set(catalogue.rows.fields) - set(own.rows.fields)
    return foreign


def sibling_owners(template_key: str, ids: Iterable[str]) -> list[str]:
    """The sibling template keys that claim any of *ids*, sorted.

    Lets a refusal name the file a manager has actually supplied — "these belong to the
    check-in call template" — rather than a fixed phrase. XIV.9.2 requires a reason
    distinguishable from every other layer's, and "the other kind of results template" is not
    one when the slot is an attendance sheet.
    """
    own = CATALOGUES.get(template_key)
    if own is None or own.is_empty:
        return []

    wanted = {_canonical(name) for name in ids}
    own_ids = _canonical_ids(own)

    owners: list[str] = []
    for key in sibling_keys(template_key):
        catalogue = CATALOGUES.get(key)
        if catalogue is None or catalogue.is_empty:
            continue
        if wanted & (_canonical_ids(catalogue) - own_ids):
            owners.append(key)
    return owners


def row_crop_fields(
    declared: Iterable[str], *, drawn: int, capacity: int, prefix: str = "row"
) -> dict:
    """The crop keywords for a template drawn as a list of rows (XIV.2, v7.1.0).

    Splatted into a :class:`FillSpec` by every image type whose rows a division may fill
    only partly, so the rule lives here and not once per service::

        spec = FillSpec(..., **row_crop_fields(declared, drawn=len(rows), capacity=cap))

    Three answers, and each of them can be "no":

    * ``crop`` — the crop point of the **last row the data filled**, or None where the
      template declares no such point. A template authored before v7.1.0 declares none, and
      renders at its full height exactly as it did.
    * ``crop_is_final`` — whether that row is also the last the *template* declares, which
      is the only case in which the crop point is expected to stand at the canvas height.
      A division drawn shorter crops higher by design and must raise nothing.
    * ``footer`` — the band beneath the rows, carried up by the crop rather than cut off.

    **A graphic with no rows at all is not cropped.** There is no ``row_0`` crop point, and
    cropping at row 1's would draw one empty row band — worse than the full canvas, and
    stating something untrue about a division that has nobody in it.
    """
    names = set(declared)
    if drawn < 1:
        return {}

    crop_id = f"{prefix}_{drawn}_{_ROW_CROP_FIELD}"
    return {
        "crop": crop_id if crop_id in names else None,
        "crop_is_final": drawn == capacity,
        "footer": FOOTER_GROUP_FIELD if FOOTER_GROUP_FIELD in names else None,
    }


def sibling_fields_declared(template_key: str, declared: Iterable[str]) -> list[str]:
    """Every id in *declared* that a **sibling** catalogue addresses and this one does not.

    A template declaring one is the wrong file in that slot, and XIV.3 makes it a fatal fault
    detected at the moment the template is named — rendering it would draw one session's
    columns under another's headings, or a check-in call's fields on an attendance sheet.

    An id belonging to **no** catalogue is not returned and is not a fault: a hand-authored SVG
    carries identifiers on every node it holds, and only the ones a catalogue claims are
    fields.

    Returns the offending ids so a report can name them; empty where the template declares
    none, which is the ordinary case.
    """
    own = CATALOGUES.get(template_key)
    if own is None or own.is_empty:
        return []

    own_ids = _canonical_ids(own)

    foreign: set[str] = set()
    for key in sibling_keys(template_key):
        catalogue = CATALOGUES.get(key)
        if catalogue is None or catalogue.is_empty:
            continue
        foreign |= _canonical_ids(catalogue) - own_ids

    if not foreign:
        return []

    found: set[str] = {name for name in declared if _canonical(name) in foreign}
    return sorted(found)


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
        capacity = catalogue.singleton_capacity(root)
    except CapacityError as exc:
        return str(exc)
    if not capacity or would_hold <= capacity:
        return None
    return (
        f"that would give the division {would_hold} reserve drivers, but the configured "
        f"lineup template declares {capacity} reserve "
        f"{'slot' if capacity == 1 else 'slots'}"
    )


def row_capacity_problem(template_key: str, root, would_hold: int) -> str | None:
    """Why *would_hold* members outgrow *template_key*'s row collection, or None.

    For the image types whose rows a **template** bounds (XIV.12): the attendance sheet's
    drivers, and any later type of the same shape. The count cannot be frozen into the
    catalogue — a league draws as many rows as its grid needs — so it is read from the file
    being checked, at the moment a command would change the data measured against it.

    XIV.12 requires overflow to be rejected at the **earliest** moment it can be detected, with
    the change unapplied. For a driver assignment that moment is the command, not the render:
    discovering it at a posting means the league has already lost the sheet.
    """
    catalogue = CATALOGUES.get(template_key)
    if catalogue is None or catalogue.rows is None or not catalogue.rows.is_derived:
        return None

    try:
        capacity = catalogue.rows.declared_capacity(root)
    except CapacityError as exc:
        # A template that cannot be counted is a fault of its own, reported where templates
        # are validated. It must not masquerade as an over-capacity here.
        del exc
        return None

    if would_hold <= capacity:
        return None
    return (
        f"the division would hold {would_hold} drivers but the configured "
        f"`{template_key}` declares {capacity} row(s)"
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

    The **lineup** is excluded twice over. Its ``rows`` capacity is *derived* from the
    template rather than fixed in code, so the filter below drops it; and its rows count
    **teams**, not drivers, so guarding a driver placement on it would refuse for the
    wrong reason. Its reserve seats are guarded at driver assignment by
    ``reserve_capacity_problem`` below — a count of reserve drivers, not of every seated
    driver.
    """
    return {
        key: catalogue.rows.capacity
        for key, catalogue in CATALOGUES.items()
        if catalogue.rows is not None
        and catalogue.rows.capacity is not None
        and catalogue.rows.capacity > 0
    }
