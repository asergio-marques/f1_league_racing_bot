# Specification Quality Checklist: Calendar Image Generation

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

**Both clarifications resolved** by the author on 2026-08-12 and carried into FR-026 and FR-027:

- **Q1 — final crop point below the declared height.** Honoured as written with a non-fatal error;
  the template is not rejected. The wip-spec's § "The vertical crop" was amended to carry this rule,
  it being the source of truth for it.
- **Q2 — a default `mystery.svg`.** Ships in `resources/tracks/`. The artwork is a deliverable of the
  implementation phase; `resources/README.md` and the README's reserved-filenames paragraph are to be
  updated when the file exists, not before.

**On the two "implementation detail" items.** The spec names template field ids (`round_<x>_date`),
the `viewBox` and `height` attributes, and `calendar_message_id`. These are held to pass the check:
the field ids are the **contract between a league manager and the bot** — a manager authors them by
hand in an SVG editor, so they are user-facing vocabulary rather than internal structure — and the
SVG attributes are the medium the feature operates on, not a technology choice made here.

**Test mode**, raised by the author after the first draft and found to be genuinely uncovered. Three
requirements were added (FR-028 to FR-030), FR-017 was qualified, and two acceptance scenarios now
verify it rather than leaving it incidental. The substantive decision: the calendar's replacement
deletion behaves the same in test mode as in live running, and does **not** join the forecast flow's
test-mode deletion guard. Recorded in the wip-spec's § "Generation and posting".

**On duplication.** Per `CLAUDE.md`, `specs/` is derived and must never restate a wip-spec rule.
This spec cites the wip-spec sections as normative and states obligations and verification rather
than copying the rules. Any conflict is to be resolved in the wip-spec's favour, and this document
corrected.
