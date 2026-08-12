# Contract: Field Resolution

How a field name becomes an element, and what may be done to it. Binds every generation utility
written from here on.

## Resolution order

Given a field name, resolution consults in order and stops at the first hit:

1. A node whose `@id` equals the name.
2. A `<g inkscape:groupmode="layer">` whose `inkscape:label` equals the name.

Where both exist and are different nodes, **1 wins** (FR-020). Where neither exists, the field is
unresolved, and whether that is fatal depends on its classification (FR-012, FR-013).

Only layer groups are indexed by label. An `inkscape:label` on an ordinary object is not an
address — Inkscape writes those without the manager choosing them (research R1).

```text
resolve("driver_name")
  ├─ id index      → <text id="driver_name">           ✓ used
  └─ (not consulted)

resolve("driver_name")            # no such id
  ├─ id index      → miss
  └─ label index   → <g inkscape:groupmode="layer"
                        inkscape:label="driver_name">  ✓ used

resolve("driver_name")            # both present, different nodes
  ├─ id index      → <text id="driver_name">           ✓ used
  └─ label index   → <g … label="driver_name">         ignored
```

## The six operations

A generation performs these upon a field and **no others** (FR-015):

| Operation | Applies to | Effect |
|---|---|---|
| Text fill | `<text>` / `<tspan>` | Replaces the element's text content |
| Image fill | any element carrying an href | Rewrites `xlink:href` and `href` to the resolved asset |
| Recolour | any element | Merges `fill:` into the element's **inline** `style` |
| Truncate | `<text>` declaring `inline-size` only | Cuts at a word boundary, appends an ellipsis, raises a notice |
| Wrap | `<text>` declaring `shape-inside` | Breaks into `<tspan>` lines against the referenced rectangle |
| Empty or remove | the field, or its `_group` | Clears the text, or deletes the node and its subtree |

**Recolour is not a fill.** A recoloured field must still be filled; recolouring does not mark it
addressed (FR-017). This is what keeps the unresolved check honest.

**Recolour writes inline.** Merged into the existing `style`, never as a presentation attribute
(which loses to the template's own stylesheet) and never as a wholesale `style` replacement
(which discards what the template declared on the same element) — FR-016.

Vertical crop is **not** a general operation. It belongs to the calendar image type and is
specified there.

## Text bounds

Decided by what the field declares, not by what the data is:

| Declares | Behaviour |
|---|---|
| `inline-size` only | Truncate at a word boundary + ellipsis; notice naming the field (FR-036). A single word wider than the room is broken within itself (FR-037) |
| `shape-inside` (with or without `inline-size`) | Wrapping field: wrapped and reduced per the verdicts graphic (FR-038, and A-002 for the `shape-inside`-only case) |
| Neither | One line of unbounded width; may overrun what stands beside it (FR-039) |

## Removable groups

Any field may be wrapped in a group named `<field>_group`. The group may itself be addressed by
id or by layer label, on the same terms as any field.

```text
<g id="sanctions_group">          ← removed entire when the value is absent
  <text id="sanctions_label">Sanctions</text>
  <rect id="sanctions_plate"/>
  <text id="sanctions">…</text>   ← left untouched; it goes with the group
</g>
```

| Situation | Behaviour |
|---|---|
| Group declared, field would be emptied or removed | Remove the group entire; leave the field itself untouched (FR-023) |
| No group declared | Empty or remove the field alone (FR-024) |
| Group declared, field is filled | Group is never removed |
| Groups nested | Removing the outer takes the inner with it |

Removing a group **never resizes the canvas** (FR-026). A block that may vanish belongs where a
gap is survivable — that is the template author's responsibility, not the generator's.

A field taken off the canvas by a group removal is **not** unresolved (Constitution XIV.3).

## Repeating rows

| Element | Name |
|---|---|
| Field in row *x* | `row_<x>_<field>` |
| The row itself | `row_<x>` |
| The row's removable group | `row_<x>_group` |

Indexed from **1**, contiguous, **unpadded**: `row_1_position`, `row_10_position` (FR-021).

A catalogue declares a collection as prefix + capacity + field suffixes, and the code constructs
the names. A catalogue that enumerates `row_1_position, row_2_position, …` violates
Constitution XIV.11.

## Invariants a utility must not break

1. A utility never reaches into the tree directly; it goes through the resolver, so every
   operation inherits the label fallback at once.
2. A utility never constructs a row id by string concatenation of its own; it asks the catalogue's
   `RowSpec`, so a change to the convention is one change.
3. A utility never decides fatality. It reports; the caller classifies against the catalogue.
