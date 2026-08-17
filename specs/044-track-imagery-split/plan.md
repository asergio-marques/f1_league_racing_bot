# Implementation Plan: Track Imagery Split

**Branch**: `044-track-imagery-split` | **Date**: 2026-08-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/044-track-imagery-split/spec.md`

## Summary

Split the imagery standing for a round into two classes and rekey the flag class by country.

Today one `track` class, keyed on the circuit, serves every graphic that pictures a round, and a
separate `flag` class, keyed on a driver's nationality adjective, serves drivers alone. After this
increment the `flag` class is keyed on a **country** and serves both; the `track` class holds
circuit maps and is drawn only by the calendar and the check-in graphic, each of which may draw a
flag, a map, both or neither.

The technical approach is deliberately narrow. **No new asset class, no new configuration, no
schema change.** The work is: one new shipped constant (nationality → country) plus its totality
test; changing the datum four drawing services emit for a round heading from the track name to the
country; adding a second optional field to the calendar and check-in catalogues; re-geometrying and
re-labelling slots in seven packaged templates; one new packaged asset; and a new per-class aspect
check in Layer 2, which is the only genuinely new machinery.

**One decision is open and is carried as a stated assumption** — see Phase 0. The seeded
`Track.country` vocabulary does not match the country names the constitution's examples assume, and
the two must be reconciled before the flag class can serve a driver and a round out of one file.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: discord.py, `fonttools` (text metrics), Inkscape (SVG → PNG rasteriser,
invoked out of process; the one dependency no package declaration installs)
**Storage**: SQLite via `src/db/`. **No migration in this increment** — `Track.country` (029) and
the image config columns (039) are read as they stand.
**Testing**: `pytest tests/ -q` from the repo root. Currently 1995 passed, 1 skipped.
**Target Platform**: Discord bot, single process, Windows and Linux alike
**Project Type**: Single project — `src/` with `models/`, `services/`, `cogs/`, `utils/`, `db/`
**Performance Goals**: Not a driver. Generation is per-posting and already bounded by the
rasteriser, which this increment does not touch.
**Constraints**: No test may require a live Discord bot. Every generated image is verified as PNG,
never as SVG in a browser. `poc/` is out of scope and untouched.
**Scale/Scope**: 7 packaged templates re-authored, 4 drawing services re-pointed, 15 catalogues
audited (7 touched), 1 new shipped constant, 1 new shipped asset, 1 new validity check.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against Constitution v5.0.0, Principle XIV in full plus the cross-cutting principles.

| Gate | Verdict | Note |
|---|---|---|
| **XIV.1** Templates are data, not code | ✅ Pass | The flag/map choice is expressed by which slot a template declares. No branch is added to any utility. |
| **XIV.2** Fields addressed by `@id` | ✅ Pass | New ids `round_<x>_flag` and `track_flag` follow the existing addressing. |
| **XIV.3** Every mandatory field resolved | ✅ Pass | Both round-imagery fields are optional on every type that declares them. The mystery literal-value paragraph governs a concealed round unchanged. |
| **XIV.4** Problems and notices distinct | ✅ Pass | A missing flag is a notice; a missing flag with no fallback is a problem. No new outcome kind. |
| **XIV.5** Text bounds | ✅ Pass | Not touched. |
| **XIV.6** Assets aspect-authored + **per-class uniformity** | ⚠️ **Requires new work** | The per-class aspect rule is newly stated and **nothing enforces it today**. Layer 2 gains the check. This is the one new mechanism in the increment and is tracked as such, not as a violation. |
| **XIV.7** Image output is additive | ✅ Pass | A graphic that drew a circuit map now draws a flag: different imagery, not less information. The country was already drawn as text beside it on every affected type. |
| **XIV.8** Images are attachments | ✅ Pass | Not touched. |
| **XIV.9** Layered template validity | ✅ Pass | The aspect check is added to Layer 2, which is ratified and enforced. No new layer is introduced. |
| **XIV.10** Catalogue as code constant | ✅ Pass | Every change is a catalogue edit in `src/models/image_catalogues.py`. No catalogue is assembled inline. |
| **XIV.11** Fixed id convention | ✅ Pass | The rename from `track_image`/`round_<x>_image` to the `_flag` form on four types is *required* by this rule — an id must name the class it draws. |
| **XIV.12** Capacity declared by template | ✅ Pass | The calendar's flag field is a member of the existing round collection and inherits its capacity. |
| **XIV.13** Slug resolution + fallback | ✅ Pass | The whole increment. Three outcomes unchanged; per-class fallback explicit. |
| **XIV.14** Verified as PNG | ✅ Pass | Quickstart mandates PNG verification for all seven re-authored templates. |
| **XIV.15/16/17** Zone, actionability, redraw | ✅ Pass | Not touched. |
| **X.** Modular feature architecture | ✅ Pass | No module gate, toggle or configuration surface changes. |
| **Testing standards** (CLAUDE.md) | ✅ Pass | Every task carries its unit test; no task requires a live bot. |

**No violation requires justification.** The Complexity Tracking table is therefore omitted.

The one item worth naming is **XIV.6**: the rule now obliges a check that does not exist. It is new
machinery rather than a violation, and Phase 1 gives it a contract of its own.

## Project Structure

### Documentation (this feature)

```text
specs/044-track-imagery-split/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── country-flag-resolution.md
│   ├── round-imagery-catalogue.md
│   └── asset-aspect.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
src/
├── models/
│   ├── image_catalogues.py      # 7 catalogue entries edited (the core change)
│   └── image_constants.py       # asset class table read as-is; no new class
├── services/
│   ├── image_calendar_service.py    # emits a second datum per round
│   ├── image_rsvp_service.py        # emits a second datum
│   ├── image_standings_service.py   # round heading datum: track name -> country
│   ├── image_attendance_service.py  # round heading datum: track name -> country
│   ├── image_weather_service.py     # track_image -> track_flag, country datum
│   ├── image_validity_service.py    # Layer 2 gains the aspect check
│   └── image_sample_data.py         # test-render fixtures for both classes
├── utils/
│   ├── nationality_data.py          # existing NATIONALITY_LOOKUP, read
│   ├── country_data.py              # NEW — nationality -> country map
│   └── asset_resolver.py            # unchanged; already class-agnostic
└── db/                              # untouched — no migration

