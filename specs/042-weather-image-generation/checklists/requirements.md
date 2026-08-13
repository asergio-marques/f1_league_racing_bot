# Specification Quality Checklist: Weather Image Generation

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-13
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

**Iteration 1** — two items failed on review of the drafted spec and were corrected:

- *No implementation details*: FR-021 described the shared rendering as living "inline inside a message
  builder", naming a code construct rather than the obligation. Rewritten to say the rendering must be
  made separately callable so both paths produce it from one place. SC-007 failed the same item from the
  other side, stating its outcome as "both paths being driven from one rendering" — an internal
  arrangement rather than an observable result. Rewritten to the observable one: the two read
  identically, and a change to the text path's rendering reaches the graphic with no further work.

Everything else passed on first review. The template field identifiers named throughout the functional
requirements (`session_<x>_slot_<y>_label` and the rest) are **not** implementation detail: they are the
authoring contract a league manager writes into a hand-authored SVG, and are user-facing in the same way
a command parameter is. The same holds for the six template slot names, each of which is reached by a
configuration command a league manager runs.

**[NEEDS CLARIFICATION] count**: 0. Four decisions that would otherwise have been markers were settled
against the constitution (v4.7.0) and the shipped code, and are recorded in the spec's Assumptions
section instead:

1. ~~The rain-likelihood rounding, where the weather wip-spec and the shipped renderer disagree.~~
   **Superseded twice.** It was first recorded as needing no ruling, on the ground that the image
   wip-spec binds the graphic to whatever the text message renders. Raised with the author anyway, and
   ruled on 2026-08-13: round to the nearest integer on both paths, so FR-023a corrects the text path.
   The author then corrected the *grounding* — `weather_module_specification.md` is stale and is not the
   source of truth for weather rules — so the ruling rests on their decision alone and the spec no longer
   cites that document as its authority. See the staleness warning at the head of the spec.
2. Where the emphasis is separated from the phase 3 summary value — constitution XIV.16 places the
   repair in the code that hands the value over, not in the image utility.
3. Whether the mystery notice creates a posting occasion — settled by the constitution amendment made
   this session (Principle IV), which records the notice as the phase 1 posting of such a round.
4. Whether a template over-declaring against its floor is a fault — constitution XIV.12 makes the
   slot-fixed capacity a floor, so over-declaration is removed silently.

**Verified against the shipped code** rather than assumed: the session-label renderer already strips the
length qualifier as FR-025 requires; the slot-sequence renderer already collapses a single-weather
session as FR-029 requires and applies its emphasis inline, which is what FR-029 obliges to be
separated; `forecast_messages` already admits phase 0; the weather icon directory currently holds
`fallback.svg` alone, which is what FR-034 changes.
