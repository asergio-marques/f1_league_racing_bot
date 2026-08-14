# Implementation Plan: Verdicts Image Generation

**Branch**: `043-verdicts-image-generation` | **Date**: 2026-08-14 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/043-verdicts-image-generation/spec.md`

## Summary

Add the seventh image type: one catalogue, one template, three kinds of verdict. A verdict graphic
replaces the textual penalty/appeal/attendance-sanction announcement in a division's verdicts channel,
the message keeping the driver mention alone.

The work divides into three unequal parts. **The catalogue and the fill** are the smallest per-type
job the module has had — no collection, no capacity, no floor, and a working precedent in the weather
mystery catalogue. **The wrapping contract** is the real work: this is the module's first type to draw
prose a person wrote, and the general contract stated at Constitution XIV.5 is only partly implemented
today. **The posting flow** is a new shape — a static graphic on a message that is never edited,
attached to three different trigger points in two other modules, none of which may be delayed or
conditioned by it.

## Technical Context

**Language/Version**: Python 3.11+ (`from __future__ import annotations` throughout)
**Primary Dependencies**: `discord.py` ≥2.0, `lxml` ≥5.0 (SVG mutation), `fonttools` ≥4.50 (text
measurement), Inkscape CLI (rasteriser, not a package — a binary the host must carry)
**Storage**: SQLite via `aiosqlite`. **No migration in this feature** — no new table, no new column.
**Testing**: `pytest` + `pytest-asyncio`, run as `pytest tests/ -q` from the repo root.
Baseline on this branch: **1876 passed, 1 skipped**.
**Target Platform**: The bot host; Windows and Linux both, the rasteriser probed at conventional
install locations with `INKSCAPE` overriding.
**Project Type**: Single project — Discord bot, `src/` + `tests/`.
**Performance Goals**: Not a driver. One render per verdict, off the interaction path entirely; a
review applying *n* penalties renders *n* graphics after finalisation has already completed.
**Constraints**: No render may delay or condition a review finalisation or a sanction enforcement
(XIV.7). No test may require a live Discord bot (CLAUDE.md). Every visual check against the rasterised
**PNG**, never the SVG in a browser (XIV.14).
**Scale/Scope**: One new catalogue entry, one new service, one new posting module, one sample-data
builder, three call sites in existing services, plus targeted changes to the shared fill pipeline.

No NEEDS CLARIFICATION items remain. The five divergences that would have produced them were put to
the author during the constitution audit and are settled in the spec; the sixth surfaced during this
plan's survey and is recorded in [research.md](./research.md) §1.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against constitution **v4.8.0**, Principles I–XIV. Principle XIV governs in detail; V, VII
and IX are touched.

| Gate | Verdict | How this plan satisfies it |
|---|---|---|
| **XIV.1** Templates are data | ✅ | `verdicts_template.svg` ships and is authored; no code emits SVG. Canvas read from the template root. |
| **XIV.2** Fields addressed by `@id` | ✅ | Catalogue names ids; existing `FieldIndex` resolves them with the layer-label fallback. `team_name_group` and the two heading groups are removable groups already in the template. |
| **XIV.3** Mandatory fields resolved | ✅ | 12 text + 2 image fields classified in the catalogue. The attendance sanction's emptied `session_name` is the **determined-empty** case, not an unresolved one — the clause added at v4.8.0. |
| **XIV.4** Problems vs notices | ✅ | Two new **problem** kinds for wrapping defects (research §2); truncation, font substitution and asset fallback stay notices. |
| **XIV.5** Text bounds | ⚠️ **work** | The contract is stated in full at v4.8.0 and the pipeline implements roughly half of it. Three gaps, all in research §2. |
| **XIV.6** Assets aspect-authored | ✅ | Flags and team images are existing classes; nothing new is shipped. |
| **XIV.7** Additive; may not decide | ✅ | Every value is read or rendered by the owning module. `translate_penalty` is called, never restated. The flag, the badge and the stage are drawn under the **floor** reading ratified at v4.8.0. |
| **XIV.8** Attachments, no new channel | ✅ | Posts to the division's existing verdicts channel. No message id persisted — the delete-and-repost lifecycle does not arise. |
| **XIV.9** Layered validity | ✅ | Layer 1 and Layer 2 already exist and are catalogue-driven; a populated catalogue enrols verdicts automatically. The wrapping checks are **structural** and belong to Layer 2. |
| **XIV.10** Catalogue as code constant | ✅ | One entry in `CATALOGUES`, keyed `verdicts_template`. Declares no collection — the second such, after the mystery notice. |
| **XIV.11** Id convention | ✅ | Flat `snake_case` ids, no discriminators, nothing to nest. |
| **XIV.12** Capacity | ✅ (vacuous) | No collection, so no capacity and no floor. |
| **XIV.13** Asset resolution | ✅ | Flag and team classes resolve by the existing normalised-slug rule; the nationality-toggle suppression is justified per field. |
| **XIV.14** Verified as PNG | ✅ | [quickstart.md](./quickstart.md) is PNG-only; no browser check is offered as evidence. |
| **XIV.15** One time zone | ✅ (vacuous) | The graphic draws no date and no time. |
| **XIV.16** Nothing actionable | ✅ | No mention on the canvas; mentions **inside** free text resolved in place per the v4.8.0 clause; the fixed absent-value text carried without its channel emphasis. |
| **XIV.17** Redraw / static | ✅ | Declared **static** on the record-of-an-event ground, with the condition that corrections arrive as new verdicts. |
| **V** Observability | ✅ | Notices to the server log channel naming season/division/round/session/driver; never to a verdicts channel. |
| **VII** Output channel discipline | ✅ | No channel category registered; the division's configured verdicts channel is used. |
| **IX** Team/division integrity | ✅ | Team resolved through the division's team holding the recorded Discord role, falling back to the role name. |

**Result: PASS.** One gate carries work rather than a violation — XIV.5 is a contract this feature is
the first to exercise, and closing the gap is implementation, not an exception. **Complexity Tracking
is therefore empty and the section is omitted.**

### Re-evaluation after Phase 1 design

Re-run against the artifacts below. **Still PASS, with one thing worth stating.**

`FR-013` makes a missing `line-height` on a wrapped field fatal, replacing a substituted default of
1.2. That is a *tightening* of XIV.4's problem set, and the constitution recorded the evidence for it
being safe at v4.8.0: of the fifteen shipped templates, `verdicts_template.svg` is the only one
declaring `shape-inside` at all, and both of its wrapped fields declare `line-height`. Phase 1 design
does not change that: no other catalogue gains a wrapped field here. The change is confined to the one
type introducing wrapping, and no template that renders today stops rendering.

## Project Structure

### Documentation (this feature)

```text
specs/043-verdicts-image-generation/
├── plan.md              # This file
├── research.md          # Phase 0 — six decisions, the survey findings
├── data-model.md        # Phase 1 — the catalogue and the drawing shapes
├── quickstart.md        # Phase 1 — how to prove it works, against PNGs
├── contracts/
│   ├── verdicts-catalogue.md   # The field catalogue as a contract
│   ├── text-wrapping.md        # The general wrapping contract and its gaps
│   └── verdicts-posting.md     # Trigger points, message shape, fallback
├── checklists/
│   └── requirements.md  # Written by /speckit-specify
└── tasks.md             # Phase 2 — /speckit-tasks, NOT created here
```

### Source code (repository root)

```text
src/
├── models/
│   ├── image_catalogues.py      # + VERDICTS_CATALOGUE, registered in CATALOGUES
│   ├── image_constants.py       # + two notice/problem kinds for wrapping defects
│   └── image_module.py          # + problem kind constants
├── services/
│   ├── image_verdict_service.py # NEW — resolution + fill spec (pure, no Discord, no DB)
│   ├── image_verdict_post.py    # NEW — the posting flow and its three triggers
│   ├── image_sample_data.py     # + build_verdict_drawing, + build_spec branch
│   ├── verdict_announcement_service.py  # + image path at the three trigger points
│   └── attendance_service.py    # autosack/autoreserve trigger reaches the image path
├── utils/
│   └── svg_fill.py              # wrapping gaps: word-breaking, leading, extent
└── cogs/
    └── image_cog.py             # + `images test verdicts` case