resources/
├── flags/mystery.svg                # NEW packaged asset, 3:2
└── templates/                       # 7 templates re-authored
    ├── calendar_template.svg            # + 12 flag slots
    ├── rsvp_template.svg                # + track_flag and its group
    ├── standings_drivers_template.svg   # round heading -> flag, re-geometried
    ├── standings_constructors_template.svg
    ├── attendance_template.svg
    ├── weather_p1_template.svg          # and p2, p2_sprint, p3, p3_sprint, mystery
    └── ...

tests/unit/                          # every task carries its test here
```

**Structure Decision**: Single project, existing layout, no new package. The one new module is
`src/utils/country_data.py`, placed beside `nationality_data.py` because it is the same kind of
thing — a shipped vocabulary constant — and its totality test reads both.

## Phase 0: Outline & Research

See [research.md](./research.md). Three unknowns were carried in; all three are resolved there.

The headline finding, which changes the shape of the work: **the seeded `Track.country` vocabulary
and the country names the specification's examples assume do not agree.** The seed carries
`'United Kingdom'` and `'United States of America'`; the constitution and wip-spec examples say
`Great Britain` → `great_britain.svg` and `united_states.svg`. Left unreconciled, the British Grand
Prix would draw `united_kingdom.svg` while a British driver drew `great_britain.svg` — two files for
one country, which is precisely the duplication the rekey exists to remove.

Resolved by **assumption, stated for override**: the nationality → country map yields exactly the
spellings `Track.country` already holds. No seed change, no migration, one vocabulary. The
documentation examples are corrected to match rather than the data being bent to the examples.

## Phase 1: Design & Contracts

Three contracts, each governing one thing this increment introduces or changes:

- **[country-flag-resolution.md](./contracts/country-flag-resolution.md)** — the one flag directory
  keyed on a country; the nationality → country map and its totality obligation; `Other`; the
  shared-country case; what a round resolves.
- **[round-imagery-catalogue.md](./contracts/round-imagery-catalogue.md)** — the two optional
  fields, which types may declare which, the id convention, and the per-type catalogue diff for all
  seven affected templates.
- **[asset-aspect.md](./contracts/asset-aspect.md)** — per-class aspect uniformity and the Layer 2
  check that enforces it. The only new mechanism.

[data-model.md](./data-model.md) records the entities — none of them a database entity — and
[quickstart.md](./quickstart.md) is the runnable validation guide, PNG-verified per XIV.14.

### Post-Design Constitution Re-check

Re-checked after the three contracts were written. **No gate changed verdict.** Two notes:

1. The aspect check (XIV.6) sits in Layer 2 rather than a new layer, so XIV.9's "a layer MUST be
   ratified before it is enforced" is not engaged — Layer 2 is already ratified and enforced.
2. XIV.11's id convention turned out to *oblige* the rename of four types' fields rather than merely
   permit it. That tightens the work rather than loosening it and is reflected in the contracts.
