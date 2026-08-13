# Implementation Plan: Standings Image Generation

**Branch**: `040-standings-image-generation` | **Date**: 2026-08-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/040-standings-image-generation/spec.md`

## Summary

Draw a division's two championships as PNGs in place of the textual standings, from two templates
under the single `standings` toggle. The rendering pipeline, asset resolution, validity layers and
posting machinery are all built and reused. What this feature adds is two catalogue entries, one
resolution utility, one posting hook, a sample builder, one migration, and one validation in the
results module.

Three decisions carry the design:

**R1 — the catalogue module grows a grid.** Standings is the first type whose fields are addressed on
*two* ordinals at once (`row_<x>_round_<z>_…`) and, on the constructors side, three
(`…_driver_<w>_…`). `RowSpec` and `NestedSpec` already model one ordinal each; the additions are a
second top-level collection, a nest hanging off a row, a nest hanging off a nest, and the
optional-as-a-unit flag XIV.3 ratified at v4.5.0. This touches the shared declaration module, which
XIV.10 constrains — see Complexity Tracking.

**R4 — the derived columns live in the standings service.** The gap, the previous position and the
position change are the first values a graphic draws that the text path does not. XIV.7 as amended at
v4.5.0 admits them as *presentation* on two conditions, one of which is that the derivation is
written where the data live. `standings_service.py` gains a `derive_movement` layer; the image utility
calls it and holds no arithmetic of its own.

**R6 — the text path learns to post one championship.** `post_standings` builds a single message
carrying both championships. FR-052 requires a fallback to carry the failed championship's section
alone, so the formatting is split at the section boundary and the composition moved to the caller.
This is the second-largest change outside the image module and the reason the feature is not purely
additive.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: discord.py; lxml (SVG mutation); fontTools (text measurement); aiosqlite;
Inkscape CLI (rasteriser, not a Python package — probed at conventional install locations, `INKSCAPE`
overrides)
**Storage**: SQLite via aiosqlite. **One migration**: `041_constructor_standings_message_id.sql`,
adding a nullable column to `driver_standings_snapshots` beside `standings_message_id`.
**Testing**: pytest from the repo root (`pytest tests/ -q`). Baseline as of 2026-08-13: **1399 passed,
1 skipped, 0 failed**.
**Target Platform**: the host running the bot (Windows and Linux both supported; the rasteriser is
probed per platform)
**Project Type**: single project — one Discord bot application, `src/` + `tests/`
**Performance Goals**: two rasterisations per standings posting per division. A full grid is the
largest graphic the module draws — 24 rows × 24 rounds × 4 sessions is ~2,300 addressed ids — so the
fill walks the tree once and indexes by id rather than searching per field, as it already does.
**Constraints**: a graphic is verified as a rasterised PNG, never as SVG in a browser
(Constitution XIV.14, CLAUDE.md). Discord admits no attachment on an already-posted message, so a
redraw replaces the message rather than editing it.
**Scale/Scope**: rows, rounds and cars are all counted from the template file, never fixed in code;
the cars of a round are additionally bounded per row by that row's team's configured seats.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1. Both passes below.*

| Principle | Gate | Verdict |
|---|---|---|
| **V. Observability & Audit** | Notices reach the calculation log channel; problems reach the audit log | **Pass** — `image_results_post.report_notices` is reused; the standings hook names season, division, round and championship (FR-053) |
| **VII. Output Channel Discipline** | The graphic rides on the source module's message in the source module's channel | **Pass** — posted by `post_standings` into the division's standings channel; no channel category registered |
| **IX. Team & Division Integrity** | Seats and team names are read, not redefined | **Pass** — the per-row car capacity reads the configured seat count at each check (XIV.12) |
| **XII. Race Results & Championship Integrity** | Nothing about standings is computed, ordered or tie-broken here | **Pass** — position and points are read from the persisted snapshot; the countback is already applied in `standing_position`. The three derived columns are arithmetic over published figures (XIV.7 as amended) |
| **XIV.1** Templates are data | No template is emitted by code; the canvas is read from the file | **Pass** |
| **XIV.2** `@id` with layer-label fallback; the six fill operations | Row, block, column and discriminated-column groups are all `FillSpec.remove` entries | **Pass** — no new operation |
| **XIV.3** Mandatory fields resolved; optional-as-a-unit | The round portion is optional as a unit; `round_<z>_number` is mandatory only where a round is declared | **Pass** — the v4.5.0 form, implemented as `optional_unit` on the spec |
| **XIV.4** Problems abort, notices survive; unit of failure is one graphic | Each championship renders on its own; one failure leaves the other and the round's results alone | **Pass** — the hook renders and posts the two independently |
| **XIV.5** Text bounds declared by the template | Driver, team and per-car driver names carry `inline-size`; truncation raises its notice | **Pass** — pipeline behaviour, unchanged |
| **XIV.6** Assets aspect-authored, referenced by URI | Flags, team images, track images and markers resolve through `asset_resolver` | **Pass** |
| **XIV.7** Additive; one rendering, two presentations; derived presentation | R4 places the derivation in `standings_service`; R6 splits the text path so a fallback carries one section | **Pass** — both conditions of the v4.5.0 clause met |
| **XIV.8** Attachments, not a channel | Each PNG attaches to the message carrying its heading and label | **Pass** |
| **XIV.9** Layered validity; structural checks refuse everywhere | R5: rows, rounds and cars are structurally checked at all three moments; the classification only at the render | **Pass** |
| **XIV.10** Catalogue as a code constant, one entry per image type | Two entries sharing their common part | **Pass, with one deviation** — see Complexity Tracking |
| **XIV.11** Ordinal discrimination, contiguous from 1; nesting | Three levels, each contributing its name and ordinal in containment order | **Pass** |
| **XIV.12** Capacity declared; overflow fatal; per-member variation | Rows and rounds counted from the file; cars bounded per row by the team's seats | **Pass** |
| **XIV.13** Slug resolution; every class carries a fallback; closed classes ship | The marker class ships `gained`/`lost`/`unchanged` (FR-032) | **Pass** |
| **XIV.14** Verified as PNG | The quickstart verifies both graphics as rasterised PNGs | **Pass** |
| **XIV.15** One configured time zone | The standings graphic draws no date and no time at all | **Not applicable** |
| **XIV.16** Nothing a reader can act on; the split is not exclusive | Mentions become names; the lifecycle label is drawn on the graphic **and** kept as message text | **Pass** — the v4.5.0 non-exclusivity clause is what permits this |

**Post-Phase-1 re-evaluation**: unchanged. The design added no principle violation. Complexity
Tracking gained no entry during Phase 1; both entries were identified before Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/040-standings-image-generation/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── standings-catalogue.md   # The two field catalogues and the grid id convention
│   ├── derived-columns.md       # The gap / previous position / position change contract
│   └── standings-posting.md     # Message ids, ordering, and the per-championship fallback
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── db/migrations/
│   └── 041_constructor_standings_message_id.sql   # NEW
├── models/
│   ├── image_catalogues.py       # + STANDINGS_DRIVERS_CATALOGUE, STANDINGS_CONSTRUCTORS_CATALOGUE,
│   │                             #   their shared common part; RowSpec.nested; NestedSpec.nested;
│   │                             #   optional_unit; per-member data-fixed capacity; a second
│   │                             #   top-level collection on FieldCatalogue
│   ├── image_constants.py        # unchanged — both template keys already registered
│   └── standings_snapshot.py     # + constructor_standings_message_id on DriverStandingsSnapshot
├── services/
│   ├── image_standings_service.py  # NEW — resolve_drawing + build_fill_spec (the utility)
│   ├── image_standings_post.py     # NEW — the two-graphic posting hook and its fallback decision
│   ├── image_sample_data.py        # + build_standings_drawing for both templates
│   ├── image_validity_service.py   # + grid structural checks in CatalogueLayer
│   ├── standings_service.py        # + derive_movement (R4) — the three derived columns
│   ├── results_post_service.py     # + the image branch in post_standings; the section split (R6);
│   │                               #   the second message id read/written
│   ├── result_submission_service.py # + the cross-session team check (FR-065)
│   └── placement_service.py        # + the row-ceiling refusal on driver/team assignment (FR-044)
├── utils/
│   └── results_formatter.py      # + format_driver_standings / format_team_standings callable
│                                 #   for one section alone; movement cell renderers
└── cogs/
    ├── image_cog.py              # + the standings guard on /images test
    └── season_cog.py             # + the row-ceiling check at season review (FR-043)

resources/markers/
├── gained.svg                    # NEW — closed class the module defines (XIV.13, v4.5.0)
├── lost.svg                      # NEW
└── unchanged.svg                 # NEW

tests/
├── test_image_standings_service.py   # NEW — resolution and projection, no Discord
├── test_image_standings_post.py      # NEW — posting, ordering, per-championship fallback
├── test_image_catalogues.py          # + the two catalogues, the grid, optional-as-a-unit
├── test_standings_service.py         # + derive_movement, including the cancelled-round step-back
└── test_result_submission_service.py # + the cross-session team check
```

