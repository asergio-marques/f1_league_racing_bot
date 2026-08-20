# Phase 1 Data Model

No database schema changes. The structures below are in-memory: catalogue specifications, the drawing a lineup resolves to, and the result of an asset lookup.

## 1. Catalogue structures

### Removed

| Structure | Where | Why it goes |
|---|---|---|
| `KeyedSpec` | `models/image_catalogues.py` | Rule XIV.11 withdraws the keyed collection. The lineup was its only user |
| `LineupBinding` | `models/image_catalogues.py` | Existed only to tell a keyed catalogue which members exist; the template now answers that |
| `FieldCatalogue.keyed` | field | — |
| `FieldCatalogue.divergent_members()` | method | Both-directions divergence is withdrawn with the by-the-data capacity |
| `binding=` parameter | `all_mandatory_ids`, `all_known_ids`, `valueless_ids`, `FillSpec` | Nothing left to bind |
| `binding_from_teams()`, `divergences()` | `services/image_lineup_service.py` | Callers of the above |

### Changed — `LINEUP_CATALOGUE`

The team collection moves from `keyed=KeyedSpec(...)` to the same `rows=RowSpec(...)` the other graphics use. Field names and asset classes are unchanged; only the discriminator and the capacity rule change.

| Aspect | Before | After |
|---|---|---|
| Discriminator | key — `team_red_bull_name` | ordinal — `team_1_name` |
| Capacity | fixed by the data (`capacity_from_data=True`) | fixed by the template (`RowSpec.capacity=None`, derived from `root`) |
| Member group | `team_red_bull_group`, mandatory-ish via binding | `team_<x>_group`, **optional** |
| Nested seats | `NestedSpec(prefix="driver")`, count from configuration | same `NestedSpec`, count from the template, **per block** |
| Divergence | fatal in both directions | overflow fatal one way; under-declaration removed silently |

`NestedSpec.capacity_per_member` stays **`False`** for the lineup's seats. It is `True` only for the results grid's cars, which are a *ceiling* — see the post-design note in [plan.md](plan.md).

### Added — `FieldCatalogue.singleton_capacity(root)`

Returns the singleton's nested slot count (the reserve block's seats) explicitly, so it no longer arrives by fall-through from `capacity()`. See research **R1**.

## 2. The lineup drawing

`LineupTeam` loses its `key` and gains an ordinal, assigned by position rather than derived from the name.

| Field | Before | After |
|---|---|---|
| `key` | `str` — normalised team name, or `"reserve"` | **removed** |
| `ordinal` | — | `int` — 1-based position in the division, reserve excepted |
| `display_name` | `str` | unchanged — drawn on `team_<x>_name` |
| `image_datum` | `str` | unchanged — the **name**, normalised at resolution to find the badge |
| `seats` | `list[LineupSeat]` | unchanged |
| `is_reserve` | `bool` | unchanged; a reserve team carries **no** ordinal |

`LineupDrawing.binding()` is removed. `LineupSeat` is unchanged.

**Validation moves out of `resolve_drawing`.** The three `LineupDataError` raises that guard the *key* — empty normalisation, collision with `reserve`, collision with another team — no longer belong there: a name that normalises badly can no longer collide with a template field, only with another team's badge file. Team-name validity is `team_service.validate_team_name`'s business at the command that sets it (Principle IX), and `season review` reports what slipped through. What `resolve_drawing` gains instead is the **seat overflow** check, per block, naming the drivers that would be dropped.

## 3. Asset resolution

### `AssetResolution` — one field added

| Field | Type | Meaning |
|---|---|---|
| `outcome` | `AssetOutcome` | `FOUND` / `FALLBACK` / `MISSING` — **unchanged**, three values |
| `path` | `Path \| None` | unchanged |
| `slug` | `str` | unchanged |
| `from_packaged` | `bool` | **new** — `True` where a `FALLBACK` outcome came from the packaged tier |

`AssetOutcome` deliberately keeps three values while resolution has four *paths*; see research **R7**. `from_packaged` is for tests and diagnostics only — FR-041 requires the notice text to be identical either way, so no caller may branch on it to change what a league is told.

### Signatures

```
resolve_asset(directory, datum, *, packaged=None) -> AssetResolution
has_fallback(directory, *, packaged=None) -> bool
```

`packaged=None` preserves today's single-tier behaviour, which keeps every existing test meaningful and makes the widening additive.

### `packaged_directory_for(asset_class) -> Path`

New in `models/image_constants.py`, derived from the same table that supplies the `images config *-directory` defaults, so the packaged path and the default configured path cannot drift apart.

## 4. Team ordering

No schema change. The `id` column of `team_instances` already carries insertion order; three queries change from `ORDER BY is_reserve ASC, name ASC` to `ORDER BY is_reserve ASC, id ASC`. See research **R5** — this is the finding that most changes the design, and it alters textual `season review` output as well as the graphic.

## 5. Files on disk

| Before | After |
|---|---|
| `resources/{tracks,teams,flags,drivers,markers,weather,tyres}/` | `resources/defaults/{...}/` |
| `resources/templates/` (15 templates) | `resources/defaults/templates/` |
| `resources/README.md` | stays; rewritten for the new split |

Contents move unaltered (FR-036). `resources/defaults/templates/lineup_template.svg` is the one file whose *content* changes — see [contracts/lineup-catalogue.md](contracts/lineup-catalogue.md).
