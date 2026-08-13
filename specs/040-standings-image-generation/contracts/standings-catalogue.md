# Contract: The two standings field catalogues

The authoritative field lists are in
[`docs/wip-specs/image_module_specification.md`](../../../docs/wip-specs/image_module_specification.md)
§ "Standings image generation". This document states how they are **declared** and how ids are
**constructed** — not the field list, which would then exist twice.

Declared in `src/models/image_catalogues.py` as `STANDINGS_DRIVERS_CATALOGUE` and
`STANDINGS_CONSTRUCTORS_CATALOGUE`, registered under the template keys `standings_drivers_template`
and `standings_constructors_template`.

## Shared and separate

The two share the declaration of their common part — `season_number`, `division_name`,
`division_tier`, `round_number`, `race_name`, `result_status` — and each names its own fields in
full. They are two entries, never one carrying a branch (XIV.10).

Their row catalogues diverge: the drivers row carries `driver_name`, `driver_flag` and `team_name`;
the constructors row carries `team_name` and no driver field at all. A field of one appearing in the
other's template is a **sibling field** and is fatal at the moment the template is named (FR-007),
detected by `sibling_fields_declared` against the pair.

## Id construction

Three levels, each contributing its name and ordinal in containment order (XIV.11). Ordinals are
unpadded and contiguous from 1; a gap at any level is fatal.

| Level | Stem | Ids |
|---|---|---|
| Row | — | `row_<x>_group`, `row_<x>_position`, `row_<x>_points`, … |
| Round heading | — | `round_<z>_group`, `round_<z>_number`, `round_<z>_image` |
| Cell (drivers) | `row_<x>` | `row_<x>_round_<z>_group`, `row_<x>_round_<z>_feature_race_result`, … |
| Car (constructors) | `row_<x>_round_<z>` | `row_<x>_round_<z>_driver_<w>_group`, `…_driver_<w>_name`, `…_driver_<w>_feature_race_result` |

The round heading is **top level and never under a row**. A cell belongs to its row and its round
both, and a node has one parent, so the cell lives under the row and the heading group carries chrome
alone (XIV.2, discriminated column group).

`row_<x>_position_change_group` is a **block group** bearing its row's discriminator: it wraps
`row_<x>_position_change` and `row_<x>_position_change_marker`, which stand or fall together.

## Capacity

| Collection | Fixed by | Under-declared | Over-declared |
|---|---|---|---|
| `row` | the template | data exceeding it is **fatal** (FR-038) | excess rows removed silently (FR-037) |
| `round` | the template | division rounds exceeding it is **fatal** (FR-040) | excess rounds removed silently (FR-039) |
| `driver` (car) | the **data, per containing row** | drivers who drove exceeding it is **fatal** (FR-041) | cars beyond that row's team's seats removed silently, per row (FR-041) |

The car collection is the per-containing-member case (XIV.12, v4.5.0): one template serves rows whose
teams have differing seat counts, so no declared count can be right for all and the declaration is a
ceiling rather than a count. The seat count is read from configuration at each check and never frozen
into the catalogue.

## The optional unit

The whole round portion — the `round` collection, the per-row cells, and the constructors' cars — is
declared **optional as a unit** (XIV.3, v4.5.0). A template declaring no member of it draws a
classification alone and is not faulty.

`round_<z>_number` is classified **mandatory within** that unit: required on every round a template
declares, owed by a template declaring none. This is the scope of the classification narrowing, not a
third classification.

## Assets

| Field | Class | Datum |
|---|---|---|
| `row_<x>_driver_flag` | `flag` | the driver's nationality, normalised |
| `row_<x>_team_image` | `team` | the team name of the wip-spec's team convention, normalised |
| `row_<x>_position_change_marker` | `marker` | `gained`, `lost` or `unchanged` |
| `round_<z>_image` | `track` | the track id, as the calendar resolves it |

The `marker` class is a **closed set the module defines**, so the module ships all three files
(XIV.13, v4.5.0). No field of either catalogue declares `fallback_when_absent`: an absent nationality
removes the flag with a notice (FR-028), and an undeterminable movement removes the whole block
quietly (FR-017), so there is no absence a fallback should depict.

## Removal, and what one decision reaches

A single capacity decision on a round ordinal removes ids from three families at once — the heading
group, every row's cell group for that round, and every car group of that round on every row (XIV.12,
one capacity governing several id families). Where a template declares no group for one of those
families, every field of that family bearing the ordinal is removed one by one instead.