**Structure Decision**: the existing single-project layout is kept exactly. The feature follows the
shape 037, 038 and 039 established — one `image_<type>_service.py` holding a pure `resolve_drawing`
and a `build_fill_spec`, one `image_<type>_post.py` holding the Discord-facing posting, and the
catalogue in the shared declaration module. Nothing new is introduced at the top level.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **Declaration-module changes**, which XIV.10 says adding an image type must not require: `FieldCatalogue` gains a second top-level collection, `RowSpec` gains a nest, `NestedSpec` gains a nest of its own and a per-containing-member capacity, and both gain the optional-as-a-unit flag | These are the *forms* v4.5.0 ratified (XIV.3 optional collection, XIV.11 three-level nesting, XIV.12 per-member capacity), not particulars of the standings type. The declaration module models one form per class; standings is simply the first type to need these three. Every later grid type — attendance's per-round points — reuses them unchanged | Expressing the grid as a flat enumeration of ids inside the standings catalogue would leave XIV.10 untouched and violate XIV.11's "never an enumeration of its members' ids", and would put a second, private id-construction rule beside the shared one. Declaring the grid in the utility rather than the catalogue would make it unreachable to validity Layer 2, which XIV.10 requires to read the *same object* the fill pipeline reads |
| **A change to the textual standings path**: `post_standings` composes one message from both championships, and is split so each section can be posted alone | FR-052 and XIV.7 as amended at v4.5.0 require a fallback to cover the failed graphic's scope and no more. With the composition welded to the posting, a single failing championship could only fall back by reposting both — duplicating whatever the surviving graphic already drew | Posting the whole textual message alongside the surviving graphic tells a league its constructor standings twice and contradicts the rule outright. Giving the image path its own text renderer would satisfy the grain and violate XIV.7's shared-rendering clause in the same stroke |

Neither is a deliberate violation left standing: the first implements forms the constitution now
carries, and the second brings the text path into line with a rule ratified before this feature.
