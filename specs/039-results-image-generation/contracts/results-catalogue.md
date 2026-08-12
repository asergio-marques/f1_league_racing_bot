# Contract: the two results field catalogues

The authoritative list of what a results render addresses, split by operation and classified
mandatory or optional (Constitution XIV.3, XIV.10). Declared as code constants in
`src/models/image_catalogues.py` and registered in `CATALOGUES` under the template keys
`results_qualifying_template` and `results_race_template`.

This is the contract a league manager authors an SVG against.

## Shared by both templates

### Whole-graphic fields

| Field | Class | Operation |
|---|---|---|
| `division_name` | Mandatory | Text |
| `round_number` | Mandatory | Text |
| `race_name` | Mandatory | Text |
| `session_name` | Mandatory | Text |
| `result_status` | Mandatory | Text |
| `season_number` | Optional | Text |
| `division_tier` | Optional | Text |
| `postrace_penalty_group` | Optional | Removed — **column group**, removed while the penalty phase stands open |
| `appeal_penalty_group` | Optional | Removed — **column group**, removed while the appeal phase stands open |

A column group carries its column's heading and **no cell of any row**; a cell belongs to the row it
stands on and leaves with that row's group (XIV.2, v4.4.0). A template declaring neither group draws
its heading over an emptied column, which is meant and is not a fault.

### The row collection

`prefix="row"`, ordinal-discriminated, **capacity counted from the template** (`capacity=None`).
Numbered contiguously from 1; a gap is fatal, and so is a template declaring no row at all.

| Suffix | Class | Operation |
|---|---|---|
| `group` | Mandatory | Removed when the session holds no entry of that ordinal |
| `position` | Mandatory | Text — filled from the row's own ordinal |
| `driver_name` | Mandatory | Text |
| `team_name` | Mandatory | Text |
| `team_image` | Mandatory | Image, class `team` |
| `postrace_penalty` | Mandatory | Text |
| `appeal_penalty` | Mandatory | Text |
| `points` | Mandatory | Text |
| `driver_flag` | Optional | Image, class `flag` |

## Qualifying template only

| Suffix | Class | Operation |
|---|---|---|
| `best_lap` | Mandatory | Text |
| `gap` | Mandatory | Text |
| `tyre` | Optional | Image, class `tyre` — **`fallback_when_absent`** |

`tyre` is the first field to carry the v4.4.0 declaration: where no compound is recorded, the tyre
directory's `fallback.svg` is drawn and **nothing is reported**; where that directory holds no
fallback, the field is removed and still nothing is reported.

## Race template only

| Field | Class | Operation |
|---|---|---|
| `row_<x>_time` | Mandatory | Text |
| `row_<x>_fastest_lap` | Mandatory | Text, **and recolour** for the entry holding the bonus |
| `row_<x>_ingame_penalty` | Mandatory | Text — a dash where the game applied none; never empty |
| `fastest_lap_group` | Optional | Removed — **block group**, removed when the session conferred no bonus |
| `fastest_lap_driver_name` | Optional | Text |
| `fastest_lap_time` | Optional | Text |

`row_<x>_fastest_lap` is the module's only data-driven recolour. The colour is
`images config fastest-lap-colour`, merged into the element's inline style. A recolour does not
consume the field: it is filled as any other (XIV.2).

## Siblings

The two catalogues are **siblings**: two image types drawn by one aspect. A template declaring a row
field belonging to the other's catalogue — `row_1_gap` in a race template, `row_1_time` in a
qualifying template — is a fatal fault, reported at the moment the template is named. An identifier
belonging to neither catalogue is chrome and is not the module's business (XIV.3, v4.4.0).

The sibling set is derived from `ASPECT_TEMPLATES`, not hard-coded for results.

## What the graphic does not carry

No image of the track, no country name, no round date, no points-configuration name, and no Discord
mention of any kind. A driver and a team are named in text (XIV.16).
