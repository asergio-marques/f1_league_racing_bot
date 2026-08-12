# Phase 1 Data Model: Template Verification & Graphic Conventions

Most of this feature is behaviour, not storage. One persisted enum widens; everything else is an
in-memory type or a code constant.

## Persisted

### `image_render_notices.notice_kind` — widened

The append-only notice record from 035 (`RenderNotice`, Constitution XIV.4) gains two kinds:

| Kind | Raised when |
|---|---|
| `FONT_SUBSTITUTED` | *(existing)* the host does not carry the font the template names |
| `WRAP_TRUNCATED` | *(existing)* a wrapping field hit its size floor and was cut |
| `INLINE_SIZE_TRUNCATED` | *(existing)* a single-line field was cut to its declared `inline-size` |
| `ASSET_FALLBACK_USED` | **new** — a datum's own file was absent and `fallback.svg` stood in (FR-043) |
| `OPTIONAL_FIELD_EMPTIED` | **new** — an optional field's value could not be determined (FR-013) |

`ASSET_PLACEHOLDER_USED`, added to the constitution earlier in this session, was never
implemented and is not introduced. The column is TEXT with no database-level constraint, so this
is a documentation and code change, not a migration.

**No new table.** Catalogues, capacities and asset classes are code constants (Constitution
XIV.10), not configuration: they describe what an image type *is*, and a league cannot vary them.

## Code constants

### `FieldCatalogue` — one per image type

Declared in `src/models/image_catalogues.py`, one entry per image type, keyed by the same
template column names `TEMPLATE_COLUMNS` already uses.

| Attribute | Type | Meaning |
|---|---|---|
| `mandatory` | set of field names | Absent from template, or undeterminable at generation → problem (FR-011, FR-012) |
| `optional` | set of field names | Absent → fine; undeterminable → emptied or `_group` removed (FR-013) |
| `assets` | field name → asset class | Which configured directory an image field resolves in (FR-042) |
| `rows` | `RowSpec` or `None` | The repeating collection this type draws, if any |

**Every one of the fifteen is declared empty in this feature.** Populating a catalogue is what a
later image-type session does, and is the *only* thing it must do to bring the mandatory-field
checks, Layer 2 and the capacity guard to life for that type.

### `RowSpec`

| Attribute | Type | Meaning |
|---|---|---|
| `prefix` | str | Always `row` under the current convention; kept explicit so a template with two tables is expressible later without a format change |
| `capacity` | int | Slots the template provides (Constitution XIV.12) |
| `fields` | set of field names | The per-row field suffixes, from which `row_<x>_<field>` is constructed |

A catalogue expresses a collection as prefix + capacity + suffixes, never as an enumerated list
of `row_1_position`, `row_2_position`, … (Constitution XIV.11).

### `ASSET_CLASS_DIRECTORIES`

Asset class → the `ImageConfig` column naming its directory. Derived from the seven entries
already in `ASSET_DIRECTORIES`; added so a catalogue can name a class without naming a column.

## In-memory types

### `FieldIndex`

Built once per parsed template. Resolves a field *name* to an element.

| Member | Behaviour |
|---|---|
| `by_id` | every `@id` in the document |
| `by_label` | `inkscape:label` of `<g inkscape:groupmode="layer">` nodes only (research R1) |
| `resolve(name)` | `by_id` first, then `by_label`; `None` if neither (FR-018–FR-020) |
| `group_for(name)` | the element whose id or label is `<name>_group`, if declared (FR-022) |

Supersedes `svg_document.index_by_id`. Rebuilt after any structural change to the tree, exactly
as `index_by_id` is today.

### `Problem`

A fatal outcome. Structured rather than a bare string, because FR-006, FR-008 and FR-028 all
require naming the individual template and the specific fault, and the same problem is rendered
into three different surfaces (a command rejection, a season review line, a log entry).

| Attribute | Meaning |
|---|---|
| `kind` | one of the classes below |
| `template_key` | the individual template at fault — never a group (FR-008) |
| `field_id` | the field concerned, where the fault has one |
| `detail` | the human sentence, in the module's own words |

