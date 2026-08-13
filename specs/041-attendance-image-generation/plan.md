# Implementation Plan: Attendance Image Generation

**Branch**: `041-attendance-image-generation` | **Date**: 2026-08-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/041-attendance-image-generation/spec.md`

## Summary

Draw a division's attendance sheet as a PNG in place of the textual sheet, and attach a static graphic
to its check-in call, from two templates under the `attendance` and `rsvp` toggles. The rendering
pipeline, asset resolution, validity layers and posting machinery are all built and reused. What this
feature adds is two catalogue entries, two resolution utilities, two posting hooks, a sample builder,
one widening of the sibling check, and three changes to the attendance module's own flows.

**No migration and no entity change.** Both graphics already have the column their lifecycle needs:
`attendance_division_config.attendance_message_id` for the sheet, `rsvp_embed_messages.message_id` for
the call. This is the first image type since 038 to reach outside the module for behaviour but not for
schema.

Four decisions carry the design:

**R1 — the catalogues need no new declaration form.** The sheet is the standings-drivers shape minus
the movement columns: a `columns` collection for the round headings, `rows` with a `nested` round cell.
The check-in graphic is a flat catalogue plus one ordinal collection. 040 ratified and built every form
both need — the second top-level collection, the nest under a row, `optional_unit` — so
`image_catalogues.py` gains two constants and no new dataclass field. This is the lightest catalogue
work of any image type so far.

**R2 — the sibling check must widen twice, and this is the one shared-module change.**
`sibling_row_fields` reads the relation from `ASPECT_TEMPLATES`, so two templates are siblings only
when they draw one *aspect*. The sheet and the check-in graphic are separate aspects and would not be
related at all. Constitution v4.6.0 widened the relation to the graphics of one **source module**, which
`ASPECT_SOURCE_MODULE` already records. Separately, `sibling_fields_declared` matches only
`<rows.prefix>_<n>_<field>`, so it would miss a check-in field on a sheet template entirely — the two
catalogues overlap in their *top-level* fields, not their rows. Both are widened; see Complexity
Tracking.

**R3 — the check-in graphic's staticness is a property of the call graph, not of a flag.** XIV.17 puts
the obligation on the author, and nothing in the module can detect a stale picture. What the design
*can* do is make the mistake structurally hard: the generator is called from exactly one place, the
initial post, and the button handler, the reserve distribution and the deadline handler reach no image
code at all. A test asserts that call graph directly.

**R4 — three changes land in the attendance module, not the image module.** The sheet's replacement
ordering is reversed in `post_attendance_sheet` and inherited by both paths (FR-045); the check-in
deadline is derived in `attendance_service` (FR-027); and a failed check-in post is reported to the log
channel (FR-062) — which fires with the `rsvp` toggle **off**, because the fault is in the call and not
in the picture.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: discord.py; lxml (SVG mutation); fontTools (text measurement); aiosqlite;
Inkscape CLI (rasteriser, not a Python package — probed at conventional install locations, `INKSCAPE`
overrides)
**Storage**: SQLite via aiosqlite. **No migration.** No entity is introduced and none amended; both
message-id columns already exist and this feature writes them through the paths that already own them.
**Testing**: pytest from the repo root (`pytest tests/ -q`). Baseline as of 2026-08-13: **1498 passed,
1 skipped, 0 failed**.
**Target Platform**: the host running the bot (Windows and Linux both supported; the rasteriser is
probed per platform)
**Project Type**: single project — one Discord bot application, `src/` + `tests/`
**Performance Goals**: one rasterisation per division per sheet posting, and one per division per round
for the check-in call — the latter *once* for the life of the call, which is the point of XIV.17. A
sheet's grid is rows × rounds, smaller than a standings grid by the session factor.
**Constraints**: a graphic is verified as a rasterised PNG, never as SVG in a browser
(Constitution XIV.14, CLAUDE.md). Discord admits no attachment on an already-posted message, which is
why the sheet replaces its message and why the check-in graphic must carry nothing that changes.
**Scale/Scope**: rows, rounds and sessions are all counted from the template file, never fixed in code.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1. Both passes below.*

| Principle | Gate | Verdict |
|---|---|---|
| **V. Observability & Audit** | Notices reach the calculation log channel; problems reach the audit log | **Pass** — `image_results_post.report_notices` is reused; both hooks name season, division and round (FR-056). FR-062 adds the one report that fires independently of the module |
| **VII. Output Channel Discipline** | The graphic rides on the source module's message in the source module's channel | **Pass** — the sheet into the division's attendance channel, the call into its RSVP channel; no channel category registered |
| **XIII. Attendance & Check-in Integrity** | Nothing about attendance is computed, decided or enforced here | **Pass** — points, totals, pardons and sanctions are read as persisted; the sheet's composition and ordering are the textual sheet's. FR-048 makes the graphic incapable of gating a sanction |
| **XIV.1** Templates are data | No template is emitted by code; the canvas is read from the file | **Pass** |
| **XIV.2** `@id` with layer-label fallback; the six fill operations | Row, block, column and discriminated-column groups are all `FillSpec.remove` entries | **Pass** — no new operation |
| **XIV.3** Mandatory fields resolved; optional-as-a-unit; **siblings** | Both grids are optional as a unit; the sibling relation is widened to the module (R2) | **Pass** — the widening is what v4.6.0 requires, not a deviation |
| **XIV.4** Problems abort, notices survive; unit of failure is one graphic | Each division renders on its own; a failed sheet leaves the sanctions and the other divisions alone | **Pass** |
| **XIV.5** Text bounds declared by the template | Driver and team names carry `inline-size`; truncation raises its notice | **Pass** — pipeline behaviour, unchanged |
| **XIV.6** Assets aspect-authored, referenced by URI | Flags, team images and track images resolve through `asset_resolver` | **Pass** |
| **XIV.7** Additive; adds no precondition; one rendering, two presentations; derived presentation | FR-048 and FR-055 keep the graphic downstream of every state change; the deadline is derived in `attendance_service` (R4) | **Pass** — the v4.6.0 precondition clause is the gate this feature exists to satisfy |
| **XIV.8** Attachments; no posting no graphic; produce before destroy; retry as text | R4 reverses the sheet ordering in the text flow; FR-060 enqueues text; FR-061 keeps the call out of the queue | **Pass, with one correction to the wip-spec** — see Complexity Tracking |
| **XIV.9** Layered validity; structural checks refuse everywhere | Rows, rounds and sessions are structurally checked at all three moments; the drivers only at the render | **Pass** |
| **XIV.10** Catalogue as a code constant, one entry per image type | Two entries, two utilities, no new declaration form | **Pass** — the sibling widening is a shared *check*, not a per-type declaration; see Complexity Tracking |
| **XIV.11** Ordinal discrimination, contiguous from 1; the ordinal that is not a datum | Rows, rounds and sessions are ordinals; FR-007 forbids drawing the row ordinal as a position | **Pass** — the v4.6.0 converse clause is why FR-007 exists |
| **XIV.12** Capacity declared; overflow fatal; **floor** | Rows, rounds and sessions counted from the file; the sheet declares a floor of one driver | **Pass** — the floor follows the calendar's existing precedent (R3) |
| **XIV.13** Slug resolution; every class carries a fallback | Flag, team-image and track-image classes, all already configured and shipped | **Pass** — no new asset class |
| **XIV.14** Verified as PNG | The quickstart verifies both graphics as rasterised PNGs | **Pass** |
| **XIV.15** One configured time zone | The check-in graphic draws the round's date, time and deadline in the configured zone with its abbreviation | **Pass** — the first attendance graphic to draw a time at all |
| **XIV.16** Nothing a reader can act on; the split is not exclusive | The three buttons and the role mention stay in the message; the graphic restates the embed's heading | **Pass** — the v4.5.0 non-exclusivity clause permits the restatement |
| **XIV.17** Redrawn when what it draws changes, unless declared static | The sheet redraws on every occasion the text does; the check-in graphic is declared static and carries no mutable value | **Pass** — R3 makes the obligation structural as far as it can be made so |

**Post-Phase-1 re-evaluation**: unchanged. The design added no principle violation. Complexity Tracking
gained no entry during Phase 1; both entries were identified before Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/041-attendance-image-generation/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── attendance-catalogues.md   # The two field catalogues and their id conventions
│   ├── sibling-and-floor.md       # The widened sibling relation and the collection floor
│   └── attendance-posting.md      # Both lifecycles: the replaced sheet and the static call
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── models/
│   ├── image_catalogues.py       # + ATTENDANCE_CATALOGUE, RSVP_CATALOGUE;
│   │                             #   sibling_row_fields widened to the source module;
│   │                             #   sibling_fields_declared widened past row fields
│   └── image_constants.py        # unchanged — both template keys and both aspects registered
├── services/
│   ├── image_attendance_service.py  # NEW — resolve_drawing + build_fill_spec for the sheet
│   ├── image_attendance_post.py     # NEW — the sheet posting hook and its fallback decision
│   ├── image_rsvp_service.py        # NEW — resolve_drawing + build_fill_spec for the call
│   ├── image_rsvp_post.py           # NEW — the one call site that generates the static graphic
│   ├── image_sample_data.py         # + build_attendance_drawing, build_rsvp_drawing
│   ├── image_validity_service.py    # unchanged — CatalogueLayer reads the catalogues as they stand
│   ├── attendance_service.py        # + derive_checkin_deadline (FR-027);
│   │                                #   post_attendance_sheet reordered to produce-before-destroy
│   │                                #   (FR-045) and given its image branch
│   └── rsvp_service.py              # + the graphic attached at the one initial post (FR-049–FR-051);
│                                    #   + the failed-post report to the log channel (FR-062)
└── cogs/
    └── image_cog.py                 # + the attendance and rsvp guards on /images test

tests/
├── test_image_attendance_service.py  # NEW — sheet resolution and projection, no Discord
├── test_image_attendance_post.py     # NEW — posting, ordering, fallback, the sanction gate
├── test_image_rsvp_service.py        # NEW — call resolution, sessions, deadline, mystery round
├── test_image_rsvp_post.py           # NEW — the static call graph, and the no-attachment fallback
├── test_image_catalogues.py          # + the two catalogues and the widened sibling check
├── test_attendance_service.py        # + derive_checkin_deadline, + the replacement ordering
└── test_rsvp_service.py              # + the failed-post report, toggle on and off
```

