# Implementation Plan: Image Module — Initial Setup & Configuration

**Branch**: `035-image-module` | **Date**: 2026-08-10 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/035-image-module/spec.md`

## Summary

Deliver the Image module's configuration surface and its diagnostic, without wiring any live
output. Three pieces of work, in dependency order:

1. **Persistence and module lifecycle** — one config table, one toggle table, one notice table,
   registered into the existing `/module enable|disable` surface, with the Principle X.6 exception
   (nothing cleared on disable).
2. **The render engine** — two pure modules and an async service, built against Constitution
   Principle XIV as their behavioural source, and made safe to call from the Discord event loop.
3. **The command surface** — 26 configuration commands, a validity reporter built as ordered
   layers, the `/season review` addendum, and `/images test`.

**Technical approach**: no new technology. `lxml` and `fonttools` are already declared in
`requirements.txt`; Inkscape is an external binary located at runtime. Principle XIV is prescriptive
enough to build the engine from directly — it names the six permitted operations, fixes recolour to
a merge into inline `style`, requires the canvas be read from the template root, and specifies the
wrap's descent and floor. Building against it keeps the engine traceable to governance, so a
reviewer checking compliance reads the document the code was written from.

**The engine is the bulk of the work** and carries the real risk: nine invariants in Principle XIV
where a plausible implementation is subtly wrong (enumerated in
[contracts/render-service.md](./contracts/render-service.md)), plus a blocking subprocess that must
stay off the event loop. The configuration surface is wide but shallow — 23 of the 26 commands are
the same store-a-string shape.

## Technical Context

**Language/Version**: Python 3.13 (matches `__pycache__` artefacts; no version change)
**Primary Dependencies**: discord.py ≥2.0, aiosqlite ≥0.19, APScheduler ≥3.10, SQLAlchemy,
lxml ≥5.0, fontTools ≥4.50 — all already in `requirements.txt`. No additions.
**External binary**: Inkscape CLI. Not installable by package declaration; discovered at runtime.
**Storage**: SQLite via `aiosqlite`, forward-only numbered SQL migrations in `src/db/migrations/`.
Next free number is **039**.
**Testing**: pytest with `asyncio_mode = auto`, `pythonpath = src`; unit tests in `tests/unit/`,
integration in `tests/integration/`.
**Target Platform**: Long-running Discord bot process; Windows and Linux hosts both supported.
**Project Type**: Single project — existing `src/{models,services,cogs,db,utils}` layout.
**Performance Goals**: `/images test` returns within Discord's 15-minute deferred-response window;
practical target under 10 s per render. Configuration commands respond within the 3-second
acknowledgement rule (Bot Behavior Standards) or defer.
**Constraints**: A rasterisation is a blocking subprocess and MUST NOT run on the event loop.
Generated PNG must stay under Discord's 25 MB attachment limit — at 1200 px wide this is not a
practical risk.
**Scale/Scope**: 15 templates, 7 asset directories, 8 aspect toggles, 11 test kinds, 26 new
commands, 1 modified command, 35 configuration values per server (27 scalar columns + 8 toggles).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

Constitution version at planning time: **v2.12.0**.

| Principle | Requirement | How this plan satisfies it | Status |
|-----------|-------------|----------------------------|--------|
| I — Trusted Configuration Authority | Two tiers, enforced separately | Directory and filename commands use the existing `server_admin_only` decorator (Administrator); toggles, preferences, view and test use `admin_only` (Manage Server). Both stack on `channel_guard`. | PASS |
| V — Observability & Audit Trail | Config mutations and notices logged | Every config mutation posts to the calculation log channel via the existing `OutputRouter.post_log`. Render notices persist to `image_render_notices` and are surfaced. | PASS |
| VI — Incremental Scope Expansion | Work falls inside a ratified domain | Domain 12 (image-based output generation) ratified at v2.11.0. The wiring half is explicitly deferred, not smuggled in. | PASS |
| VII — Output Channel Discipline | No unregistered posting | This increment posts nothing to a public channel. `/images test` responds ephemerally; config mutations log to the already-registered log channel. The module registers no channel. | PASS |
| X — Modular Feature Architecture | Default-off, gate enforcement, atomic enable/disable | Default-off via `DEFAULT 0`. Every `/images` command gates on the enabled flag. Enable and disable are single transactions. | PASS |
| X.6 exception (v2.12.0) | Stale-proof config retained; qualifying values enumerated | Nothing is cleared on disable. All 31 values enumerated in [data-model.md](./data-model.md) and spec FR-004a; none names a channel, role, message or job. | PASS |
| XIV.1 — Templates are data | No template emitted by code; canvas read from root | `canvas_of()` reads `width`/`height` off the root. No code path writes a template. | PASS |
| XIV.2 — Six fill operations | Closed set; recolour merged into inline style | `svg_fill.fill()` implements exactly six operations; invariants 1–3 in the render contract test the merge, the inline requirement and non-consumption. | PASS |
| XIV.3 — Every field resolves | Unresolved field is a render failure | `FillResult.unresolved`; non-empty is a problem, which empties `png_paths`. | PASS |
| XIV.4 — Problems vs notices | Problems abort, notices survive and log | Two distinct return channels in the service API; see [contracts/render-service.md](./contracts/render-service.md). | PASS |
| XIV.5 — Template-declared bounds | `inline-size` and `shape-inside` honoured | Wrap and bound operations in `svg_fill`; invariants 7–9 test the half-pixel descent, the half-size floor, the recomputed leading and the word-boundary cut. | PASS |
| XIV.6 — Assets aspect-authored, no padding | Generator never pads | No padding code is written. Documented in the quickstart. | PASS |
| XIV.7 — Additive output | Text path untouched | No source-module posting path is modified in this increment at all. | PASS |
| XIV.8 — Images are attachments | No channel category registered | No channel config exists in the data model. | PASS |
| XIV.9 — Layered validity contract | Ordered named layers, declared depth, stable surface | `ValidityLayer` protocol with an ordered registry; report carries `depth_checked`. Layer 1 only in this increment. | PASS |
| Bot Behavior Standards | `/domain action` subcommand groups; ephemeral config responses; 3-second ack | `/images` group with `config`, `test` subgroups. All responses ephemeral. Test defers. | PASS |

**Result: all gates pass. No Complexity Tracking entries required.**

One **naming reconciliation** is recorded rather than silently resolved — see
[research.md](./research.md) R3 and the note in Complexity Tracking below.

## Project Structure

### Documentation (this feature)

```text
specs/035-image-module/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── commands.md          # The 26 new + 1 modified command surface
│   ├── render-service.md    # Engine API, problems vs notices
│   └── validity-layers.md   # The extension point later sessions plug into
├── checklists/
│   └── requirements.md  # Written by /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── models/
│   └── image_module.py            # NEW — ImageConfig, ImageAspectToggle,
│                                  #       RenderNotice, ValidityReport dataclasses
├── services/
│   ├── image_config_service.py    # NEW — CRUD over the three tables; defaults;
│   │                              #       path containment validation
│   ├── image_render_service.py    # NEW — rasterise + RenderOutcome assembly.
│   │                              #       Async wrapper over blocking work
│   ├── image_validity_service.py  # NEW — ordered layer registry; Layer 1;
│   │                              #       report assembly with declared depth
│   ├── image_sample_data.py       # NEW — per-kind sample data for /images test
│   └── module_service.py          # MODIFIED — is_images_enabled / set_images_enabled
├── cogs/
│   ├── image_cog.py               # NEW — /images group: config *, view, test
│   ├── module_cog.py              # MODIFIED — "images" choice + _enable/_disable_images
│   └── season_cog.py              # MODIFIED — image section in /season review
├── utils/
│   ├── svg_fill.py                # NEW — the six fill operations, crop, wrap,
│   │                              #       bounds. Pure; no DB, no Discord
│   ├── font_metrics.py            # NEW — font index + string measurement.
│   │                              #       Pure; no DB, no Discord
│   └── colour.py                  # NEW — hex parsing, WCAG contrast ratio
└── db/migrations/
    └── 039_image_module.sql       # NEW — three tables

