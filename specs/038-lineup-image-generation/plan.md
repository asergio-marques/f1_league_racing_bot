# Implementation Plan: Lineup Image Generation

**Branch**: `038-lineup-image-generation` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/038-lineup-image-generation/spec.md`

## Summary

Draw a division's lineup as a PNG and post it in place of the textual embed, wherever the league has
switched the `lineup` aspect on. The render itself is a small addition — one catalogue entry and one
resolution service, exactly as the calendar was.

What makes this increment large is that the lineup is the **first image type whose fields are named
after the league's own data**. Three things follow, and they are the whole of the design risk:

1. The catalogue declaration cannot express it. `RowSpec` describes one collection, discriminated by
   an ordinal, whose capacity is counted from the template. The lineup needs members keyed by a
   normalised team name, seats nested inside them, a singleton reserve block, and a capacity fixed
   by the **division** rather than by the template. Constitution v4.3.0 admitted all four; nothing in
   the code implements any of them.
2. A catalogue that depends on data cannot enumerate its own ids from a template alone. Every
   consumer — Layer 2, `_verify_against_data`, the fill pipeline — calls `all_mandatory_ids(root)`.
   The lineup needs the division too, and at two of the three verification moments there is no
   division to be had. A **binding** (the division's teams and seat counts) is threaded through as
   an optional argument, absent at configuration time and present at generation.
3. Team names become load-bearing. A name that does not normalise to a usable XML identifier makes a
   template unauthorable, so `/team add` and `/team rename` gain validation — and per the author's
   ruling that validation binds whether or not the image module is enabled.

The textual lineup is not reformed. It keeps its embed, its triggers and its delete-then-build
order; the image path is added beside it and the two diverge only where FR-025 requires.

## Technical Context

**Language/Version**: Python 3.11 (`from __future__ import annotations` throughout)
**Primary Dependencies**: discord.py (cogs, app_commands), lxml (`utils/svg_document`,
`utils/svg_fill`), aiosqlite (`src/db`), Inkscape as the external rasteriser (probed, never assumed
on PATH)
**Storage**: SQLite. **No new table and no new column** — `divisions.lineup_message_id` was added at
v2.8.0 and already carries what this feature needs.
**Testing**: pytest, `pytest tests/ -q` from the repo root. Baseline as of 2026-08-12: 1135 passed,
1 skipped, 0 failed. Run before and after and compare.
**Target Platform**: Windows host (dev) and Linux (deploy); the rasteriser path is probed on both.
**Project Type**: Single project — Discord bot, `src/` + `tests/`.
**Performance Goals**: Not throughput-bound. One render per division per refresh; a lineup refresh
already performs Discord I/O and a rasterise is comparable. The constraint that matters is that a
failed render must never delay or block a driver placement.
**Constraints**: Verification of any graphic is by rasterised PNG, never by SVG in a browser
(Constitution XIV.14). Assets are SVG only. A graphic carries no Discord mention.
**Scale/Scope**: ~12 teams × ~2 seats + an unbounded reserve block per division; up to ~6 divisions
per season. Field counts around 100–150 ids per template, matching the shipped example.

## Constitution Check

*GATE: evaluated before Phase 0, re-evaluated after Phase 1. Principles I–XIV, v4.3.0.*

| Principle | Bearing on this feature | Verdict |
|---|---|---|
| **II. Multi-Division Isolation** | One graphic per division; a failure in one must not affect another. FR-021 and SC-003. | ✅ Pass |
| **V. Observability & Change Audit Trail** | Notices to the calculation log; the existing `SIGNUP_LINEUP_POSTED` audit entry is kept on both paths. | ✅ Pass |
| **VII. Output Channel Discipline** | The graphic rides on the lineup channel the signup module already registered. No new channel category. | ✅ Pass |
| **IX. Team & Division Structural Integrity** | Directly extended at v4.3.0 — team name validity, reserve at server scope, uniform divisions gated on the aspect. FR-010 … FR-014, FR-018. | ✅ Pass |
| **X. Modular Feature Architecture** | The `lineup` aspect toggle already exists and defaults off. The team-name constraint is **not** module-gated, which is Principle IX's business rather than an image-module reach. | ✅ Pass |
| **XIV.2 (addressing, groups)** | `reserve_group` is a **mandatory** group — admitted at v4.3.0 and used here first. | ✅ Pass |
| **XIV.3 (classification)** | Per-member classification (`reserve_driver_1_name` mandatory, beyond it optional) — admitted at v4.3.0. | ✅ Pass |
| **XIV.4 (problems vs notices)** | Configured absence raises no notice — first use of the v4.3.0 suppression clause. | ✅ Pass |
| **XIV.7 (additive output)** | `render_for_posting` already enforces the commanded/uncommanded split; the lineup supplies call sites, not a second rule. | ✅ Pass |
| **XIV.9 (layered validity)** | Three moments, one evaluation. Stand-in warns, real data refuses. Requires a **warning** outcome the config command does not have today (research R5). | ⚠️ See Complexity Tracking |
| **XIV.10 (catalogue as code constant)** | One entry in `models/image_catalogues.py`. But "adding an image type MUST NOT require a change to the fill pipeline" is **not** met (research R2). | ⚠️ See Complexity Tracking |
| **XIV.11 / 12 (ids, capacity)** | Keyed, nested, singleton, data-fixed — all four newly implemented. | ✅ Pass |
| **XIV.13 (assets)** | Team, flag and driver classes are already configured with directories and each ships `fallback.svg`. | ✅ Pass |
| **XIV.14 (PNG verification)** | Quickstart and every visual test assert against the rasterised PNG. | ✅ Pass |
| **XIV.15 (one time zone)** | Not applicable — the lineup draws no date and no time. | ✅ N/A |

Two gates need justification and are recorded in Complexity Tracking below. Neither is a violation of
a rule's *intent*; both are places where the code's current shape cannot express what v4.3.0 admits.

## Project Structure

### Documentation (this feature)

```text
specs/038-lineup-image-generation/
├── plan.md              # This file
├── research.md          # Phase 0 output — 11 decisions
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── lineup-catalogue.md   # The field catalogue as a contract
├── checklists/
│   └── requirements.md  # Written by /speckit-specify
└── tasks.md             # Phase 2 — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
src/
├── models/
│   ├── image_catalogues.py      # MODIFIED — KeyedSpec, NestedSpec, SingletonSpec,
│   │                            #   LineupBinding; LINEUP_CATALOGUE entry
│   └── image_constants.py       # unchanged
├── services/
│   ├── image_lineup_service.py  # NEW — resolve_drawing / build_fill_spec, modelled
│   │                            #   line for line on image_calendar_service.py
│   ├── image_lineup_post.py     # NEW — the posting decision and message replacement
│   ├── image_render_service.py  # MODIFIED — thread `binding` into _verify_against_data
│   ├── image_validity_service.py# MODIFIED — CatalogueLayer binding-free path; warnings
│   ├── image_sample_data.py     # MODIFIED — build_lineup_drawing for /images test lineup
│   ├── placement_service.py     # MODIFIED — image branch in _refresh_lineup_post;
│   │                            #   reserve-overflow guard on assign
│   └── team_service.py          # MODIFIED — name validation; reserve at server scope
├── cogs/
│   ├── image_cog.py             # MODIFIED — warning outcome on /images template lineup
│   ├── team_cog.py              # MODIFIED — /team lineup posts the graphic
│   └── season_cog.py            # MODIFIED — review: team names, uniformity, lineup image
└── utils/
    └── asset_resolver.py        # unchanged — normalise() is reused as the key rule

