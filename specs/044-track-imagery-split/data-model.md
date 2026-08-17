# Phase 1 Data Model: Track Imagery Split

**Feature**: `044-track-imagery-split`
**Date**: 2026-08-17

**No database entity is created, amended or removed, and there is no migration.** That is the
headline of this document and it is stated first so it is not re-derived. Constitution v5.0.0's
"New Entities (v5.0.0)" section records the same.

What follows are the module-level entities the increment introduces or changes.

---

## 1. `NATIONALITY_COUNTRIES` — new shipped constant

**What it is**: a total map from each canonical nationality adjective to the name of its country.
Lives in `src/utils/country_data.py`, beside the existing `NATIONALITY_LOOKUP`.

| Property | Value |
|---|---|
| Kind | Module-shipped constant. Not a table, not configuration, not league-editable. |
| Key | Canonical nationality adjective as `NATIONALITY_LOOKUP` yields it — `British`, `Argentine` |
| Value | Country name, spelled as `tracks.country` spells it — `United Kingdom` (see research R-001) |
| Totality | **Obligatory.** Every distinct value of `NATIONALITY_LOOKUP` is a key here. |

**Special member**: `Other` is present and maps to `Other`. It is *not* a country and gains none;
it is carried through so that a driver who stated no nationality resolves `other.svg` exactly as
today. Modelling it as an absence instead would make the map partial and defeat the totality test.

**Validation rules** (each a unit test, none requiring a bot):

- **V-1 Totality**: `set(NATIONALITY_LOOKUP.values()) == set(NATIONALITY_COUNTRIES.keys())`.
  A canonical nationality with no country is a defect of the module, caught here and never by a
  fallback drawn at render (FR-003).
- **V-2 Consistency**: every value except `Other`, lowercased, is a key of `NATIONALITY_LOOKUP`.
  This is what stops the two vocabularies drifting apart.
- **V-3 Track coverage**: every distinct `tracks.country` in migration 029 is a value this map can
  yield. This is the test that catches R-001's class of fault: a circuit whose country no driver
  could ever resolve means two files for one country.
- **V-4 Slug stability**: normalising a value yields the same slug the round path produces for the
  same country — one country, one filename, whichever path asked for it.

---

## 2. Asset classes — one changed in keying, one narrowed in reach

Neither is added; both already exist in `ASSET_CLASS_DIRECTORIES`.

### `flag` → `flag_directory` (default `resources/flags`)

| | Before | After |
|---|---|---|
| Datum for a driver | Nationality adjective (`British`) | Country of that nationality (`United Kingdom`) |
| Datum for a round | *(did not draw one)* | `Track.country` |
| Slug example | `british.svg` | `united_kingdom.svg` |
| Reserved names | `fallback.svg` | `fallback.svg`, **`mystery.svg` (new)** |
| Aspect | 3:2 | 3:2, and now **uniform across every template** |

### `track` → `track_image_directory` (default `resources/tracks`)

| | Before | After |
|---|---|---|
| Datum | Track name | Track name — **unchanged** |
| Drawn by | Calendar, check-in, standings ×2, attendance, weather | **Calendar and check-in only** |
| Reserved names | `fallback.svg`, `mystery.svg` | Unchanged; both already ship |
| Aspect | 1:1 | 1:1, uniform |

---

## 3. `ASSET_CLASS_ASPECTS` — new constant

**What it is**: asset class → the aspect ratio every slot of that class must declare. Lives in
`src/models/image_constants.py` beside `ASSET_CLASS_DIRECTORIES`, which it parallels exactly.

| Class | Aspect | Authored as |
|---|---|---|
| `flag` | 3:2 | 120 × 80 |
| `track` | 1:1 | 120 × 120 |
| `team` | 1:1 | 120 × 120 |
| `driver` | 1:1 | 120 × 120 |
| `marker` | 1:1 | 64 × 64 |
| `weather` | 1:1 | 64 × 64 |
| `tyre` | 1:1 | 64 × 64 |

The ratio is what binds, not the pixel dimensions — a template may draw a flag slot at any size so
long as it is 3:2. Constitution XIV.6 deliberately leaves the numbers out of governance, so this
table is the authority and `resources/README.md` is its league-facing statement.

Consumed by the Layer 2 aspect check ([asset-aspect.md](./contracts/asset-aspect.md)) and by
nothing else. No runtime path reads it.

---

## 4. `RoundHeading` — one field added, two services

`image_standings_service.RoundHeading` and `image_attendance_service.RoundHeading` each carry
`track: str | None` and emit it as the `track`-class datum for a round column heading.

| Field | Change |
|---|---|
| `track: str \| None` | **Retained.** Still the round's track name, still used for the heading's text and the mystery determination. It simply stops being an asset datum on these two types. |
| `country: str \| None` | **Added.** The datum the `flag` class resolves for that round. |

`None` for either keeps the existing meaning: the round has no track resolvable, its group is
removed or its cells emptied as the type's catalogue already declares. No new absence semantics.

Three services need no change here — `image_calendar_service` (`country_name` on its round entry),
`image_rsvp_service` and `image_weather_service` (`country_name` on their drawing) already carry
the country, because all three already draw it as text.

---

## 5. Packaged assets

| Path | Status | Aspect |
|---|---|---|
| `resources/flags/mystery.svg` | **New** — the only asset this increment adds | 3:2 |
| `resources/flags/fallback.svg` | Unchanged | 3:2 |
| `resources/tracks/mystery.svg` | Unchanged, already ships | 1:1 |
| `resources/tracks/fallback.svg` | Unchanged | 1:1 |

The new file is plain SVG, no `clipPath`/gradient/filter, and **carries no text** — text in an
asset font-substitutes and rasterises differently machine to machine (FR-012a). Concealment is
conveyed by shape, as `tracks/mystery.svg` already does.

---

## 6. What is deliberately *not* modelled

- **No configuration entity.** Both directories are already configurable; no command, parameter or
  stored setting is added. The flag/map choice lives in the template, by author's ruling.
- **No second flag directory**, and no per-graphic toggle. Both were considered and rejected when
  the rules were ratified.
- **No change to `asset_resolver.py`.** It takes a directory and a datum and knows nothing of
  classes, so the rekey is entirely a matter of which datum a drawing service emits.
- **No change to `Track`.** `country` has existed since v2.9.0 and is read as it stands.
