# Contract: The Widened Sibling Relation and the Collection Floor

**Feature**: 041-attendance-image-generation
**Changes**: `src/models/image_catalogues.py`, `src/services/image_attendance_service.py`
**Normative source**: Constitution XIV.3 and XIV.12 (v4.6.0)

Two shared-behaviour changes. The first is the one place this feature touches machinery every image
type reads; the second is deliberately *not* shared, and this document says why.

---

## Part 1 — The sibling relation

### What it is today

```python
siblings = [key for keys in ASPECT_TEMPLATES.values()
            if template_key in keys for key in keys if key != template_key]
```

and the surface compared is `catalogue.rows.fields`, matched against `^<rows.prefix>_\d+_(.*)$`.

Together this reads: *siblings are two templates of one **aspect** that differ in their **rows***.
True of qualifying/race results and of the two standings. False here in both halves.

### Why it fails for attendance

| | Results / standings | Attendance |
|---|---|---|
| Relation | One aspect holds both templates | **Two aspects** (`attendance`, `rsvp`), one source module |
| Overlap | Their rows differ; top level is shared | **Their top level overlaps**; their collections are disjoint |

A sheet template declaring `round_format`, `round_date` or `session_1_name` is the wrong file in that
slot. Under today's code it is related to nothing, and even if it were, the row-prefix regex matches
none of those ids. It would pass every check and fail at the render.

### The widening

**Relation** — union of two sources:

```
siblings(template_key) =
      { k : k shares an ASPECT_TEMPLATES entry with template_key }
    ∪ { k : ASPECT_SOURCE_MODULE[aspect_of(k)] == ASPECT_SOURCE_MODULE[aspect_of(template_key)] }
    − { template_key }
```

Both maps already exist in `image_constants.py`. `ASPECT_SOURCE_MODULE` maps `attendance` and `rsvp`
to `"attendance"`.

**Surface** — the full addressable id set of each sibling catalogue, not `rows` alone: top-level
`mandatory` ∪ `optional`, plus every collection's constructed ids (`rows`, `rows.nested`, `columns`,
`keyed`, `singleton`). A foreign id is one the sibling addresses and this catalogue does not.

### Why the union, and not the source module alone

Replacing the aspect relation would **break the pairs that work today**. The two results templates and
the two standings templates are each one aspect; their source modules (`results`) are shared with types
that are not their siblings. The union preserves every relation that holds now and adds the one v4.6.0
ratified.

### What must stay false

- An id belonging to **no** catalogue is not a fault (XIV.3). A hand-authored SVG carries identifiers on
  every node; only ids a catalogue claims are fields.
- Two types of **different** modules are not siblings. A calendar template declaring a lineup's field
  states nothing about a calendar, and the constitution says so explicitly.

### Test obligations

1. `siblings("attendance_template")` contains `rsvp_template`, and the converse.
2. `siblings("results_qualifying_template")` still contains `results_race_template` and nothing new.
3. `siblings("calendar_template")` does **not** contain `lineup_template`.
4. A sheet template declaring `round_format` is refused, naming that id.
5. A check-in template declaring `row_1_driver_name` is refused, naming that id.
6. A template declaring `some_hand_authored_layer` is accepted.
7. Every existing template fixture that passes today still passes — the widening adds no fault to a
   file that renders now.

---

## Part 2 — The floor

### The rule

An image type MAY name a collection below whose emptiness the graphic has no subject. Drawing it
against empty data is a **problem**, rejected at the earliest moment it can be detected (XIV.12,
v4.6.0). Declared per image type and never inferred.

### Where it lives, and why not in the catalogue

**In the type's `resolve_drawing`**, raising the utility's data error — exactly as
`image_calendar_service.py:150` already does for a division holding no round:

> `the division \`{division_name}\` holds no round at all, so there is no …`

`image_attendance_service.resolve_drawing` raises the same shape when the composed sheet holds no
driver.

It is **not** a `RowSpec` field, for three reasons:

1. A catalogue declares **fields** — what the template must carry and how each is classified. A floor
   is a statement about **data**. A template declaring one row is perfectly valid; a division holding
   no driver is not drawable. Different inputs, different moments.
2. XIV.12 requires the floor to be checked against the concrete data at generation and forbids
   approximating it earlier (XIV.9). A catalogue field would be read by template checks that hold no
   data and could do nothing with it.
3. The calendar has carried this floor since 037 and implements it this way. A second mechanism for
   the second instance would leave two ways to express one rule, and migrating the calendar is not
   this increment's to own.

### Which collections have one

| Type | Collection | Floor |
|---|---|---|
| Calendar | rounds | One round — existing |
| Attendance sheet | rows (drivers) | One driver — **new** |
| Check-in graphic | sessions | **None** — a template declaring no session is valid (FR-004) |
| Results, standings, weather, verdicts | — | **None** — silent removal, unchanged |

### Ordering within `resolve_drawing`

The floor is raised **before any template measurement**, so that a division with no drivers reports
"holds no driver at all" rather than a capacity divergence against a template that is not at fault.

### Test obligations

1. A sheet drawn for a division holding no driver raises the data error naming the division.
2. The message names the division, not the template.
3. A division holding one driver against a ten-row template draws, with nine rows removed and no error.
4. A check-in template declaring no session draws the call without a session list and reports nothing.
5. The results and standings types are unaffected: an empty classification behaves exactly as it does
   on `main`.
