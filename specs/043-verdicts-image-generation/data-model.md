# Phase 1 Data Model: Verdicts Image Generation

**Feature**: `043-verdicts-image-generation` | **Date**: 2026-08-14

## Persisted entities

**None.** This feature introduces no table, amends no table, and adds no migration.

A verdict is posted once and never edited, replaced or deleted, so no message id is recorded — the one
piece of state every other image type keeps. See [research.md](./research.md) §6; the rule is FR-050 and
the licence is Constitution XIV.17's static declaration.

What is read, and where it already lives:

| Datum | Source | Notes |
|---|---|---|
| Season number, division name, division tier | `seasons`, `divisions` | As the textual announcement reads them |
| Round number, round format | `rounds` | Format selects the session-name vocabulary |
| Grand prix name | the round's track object | `"Mystery GP"` for a mystery-format round |
| Session type | `session_results` via the penalty's result row | Absent for an attendance sanction |
| Penalty type, magnitude, description, justification | `PenaltyRecord` (026) | Free text is the steward's, verbatim |
| Appeal outcome | `AppealRecord` (026) | Same shape as a penalty for drawing purposes |
| Autosack / autoreserve, threshold | attendance module | Composes its own description and justification |
| Driver identity | `driver_profiles`, `signup_records` | Resolved by the five-link chain |
| Team role | the session result row | Resolved to the division's team, else the role name |
| Verdicts channel | `division_results_config.penalty_channel_id` | Skipped silently when null or unreachable |

---

## The field catalogue

`VERDICTS_CATALOGUE`, registered in `CATALOGUES` under the key `verdicts_template`. A
`FieldCatalogue` with `rows`, `columns`, `keyed` and `singleton` all `None` — the second such catalogue,
after `WEATHER_MYSTERY_CATALOGUE`.

### Mandatory (8)

| Id | Value |
|---|---|
| `division_name` | Division name |
| `round_number` | Human-readable round number |
| `session_name` | Session label, **emptied** for an attendance sanction |
| `verdict_stage` | One of three fixed strings |
| `driver_name` | Resolved driver name; never a mention |
| `penalty` | Descriptive sanction rendering |
| `description` | Steward's description, verbatim |
| `justification` | Steward's justification, verbatim, mentions resolved in place |

### Optional (6 + 3 groups)

| Id | Value |
|---|---|
| `season_number` | Season number |
| `division_tier` | Division tier; emptied where none |
| `race_name` | Grand prix name; `"Mystery GP"` for a mystery round |
| `team_name` | Team name; **emptied** for an attendance sanction |
| `season_number_group` | Removable group |
| `division_tier_group` | Removable group |
| `team_name_group` | Removable group — wraps team name, team image and the TEAM label |

### Image fields (2)

| Id | Asset class | Absent datum |
|---|---|---|
| `driver_flag` | `flag` | Field removed. Notice **unless** nationality collection is switched off |
| `team_image` | `team` | Field removed. No notice for an attendance sanction |

### Declared absent

No track image, no country, no date or time, no session result, no points, no lifecycle label, no
steward name, and no collection of any kind (FR-005, FR-002).

### Why `session_name` stays mandatory while being emptied

The template must declare it — every kind of verdict but one draws it. For an attendance sanction the
data **determine** its value to be nothing, which XIV.3 holds is determined and not missing. It is
drawn empty, its group removed where declared, and no notice arises. This is the clause added at
v4.8.0, and it is the one place this catalogue's classification could be misread as a bug.

---

## In-memory shapes

Pure dataclasses in `image_verdict_service`, mirroring the other six types. No Discord object and no
database handle reaches them, which is what keeps the service testable without a bot.

### `VerdictKind`

An enum of three: `PENALTY`, `APPEAL`, `ATTENDANCE_SANCTION`. It selects the stage string and decides
whether a session and a team exist. It is **not** a template selector — all three draw from one slot.

### `VerdictDrawing`

Everything one graphic needs, already resolved. Fields correspond one-to-one with the catalogue, plus:

- `kind: VerdictKind`
- `nationality_collected: bool` — distinguishes a league that switched collection off (no notice) from
  a driver who stated none (notice). Carried from the lineup's existing handling.
- `driver_nationality: str | None` — the datum the flag slug is normalised from, not a path.
- `team_slug_source: str | None` — the name normalised for the badge; equals `team_name` in every case
  the module can currently produce, and is kept separate so a divergence is representable rather than
  assumed away.

A drawing is built two ways and the difference is confined to construction:

- from the database, by `image_verdict_post`;
- from fabricated values, by `image_sample_data.build_verdict_drawing`.

### The fill spec

`build_fill_spec(root, drawing) -> FillSpec` maps a drawing onto the catalogue's ids: text fills, two
image fills, and the group removals for emptied blocks. It is pure and returns the module's existing
`FillSpec`; the shared pipeline does the rest.

---

## State transitions

None. A verdict graphic has one lifecycle event — posted — and no subsequent state. A correction of the
decision it records is a **different** verdict with its own graphic, which is what makes the static
declaration sound (XIV.17, and FR-007).

---

## Validation rules, and where each is enforced

| Rule | Enforced at | Moment |
|---|---|---|
| Mandatory field absent from the template | Validity Layer 2, catalogue-driven | Configure, season review, pre-render |
| `shape-inside` names a missing rectangle | Fill pipeline (exists) + Layer 2 (added) | All three |
| Wrapped field with no resolvable `line-height` | Fill pipeline + Layer 2 | All three |
| Wrapped field whose rectangle has no extent | Fill pipeline + Layer 2 | All three |
| Sibling's field declared | Layer 2, catalogue-driven | Configure |
| Mandatory value undeterminable | Fill pipeline | Pre-render |
| Asset resolves to no file, no fallback | Asset resolution | Pre-render |

Every one of these is checkable against the **template alone** except the last two, which is why this
type's catalogue is verified in its entirety at all three moments (FR-041) — there is no field here
whose check must wait for a division, a round or a classification.
