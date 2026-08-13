# Contract: The Two Attendance Field Catalogues

**Feature**: 041-attendance-image-generation
**Declared in**: `src/models/image_catalogues.py`
**Normative source**: `docs/wip-specs/image_module_specification.md` § "Attendance image generation"

Two entries in `CATALOGUES`, keyed by the template slot they fill:
`attendance_template` and `rsvp_template`. They are siblings (see
[sibling-and-floor.md](./sibling-and-floor.md)) and share no collection.

---

## `ATTENDANCE_CATALOGUE` — the sheet

### Top level

| Id | Class | Notes |
|---|---|---|
| `season_number` | Optional | |
| `division_name` | **Mandatory** | |
| `division_tier` | Optional | |
| `round_number` | **Mandatory** | The round the sheet stands *after* |
| `race_name` | Optional | That round's grand prix name |
| `autoreserve_limit` | Optional | Group removed entirely when the functionality is off |
| `autosack_limit` | Optional | Likewise |

`autoreserve_group` and `autosack_group` are **block groups** (XIV.2), declared in
`valueless_fields` — the template must be able to carry them, and they receive no value of their own.

### `rows` — the drivers

```
prefix          = "row"
capacity        = None            # counted from the template (XIV.12, by the template)
fields          = {group, driver_name, driver_flag, team_name, team_image, points, sanction}
mandatory_fields= {group, driver_name, points}
valueless_fields= {group}
assets          = {driver_flag: "flag", team_image: "team"}
floor           = one driver, raised by the utility (see sibling-and-floor.md)
```

`row_<x>_group` is a **mandatory** group: the template must provide it, and it is removed when the
sheet holds no driver of that ordinal, which is the ordinary behaviour of a group and no fault
(XIV.2).

### `rows.nested` — the per-round cells

```
prefix          = "round"
capacity        = None
optional_unit   = True
fields          = {points}
mandatory_fields= {}
```

Builds `row_<x>_round_<z>_points`. The cell hangs off the **row**, not the column, because a cell
belongs to its row and its column both and a node of an SVG file has one parent (XIV.2).

### `columns` — the round headings

```
prefix          = "round"
capacity        = None
optional_unit   = True
fields          = {group, number, image}
mandatory_fields= {number}
valueless_fields= {group}
assets          = {image: "track"}
```

Builds `round_<z>_group`, `round_<z>_number`, `round_<z>_image`. Carries **chrome alone** and no cell
of any row.

### One ordinal, two id families

Removing round *z* takes `round_<z>_*` **and** `row_<x>_round_<z>_points` on every row — XIV.12's "one
capacity may govern several id families". Where the template declares no `round_<z>_group`, every field
bearing that ordinal is removed one by one (FR-039).

### The optional unit

`columns` and `rows.nested` are optional **together**: a template declaring neither draws the totals
alone and is not faulty (FR-003). `round_<z>_number` is mandatory only where that round is declared at
all (FR-005).

---

## `RSVP_CATALOGUE` — the check-in call

### Top level

| Id | Class | Notes |
|---|---|---|
| `season_number` | Optional | |
| `division_name` | **Mandatory** | |
| `division_tier` | Optional | |
| `round_number` | **Mandatory** | |
| `race_name` | **Mandatory** | Grand prix name; "Mystery GP" for a mystery round |
| `track_name` | Optional | The embed's Location |
| `country_name` | Optional | |
| `track_image` | Optional | Asset class `track` |
| `round_format` | **Mandatory** | "Normal" / "Sprint" / "Endurance" / "Mystery" |
| `round_date` | **Mandatory** | Configured date format |
| `round_time` | **Mandatory** | Configured time format + zone abbreviation (XIV.15) |
| `deadline_date` | Optional | |
| `deadline_time` | Optional | |

### `rows` — the sessions

```
prefix          = "session"
capacity        = None
optional_unit   = True
fields          = {group, name}
mandatory_fields= {group, name}
valueless_fields= {group}
```

Builds `session_<x>_group`, `session_<x>_name`, numbered in the order the sessions are run. `rows` is
the declaration slot for a type's one ordinal collection whatever it repeats — `RowSpec.prefix`'s own
docstring names `session` as an example.

### What it must never declare

No driver name, no team name, no RSVP status, no attendance point, no Discord mention (FR-009). This
is not merely an omission: it is what licenses the static declaration (XIV.17). **Adding any field
here whose value can change while the call stands is an amendment of that declaration**, not a
catalogue edit, and must be reviewed as one.

---

## Shared conventions, called and not restated

| Concern | Where the rule lives |
|---|---|
| Name of a person | wip-spec § "The name of a person" |
| Name of a team, and its normalisation for the team image | wip-spec § "The name of a team" |
| Mystery round values and `mystery.svg` | wip-spec § "A round of the mystery format" |
| Asset resolution and fallbacks | wip-spec § "The fallback image" |
| Date, time and zone | wip-spec § "The zone in which a time is drawn" |
| Empty rather than a dash | FR-030 |

---

## Test obligations

1. Every id either catalogue constructs matches the wip-spec's field list exactly, in both directions.
2. `ATTENDANCE_CATALOGUE.all_mandatory_ids()` with no template returns the top-level mandatory set
   alone — the per-row and per-round sets are unknowable without a file to count.
3. A template declaring `round_1_number` but no `round_1_group` is valid, and removing round 1 removes
   its fields one by one.
4. `RSVP_CATALOGUE` declares no id matching `driver`, `team`, `rsvp`, `status` or `points`.
