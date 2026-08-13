# Phase 1 Data Model: Standings Image Generation

One persisted column is added. Everything else below is an in-memory shape the resolution utility
builds and the fill pipeline consumes — declared here so the contracts can reference it.

## Persisted change

### `driver_standings_snapshots` — one column added

| Column | Type | Null | Meaning |
|---|---|---|---|
| `constructor_standings_message_id` | TEXT | yes | The id of the message carrying the **constructor** standings for this round and division |

Migration `041_constructor_standings_message_id.sql`.

**Where it is written.** On the row of the top-ranked driver, exactly as `standings_message_id`
already is. The existing column continues to hold the driver standings message — under the textual
flow, the one message carrying both championships; under the image flow, the driver graphic's message.

**Why a second column and not a second table.** The two ids are one-to-one with the round-and-division
the snapshot already keys, and the existing id has been carried this way since before the image module
existed. A table would add a join to every posting to hold one nullable integer.

**Existing rows** need no backfill: null is the correct value for a round posted by the textual flow,
and null is what every row holds today.

**Read path.** `_get_standings_message_id` gains a sibling, or a parameter naming which championship;
both read the top-ranked driver's row for the division and round.

---

## Declaration-module additions

These extend `src/models/image_catalogues.py`. Each is a *form* the constitution ratified at v4.5.0,
not a particular of the standings type — see [plan.md](./plan.md) § Complexity Tracking.

### `FieldCatalogue.columns: RowSpec | None`

A second top-level ordinal collection, beside `rows`. The standings grid's round headings
(`round_<z>_number`, `round_<z>_image`, `round_<z>_group`) are its members.

Distinct from `rows` because the two are counted against different data — rows against the
classification, columns against the division's calendar — and diverge in opposite directions
independently.

### `RowSpec.nested: NestedSpec | None`

Links a row to the collection repeating **within** it. `NestedSpec` already takes its parent's id as
a `stem`, so `row_3` is a valid stem today and `field_id("row_3", 7, "feature_race_result")` already
yields `row_3_round_7_feature_race_result`. Only the link is new.

### `NestedSpec.nested: NestedSpec | None`

The third level. `row_3_round_7` is a valid stem by the same construction, giving
`row_3_round_7_driver_2_name`.

### `optional_unit: bool` on `RowSpec` and `NestedSpec`

XIV.3 as amended at v4.5.0. Where true:

- a template declaring **no** member of the collection is not faulty, and every field of the
  collection and of anything nested inside it is skipped;
- where a template declares **any** member, `mandatory_fields` binds on every member declared.

This is the scope of a classification narrowing, not a third classification. `round_<z>_number` is
mandatory *within* an optional unit: required on every round drawn, owed by a template drawing none.

### Per-containing-member capacity

`NestedSpec.capacity` stays `None`, and the count arrives per containing member through the binding —
the seats configured for the team on that row. Over-declaration is trimmed for that member alone;
the fatal test is against the data drawn. XIV.12 as amended at v4.5.0.

---

## In-memory shapes

Built by `image_standings_service.resolve_drawing`, consumed by `build_fill_spec`. Pure — no Discord
object, no database handle.

### `StandingsEntry`

One row of either championship.

| Field | Meaning |
|---|---|
| `ordinal` | position in the classification, from 1; also the row id's ordinal, and what the position field is filled from (XIV.11) |
| `display_name` | the driver's name, or the team's; resolved by the wip-spec's conventions |
| `team_name` | drivers graphic only — the division team seating the driver **now**, the reserve team for a reserve |
| `team_slug` | normalised, for the team image |
| `nationality` | drivers graphic only; `None` where none is recorded |
| `points` | the total from the snapshot |
| `movement` | a `Movement`, or `None` where it cannot be determined |
| `cells` | round ordinal → `RoundCells` |

### `Movement`

The three derived columns. Produced by `standings_service.derive_movement` (R4), never by the image
utility.

| Field | Meaning |
|---|---|
| `gap_to_leader` | leader's points less this entry's, rendered with a leading minus; empty for the leader |
| `previous_position` | the position held after the reference round |
| `change` | positions separating the two, unsigned; `0` where unchanged |
| `direction` | `gained` / `lost` / `unchanged` — the marker's asset datum |

`None` for the whole record where the position change cannot be determined: no earlier round holds
standings at all, or this entry is absent from the one that does. The row's
`position_change_group` is then removed, or its number emptied and marker removed where no group is
declared, and the previous position emptied. Raises no notice.

### `RoundCells`

The cells of one round on one row.

- **Drivers graphic**: session key → cell text. Keys are `sprint_qualifying`, `sprint_race`,
  `feature_qualifying`, `feature_race`.
- **Constructors graphic**: car ordinal → (`driver_name`, session key → cell text). A car no driver
  drove carries `None`, and its group is removed.

A cell's text is a finishing position, or `DNF` / `DNS` / `DSQ`. An **empty string** means determined
empty — no session of that type, round unrun or cancelled, or the driver took no part — and goes
through `FillSpec.empty_quietly`.

### `StandingsDrawing`

What the utility hands to `build_fill_spec`.

| Field | Meaning |
|---|---|
| `championship` | `drivers` or `constructors` — selects the catalogue and the template key |
| `season_number`, `division_name`, `division_tier` | the heading fields |
| `round_number`, `race_name`, `result_status` | the round the standings stand after, and its lifecycle label |
| `entries` | the classification, in order |
| `rounds` | round ordinal → (`number`, `track_slug`) for every round the division holds |
| `seats` | row ordinal → that row's team's configured seat count (constructors only) |
| `notices` | degradations gathered during resolution |

---

## Entities read and not changed

- **DriverStandingsSnapshot / TeamStandingsSnapshot** — position, points and the team role. The
  countback is already applied in `standing_position`; both are read, never recomputed.
- **Round** — number, track, format (which sessions exist), cancelled state, lifecycle stage.
- **QualifyingSessionResult / RaceSessionResult** — each cell's recorded position or outcome, and the
  team role placing a driver on a car.
- **Team / Division** — configured seats per team, division name and tier, and the reserves toggle
  deciding the driver classification's composition.

## Validation rules

| Rule | Source | Where enforced |
|---|---|---|
| Rows contiguous from 1, ≥1 declared | FR-035, FR-042 | `RowSpec.declared_capacity` (existing) |
| Rounds contiguous from 1, each with its number field | FR-035 | `RowSpec.declared_capacity` on `columns` |
| Cars contiguous from 1 | FR-035 | `NestedSpec.declared_capacity` (existing) |
| No sibling-catalogue row field | FR-007 | `sibling_fields_declared` (existing, extended to the new pair) |
| Entries ≤ rows declared | FR-038 | `FillSpec.row_count` (existing) |
| Division rounds ≤ rounds declared | FR-040 | new, same shape as `row_count` |
| Drivers per team per round ≤ cars declared | FR-041 | new, per row |
| Classification ≤ row ceiling at assignment | FR-044 | `placement_service` |
| One driver, one team, per round | FR-065 | `result_submission_service._validate` |
