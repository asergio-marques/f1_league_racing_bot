# Implementation Plan: Results Image Generation

**Branch**: `039-results-image-generation` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/039-results-image-generation/spec.md`

## Summary

Draw one session's classification as a PNG in place of its textual table, from two templates —
qualifying and race — under the single `results` toggle. The rendering pipeline, the asset
resolution, the recolour operation, the validity layers and the posting decision are all already
built and are reused unchanged. What this feature adds is two catalogue entries, one resolution
utility, one posting hook, and a sample builder.

The load-bearing decision is **R3**: every value the graphic and the textual table both draw is
produced once. `results_formatter.py` gains a row-building layer returning the resolved cells; the
existing `format_qualifying_table` / `format_race_table` render those cells into text and the image
utility places the same cells onto fields. Neither path derives a value the other derives.

Three v4.4.0 forms appear for the first time and each maps onto machinery that already exists: the
**column group** and the **block group** are ids in `FillSpec.remove`; the **absent datum drawing
the class fallback** is the one genuine addition to the fill pipeline, and it is declared by the
catalogue rather than by the caller.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: discord.py; lxml (SVG mutation); fontTools (text measurement); aiosqlite;
Inkscape CLI (rasteriser, not a Python package — probed at conventional install locations, `INKSCAPE`
overrides)
**Storage**: SQLite via aiosqlite. **No migration**: `session_results.results_message_id` already
holds the message the image flow replaces.
**Testing**: pytest from the repo root (`pytest tests/ -q`). Baseline as of 2026-08-12: 1135 passed,
1 skipped, 0 failed.
**Target Platform**: the host running the bot (Windows and Linux both supported; the rasteriser is
probed per platform)
**Project Type**: single project — one Discord bot application, `src/` + `tests/`
**Performance Goals**: one rasterisation per session; up to four sessions per round per division.
Each render is an Inkscape subprocess, so every command path defers its Discord interaction before
rendering, as `/images test` already does.
**Constraints**: a graphic is verified as a rasterised PNG, never as SVG in a browser
(Constitution XIV.14, CLAUDE.md). Discord admits no attachment on an already-posted message, so a
redraw replaces the message rather than editing it.
**Scale/Scope**: a template declares as many rows as a league's grid needs (typically 16–24); the
capacity is counted from the file, not fixed in code.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1. Both passes below.*

| Principle | Gate | Verdict |
|---|---|---|
| **V. Observability & Audit** | Notices reach the calculation log channel; problems reach the audit log | **Pass** — `ImageRenderService.report_notices` already routes them; the results hook names season, division, round and session in the detail (FR-039) |
| **VII. Output Channel Discipline** | The graphic rides on the source module's message in the source module's channel | **Pass** — posted by `results_post_service.post_session_results`, in the division's results channel; no channel category is registered |
| **XII. Race Results & Championship Integrity** | Nothing about results is computed, submitted or reordered here | **Pass** — the utility reads the persisted classification, including the renumbering the penalty wizard already performed |
| **XIV.1** Templates are data | No template is emitted by code; the canvas is read from the file | **Pass** |
| **XIV.2** `@id` with layer-label fallback; the six fill operations | Column and block groups are `remove` entries; the recolour is the existing operation | **Pass** — no new operation |
| **XIV.3** Mandatory fields resolved; determined-empty is determined | Sanction fields of an open phase, and the gap of the reference entry, go through `FillSpec.empty_quietly` | **Pass** — the field already exists and carries exactly this meaning |
| **XIV.4** Problems abort, notices survive; unit of failure is one graphic | Each session renders on its own; one failure leaves the round's other sessions and the standings alone | **Pass** — `post_session_results` is per session already |
| **XIV.5** Text bounds declared by the template | Driver and team names carry `inline-size` in the shipped templates; truncation raises its notice | **Pass** — pipeline behaviour, unchanged |
| **XIV.6** Assets aspect-authored, referenced by URI | Flags, team images and tyres resolve through `asset_resolver` | **Pass** |
| **XIV.7** Additive; **one rendering, two presentations** | R3: the row-building layer is the single derivation | **Pass, with one correction** — see Complexity Tracking |
| **XIV.8** Attachments, not a channel | The PNG attaches to the message carrying the heading and label | **Pass** |
| **XIV.9** Layered validity; structural checks refuse everywhere | R4: the row structure is checked at all three moments; the entry count only at the render | **Pass** |
| **XIV.10** Catalogue as a code constant, one entry per image type | Two entries, `results_qualifying_template` and `results_race_template`, sharing their common part | **Pass, with one deviation** — see Complexity Tracking |
| **XIV.11** Ordinal discrimination, contiguous from 1 | `RowSpec(prefix="row", capacity=None)`; the position field is filled from the ordinal | **Pass** |
| **XIV.12** Capacity by the template; overflow fatal | Counted from the file; `FillSpec.row_count` raises the existing capacity problem | **Pass** |
| **XIV.13** Slug resolution, every class carries a fallback | Unchanged for flags and team images; the absent tyre uses the v4.4.0 declaration | **Pass** |
| **XIV.14** Verified as PNG | The quickstart verifies both graphics as rasterised PNGs | **Pass** |
| **XIV.15** One configured time zone | The results graphic draws no date and no time at all | **Not applicable** |
| **XIV.16** Nothing a reader can act on | Mentions become names; the heading and lifecycle label stay message text | **Pass** |

**Post-Phase-1 re-evaluation**: unchanged. The design added no principle violation; the two entries
in Complexity Tracking were both identified before Phase 0 and neither grew.

## Project Structure

### Documentation (this feature)

```text
specs/039-results-image-generation/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── results-catalogue.md      # The two field catalogues
│   └── shared-rendering.md       # The row-building contract between text and graphic
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── models/
│   ├── image_catalogues.py        # + RESULTS_QUALIFYING_CATALOGUE, RESULTS_RACE_CATALOGUE,
│   │                              #   the shared common part, sibling-field detection,
│   │                              #   RowSpec.fallback_when_absent
│   └── image_constants.py         # unchanged — both template keys already registered
├── services/
│   ├── image_results_service.py   # NEW — resolve_drawing + build_fill_spec (the utility)
│   ├── image_results_post.py      # NEW — the posting hook and its fallback decision
│   ├── image_sample_data.py       # + build_results_drawing for both templates
│   ├── image_validity_service.py  # + the sibling-field check in CatalogueLayer
│   └── results_post_service.py    # + the image branch inside post_session_results
├── utils/
│   ├── results_formatter.py       # + build_qualifying_rows / build_race_rows (R3);
│   │                              #   _pen_col corrected to the wip-spec's precision
│   └── svg_fill.py                # + catalogue-declared absent-datum fallback
└── cogs/
    └── image_cog.py               # + the results guard on /images test

