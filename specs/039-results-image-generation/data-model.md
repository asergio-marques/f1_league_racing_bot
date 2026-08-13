# Phase 1 Data Model: Results Image Generation

## Persisted entities

**None added, none amended.** No migration is written for this feature.

| Entity | Read for | Why nothing changes |
|---|---|---|
| `session_results` | `results_message_id` — the message the image flow replaces; `session_type` — which of the two templates draws it | The column exists and is already written by `post_session_results`; the image flow persists the id of its replacement into the same column |
| `qualifying_session_results` | position, outcome, tyre, best lap, points, team role, driver | The classification as the results module persisted it, renumbering included |
| `race_session_results` | position, outcome, base time, laps behind, the three penalty columns, fastest lap, fastest-lap bonus, points | Same |
| `penalty_records` / `appeal_records` | which phase disqualified an entry, via the existing `_load_dsq_phase_map` | Read only |
| `rounds` | `result_status`, `round_number`, `track_name`, `format` | `result_status` gives both the lifecycle label and the two phase closures |
| `divisions` / `seasons` | division name, tier, season number | Read only |
| `teams` | the name behind an entry's `team_role_id` | Read only |
| points configuration | whether the session conferred a fastest-lap bonus | Read only |
| `image_config` | the `results` toggle, the fastest-lap colour, and the flag, team-image and tyre directories | All delivered at 035/036 |

## In-memory structures

These are the feature's real data model: values decided once, then projected onto a template. They
mirror `CalendarDrawing` / `CalendarRound` from 037.

### `ResultsDrawing` — `services/image_results_service.py`

One session, every value resolved, no template in view.

| Field | Type | Notes |
|---|---|---|
| `session_kind` | `QUALIFYING` \| `RACE` | Selects the catalogue and the row shape |
| `division_name` | `str` | Mandatory field |
| `division_tier` | `str \| None` | Optional field |
| `season_number` | `str \| None` | Optional field |
| `round_number` | `str` | Mandatory field |
| `race_name` | `str` | The grand prix name from the track record |
| `session_name` | `str` | From `format_session_label` — the same call the message heading makes |
| `result_status_label` | `str` | From `_label_from_status` — the same text the message carries |
| `penalty_phase_closed` | `bool` | `result_status` is `POST_RACE_PENALTY` or `FINAL` |
| `appeal_phase_closed` | `bool` | `result_status` is `FINAL` |
| `fastest_lap` | `FastestLapBlock \| None` | Race only; `None` where the session conferred no bonus |
| `entries` | `list[ResultsEntry]` | Ordered by the persisted finishing position |

### `ResultsEntry`

One row. Every text cell is already the string that will be drawn, or `None` for "does not apply".

| Field | Type | Fills |
|---|---|---|
| `ordinal` | `int` | The row's discriminator **and** `row_<x>_position` (XIV.11 — filled from the ordinal, never reconciled) |
| `driver_name` | `str` | `row_<x>_driver_name` |
| `nationality` | `str \| None` | The datum behind `row_<x>_driver_flag`; `None` removes the field |
| `team_name` | `str` | `row_<x>_team_name`, and the datum behind `row_<x>_team_image` |
| `postrace_penalty` | `str \| None` | `row_<x>_postrace_penalty`, subject to `penalty_phase_closed` |
| `appeal_penalty` | `str \| None` | `row_<x>_appeal_penalty`, subject to `appeal_phase_closed` |
| `points` | `str` | `row_<x>_points` |
| `tyre` | `str \| None` | Qualifying. `None` draws the class fallback quietly (R6) |
| `best_lap` | `str \| None` | Qualifying. Carries the outcome literal for DNF/DNS/DSQ |
| `gap` | `str \| None` | Qualifying. `None` for the reference entry and where no entry set a lap |
| `time` | `str \| None` | Race. Total time, interval, lap count, or outcome literal |
| `fastest_lap` | `str \| None` | Race |
| `ingame_penalty` | `str` | Race. Never `None` — a dash where the game applied none (FR-018) |
| `holds_fastest_lap` | `bool` | Race. Drives the recolour, and only for the one entry |

### `FastestLapBlock`

| Field | Type | Fills |
|---|---|---|
| `driver_name` | `str` | `fastest_lap_driver_name` |
| `lap_time` | `str` | `fastest_lap_time` |

`None` for the whole block removes `fastest_lap_group`, or empties the two fields where the template
declares no group.

### Row records — `utils/results_formatter.py`

`QualifyingRow` and `RaceRow` are the **shared** derivation of R3, returned by
`build_qualifying_rows` / `build_race_rows` and consumed by both the text presenter and
`ResultsEntry`. Their cells are the resolved strings; `None` means "does not apply", which the text
presenter renders as "—" and the graphic empties. They carry no Discord reference of any kind — the
mention substitution happens above them, in each presenter.

## State transitions

The graphic holds no state of its own. It follows the round's results lifecycle:

```
PROVISIONAL          → both sanction fields empty on every row;
                       postrace_penalty_group and appeal_penalty_group removed
POST_RACE_PENALTY    → penalty field resolved on every row; appeal field empty;
                       appeal_penalty_group removed
FINAL                → both fields resolved; neither group removed
```

Each transition is one of the six redraw occasions of FR-035; the others (resync, amendment
approved, points recalculation) redraw without changing the stage.

## Validation rules

| Rule | Source | Where enforced |
|---|---|---|
| At least one row, contiguous from 1 | FR-029 | `RowSpec.declared_capacity`, raising `CapacityError` |
| Every mandatory field present | FR-028 | `CatalogueLayer`, at all three moments |
| No field of the sibling catalogue | FR-005 | `CatalogueLayer` (R2) |
| Entries ≤ declared rows | FR-033 | `FillSpec.row_count`, against the counted capacity |
| Every mandatory value determinable | FR-030 | `_verify_against_data`, immediately before the render |
| A determined-empty value is not undeterminable | FR-014, XIV.3 | `FillSpec.empty_quietly` |
