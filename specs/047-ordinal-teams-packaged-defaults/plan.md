# Implementation Plan: Ordinal addressing of teams, and packaged asset defaults

**Branch**: `047-ordinal-teams-packaged-defaults` | **Date**: 2026-08-20 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/047-ordinal-teams-packaged-defaults/spec.md`

## Summary

Two changes to the image module, joined by one aim: a league gets working graphics without authoring a template against its own team list and without hand-placing a fallback image.

The lineup's **keyed** team collection becomes an **ordinal** one, which is a deletion as much as a rewrite: `KeyedSpec`, `LineupBinding`, `divergent_members` and the binding plumbing that carries them through the fill pipeline all go, replaced by the `RowSpec` the other six graphics already use. Capacity passes from the data to the template, so a divergence fatal in both directions becomes an overflow fatal in one. The shipped `lineup_template.svg` is redrawn against ordinals.

Asset resolution gains a second fallback tier, which is a much smaller change than it sounds: every asset in the module resolves through **one** call site, so the tier lands in one place and reaches all fifteen graphics at once. The packaged directories move to `resources/defaults/`.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: discord.py ≥2.0, lxml ≥5.0 (SVG as XML), aiosqlite, APScheduler, fonttools
**Storage**: SQLite via `aiosqlite`; templates and assets are files on disk under `resources/`
**Testing**: pytest (+ `pytest-asyncio`), `coverage`; the `rasteriser` marker deselects Inkscape-dependent tests in CI
**Target Platform**: Discord bot, run on Windows and Linux alike; Inkscape is the local SVG rasteriser
**Project Type**: Single project — `src/` with `models/`, `services/`, `cogs/`, `utils/`; `tests/unit/` and `tests/integration/`
**Performance Goals**: Not a factor. Generation is per-posting and already bounded by the rasteriser
**Constraints**: No test may require a live Discord bot. Line coverage must stay ≥ 75 (`MIN_COVERAGE_REQUIRED` in `.github/workflows/unit-test.yml`)
**Scale/Scope**: Fifteen templates, seven asset classes, eight graphics; ~2440 tests in the suite today

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against **constitution v6.0.0**, amended 2026-08-20 specifically to admit this feature. Dependency `D-001` of the spec is thereby cleared.

| Principle / Rule | Bearing on this feature | Verdict |
|---|---|---|
| **IX** — Team & Division Structural Integrity | "Team name validity" now normalises to a *filename*; the leading-letter rule is withdrawn. "Divisions MAY differ in composition" replaces the uniformity invariant | **PASS** — the feature implements exactly what v6.0.0 states |
| **XIV.11** — Template ids | Ordinal-only discrimination; the keyed collection is withdrawn; a singleton keeps its reserved name | **PASS** — the lineup is the only keyed collection and this feature removes it |
| **XIV.12** — Collection capacity | Two ways, not three; teams pass to the template; the nested **ceiling** now covers the lineup's seats as well as the results grid's cars | **PASS** — FR-018 makes the two behave identically, which is what the rule already described |
| **XIV.13** — Asset resolution | Four outcomes; packaged directory at `resources/defaults/<class>`; normalisation names a file and never a field | **PASS** |
| **XIV.2** — Removable groups | `team_<x>_group` is a member group of the ordinary `<collection>_<x>_group` form | **PASS** |
| **XIV.3** — Mandatory/optional | `team_<x>_name` and `team_<x>_driver_<y>_name` mandatory throughout declared blocks; `team_<x>_group` optional | **PASS** |
| **XIV.9** — Verification moments | The lineup gains a property no other graphic has: **no** stand-in moment. Every field is checkable against the template alone | **PASS** — a strengthening, not a violation |
| **XIV.14** — PNG verification | The redrawn template must be verified as a rasterised PNG, never as SVG in a browser | **PASS** — carried as an explicit task |
| **Testing discipline** (CLAUDE.md) | Every implementation task carries its unit test; no task requires a live bot | **PASS** |

**No violations.** The Complexity Tracking table is therefore omitted.

One consequence reaches **outside** the image module and should be seen before implementation starts: R5's ordering fix changes how teams are listed in textual `season review` output, from alphabetical to the order they were added. Principle II (Multi-Division Isolation) and Principle IX are unaffected — no team moves between divisions and no invariant is relaxed — but a league will see a different order in a place the image module does not own. It is carried here rather than hidden in a task.

One further item is **not** a violation but is worth recording: this feature deletes public-ish surface (`KeyedSpec`, `LineupBinding`, `binding_from_teams`, `divergences`, `FieldCatalogue.divergent_members`, the `binding=` parameter threaded through `FillSpec` and the catalogue accessors). Constitution XIV.10 requires each image type to declare a catalogue as a code constant; it does not require any particular spec class to exist. Removing the keyed machinery is the change, not a shortcut around it.

## Project Structure

### Documentation (this feature)

```text
specs/047-ordinal-teams-packaged-defaults/
├── plan.md              # This file
├── research.md          # Phase 0 output — the eight decisions this design turns on
├── data-model.md        # Phase 1 output — catalogue and resolution structures
├── quickstart.md        # Phase 1 output — how to validate the feature end to end
├── contracts/
│   ├── lineup-catalogue.md      # The lineup template's field contract, ordinal form
│   └── asset-resolution.md      # The two-tier resolver's contract
├── checklists/
│   └── requirements.md  # Written by /speckit-specify; carries two audits
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── models/
│   ├── image_catalogues.py       # KeyedSpec + LineupBinding + divergent_members removed;
│   │                             #   LINEUP_CATALOGUE re-expressed as rows=RowSpec(...);
│   │                             #   singleton_capacity() added to break the capacity() collision
│   └── image_constants.py        # Eight default directories repathed; packaged_directory_for()
├── services/
│   ├── image_lineup_service.py   # resolve_drawing keyed→ordinal; per-block seat overflow;
│   │                             #   binding_from_teams/divergences removed
│   ├── image_lineup_post.py      # team query → ORDER BY is_reserve ASC, id ASC (R5)
│   ├── image_preview_service.py  # same ordering change (R5)
│   ├── image_render_service.py   # divergent_members call site removed
│   ├── image_preview_league.py   # stale keyed rationale reworded (FR-052)
│   └── team_service.py           # leading-letter rule removed; diagnostics reworded;
│                                 #   get_division_teams ordering (R5) — also changes
│                                 #   textual `season review` output
├── cogs/
│   └── season_cog.py             # _lineup_problems: uniformity check removed, ungated,
│                                 #   recast as a count measurement
└── utils/
    ├── asset_resolver.py         # resolve_asset/has_fallback gain the packaged tier
    └── svg_fill.py               # the single resolve_asset call site; binding= removed