tests/
├── unit/
│   ├── test_image_config_service.py     # defaults, containment, retention
│   ├── test_image_validity_layers.py    # Layer 1, depth declaration, attribution
│   ├── test_svg_fill.py                 # six operations, crop, wrap, inline-size
│   ├── test_colour.py                   # hex validation, contrast ratio
│   └── test_font_metrics.py             # substitution notice, measurement
└── integration/
    └── test_image_module_flow.py        # enable → configure → view → disable →
                                         # re-enable retention; season review addendum
```

**Structure Decision**: Single project, following the established `src/{models,services,cogs,db,utils}`
split exactly as the Attendance module (the closest analogue, feature 031) does. Three services
rather than one because the three concerns have genuinely different lifetimes: configuration is
pure CRUD, validity is the extension point later sessions modify, and the render engine changes
only when Principle XIV does. The two `utils/` modules are pure functions with no database or
Discord dependency, which is what makes the engine unit-testable without a bot and is the property
most worth protecting as the module grows.

## Phase 0 — Research

Complete. See [research.md](./research.md). Eleven decisions recorded; no unresolved
NEEDS CLARIFICATION items remain.

The findings that most shape the build:

- **R1** — the engine is built from Principle XIV in three layers, two of them pure. Nine
  invariants in that principle are where a plausible implementation goes subtly wrong; each gets a
  unit test.
- **R2** — rasterisation is a blocking subprocess and must be pushed off the event loop with
  `asyncio.to_thread`. This is the single most likely way to get this feature wrong: it passes every
  unit test and degrades the whole bot in production.
- **R9** — provisioning the template and asset files is out of scope. The module resolves configured
  paths and reports what it finds; nothing bundles, packages or installs them.

## Phase 1 — Design & Contracts

Complete. Artefacts:

- **[data-model.md](./data-model.md)** — three tables, their columns and defaults, the migration,
  and the enumeration of stale-proof values the X.6 exception requires.
- **[contracts/commands.md](./contracts/commands.md)** — the 26 new commands and 1 modified, with parameters,
  permission tier, validation and response shape.
- **[contracts/render-service.md](./contracts/render-service.md)** — the engine's public API and the
  problem/notice split that Principle XIV.4 requires.
- **[contracts/validity-layers.md](./contracts/validity-layers.md)** — the `ValidityLayer` protocol,
  the registry, and the four invariants a later session must not break.
- **[quickstart.md](./quickstart.md)** — runnable validation of the whole increment.

### Post-Design Constitution Re-check

Re-evaluated after the artefacts above were written. All gates still pass. Two design choices were
made specifically to hold a gate that the obvious implementation would have broken:

1. **XIV.9 declared depth** — the natural design returns a boolean per template. That cannot
   express "checked to Layer 1 only", so `ValidityReport` carries `depth_checked` and the renderer
   of the report is required to print it. Without this the module would silently overstate.
2. **XIV.4 problems vs notices** — the natural design raises on failure and logs notices as a side
   effect. Instead `RenderOutcome` returns both lists explicitly, so a caller cannot accidentally
   treat a degraded render as a clean one, and `/images test` can show both.

## Complexity Tracking

No constitutional violations require justification. One item is recorded for visibility rather than
as a violation:

| Item | Why recorded | Resolution proposed |
|------|--------------|---------------------|
| Entity granularity mismatch between constitution and spec | Constitution v2.11.0 names **ImageTypeState**, keyed per *image type* (15 rows). The spec's toggle is per *aspect* (8 rows), which is what the author's brief specifies. Storing 15 rows to serve an 8-valued command would be over-engineering. | Implement per aspect as **ImageAspectToggle** (spec is authoritative on the command surface). Propose a **PATCH** constitution amendment renaming the entity and re-graining it to the aspect, once this plan is accepted. Recorded so the divergence is deliberate and visible, not discovered later. |

## Open Decisions for Implementation

One, and it does not block `/speckit-tasks`.

**Reconciliation against `docs/wip-specs/image_module_specification.md`** (R11). That document was
unreadable during specification and planning, being deny-listed in `.claude/settings.json`. The
26-command surface, the defaults and the eleven test kinds here were derived from the author's
brief; the engine's behaviour from Constitution Principle XIV. Reconcile before implementing —
specifically the field catalogue Layer 2 will need, the `images test <type>` sample data, and the
per-image-type template inventory. The risk is omission, not contradiction.