tests/
├── unit/
│   ├── test_image_verdicts_catalogue.py   # NEW
│   ├── test_image_verdicts_fill.py        # NEW
│   ├── test_image_verdicts_service.py     # NEW
│   ├── test_image_verdicts_post.py        # NEW
│   ├── test_image_verdicts_notices.py     # NEW
│   ├── test_image_verdicts_validity.py    # NEW
│   ├── test_svg_fill.py                   # + wrapping contract cases
│   └── test_image_sample_data.py          # + the six verdict cases
└── integration/
    └── test_image_module_flow.py          # + verdict posting end to end (Discord stubbed)
```

**Structure Decision**: The module's established per-type shape is followed exactly — one
`image_<type>_service.py` holding pure resolution and fill-spec construction, one
`image_<type>_post.py` holding everything that touches Discord and the database, one catalogue entry,
one sample-data builder, and a test file per concern. Six types already sit in this shape; deviating
would make the seventh the odd one and would not pay for itself.

The one departure from precedent is that **the source module is edited**. Every previous type attached
to a posting the source module already made, at a single point. A verdict has **three** trigger points
across two modules (`post_penalty_announcements`, `post_appeal_announcements`,
`post_autosanction_announcement`), and each must gain an image path in front of its text path without
becoming a precondition of anything. Contract: [verdicts-posting.md](./contracts/verdicts-posting.md).

## Phase sequencing

Ordered so each phase leaves the suite green and delivers something testable.

1. **The wrapping contract** (`svg_fill`, `test_svg_fill`) — closes the XIV.5 gaps *before* anything
   depends on them. Independently testable against synthetic SVG; no catalogue and no verdict needed.
2. **The catalogue** (`image_catalogues`, its test) — enrols verdicts in validity Layer 2 by existing
   machinery. Delivers User Story 4 whole.
3. **Resolution and fill** (`image_verdict_service`, its tests) — pure; the three kinds, the emptied
   session and team, the mention resolved inside free text.
4. **Sample data and the test command** (`image_sample_data`, `image_cog`) — delivers User Story 1,
   which is what makes the rest cheap to verify by eye.
5. **The posting flow** (`image_verdict_post`, the three triggers) — delivers User Stories 2 and 3.
6. **Notices and reporting** — delivers User Story 5.

Phase 1 is deliberately first even though Story 1 is P1: the sample data of step 4 is what exercises
the wrapping, and building it against a half-implemented contract would mean writing the interesting
test cases twice.