resources/
└── defaults/                     # tracks, teams, flags, drivers, markers, weather, tyres,
    └── templates/                #   templates — all moved here by `git mv`
        └── lineup_template.svg   # redrawn: 11 ordinal blocks x 2 seats, each in a group

tests/
├── unit/                         # test_asset_resolver, test_image_catalogues,
│                                 #   test_image_lineup_service, test_team_service,
│                                 #   test_image_preview_league, + a new per-graphic sweep
└── integration/                  # test_image_module_flow — repathed
```

**Structure Decision**: The existing single-project layout is kept unchanged. No new package, module boundary or directory is introduced in `src/`; every edit lands in a file that exists today. The one structural change is under `resources/`, which gains a `defaults/` level.

## Phase 0 — Research

See [research.md](research.md). Eight decisions, of which three were not obvious from the spec and would have caused rework if discovered during implementation:

- **R1** the `FieldCatalogue.capacity()` collision — giving the lineup a `rows` spec silently redirects the reserve-slot count, breaking `reserve_capacity_problem` in a way no current test would catch;
- **R4** the shipped template has **no per-team `<g>` element**, so `team_<x>_group` is an SVG-authoring job and not a rename;
- **R6** `resolve_asset` has exactly one call site in `src/`, which is what makes the two-tier change small.

**R5 is the finding that most changes this design, and it was not visible from the spec.** A division's teams are ordered **alphabetically by name** in all three queries that produce the list — the posting path, the preview path and `season review`. That falsifies FR-008: adding a team whose name sorts early pushes every later team down one ordinal, and renaming moves a team. The fix is `ORDER BY is_reserve ASC, id ASC` at all three sites, which needs no schema change because `id` already carries insertion order. It also changes the order teams are listed in **textual** `season review` output, which is intended — the graphic and the text beside it must agree, or an ordinal in one will not match a position in the other.

## Phase 1 — Design & Contracts

- [data-model.md](data-model.md) — the catalogue structures before and after, and the resolution result type.
- [contracts/lineup-catalogue.md](contracts/lineup-catalogue.md) — the field contract a lineup template is authored against.
- [contracts/asset-resolution.md](contracts/asset-resolution.md) — the resolver's four paths and the signature that carries them.
- [quickstart.md](quickstart.md) — how to validate, including the rasteriser pass CI cannot run.

### Constitution re-check, post-design

Re-evaluated after Phase 1. Still **PASS**, with one observation the design surfaced and resolved rather than deferred:

An earlier draft of this plan had the lineup's seats and the results grid's cars behaving differently — the grid a **ceiling**, the lineup a template-fixed capacity measured against a team's *configured* seats. FR-018 withdrew that split, and the plan is corrected rather than defended.

Both are a nested collection bounded by a configured value of the member containing it, which is exactly what Rule XIV.12's ceiling paragraph already describes, and the rule's own words settle it: *"the fatal test is against the data actually drawn"*. So the lineup's seats set `NestedSpec.capacity_per_member = True` like the grid's cars, over-declaration is never an error in either, and a fatal error arises only where a driver who actually occupies a seat would vanish from the picture.

The one behaviour this gives up is visible: a team configured with three seats but seating two draws without complaint on a two-slot template, and the empty third seat is not shown. A vacancy is only displayed where the block is large enough to hold it. That is a consequence worth knowing, not a defect.

## Complexity Tracking

Not applicable — the Constitution Check records no violations.