Kinds: `EXTENSION` (FR-001), `NOT_FOUND` (FR-002), `NOT_SVG` (FR-003, carrying a named parse
fault per research R3), `MISSING_MANDATORY_FIELD` (FR-004, FR-012), `UNRESOLVED_VALUE` (FR-011),
`UNKNOWN_FIELD`, `ASSET_UNRESOLVED` (FR-044), `CAPACITY_EXCEEDED` (FR-028), `RASTERISER`,
`UNKNOWN_IMAGE_TYPE`.

`UNKNOWN_IMAGE_TYPE` is the odd one: a render was asked for a type the module does not know,
which no league can cause — it is a caller defect. It is a `Problem` rather than an exception so
that **every** failure path returns uniformly and nothing escapes as a traceback into a Discord
surface. Its `detail` names the type asked for; the caller that produced it is identified in the
application log, since there is nothing a user could do about it.

### `PostingOrigin`

An enum with two members, required at the render-and-post entry point (research R6).

| Member | On a problem |
|---|---|
| `COMMANDED` | Reject; post nothing; tell the caller what is at fault (FR-030) |
| `SCHEDULED` | Fall back to the text output (FR-029) |

Covers horizon, scheduler and startup postings under `SCHEDULED`.

### `RenderOutcome` — unchanged in shape

Already carries `png_paths`, `problem` and `notices` with the invariant that `png_paths` is empty
whenever `problem` is set. `problem` becomes a `Problem` rather than a string; the invariant is
what FR-014 requires and is retained.

## Relationships

```text
ImageConfig ──1:15── template filename columns
     │                        │
     │                        ▼
     │                 ValidityReport ◄── ValidityLayer (1 Resolution, 2 Catalogue)
     │                        │                              │
     │                        │                              ▼
     │                        │                      FieldCatalogue ──0:1── RowSpec
     │                        │                              │
     ├──1:7── asset directory columns ◄────── asset class ───┘
     │
     ▼
AspectStatus (8) ──── rolls up ValidityReports per aspect

Template (SVG) ──parsed──► FieldIndex ──resolve(name)──► element
                                             │
                                             ▼
                                     fill operations ──► RenderOutcome
                                                          ├── Problem (0..1)
                                                          └── RenderNotice (0..n) ──► persisted
```

## Validation rules, by source requirement

| Rule | Source | Applied at |
|---|---|---|
| Filename ends `.svg`, case-insensitively | FR-001 | config command, before any filesystem access |
| File exists at directory + filename | FR-002 | config command; season approve; Layer 1 |
| File parses, named fault on failure | FR-003, FR-046 | config command; season approve; Layer 1 |
| Root declares a canvas | XIV.1 | Layer 1 *(existing)* |
| Every mandatory field present in template | FR-004, FR-012 | config command; season approve; Layer 2 |
| Every mandatory value determinable | FR-011 | generation only — needs the data |
| Data supplies no field the template lacks | XIV.3 | generation only |
| Row data ≤ declared capacity | FR-028 | `assign_driver`; generation |
| Configured path stays inside the project root | *(existing)* | `resolve_within_project_root` |

## State transitions

**Template configuration** is the only state machine this feature changes.

```text
                    ┌──────────────────────────────────────┐
                    │  stored filename (the current value) │
                    └──────────────────────────────────────┘
                                    │
        /images template <kind> ────┤
                                    ▼
                        ┌───────────────────────┐
                        │ candidate ImageConfig │   (not persisted)
                        └───────────────────────┘
                                    │
                   FR-001 … FR-004 evaluated on the candidate
                        ┌───────────┴───────────┐
                     valid                   invalid
                        │                       │
                        ▼                       ▼
                 write; log the         reject; name the fault;
                 change; confirm        stored value UNCHANGED (FR-005)
```

Today the write happens before the diamond. Moving it after is the whole of FR-005.
