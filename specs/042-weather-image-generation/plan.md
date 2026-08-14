# Implementation Plan: Weather Image Generation

**Branch**: `042-weather-image-generation` | **Date**: 2026-08-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/042-weather-image-generation/spec.md`

## Summary

Draw the three weather forecast phases and the mystery-round notice as PNGs in place of their textual
messages, from six templates under one `weather` toggle. The rendering pipeline, asset resolution,
validity layers, fallback machinery and notice reporting are all built and reused. What this feature
adds is six catalogue entries, one resolution utility, one posting hook, a template-selection function,
sample builders, eight shipped icons — and **one new declaration form in the shared catalogue module**.

**No migration and no entity change.** `forecast_messages` already keys a message by round, division and
phase and already admits phase `0` for the mystery notice. Every lifecycle this feature needs is
addressable through rows that exist.

Six decisions carry the design:

**R1 — the declaration floor is a genuinely new catalogue form, and it is this feature's one shared
change.** `RowSpec.capacity` is today either an integer (fixed by the image type) or `None` (derived —
counted from the template). Constitution v4.7.0's third capacity, **fixed by the template slot**, is
neither: it is a *floor*, under which the template is refused and over which the surplus is removed in
silence. Both `RowSpec` and `NestedSpec` gain a `minimum` field. See Complexity Tracking.

**R2 — the floor then refuses at all three moments for free.** `declared_capacity` already raises
`CapacityError` for a collection with no member and for a gap in the numbering, and `CatalogueLayer`
already catches that exception and reports it in its own words. Extending the same raise to "fewer than
the floor" makes FR-016 true at the naming command, at season review and before the render without a
single new call site — which is exactly what XIV.9 means by a structural check being complete at every
moment.

**R3 — `session_<x>_slot_type` costs nothing, verified rather than assumed.** The worry that `slot`
names both a field prefix and a nested collection does not survive contact with the code.
`NestedSpec.declared_capacity` matches `^session_1_slot_(\d+)(?:_.*)?$`, and `type` is not `\d+`, so the
session-level field cannot be miscounted as a slot member. `_canonical` reduces the two to
`session_#_slot_type` and `session_#_slot_#_label`, which are distinct. FR-009 needs no code.

**R4 — the sibling relation already covers all six.** `ASPECT_TEMPLATES["weather"]` holds the six keys
and `ASPECT_SOURCE_MODULE["weather"]` is `"weather"`, so `sibling_keys` returns the other five for each
without a change. FR-002 is satisfied by machinery 041 widened. This feature touches no sibling code.

**R5 — the textual weather flow must be reordered, exactly as 041 reordered the attendance sheet.**
`phase2_service` and `phase3_service` call `delete_forecast_message` for the previous phase **before**
`post_forecast` for the current one. XIV.8 and FR-045 require the reverse. Left alone, a failed phase 3
render would delete the phase 2 message and then fall back — leaving the division momentarily with
nothing, on the path where something has already gone wrong. See Complexity Tracking.

