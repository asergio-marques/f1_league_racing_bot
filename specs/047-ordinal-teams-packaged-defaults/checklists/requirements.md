# Specification Quality Checklist: Ordinal addressing of teams, and packaged asset defaults

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-20
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

Field identifiers (`team_<x>_name`, `reserve_group`) and packaged paths (`resources/defaults/<class>`) are stated verbatim throughout. They are not implementation leakage: a template field name is the contract a league manager authors against in an SVG editor, and a packaged path is what `resources/README.md` tells a league to look at. The specification treats them as user-facing vocabulary, which is how `docs/wip-specs/image_module_specification.md` treats them.

Two decisions were taken in conversation and are recorded as requirements rather than assumptions: the shipped template's shape (FR-034) and the retention of the `/images test` refusal on a server with no configured team (FR-045). Both were also written back into `docs/wip-specs/`, where the project holds its rules.

### Requirements audit, 2026-08-20

The requirements were re-read on their own, without reference to the implementation, against two questions.

**Can a league field 1..n teams with names of its choosing, differing across divisions?** Differing across divisions was already guaranteed (FR-018 withdraws the uniformity rule; FR-006, FR-007 and FR-021 resolve and check each division on its own). Three gaps were found and closed:

- The bound on *n* was assemblable from FR-002 and FR-010 but never stated. FR-010 now states it outright, including that authoring a template with more blocks is the only way to raise it.
- FR-026 bound the normalised form to "what a filename admits" while the rule itself appeared nowhere in the document. FR-025 now states it in full, with worked examples.
- FR-029 refused a name colliding "within the same scope" without defining scope. It now defines it, and says two divisions may each field a team normalising alike.

Two consequences of positional addressing were also unstated and are now requirements: FR-015 (a division may field zero teams and still draw) and FR-016 (seat capacity belongs to the block, not the team, so a league with teams of differing seat counts declares the largest at every block). The User Story 2 narrative contradicted FR-011 and FR-034 by drawing three-seat teams from the shipped 11 x 2 template, and was corrected.

**Are all seven graphics that draw a team targeted, not just the lineup?** The rules already reached them — FR-028 enumerates the seven, and FR-039 to FR-042 are phrased for asset resolution as such rather than for the lineup. What was missing was any obligation to *verify* it: SC-005 could be satisfied by a single resolver test. FR-043 now states the two-tier resolution is not particular to the team class or the lineup, FR-044 requires each of the seven to be exercised, and SC-006 measures it.

### Second audit, 2026-08-20 — test mode and a season under review

Two guarantees the user named were checked against the requirements. **Neither was carried by this spec.** Both are established module-wide in `docs/wip-specs/image_module_specification.md` — a test-mode driver is drawn by its mock name and never as an unoccupied seat, a division seated wholly with such drivers holds seated drivers, a command reads the same data the same way whether test mode is set or not, and a season pending approval is drawn exactly as an approved one. This spec neither restated them nor guarded them, while rewriting the very seat-resolution path that would break them. FR-045 to FR-049 now carry them, with acceptance scenarios 9 to 11 on User Story 1 and SC-011 and SC-012 measuring them.

`season review` was named in the spec only as a moment of *validation* (FR-021, FR-031), never as a moment at which the lineup is *drawn* — which it is. What happens when validation fails was therefore unspecified. FR-049 settles it: the graphic is drawn where the season passes, and a fatal excess is reported as a failure of validation with a fall back to the textual lineup, never as a failure to render.

The audit also turned up a collision this feature creates with an existing rule. The wip-spec's "a command of this family shall not substitute the packaged directories for those the league configured" predates two-tier resolution, and now reads as though `/images test` should be denied the packaged fallback tier — which would make a preview answer differently from the posting it exists to predict. FR-050 resolves it, and the wip-spec sentence was corrected in the same pass.

One dependency is outside the spec's reach and must be handled before implementation: D-001, the constitution amendment. Principles IX, XIV.11, XIV.12 and XIV.13 state rules this feature withdraws, and the constitution is amended only via `/speckit-constitution`.
