# Phase 1 Data Model: Weather Image Generation

**Feature**: `042-weather-image-generation` | **Date**: 2026-08-13

## No migration, and no entity amended

This feature introduces no table, adds no column, and changes no constraint. The reasoning is recorded
here so it is not re-derived by a later session.

### What already exists and is sufficient

| Store | What it holds | Why nothing is needed |
|---|---|---|
| `forecast_messages` | `(round_id, division_id, phase_number, message_id, posted_at)`, unique on the first three, `phase_number IN (0,1,2,3)` | Each phase's message is separately addressable and separately replaceable, which is the whole of what the chain of FR-044 needs. Phase `0` was admitted for the mystery notice by migration 006 |
| `Session` | `phase2_slot_type` (`rain`/`mixed`/`sunny`), `phase3_slots` (list of weather labels) | These are exactly the values the phase 2 and phase 3 graphics place. Read as the phase services persisted them; never recomputed (FR-026, FR-027) |
| The round record | number, format, track | `round_number`, the selecting datum of FR-012, and the track the heading fields draw |
| The track object | grand prix name, country | `race_name` and `country_name` (FR-024) |
| The division record | name, tier, forecast channel, mention role | The heading fields, the channel and the message's mention |
| The stored phase 1 result | the rain probability coefficient | `rain_probability` on all three phase graphics (FR-023) |
| `image_config` | the six template filenames, the `weather` toggle, the weather icon and track image directories | Delivered at 035 and 036; read as they stand |

### Why the image flow needs no message-id column of its own

The standings increment (040) needed a second column because one textual message carried two
championships where the image flow posts two graphics. Weather has the opposite shape: the textual flow
already posts **one message per phase**, and `forecast_messages` already keys by phase. The image flow
posts one message per phase too. The two flows write the same rows, which is what makes FR-046 —
the manner of a message being no part of the chain — true without any bookkeeping: an occasion reads
which message stands for the phase before it and deletes that, learning nothing about how it was drawn.

---

## The catalogue declaration form (the one structural change)

`RowSpec` and `NestedSpec` each gain one field.

```
minimum: int | None = None
```

**Meaning.** The least a template filling this slot must declare for this collection. `None` leaves
today's behaviour untouched, so every existing catalogue is unaffected.

**Interaction with `capacity`.** The two are independent readings and only one combination is new:

| `capacity` | `minimum` | Reading | Used by |
|---|---|---|---|
| `<int>` | `None` | Fixed by the image type | results rows, standings rows |
| `None` | `None` | Fixed by the template — count what is declared | calendar rounds, reserve seats, rsvp sessions |
| `None` | `<int>` | **Fixed by the template slot** — count what is declared, refuse below the floor | **weather sessions and slots** |
| `<int>` | `<int>` | *Not admitted.* A fixed capacity is already both bounds | — |

**Where it is enforced.** Inside `declared_capacity` on both specs, raising `CapacityError` — the same
exception the no-member and gap-in-numbering faults already raise, so it travels the path R2 describes and
refuses at all three validity moments without a new call site.

**What the message must name** (FR-016): the collection, the count the template declares, and the count
required. It is the whole of what a league manager is told.

---

## The six catalogues

All six are `FieldCatalogue` constants keyed into `CATALOGUES` by their template column. No new
`FieldCatalogue` field is needed; the shapes are ones 037–041 already built.

| Catalogue | Shape | Floor |
|---|---|---|
| `weather_p1_template` | flat: heading fields only | — |
| `weather_mystery_template` | flat: four heading fields only | — |
| `weather_p2_template` | flat + `RowSpec(prefix="session")` | 2 sessions |
| `weather_p2_sprint_template` | flat + `RowSpec(prefix="session")` | 4 sessions |
| `weather_p3_template` | flat + `RowSpec(prefix="session", nested=NestedSpec(prefix="slot"))` | 2 sessions, 4 slots each |
| `weather_p3_sprint_template` | flat + `RowSpec(prefix="session", nested=NestedSpec(prefix="slot"))` | 4 sessions, 3 slots each |

The floors are derived from `SESSIONS_BY_FORMAT` and `MAX_SLOTS` at module load — the greatest each slot's
served formats can demand — and are never written as literals. See
[contracts/declaration-floor.md](./contracts/declaration-floor.md) for the derivation and
[contracts/weather-catalogues.md](./contracts/weather-catalogues.md) for the field lists.

---

## Values the graphic reads, and where each is settled

Nothing in this column is computed by the image module. The right-hand column names the owner.

| Value | Owner | Note |
|---|---|---|
| Rain probability | phase 1, persisted | Rendered by the shared renderer (R6); the same stored value appears on all three phase graphics (FR-023) |
| Session weather type | phase 2, persisted on `Session` | The phase 3 graphic carries what phase 2 drew (FR-026) |
| Slot sequence | phase 3, persisted on `Session` | Drawn in the order drawn (FR-030) |
| Session name | `session_type_label` | Already strips the length qualifier; no work (FR-025) |
| Session summary | `format_slots_for_forecast`, unemphasised form | The emphasis is the message's, not the value's (FR-029) |
| Phase description | fixed text per phase | FR-022 |
| Round number, format, track | the round record | The format is also the selecting datum (FR-012) |
| Grand prix name, country | the track object | FR-024 |
| Division name, tier | the division record | Tier emptied where unset (FR-031) |
| Season number | the season record | FR-003 |

## State transitions

None. No entity gains a state and no lifecycle is added. The three phase horizons, the mystery notice
horizon and the amendment-invalidation re-run are all Principle IV's, unchanged; this feature changes only
what those occasions post, and — per R5 — the order in which one of them deletes and posts.
