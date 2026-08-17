# Phase 0 Research: Track Imagery Split

**Feature**: `044-track-imagery-split`
**Date**: 2026-08-17

Three unknowns were carried into Phase 0. All three are resolved. The first is the one that
changes the shape of the work and it needs the author's confirmation; the plan proceeds on the
recommended resolution and says so.

---

## R-001 — The two country vocabularies do not agree *(blocking finding)*

**Decision**: The nationality → country map MUST yield **exactly the spellings `Track.country`
already holds**. `British` maps to `United Kingdom`, not to `Great Britain`; every United States
circuit maps to `United States of America`. No seed change, no migration, no aliasing layer.

**Why this was not obvious.** The whole point of rekeying the flag class by country is that *one*
file serves a driver and a round alike. That only holds if the country a driver resolves to and the
country a round resolves to are spelled the same. They come from two independent sources:

| Source | Value | Slug it produces |
|---|---|---|
| `tracks.country` seeded by migration 029 | `United Kingdom` | `united_kingdom.svg` |
| `tracks.country` seeded by migration 029 | `United States of America` | `united_states_of_america.svg` |
| Constitution XIV.13 example | `Great Britain` | `great_britain.svg` |
| wip-spec example | `Great Britain` | `great_britain.svg` |
| Spec SC-001 / edge cases example | `United States` | `united_states.svg` |

Unreconciled, the British Grand Prix draws `united_kingdom.svg` while a British driver draws
`great_britain.svg` — **two files for one country**, which is the exact duplication this increment
exists to remove, reintroduced by the increment itself. Three US circuits would likewise split from
US drivers.

**Rationale for taking the seed's spelling as authoritative**:

- It is **data that exists**; the examples are prose. Bending 28 seeded rows and any future track a
  league adds to match an illustration is the larger and riskier change.
- It needs **no migration**, which keeps the increment's "no schema change" claim true.
- `Track.country` is already drawn as **text** on the calendar, check-in and weather graphics via
  the `country_name` field. Changing it would change what those graphics say, which is a
  user-visible change nobody asked for.
- The cost is cosmetic: `united_states_of_america.svg` is a long filename. It is a filename.

**Consequence — documentation must be corrected, not just extended**: the examples in Constitution
XIV.13, in the wip-spec's "The country a flag stands for", and in this feature's spec each name a
country the data does not carry. They are illustrative rather than normative, but an illustration
that contradicts the seed is worse than none. Correcting them is a task in Phase 2.

**Alternatives considered**:

| Alternative | Rejected because |
|---|---|
| Renormalise `tracks.country` to short names (`United Kingdom` → `Great Britain`) | Needs a migration, changes text already drawn on three graphics, and makes every league-added track a place to get it wrong again. |
| An alias table mapping many spellings to one canonical slug | A second normalisation rule, which XIV.13 explicitly forbids: "A second normalisation rule would be a second way for the id and the filename to disagree." |
| Key a round's flag on something other than the country | Overruled already — the author ruled circuits sharing a country share a flag. |

> **Open for override.** This is the one decision in the increment the author has not explicitly
> ruled on. Everything downstream assumes it. If the seed's spellings are to change instead, R-001
> flips and a migration task joins Phase 2.

---

## R-002 — How to derive the nationality → country map

**Decision**: **Author it explicitly** as a new shipped constant in `src/utils/country_data.py`,
and **validate** it against `NATIONALITY_LOOKUP` by test rather than deriving it from that lookup.

**Rationale**: `NATIONALITY_LOOKUP` maps *both* adjectives and country names onto a canonical
adjective, many keys to one value:

```
"argentine": "Argentine", "argentinian": "Argentine", "argentina": "Argentine"
"afghan": "Afghan",       "afghanistan": "Afghan"
```

Inverting it cannot tell which key was the country. `argentina` is, `argentinian` is not, and no
morphological rule separates them reliably across ~240 entries. A derived map would be wrong
silently, in a directory of files nobody checks until a flag looks odd.

An explicit map is checkable three ways, all cheap and all offline:

1. **Totality** — every canonical value of `NATIONALITY_LOOKUP` has an entry. This is FR-003 and is
   the test the wip-spec obliges.
2. **Consistency** — every country the map yields appears as a *key* of `NATIONALITY_LOOKUP`
   (lowercased), so the two vocabularies cannot drift apart.
3. **Track coverage** — every distinct `tracks.country` value is a value the map can also yield, so
   no seeded circuit needs a flag no driver could ever need. This is the test that would have caught
   R-001, and it is why it is listed here.

**Alternatives considered**: deriving by heuristic (rejected, above); a third-party dataset such as
`pycountry` (rejected — a new runtime dependency for a static 240-row table, and it would not carry
the `Other` case or agree with the seed's spellings without mapping anyway).

---

## R-003 — Where the per-class aspect check belongs

**Decision**: **Layer 2 (`CatalogueLayer`)** in `src/services/image_validity_service.py`, as an
additional check within the existing layer. Not a new layer.

**Rationale**: the check needs exactly two things — the slot's declared geometry, and the asset
class the catalogue assigns that field. Layer 2 is the layer that holds the catalogue; Layer 1 is
resolution and canvas only and does not know a field's class. Adding a layer would engage XIV.9's
ratification requirement for no benefit, whereas Layer 2 is ratified and enforced already.

**What the check does**: for each image field the catalogue names, read the slot's declared width
and height, compute the ratio, and compare against the class's declared aspect within a tolerance.
A mismatch is a **problem** naming the field, the class, the expected aspect and the found one.

**Tolerance is required, not optional.** Template geometry is authored in Inkscape and carries
floating-point values; `120.00001 / 80` is not `1.5` in binary floating point. A small relative
tolerance (1%) admits honest authoring and still catches a square slot given a 3:2 flag, which is a
50% error. An exact comparison would reject every template a human drew.

**Where the expected aspect is declared**: a new table in `image_constants.py` beside
`ASSET_CLASS_DIRECTORIES`, keyed by asset class. Constitution XIV.6 deliberately leaves the numbers
out of governance — "The aspect a class carries is not fixed by this Principle" — so they live in
code beside the other class tables, with `resources/README.md` as the league-facing statement.

**Alternatives considered**: checking at generation instead of validation (rejected — a league
learns at render time, per-posting, rather than when configuring the template, and XIV.9 exists to
catch structural faults early); a new Layer 5 (rejected, above); no check at all, relying on the
documentation (rejected — the wip-spec now says such a template "shall be refused", and the
letterboxing it prevents is invisible in the SVG and only appears in the raster).

---

## Resolved Technical Context

No `NEEDS CLARIFICATION` markers remain in the plan's Technical Context. For the record:

- **No new runtime dependency.** `country_data.py` is a dict literal.
- **No migration.** `Track.country` (029) and the image config columns (039) are read as they stand.
- **`asset_resolver.py` needs no change.** It already takes a directory and a datum and knows
  nothing of classes, so rekeying is entirely a matter of which datum the drawing services emit.
- **The affected drawing services already carry the country** on three of five types
  (`image_calendar_service`, `image_rsvp_service`, `image_weather_service` each hold
  `country_name`). The two that do not — `image_standings_service` and `image_attendance_service` —
  carry a `RoundHeading` with `track: str | None` and need a `country` field added beside it.
