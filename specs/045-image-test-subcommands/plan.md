# Implementation Plan: Image test commands drawn from the league's own configuration

**Branch**: `hotfix/image-module-poc` | **Date**: 2026-08-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/045-image-test-subcommands/spec.md`

## Summary

Replace `/images test <kind>` — which draws every image type from an invented "Test Division" and the packaged artwork — with eleven subcommands, each drawing a named division of the active season and, for nine of them, a named round, using the league's own teams, drivers, calendar and asset directories. Only the outcomes a league cannot configure in advance are fabricated: classifications, standings, attendance records, forecasts and verdicts.

The technical approach follows from one finding (research R1): every image type is already split into a database-reading half (`image_*_post.py`) and a pure `resolve_drawing(**values)` / `build_fill_spec(drawing, root, asset_directories=…)` half (`image_*_service.py`). The preview replaces the first half and shares the whole of the second. No `resolve_drawing` signature changes, and no posting path is touched.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: discord.py (slash commands), aiosqlite, lxml (SVG), fonttools (text metrics), Inkscape (external rasteriser, not pip-installable)
**Storage**: SQLite. **No schema change** — the preview reads existing tables and writes nothing (see [data-model.md](data-model.md))
**Testing**: pytest, Discord stubbed. No test may require a live bot
**Target Platform**: Windows and Linux hosts running the bot
**Project Type**: Single project — `src/` and `tests/`
**Performance Goals**: Each command defers immediately; a multi-picture render is a subprocess per picture and is not expected within Discord's 3-second acknowledgement, which is what the deferral is for
**Constraints**: Discord permits one level of subcommand groups and at most 25 subcommands per group; the eleven sit inside a nested `test` group (research R4)
**Scale/Scope**: Eleven commands, one shared resolution path, one fabrication module; `image_sample_data.py` (1264 lines) retired

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design. Both passes clean.*

| Principle | Bearing on this feature | Verdict |
|---|---|---|
| **XIV.13** — asset resolution has three outcomes | The withdrawn command sidestepped all three by hardcoding the packaged directories. FR-035 makes the preview resolve the league's configured directories, so the rule applies as written | ✅ **Compliance gained.** This feature moves the preview *into* XIV.13, not away from it |
| **XIV** — a commanded posting MUST NOT fall back | FR-007: a fatal error rejects and posts nothing; no preview has a textual counterpart | ✅ Matches |
| **XIV.14** — a generated image is verified as PNG | [quickstart.md](quickstart.md) directs judgement to the PNG and forbids the browser | ✅ Matches |
| **XIV.1** — templates are data, not code | No template is generated or edited; catalogues, capacities and crop rules are untouched | ✅ Untouched |
| **XIV.9** — stable surface | The rule binds the addition of *validity layers*. This adds none | ✅ Not engaged |
| **XIV.7** — one rendering, two presentations | FR-034 draws sanctions through `verdict_announcement_service.describe_penalty`, the rendering the rule obliges the graphic to call, and invents no second one | ✅ Matches |
| **Bot Behavior Standards** — `/domain action` | `/images test calendar` is command → group → subcommand, the depth `/images config toggle` already uses | ✅ Matches |
| **II** — multi-division isolation | Every preview is scoped to one named division; nothing crosses | ✅ Matches |
| **V** — observability | Notices continue to reach the log channel; the reply gains the artwork report | ✅ Matches |

**One deferred amendment, and it is not a gate.** Two statements in the constitution's versioned entity inventory describe the test command as a parameter carrying choice values — "the `images test verdicts` value" (New Entities v4.8.0) and "the four `images test weather-*` values" (v4.7.0). This feature makes those values subcommands. That is a **PATCH** under the versioning policy: no Core Principle is removed or redefined. Per the user's direction and the constitution's own precedent for `README.md` — a document describing the bot as it is, corrected when the behaviour it describes exists — the amendment is raised **at the end of implementation, if anything remains to raise**, through `/speckit-constitution` and never by hand.

**No Complexity Tracking table.** There is no violation to justify.

## Project Structure

### Documentation (this feature)

```text
specs/045-image-test-subcommands/
├── plan.md                        # This file
├── spec.md                        # Phase -1
├── research.md                    # Phase 0
├── data-model.md                  # Phase 1
├── quickstart.md                  # Phase 1
├── contracts/
│   └── command-surface.md         # Phase 1
├── checklists/
│   └── requirements.md
└── tasks.md                       # Phase 2 — NOT created by /speckit-plan
```

### Source code

```text
src/
├── cogs/
│   └── image_cog.py               # MODIFIED — `test` becomes a nested Group of 11
├── services/
│   ├── image_preview_service.py   # NEW — resolution, refusals, asset directories
│   ├── image_preview_data.py      # NEW — fabrication of outcomes
│   ├── image_sample_data.py       # RETIRED — long-text constants move out first
│   ├── image_calendar_service.py  # unchanged — resolve_drawing called as it stands
│   ├── image_lineup_service.py    # unchanged
│   ├── image_results_service.py   # unchanged
│   ├── image_standings_service.py # unchanged
│   ├── image_attendance_service.py# unchanged
│   ├── image_rsvp_service.py      # unchanged
│   ├── image_weather_service.py   # unchanged
│   ├── image_verdict_service.py   # unchanged
│   └── image_*_post.py            # unchanged — no posting path is touched
├── models/
│   └── image_constants.py         # MODIFIED — TEST_KIND_TEMPLATES reshaped to the 11
└── utils/
    └── svg_fill.py                # unchanged — already emits the fallback notices

