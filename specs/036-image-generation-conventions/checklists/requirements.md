# Specification Quality Checklist: Template Verification & Graphic Conventions

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

**Status: all items pass.**

## Notes

### Iteration 1

Two [NEEDS CLARIFICATION] markers were raised, both recording a conflict between the author's
brief and constitution v2.13.0 rather than a gap in the brief:

- **FR-021** — repeating-row identifier form.
- **FR-042** — outcome when an image field's file cannot be resolved.

### Iteration 2 — both resolved

- **FR-021** resolved by the author: `row_<x>_<field>`, index unpadded. Constitution XIV.11 is
  superseded.
- **FR-042 – FR-045** resolved by the author, who introduced a **generic fallback image** for
  both mandatory and optional fields and pointed at the proof of concept for the handling. The
  POC supplied the resolution rule (`normalize()` in `resources/poc/build_poc.py`, an
  **underscore** slug) but no fallback of its own, so the fallback mechanism is specified here
  as a reserved `fallback.svg` per asset directory (A-007) and the resolution rule is adopted
  verbatim from the POC.

### Deliberate retentions

**"No implementation details" — SVG constructs are kept.** The spec names `inline-size`,
`shape-inside`, `_group`, node identifiers and layer labels. These are not implementation
leakage: they are the authoring contract a league manager works to when drawing a template in an
SVG editor, and the brief states them as normative. Removing them would leave the requirements
untestable.

**Success criteria** avoid all technology references and are stated as observable outcomes.

### Resolved by assumption rather than by asking

- Case-insensitive `.svg` matching (A-001).
- `shape-inside` without `inline-size` → treated as a wrapping field (A-002).
- Meaning of "emptied" vs "removed" (A-003).
- Vacuous pass for image types with no generation specification yet (A-005).
- `fallback.svg` as the reserved fallback name (A-007).
- A mandatory field tolerates a missing *datum* image where a fallback exists (A-008).

### Blocking follow-up before `/speckit-plan`

The spec's **Constitution Impact** section records five contradictions with constitution
v2.13.0, all now settled in the spec's favour. Two of them (XIV.11, XIV.13) are rules ratified
in the previous session that turned out to be wrong — notably XIV.13's hyphenated asset slug,
which contradicts every asset already shipped under `resources/`. The constitution should be
amended via `/speckit-constitution` before planning, so the Constitution Check in `plan.md` is
run against rules that agree with this spec.
