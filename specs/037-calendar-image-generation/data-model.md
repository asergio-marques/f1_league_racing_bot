# Phase 1 Data Model: Calendar Image Generation

Two persisted changes, one extended code constant, and one new in-memory shape. Nothing else in the
schema is touched.

---

## Persisted

### `divisions.calendar_message_id` — new column

| Property | Value |
|---|---|
| Type | `TEXT`, nullable |
| Default | `NULL` |
| Migration | `src/db/migrations/040_calendar_message_id.sql` |
| Model | `src/models/division.py` → `Division.calendar_message_id: str | None = None` |

Holds the id of the message carrying this division's calendar in its `calendar_channel_id` channel.

**Why it is needed**: an attachment cannot be introduced into a message already posted, so the image
flow replaces the calendar message rather than editing it, and must know which message to delete. The
textual calendar has been posted once and never replaced, so no id was ever held.

**Written by**: every calendar posting, graphic **or** textual, so the two flows never disagree about
which message is the calendar. It sits beside `lineup_message_id`, added at constitution v2.8.0, and
follows its read/write pattern in `season_service`.

**Cleared when**: the message it names is deleted and no replacement is produced. A stale id — the
message having been deleted in Discord by hand — is not an error: the replacement is posted and the
id overwritten.

> **Migration note.** `Division` is loaded through `season_service._row_to_division`, which guards
> each optional column with an `in keys` check. The new column follows that pattern, so a database
> that has not yet run migration 040 continues to load divisions rather than raising.

---

## Extended code constant

### `CATALOGUES["calendar_template"]` — the first populated entry

Declared in `src/models/image_catalogues.py`. Full field-by-field contract in
[contracts/calendar-catalogue.md](./contracts/calendar-catalogue.md); the shape is:

```text
FieldCatalogue(
    mandatory = { division_name }
    optional  = { season_number, division_tier }
    assets    = { }                       # the round image is per-member, declared on the RowSpec
    rows      = RowSpec(
        prefix           = "round",
        capacity         = None,          # ← derived from the template (R1)
        fields           = { group, image, number, country_name, race_name,
                             track_name, format, date, time, vertical_crop_point },
        mandatory_fields = { number, country_name, race_name, date, vertical_crop_point },
    )
)
```

### `RowSpec` — two changes

| Change | Detail |
|---|---|
| `capacity: int \| None` | `None` means "count what the template declares". An integer keeps today's behaviour for every unpopulated type. |
| `assets: dict[str, str]` | New — per-member field suffix → asset class, so `image` → `track`. Previously only whole-image fields could carry an asset class, which no per-row image needed until now. |

New methods, each taking the parsed root when the capacity is derived:

- `declared_capacity(root) -> int` — counts contiguous members from 1; raises on a gap.
- `capacity_for(root) -> int` — the integer capacity where fixed, else `declared_capacity(root)`.

`FieldCatalogue.all_mandatory_ids()` and `all_known_ids()` gain an optional `root` parameter, used
only when the capacity is derived. Existing callers that pass nothing continue to work against fixed
capacities, which is every other type.

**Constitution note (XIV.10)**: this remains *one* catalogue object, consulted by both the fill
pipeline and validity Layer 2 with the same root. Two lists that could disagree are not a catalogue,
and deriving the capacity does not create a second list — it makes both readers ask the same question
of the same file.

---

## In-memory

### `CalendarDrawing` — what one division's calendar resolves to

Built by `image_calendar_service`, consumed by `calendar_post_service`. Not persisted.

| Field | Meaning |
|---|---|
| `division_id`, `division_name`, `division_tier` | Heading values |
| `season_number` | Heading value |
| `rounds: list[CalendarRound]` | Ordered by round number, ascending |
| `capacity` | Slots the configured template declares |
| `final_round_index` | The ordinal whose crop point the image is cut at |
| `rounds_beside_final: list[int]` | Ordinals standing beside the final round, removed by `_group` |
| `overflow: list[int]` | Division rounds beyond the template's capacity — non-empty means fatal |

### `CalendarRound`

| Field | Meaning |
|---|---|
| `ordinal` | The `<x>` its fields are addressed by; equals the round number |
| `number` | Human-readable round number as text |
| `format_label` | "Sprint" / "Endurance" / "Mystery", or empty for a normal round |
| `date_text`, `time_text` | Rendered through the configured format and zone. `time_text` is always populated: a round records date and time as one moment, by design (see research R5) |
| `country_name`, `race_name`, `track_name` | From the track, or the mystery literals |
| `image_datum` | The datum the track image resolves from — the track name, or "Mystery" |

**Why a resolved intermediate rather than building a `FillSpec` directly**: the crop decision needs
the whole round list before any field is written, and the overflow check must fail before anything is
drawn. Resolving first, then projecting to a `FillSpec`, makes both testable without a template and
keeps the fatal checks ahead of the expensive ones.

---

## Relationships

```text
Season ──< Division ──< Round ──> Track
             │                      (joined by Round.track_name; absent for a mystery round)
             ├── calendar_channel_id   (existing, v2.8.0)
             ├── calendar_message_id   (NEW)
             └── lineup_message_id     (existing, v2.8.0)
```

The calendar reads Season, Division, Round and Track. It **writes** only
`divisions.calendar_message_id`. No round, track, driver or team record is modified — consistent with
the module being a consumer (Principle X).

---

## Validation rules

Drawn from the spec's requirements; each is enforced at the moment named.

| Rule | Moment | Outcome |
|---|---|---|
| Template declares ≥ 1 round | Template named; season review; pre-render | Fatal |
| Round numbering contiguous from 1 | Template named; season review; pre-render | Fatal |
| Every mandatory round field present on each declared round | Template named; season review; pre-render | Fatal |
| Every division-independent mandatory field present | Template named; season review; pre-render | Fatal |
| Division round count ≤ template capacity | Season review (warning); round-add (refusal); pre-render (fatal) | Per column |
| Division holds ≥ 1 round | Pre-render | Fatal |
| Mandatory field value determinable | Pre-render | Fatal |
| Track image resolves, or a fallback exists | Pre-render | Fatal without fallback; notice with |
| Final declared round's crop point at the declared height | Pre-render | Notice (FR-026) |

The three-moment split follows constitution XIV.9: a check is made at the earliest moment its data
exists and repeated before the render. Season review can only compare against the season's most
demanding division, so its capacity divergence is a **warning**; the same divergence is **fatal** once
a specific division is being drawn.