tests/
└── unit/
    ├── test_image_preview_service.py   # NEW — resolution and the 9 refusals
    ├── test_image_preview_data.py      # NEW — fabrication invariants
    └── test_image_cog_test_commands.py # NEW — command surface, ordering, reply shape
```

**Structure Decision**: Single project, matching the repository as it stands. The two new services sit beside the existing `image_*_service.py` modules and follow their conventions; the cog is the only existing file whose behaviour changes.

## Implementation sequence

Ordered so that each step is covered by a passing test before the next begins, per the repository's testing rule.

1. **Resolution and refusals** — `image_preview_service.py`: active season, division by name, round by number, teams, drivers, the nine refusals in their contracted order, and the `PreviewContext` shape. Covered by `test_image_preview_service.py`. This is US1's foundation and every other step depends on it.
2. **Asset directories** — resolve the league's eight configured directories, capturing the `DirectoryFault` the posting path discards (research R3). Covered in the same test module.
3. **Calendar and lineup previews** — the two kinds that fabricate no outcome; the all-or-nothing driver fabrication rule lands here. Completes **US1** as an independently shippable slice.
4. **Fabrication module** — `image_preview_data.py`: classifications, standings, attendance records, verdicts, with the invariants of [data-model.md](data-model.md). Covered by `test_image_preview_data.py`.
5. **Results, standings, attendance, verdict previews** — completes **US2**.
6. **Forecast fabrication and the four weather previews** — completes **US3**.
7. **Command surface** — the nested `test` group, autocomplete on `division`, the reply shape including the artwork report, and `_verify_discord_group_limits()` extended to the new group.
8. **Retire `image_sample_data.py`** — move the long-name and long-prose constants first, delete the rest, and remove `TEST_KIND_TEMPLATES`' old shape.
9. **Documentation** — `README.md`, `docs/how-to/configuring-the-image-module.md` (including the standing warning FR-035 withdraws) and `docs/how-to/configuring-the-results-module.md`, per the spec's Documentation impact section. Then the constitution PATCH, if anything remains.

## Notes carried forward

- **A defect to log, not to fix.** The posting path's `except Exception: pass` when resolving an asset directory silently turns a rejected path into "asset class not configured" on a real post, with no reason given. Out of scope here; belongs in `docs/wip-specs/known_issues.md` (research R3).
- **A test case deliberately lost.** The verdict preview no longer forces a sprint session; that case is now reached by previewing a sprint round (research R7).
