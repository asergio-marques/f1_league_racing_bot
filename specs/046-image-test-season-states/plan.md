# Implementation Plan: Image previews across every season state

**Branch**: `046-image-test-season-states` | **Date**: 2026-08-19 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/046-image-test-season-states/spec.md`

## Summary

The eleven `/images test` previews resolve the server's ACTIVE season and refuse where there is none, which locks them out of the two moments they are most wanted: before a season is approved, and on a server that has no season at all.

The change is in three parts, and only the second is large.

1. **Widen the season lookup.** A season pending approval already holds its divisions, rounds, teams, seats and driver assignments in the same tables and the same shape as an approved one, so drawing it needs a new season lookup and nothing else.
2. **Fabricate a league where there is no season.** A new factory builds a `PreviewContext` that is complete without a database row behind it — a randomised division, calendar and roster over the server's own configured team list. Because every one of the eleven builders reads its data from the context, a fabricated context flows through all eleven unchanged. The one obstacle is that three builders re-query the database by `division_id` rather than reading the context; making the context self-sufficient is the enabling refactor.
3. **Make the two parameters optional**, required where a season exists and disregarded where none does.

Test mode needs no mechanism of its own. A mock driver is already named by its mock name once the season lookup reaches a SETUP season; the remaining work is to pin that as tested behaviour and to report drivers drawn without a nationality.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: `discord.py` (app commands), `aiosqlite`, `lxml` (SVG fill), `fonttools` (text metrics); Inkscape as the external rasteriser
**Storage**: SQLite via `src/db/database.py`, migrations under `src/db/migrations/`
**Testing**: `pytest` with `pytest-asyncio`, run as `pytest tests/ -q` from the repo root. Discord is stubbed throughout; no test may require a live bot.
**Target Platform**: Long-running Python process hosting a Discord bot
**Project Type**: Single project — `src/` and `tests/`
**Performance Goals**: Not a factor. A preview is invoked by hand and already spends seconds in the rasteriser subprocess; the fabrication is in-memory and adds nothing measurable.
**Constraints**: No schema change and no migration — the feature reads what exists and invents the rest in memory. No write of any kind to the league's records (FR-025). Fabrication must be injectable for tests, which otherwise cannot assert on randomised output.
**Scale/Scope**: Eleven commands, one cog, one service module plus one new one, and their unit tests. Roughly 11 fabricated rounds and 20-odd fabricated drivers per invocation.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Rule | Bearing on this feature | Verdict |
|---|---|---|
| **I. Trusted Configuration Authority** | The eleven commands keep `@channel_guard` + `@admin_only`. Making a parameter optional does not widen who may invoke one. | ✅ Pass |
| **VI. Incremental Scope Expansion** | The feature widens an existing command family and adds no module, no aspect and no toggle. | ✅ Pass |
| **VII. Output Channel Discipline** | A preview posts nothing to any division channel and replies ephemerally to its invoker. FR-025 keeps that true of the fabricated path. | ✅ Pass |
| **XIV.1 Templates are data** | No template is read differently, generated or amended. The fabricated league changes what fills a template, never the template. | ✅ Pass |
| **XIV.3 Every mandatory field MUST be resolved** | The fabricated league must supply every datum the eleven catalogues declare mandatory — division name, tier, season number, round number, track, schedule — or the render fails on data the feature itself invented. This is the sharpest obligation on the factory and is a named test. | ✅ Pass, under test |
| **XIV.4 Problems and notices are distinct** | Refusals are evaluated before any render (FR-006's ordering, inherited from 045) and are neither problems nor notices. The fabricated path adds no new outcome kind. | ✅ Pass |
| **XIV.13 Asset resolution and fallback** | FR-023 keeps the league's own configured directories on the fabricated path, so a fabricated league resolves and falls back exactly as a real one does. Drawing the packaged directories instead would breach this rule, and is what 045 removed. | ✅ Pass |
| **XIV.16 A graphic draws nothing a reader can act on** | A preview is ephemeral to its invoker and posts nowhere. FR-024 additionally obliges the reply to mark invented data, so a fabricated league cannot be mistaken for the league's own. | ✅ Pass |
| **Data & State Management** | No migration, no schema change, no new entity. The fabricated league exists for the duration of one invocation. | ✅ Pass |

**No violations.** The Complexity Tracking section is therefore omitted.

**Post-design re-check (after Phase 1).** Still clean, with one obligation sharpened. Designing the fabricated league made XIV.3 the feature's principal risk rather than a formality: a context invented in memory must satisfy every mandatory field of all eleven catalogues, and a miss shows up as the render abandoning on data the feature itself made up. [quickstart.md](quickstart.md) Scenario 4 therefore asserts *no `problem` outcome* per kind rather than merely that a picture came back, and `tasks.md` must carry that as a test per kind. No other gate moved: no entity was added by the design, nothing is written, and the asset path is the league's own on both routes.

**One amendment is owed, and is not this feature's.** The constitution stands at v5.0.0, last amended 2026-08-17, which precedes feature 045. Its versioned entity inventory still calls the test subcommands parameter *values* — "the `images test verdicts` value" under New Entities (v4.8.0), and "the four `images test weather-*` values" under New Entities (v4.7.0). That correction is a PATCH owed from 045, raised through `/speckit-constitution` and never by hand. This feature adds no entity and redefines none, so it carries no amendment of its own.

## Project Structure

### Documentation (this feature)

```text
specs/046-image-test-season-states/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── commands.md      # Phase 1 output — the eleven command contracts
├── checklists/
│   └── requirements.md  # Written by /speckit-specify
├── spec.md              # Written by /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── cogs/
│   └── image_cog.py                  # AMEND — optional parameters, kind classification,
│                                     #   autocomplete, reply banners
├── services/
│   ├── season_service.py             # AMEND — get_previewable_season,
│   │                                 #   get_previous_season_number
│   ├── image_preview_service.py      # AMEND — season lookup, self-sufficient context,
│   │                                 #   refusal split, nationality tally
│   ├── image_preview_league.py       # NEW — the fabricated league factory
│   └── image_preview_data.py         # UNCHANGED — 045's outcome fabrication
└── models/
    └── image_constants.py            # AMEND — the kind classification constant

tests/
└── unit/
    ├── test_image_preview_service.py     # AMEND — season states, refusal split
    ├── test_image_preview_league.py      # NEW — the fabricated league
    ├── test_image_cog_test_commands.py   # AMEND — optional parameters, banners
    └── test_image_preview_testmode.py    # NEW — mock drivers, nationality tally
```

**Structure Decision**: The repository is a single Python project — `src/` holding `cogs/`, `services/`, `models/`, `db/`, `utils/`, and `tests/` holding `unit/` and `integration/`. This feature stays inside that layout and adds one service module and two test modules. No new top-level directory, no migration, and no change under `resources/`.

The one deliberate placement decision is that the fabricated league goes in a **new module** rather than into `image_preview_service.py`. That service is already 1052 lines and owns resolution-of-real-data; the factory is a distinct job with a distinct dependency (randomness) that the tests must be able to pin. Keeping 045's `image_preview_data.py` untouched follows the spec's A-007: this feature invents a *league*, not an outcome.