**Structure Decision**: the existing single-project layout is kept exactly. The feature follows the
shape 037–040 established — one `image_<type>_service.py` holding a pure `resolve_drawing` and a
`build_fill_spec`, one `image_<type>_post.py` holding the Discord-facing posting, and the catalogue in
the shared declaration module. Two types means two of each, which is what the standings increment did.
Nothing new is introduced at the top level.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **A change to the shared sibling check**, which XIV.10 says adding an image type must not require: `sibling_row_fields` gains the source-module relation beside the aspect relation, and `sibling_fields_declared` gains the top-level and non-row surfaces | Both are the *form* v4.6.0 ratified, not a particular of the attendance types. The existing code reads the relation from `ASPECT_TEMPLATES` and the surface from `rows.prefix`, which together express "siblings are two templates of one aspect that differ in their rows" — true of results and standings, and false of the first module whose two graphics share a module but not an aspect and overlap in their top-level fields rather than their rows. Every later module with two graphics reuses the widened form unchanged | Hard-coding the sheet/call pair as siblings inside the attendance catalogues would leave the shared check untouched and put a second, private relation beside the declared one, which is the thing XIV.10 exists to prevent. Leaving the surface at row fields would let a sheet template declare `round_format` and `session_1_name` and pass every check, then fail at the render — exactly the "wrong file in that slot" fault the rule is for, undetected at the only moment it is cheap to fix |
| **A change to the textual attendance path**: `post_attendance_sheet` deletes the previous sheet *before* posting its successor, and is reordered to produce first | FR-045 and XIV.8 require the replacement to exist before the original is destroyed. Left as it is, a failed render leaves a division with no sheet at all — the precise outcome the rule exists to prevent — and the image path cannot honour an ordering that the text path it falls back to breaks | Giving the image path its own produce-before-destroy while leaving the text path deleting first satisfies the rule for the graphic and leaves the fallback, which is the *more* failure-prone path, violating it. Two orderings in one flow would also drift, which is what the author's ruling of 2026-08-13 — "the image path should inherit this" — settles |

Neither is a deliberate violation left standing: the first implements a form the constitution now
carries, and the second brings the text path into line with a rule ratified before this feature.

**One wip-spec correction was required in the same window.** § "Generation and posting" required a
failed check-in call to be enqueued for retry. `retry_service.enqueue` accepts `content: str` and
`attempt_delivery` reposts it as chunked text, so a call so enqueued would arrive with no embed, no
roster and no buttons. The wip-spec now keeps the call out of the queue and reports it to the log
channel instead (FR-061, FR-062).