tests/
├── unit/          # catalogue shapes, binding, name validation, resolution, fill spec
└── integration/   # the three verification moments, refresh paths, fallback isolation
```

**Structure Decision**: Single project, matching every prior increment. The lineup follows the
calendar's two-service split — `image_lineup_service.py` resolves data with no template in hand and
projects onto a template with no Discord in hand — with one addition the calendar did not need:
`image_lineup_post.py`, because the lineup has five posting surfaces against the calendar's two, and
FR-025/FR-025a require the image path and the textual path to differ in their replacement order.
Putting that decision in one module is what stops it being re-derived at each of the five.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| **XIV.10** — adding an image type changes the fill pipeline, not just the catalogue | The lineup's mandatory ids cannot be derived from the template alone; they depend on the division's teams and seat counts. `_verify_against_data` and `CatalogueLayer` both call `all_mandatory_ids(root)`, so an optional `binding` argument must be threaded through `FillSpec` and those two call sites. | **Letting the lineup service do its own verification** and leaving the catalogue thin was tried on paper and rejected: XIV.10 forbids two lists that could disagree, and it would put the mandatory-field check in two places for the two types. Threading one optional argument keeps a single catalogue as the authority. The change is additive — every existing call passes no binding and behaves as before — and future keyed types reuse it rather than extending again. |
| **XIV.9** — the config command gains a third outcome (accept-with-warning) | At the moment a lineup template is named there is no division, so a team-field divergence is a **stand-in** finding, which v4.3.0 makes a warning and not a refusal. `_set_template_filename` today can only accept or reject. | **Rejecting on the stand-in** would refuse a template that is correct for the season about to be built, which is the exact failure v4.3.0's stand-in rule exists to prevent. **Staying silent** would leave a manager to discover the divergence at season review with no earlier hint. The warning is reported alongside the command's own output, which is where XIV.4 already puts notices. |

Neither changes a command's signature, the three reported validity states, or the structure of a
validity report, so XIV.9's stable-surface invariant holds.

## Constitution Re-check (post-Phase 1)

Re-evaluated against the Phase 1 artefacts. No gate moved, and the design surfaced two things worth
recording:

**The two flagged gates got smaller, not larger.** Research R2 settled the binding as a single
optional parameter defaulting to `None`, threaded through one dataclass field and two call sites.
Every existing caller — the calendar's included — passes nothing and behaves exactly as before. R5
settled the warning as an addition to a command's *reply*, not to its surface. Both remain
justified rather than resolved, and both stay in Complexity Tracking.

**One gate was strengthened by the design.** XIV.9's "no silent pass" looked at risk while the
lineup's Layer 2 had no division to check against — the tempting shortcut being to skip the layer
entirely. R4 found that the reserve block is a *singleton* and therefore team-independent, so
`reserve_group`, contiguous reserve slots and `reserve_driver_1_name` are all checkable the moment a
template is named. The lineup reports genuine depth 2 rather than a skip.

**No new entity, no migration, no configuration.** data-model.md confirms the feature adds nothing
to the schema: `divisions.lineup_message_id` (v2.8.0) and the seven asset directories plus the
`lineup` toggle (035) already cover it. Principle X's configuration-isolation exception is not
engaged, since no configuration is introduced.

**Principle IX carries the one change outside the image module.** Team-name validation binds with
the module disabled, which is a reach beyond an optional module's own surface — and is exactly why
v4.3.0 put it in Principle IX rather than in Principle XIV. It is structural integrity, governed
where structural integrity is governed.
