# Implementation Plan: Template Verification & Graphic Conventions

**Branch**: `036-image-generation-conventions` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/036-image-generation-conventions/spec.md`

## Summary

Put the module's cross-cutting rules in place before any per-image-type generation utility is
written. Three things happen:

1. **Verification moves from advisory to blocking.** `/images template <kind>` today writes the
   filename and then reports validity as a warning. It must validate a *candidate* configuration
   and refuse to write when the file is absent, unparseable, or missing a mandatory field. The
   same verification runs over all fifteen templates as a `/season approve` gate.
2. **The conventions become machinery.** Field resolution gains a layer-label fallback, the
   `_group` removable-block rule, the `row_<x>_<field>` index form, and asset resolution by
   normalised slug with a per-directory `fallback.svg`.
3. **The field catalogue is created empty.** Fifteen catalogue entries are declared with no
   mandatory fields, which makes validity Layer 2 implementable now and every later image type a
   one-entry change. Until a type's catalogue is populated, its mandatory-field checks pass
   vacuously and are reported as unchecked, never as passed.

No command is added, removed or renamed. No image type is specified here.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: discord.py (slash commands), lxml (SVG tree), aiosqlite (storage).
Inkscape is an external binary the host must carry; no package installs it.
**Storage**: SQLite via `aiosqlite`. This feature adds no table and one enum widening
(`image_render_notices.notice_kind`).
**Testing**: pytest, with `tests/unit/` for the pure engine and `tests/integration/` for command
flows. The fill and resolution code is pure — no database, no Discord — and is unit-testable
without a bot.
**Target Platform**: single-process asyncio Discord bot, hosted on Windows or Linux.
**Project Type**: single project (`src/` + `tests/`).
**Performance Goals**: verification at configuration must complete inside one Discord interaction
round-trip (3 s to defer, 15 min to follow up); a season review verifies fifteen templates, so
parse results are reused within a single review rather than re-read per template.
**Constraints**: no new runtime dependency. Rasterisation is a blocking subprocess and MUST stay
off the event loop. Discord permits at most 25 subcommands per group, which is why the template
setters already live under `/images template` — this feature must not add to that group.
**Scale/Scope**: 15 templates, 8 toggleable aspects, 7 asset directories, 1 rasteriser.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against **v3.0.0**, amended earlier in this session specifically to agree with this spec.

| Principle | Gate | Verdict |
|---|---|---|
| **V. Observability & Change Audit Trail** | Notices reach the calculation log; configuration mutations are logged | **PASS** — FR-031; the existing `_log` on every config command is retained, and rejections are logged too |
| **VII. Output Channel Discipline** | No new channel category | **PASS** — FR-032 only restricts where errors may appear; nothing registers a category |
| **X. Modular Feature Architecture** | Module config isolated; capability off by default | **PASS** — no new config field except behaviour; the `images` module's disable-exception is untouched |
| **XIV.1** Templates are data | Canvas read from the template | **PASS** — FR-033 restates it |
| **XIV.2** Addressing by `@id`, label fallback, `_group` | Six operations and no others | **PASS** — FR-015 enumerates exactly the operations the amended table lists; FR-018–FR-020 implement the fallback; FR-022–FR-026 the groups |
| **XIV.3** Every mandatory field resolved | Mandatory/optional split honoured | **PASS** — FR-011–FR-013 |
| **XIV.4** Problems vs notices | Distinct outcomes, distinct destinations | **PASS** — FR-027–FR-032 |
| **XIV.5** Text bounds | Truncate vs wrap by declaration | **PASS** — FR-036–FR-039 |
| **XIV.6** Assets aspect-authored | Generator never pads | **PASS** — FR-040, FR-041; FR-045 binds the fallback image to the same rule |
| **XIV.7** Additive output | Text path survives; fallback only when uncommanded | **PASS** — FR-029, FR-030 |
| **XIV.9** Layered validity | Layers ordered, attributed, depth declared, no silent pass | **PASS** — Layer 2 is added as one class and one registry entry; see research R4 |
| **XIV.10** Catalogue as a code constant | One shared module, mandatory/optional, asset class named | **PASS** — created here, empty per type |
| **XIV.11** Id convention | `row_<x>_<field>`, unpadded | **PASS** — FR-021 |
| **XIV.12** Capacity declared, overflow fatal | Rejected at the earliest moment | **PARTIAL** — mechanism built, inert until a catalogue declares a capacity. Recorded in Complexity Tracking |
| **XIV.13** Slug + per-directory fallback | Underscore normalisation | **PASS** — FR-042–FR-045 |
| **XIV.14** Verified as PNG | Correctness judged on the raster | **PASS** — quickstart drives `/images test` and inspects PNGs |

**Result: gates pass.** One item is recorded in Complexity Tracking for visibility rather than as
a violation.

### Re-evaluation after Phase 1 design

Re-checked against the design in [research.md](./research.md), [data-model.md](./data-model.md)
and [contracts/](./contracts/). **Still passing**, with three points the design had to be shaped
around:

- **XIV.9 "no silent pass"** was the binding constraint on Layer 2. Adding a layer that passes
  trivially for an empty catalogue would report depth 2 for a template nothing was checked
  against. `applies_to()` returning False keeps such a template at depth 1 and keeps
  `depth_summary` honest (research R4, [contracts/verification.md](./contracts/verification.md)).
- **XIV.10 "one entry plus one utility"** was checked against the design as a whole: populating a
  single catalogue must switch on the mandatory-field check at all three verification moments,
  Layer 2, and the capacity guard, with no other edit. The design meets this because all four read
  the same catalogue object.
- **XIV.4's "no partial image"** survives the new fallback path. A fallback image is a successful
  fill that raises a notice, not a partial render, so `png_paths`/`problem` stay mutually
  exclusive ([contracts/error-taxonomy.md](./contracts/error-taxonomy.md), invariant 1).

No new violation surfaced, and the Complexity Tracking entry is unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/036-image-generation-conventions/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── field-resolution.md    # Addressing, groups, the six operations
│   ├── asset-resolution.md    # Normalisation, fallback, failure classification
│   ├── verification.md        # The three verification moments and their outcomes
│   └── error-taxonomy.md      # Problem/notice kinds, destinations, posting origin
├── checklists/
│   └── requirements.md  # Written by /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── models/
│   ├── image_catalogues.py     # NEW — the fifteen catalogues, empty of mandatory fields
│   ├── image_constants.py      # notice kinds extended; asset-class map added
│   └── image_module.py         # Problem dataclass; PostingOrigin; RenderNotice kinds
├── services/
│   ├── image_config_service.py # candidate-config construction for validate-then-store
│   ├── image_render_service.py # posting-origin split; problem/notice routing
│   └── image_validity_service.py # CatalogueLayer (Layer 2); shared parse cache
├── utils/
│   ├── svg_document.py         # FieldIndex (id → label fallback); named parse faults
│   ├── svg_fill.py             # _group removal; optional-field emptying; row helpers
│   └── asset_resolver.py       # NEW — normalise(), resolve_asset(), fallback
├── cogs/
│   ├── image_cog.py            # validate-then-store; rejection messages
│   └── season_cog.py           # template verification as an approval gate
└── services/placement_service.py # division-capacity guard at assign_driver

tests/
├── unit/
│   ├── test_svg_field_resolution.py   # NEW — id/label precedence, groups
│   ├── test_asset_resolver.py         # NEW — normalisation, fallback
│   ├── test_svg_parse_faults.py       # NEW — named faults, never raw parser text
│   └── test_image_validity_layers.py  # extended for Layer 2
└── integration/
    └── test_image_module_flow.py      # extended: rejection leaves config untouched
```

**Structure Decision**: the existing single-project layout is kept. Everything this feature adds
sits beside the 035 module it extends; the two genuinely new files (`image_catalogues.py`,
`asset_resolver.py`) follow the established `models/` and `utils/` split — a declaration module
and a pure helper, neither touching the database or Discord.

## Complexity Tracking

No constitutional violations require justification. One item is recorded for visibility:

| Item | Why recorded | Resolution |
|------|--------------|------------|
| **XIV.12's division-capacity rejection is built but inert** | FR-028 requires refusing "a command that would carry a division past what its configured templates can draw". No catalogue declares a capacity yet, because no image type is specified, so the guard has nothing to compare against and passes for every division. | Implement the guard at `placement_service.assign_driver` — the single choke point through which a driver enters a division — reading capacities from the catalogue module. It activates by data, not by code, the moment the first image type declares one. Building it now is what keeps the first image type a one-entry change; deferring it would put a cross-cutting guard into a session scoped to one graphic. |
