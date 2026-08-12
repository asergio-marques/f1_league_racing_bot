# Contract: The Calendar Field Catalogue

The catalogue is the contract between a league manager authoring an SVG by hand and the bot filling
it. It is declared once in `src/models/image_catalogues.py` under the key `calendar_template`, and is
the same object read by the fill pipeline and by validity Layer 2 (constitution XIV.10).

The normative field list is the wip-spec's § "Calendar image generation". This document states the
catalogue's *shape* — how those fields are expressed as a code constant — not the rules themselves.

## Whole-graphic fields

| Field id | Class | Operation | Source |
|---|---|---|---|
| `division_name` | **Mandatory** | Text fill | `Division.name` |
| `season_number` | Optional | Text fill | The season's number |
| `division_tier` | Optional | Text fill | `Division.tier` |

## The `round` collection

Declared as a `RowSpec`, never as an enumerated id list (XIV.11).

- **Prefix**: `round` — so a member's fields are `round_<x>_<field>` and its group `round_<x>_group`.
- **Capacity**: `None`, meaning derived from the template. The number of slots is whatever the league
  drew; see [research.md § R1](../research.md).
- **Indexing**: from 1, unpadded, contiguous. `<x>` equals the round's own number.

| Field suffix | Class | Operation | Source |
|---|---|---|---|
| `number` | **Mandatory** | Text fill | `Round.round_number` |
| `country_name` | **Mandatory** | Text fill | `Track.country`, or "Mystery" |
| `race_name` | **Mandatory** | Text fill | `Track.gp_name`, or "Mystery GP" |
| `date` | **Mandatory** | Text fill | `Round.scheduled_at`, configured date format |
| `vertical_crop_point` | **Mandatory** | Crop reference | The node whose `y` the cut is taken at |
| `track_name` | Optional | Text fill | `Track.name`, or "Mystery" |
| `format` | Optional | Text fill | Format label, empty for a normal round |
| `time` | Optional | Text fill | `Round.scheduled_at`, configured time format and zone |
| `image` | Optional | Image fill, asset class `track` | Datum: `Track.name`, or "Mystery" |
| `group` | Optional | Removal target | Removed whole when the round is not drawn |

## Capacity derivation

```text
declared_capacity(root):
    ordinals ← every n where the template declares any id matching  round_<n>_*
    if ordinals is empty                     → fatal: template declares no round
    if ordinals ≠ {1 … max(ordinals)}        → fatal: gap in the numbering
    return max(ordinals)
```

The scan reads through `FieldIndex`, so a round addressed by a **layer label** rather than an `@id`
counts exactly as one addressed by an id (XIV.2).

## What a template author must hold to

The obligations below are what the catalogue enforces. They are stated for a manager in the README's
template section; this table is the machine-checkable form.

| Obligation | Checked at |
|---|---|
| Declare `division_name` | Template named |
| Declare at least one round | Template named |
| Number rounds contiguously from 1 | Template named |
| Give every declared round all five mandatory round fields | Template named |
| Put the last round's crop point at the declared canvas height | Pre-render (notice only) |
| Draw nothing below a round's crop point, and let no element span one | Not machine-checked |
| Lay rounds across and then down, never down one column then the next | Not machine-checked |

The last two cannot be verified from the file — an element's visual role is not in its geometry — and
are the reason `/images test calendar` exists. A manager reads the PNG.

## Compatibility

Populating this entry activates, for the calendar alone and by data rather than by code:

- the pre-render mandatory-field and capacity checks in `image_render_service._verify_against_data`;
- validity **Layer 2**, whose `applies_to` returns true once the catalogue is non-empty;
- the round-capacity guard on round-add (new; see [research.md § R3](../research.md)).

The fourteen other catalogues stay empty and every one of those checks continues to pass vacuously
for them, which is what constitution XIV.9's "no silent pass" requires: they keep reporting depth 1.