**R6 — three shared renderings must be lifted out of the message builders.** The rain percentage is
computed inline inside `phase1_message`; the session weather type is capitalised inline inside
`phase2_message`; and `format_slots_for_forecast` bakes its italics into the value it returns. XIV.7
requires one rendering for both paths and XIV.16 forbids the graphic stripping markup back out, so all
three are extracted. The rain percentage is corrected to whole-number rounding in the same stroke
(FR-023a, the author's ruling of 2026-08-13).

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: discord.py; lxml (SVG mutation); fontTools (text measurement); aiosqlite;
Inkscape CLI (rasteriser, not a Python package — probed at conventional install locations, `INKSCAPE`
overrides)
**Storage**: SQLite via aiosqlite. **No migration.** No entity is introduced and none amended;
`forecast_messages` already carries `(round_id, division_id, phase_number)` with phase `0` admitted for
the mystery notice by migration 006.
**Testing**: pytest from the repo root (`pytest tests/ -q`). Baseline as of 2026-08-13: **1707 passed,
1 skipped, 0 failed**.
**Target Platform**: the host running the bot (Windows and Linux both supported; the rasteriser is
probed per platform)
**Project Type**: single project — one Discord bot application, `src/` + `tests/`
**Performance Goals**: one rasterisation per division per phase — three per division per round, plus one
for a mystery round. Every weather canvas is small: at most four sessions of at most four slots, an
order of magnitude under a standings grid.
**Constraints**: a graphic is verified as a rasterised PNG, never as SVG in a browser
(Constitution XIV.14, CLAUDE.md). Discord admits no attachment on an already-posted message, which is
why each phase posts a new message and the chain deletes the previous one.
**Scale/Scope**: sessions and slots are counted from the template file and floored by the format the
slot serves; neither is fixed in code beyond the floor, which is derived from `SESSIONS_BY_FORMAT` and
`MAX_SLOTS`.

> **`weather_module_specification.md` is stale and is not a source for this plan.** Confirmed by the
> author on 2026-08-13. Principle IV and the shipped code govern the pipeline; the constitution's Round
> Formats table was verified against `MAX_SLOTS` before the floors of R1 were derived from it.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1. Both passes below.*

| Principle | Gate | Verdict |
|---|---|---|
| **IV. Three-Phase Weather Pipeline** | Nothing about the pipeline is computed, decided or re-run here | **Pass** — every value is read as the phase services persisted it. The mystery notice is drawn at the phase 1 horizon per the paragraph amended at v4.7.0 |
| **V. Observability & Audit** | Notices reach the calculation log channel, naming what they pertain to | **Pass** — `image_results_post.report_notices` is reused; FR-059 names season, division, round and phase. The calculation log stays textual entire (FR-033) |
| **VII. Output Channel Discipline** | The graphic rides on the source module's message in the source module's channel | **Pass** — into the division's forecast channel alone; no channel category registered |
| **XIV.1** Templates are data | No template is emitted by code; the canvas is read from the file | **Pass** |
| **XIV.2** `@id` with layer-label fallback; the six fill operations | Session groups and slot groups are `FillSpec.remove` entries | **Pass** — no new operation |
| **XIV.3** Mandatory fields resolved; **siblings**; a kind with its own type | Six sibling catalogues; the mystery notice is the first kind given a type of its own | **Pass** — R4; the v4.7.0 own-type clause is what admits the mystery catalogue |
| **XIV.4** Problems abort, notices survive; unit of failure is one graphic | Each division and each phase renders on its own (FR-049) | **Pass** |
| **XIV.5** Text bounds declared by the template | Track names and session summaries carry `inline-size`; truncation raises its notice | **Pass** — pipeline behaviour, unchanged |
| **XIV.6** Assets aspect-authored, referenced by URI | Track images and weather icons resolve through `asset_resolver` | **Pass** |
| **XIV.7** Additive; adds no precondition; one rendering, two presentations | R6 extracts the three shared renderings; FR-051 keeps every draw and every log entry upstream of the picture | **Pass** — the v4.7.0 another-message clause is what admits the rain likelihood on the phase 2 and 3 graphics |
| **XIV.8** Attachments; no posting no graphic; produce before destroy; retry as text; **the chain across occasions** | R5 reorders the text flow and the image flow inherits it; FR-057 enqueues text; FR-046 makes the chain indifferent to manner | **Pass, with one correction to the text path** — see Complexity Tracking |
| **XIV.9** Layered validity; structural checks refuse everywhere | The floor, the session count and the slot count are structural and refuse at all three moments (R2); the round's actual sessions only at the render | **Pass** |
| **XIV.10** Catalogue as a code constant, one entry per image type; **the selecting datum** | Six entries, one utility; the format of the round is declared as the selector | **Pass** — the `minimum` field is a shared *form*, not a per-type declaration; see Complexity Tracking |
| **XIV.11** Ordinal discrimination, contiguous from 1; a field may begin with a nested collection's name | Sessions and slots are ordinals; FR-010 forbids drawing the session ordinal | **Pass** — R3 confirms the v4.7.0 clause needs no code |
| **XIV.12** Capacity declared; overflow fatal; **fixed by the template slot** | The new `minimum` form (R1); over-declaration removed silently (FR-017) | **Pass** — the form is what v4.7.0 ratified |
| **XIV.13** Slug resolution; every class carries a fallback; a module-defined set ships complete | The weather icon class is the second module-defined vocabulary; eight files ship (FR-034) | **Pass** |
| **XIV.14** Verified as PNG | The quickstart verifies all six graphics as rasterised PNGs | **Pass** |
| **XIV.15** One configured time zone | **Not reached** — no weather graphic draws a date or a time (FR-011) |
| **XIV.16** Nothing a reader can act on; **channel markup is not content** | The role mention stays in the message; R6 separates the summary's emphasis at its source | **Pass** — the v4.7.0 markup clause is why the separation is in the renderer and not in the utility |
| **XIV.17** Redrawn when what it draws changes | Every weather type redraws on each occasion the text does (FR-043); none is declared static | **Pass** |

**Post-Phase-1 re-evaluation**: unchanged. The design added no principle violation and Complexity
Tracking gained no entry during Phase 1; both entries were identified before Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/042-weather-image-generation/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── weather-catalogues.md      # The six field catalogues and their id conventions
│   ├── declaration-floor.md       # The capacity fixed by the template slot
│   └── weather-posting.md         # The chain across occasions, selection, and fallback
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── models/
│   ├── image_catalogues.py       # + `minimum` on RowSpec and NestedSpec (R1);
│   │                             #   + the six weather catalogues
│   └── image_constants.py        # unchanged — six template keys, the aspect, the four
│                                 #   test kinds and the icon directory all registered
├── services/
│   ├── image_weather_service.py  # NEW — resolve_drawing + build_fill_spec for all six,
│   │                             #   and weather_template_key (the selecting datum, R7)
│   ├── image_weather_post.py     # NEW — the posting hook, per-phase, and its fallback
│   ├── image_sample_data.py      # + build_weather_drawing for the six test images
│   ├── image_validity_service.py # unchanged — CatalogueLayer already reports CapacityError
│   ├── phase1_service.py         # + the image branch
│   ├── phase2_service.py         # + the image branch; delete/post reordered (R5)
│   ├── phase3_service.py         # + the image branch; delete/post reordered (R5)
│   └── mystery_notice_service.py # + the image branch
├── utils/
│   └── message_builder.py        # R6 — three renderings extracted; the phase 1 percentage
│                                 #   corrected to whole-number rounding (FR-023a)
└── cogs/
    └── image_cog.py              # + the four weather guards on /images test

resources/
└── weather/                      # + sunny, mixed, rain, clear, light_cloud, overcast,
                                  #   wet, very_wet (FR-034)

tests/unit/
├── test_image_weather_service.py    # NEW — resolution, selection, sessions, slots, summaries
├── test_image_weather_post.py       # NEW — the chain, the ordering, mixed-manner, fallback
├── test_image_weather_catalogue.py  # NEW — the six catalogues, the floor, the slot_type
│                                    #   distinction, the sibling relation across all six
├── test_image_weather_fill.py       # NEW — group removal, per the *_fill.py convention
├── test_image_weather_validity.py   # NEW — the floor refusing at all three moments
├── test_message_builder.py          # + the three extracted renderings and the new rounding
└── test_forecast_cleanup.py         # + the reordering, and the mixed-manner chain

tests/integration/
└── test_round_lifecycle.py          # + a full round posted as graphics, toggled both ways
```

The per-type test file names follow the convention 037–041 established —
`test_image_<type>_{catalogue,fill,service,post,validity}.py` — rather than the single file the first
draft of this plan assumed. There is no existing `test_phase*_service.py`; the phase services are covered
through `test_forecast_cleanup.py` and the round-lifecycle integration test, which is where the
reordering of R5 is asserted.

**Structure Decision**: the existing single-project layout is kept exactly. The feature follows the
shape 037–041 established — one `image_<type>_service.py` holding a pure `resolve_drawing` and a
`build_fill_spec`, one `image_<type>_post.py` holding the Discord-facing posting, and the catalogues in
the shared declaration module. Six templates does **not** mean six service pairs: the six share one
resolution utility parameterised by phase, because they draw one subject and differ only in which parts
of it they carry. Nothing new is introduced at the top level.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **A new field on the shared catalogue specs**, which XIV.10 says adding an image type must not require: `RowSpec` and `NestedSpec` each gain `minimum: int \| None` | It is the *form* v4.7.0 ratified — a capacity fixed by the template slot — and not a particular of the weather types. The existing pair of readings (an integer fixed by the type, `None` counted from the template) cannot express a floor: an integer would refuse the over-declaration the rule requires to be silent, and `None` would admit a template too small to draw a round the league has already scheduled. Any later aspect whose slots serve known subsets of the data reuses the field unchanged | Putting the floor in the weather utility and checking it at generation would leave the shared declaration honest-looking and move the check to the one moment XIV.9 says it must *not* be deferred to — a league would learn its template was too small two hours before a race. Encoding the floor as a fixed `capacity` would make a template declaring more members a fatal error, contradicting FR-017 and obliging every sprint author to draw exactly four sessions and no chrome beyond |
| **A change to the textual weather path**: `phase2_service` and `phase3_service` delete the previous phase's message before posting the current one, and are reordered to produce first | FR-045 and XIV.8 require the replacement to exist before the original is destroyed. Left as it is, a failed render deletes the standing forecast and *then* falls back, so the division holds nothing during the window in which something has already gone wrong. The image path cannot honour an ordering that the text path it falls back to breaks | Giving the image path its own produce-before-destroy while leaving the text path deleting first satisfies the rule for the graphic and leaves the fallback — the more failure-prone path — violating it. This is the identical defect 041 found in `post_attendance_sheet` and repaired there for the identical reason; a second flow left with the opposite ordering would be a standing invitation to copy the wrong one |

Neither is a deliberate violation left standing: the first implements a form the constitution now
carries, and the second brings the text path into line with a rule ratified before this feature.

**One correction to the text path is in scope beyond the ordering.** FR-023a changes the phase 1 rain
likelihood from one decimal place to the nearest whole number, on the author's ruling of 2026-08-13.
Nothing pins the present format — no test asserts on it — and both paths draw the corrected value from
the one rendering R6 extracts.