tests/
├── test_image_results_service.py  # NEW — resolution and projection, no Discord
├── test_image_results_post.py     # NEW — posting, replacement ordering, fallback
├── test_image_catalogues.py       # + the two catalogues and sibling detection
└── test_results_formatter.py      # + the row builders and the penalty precision
```

**Structure Decision**: the existing single-project layout is kept exactly. The feature follows the
shape 037 and 038 established — one `image_<type>_service.py` holding a pure `resolve_drawing` and a
`build_fill_spec`, one `image_<type>_post.py` holding the Discord-facing posting, and the catalogue
in the shared declaration module. Nothing new is introduced at the top level.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **A fill-pipeline change**, which XIV.10 says adding an image type must not require: `svg_fill` learns that a catalogue may declare an image field whose **absent datum** draws the class fallback with no notice | The rule is XIV.13 as amended at v4.4.0, ratified before this feature and generic to every image type. The results type is merely the first to declare it | Passing a sentinel datum from the caller would reach the fallback, but through the *notice-raising* branch — the render would report "no tyre image for ''" once per row, which is precisely what the amendment settled against. Keeping the declaration in the catalogue is also what XIV.10 requires of the object the fill pipeline consults |
| **A correction to the textual table**: `_pen_col` truncates a penalty to whole seconds (`ms // 1000`), so a 5.5-second penalty renders "+5s" today | The wip-spec states the precision rule for a time penalty "**wherever one is placed**", and XIV.7 now requires one rendering for both paths. The graphic cannot be correct while sharing a renderer that is not | Giving the graphic its own penalty renderer would satisfy the wip-spec's precision and violate XIV.7 in the same stroke — two derivations of one value, free to drift. The spec's Out of Scope has been amended to admit this one correction |

Neither is a deliberate violation left standing: the first implements a rule the constitution now
carries, and the second brings existing code into line with a rule that predates it.
