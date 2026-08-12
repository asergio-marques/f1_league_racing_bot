# Specification Quality Checklist: Lineup Image Generation

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-12
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.

### Validation record

**Iteration 1** found three failures, each now corrected:

- *No implementation details*: FR-002, FR-025 and several assumptions name source files
  (`models/image_catalogues.py`, `placement_service._refresh_lineup_post`,
  `utils/asset_resolver.normalise`, `team_service._RESERVE_NAME`). **Retained deliberately.** These
  are not design choices being smuggled in — they identify the existing artefacts the feature must
  extend or correct, and FR-025 in particular states that a *present behaviour is wrong*, which
  cannot be said without naming where it lives. This repo's specs cite the codebase in this way
  (see `specs/037-calendar-image-generation/spec.md` FR-017); the constitution's own Rule 10 makes
  the catalogue's location normative. Judged a pass in this project's house style, and recorded here
  so the departure is deliberate rather than unnoticed.
- *Scope clearly bounded*: the first draft did not say what happens to the other fourteen image
  types or to the calendar. FR-033 added.
- *Dependencies and assumptions identified*: the first draft assumed silently that the ten default
  team names survive FR-010. Now stated, with the reasoning, as the first assumption.

**Iteration 2**: all items pass.

**Iteration 3** (author direction, 2026-08-12): FR-025 was written as though the lineup wip-spec
reformed the textual lineup's delete-then-build ordering generally. It does not — *"be it the image
or, **in the case of a fallback**, the textual lineup"* scopes that sentence to the image flow.
Split into FR-025 (image flow) and FR-025a (textual path unchanged), with FR-032, SC-004, SC-007,
US4 scenarios 6–7a and the assumptions realigned. The author's direction is that the current
implementation's behaviour is the requirement, and that this feature adds the possibility of an
image in place of the text and nothing else.

### Open risks carried into planning

Not spec defects — matters `/speckit-plan` must confront:

- **The catalogue declaration is the load-bearing extension.** `RowSpec` today expresses one ordinal
  collection with a template-counted capacity. The lineup needs keyed members, nested members, a
  singleton, and a data-fixed capacity (FR-002). This is the largest single piece of design in the
  feature and the one most likely to affect the calendar if done carelessly.
- **FR-025 / FR-025a split one refresh path in two.** `placement_service._refresh_lineup_post` is
  reached from `placement_service`, `attendance_service` and `season_cog`. The image flow must
  produce its replacement before deleting; the textual-only path must keep its present
  delete-then-build order untouched. The risk is a refactor that unifies them and silently changes
  the textual behaviour, which SC-007 exists to catch.
- **The `lineup` aspect has more triggers than any image type built so far**, and FR-024 requires one
  attendance trigger to be deliberately *excluded*.
