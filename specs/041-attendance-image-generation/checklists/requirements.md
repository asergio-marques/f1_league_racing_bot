# Specification Quality Checklist: Attendance Image Generation

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

### Validation findings, iteration 1

Three items initially failed, were put to the author, and were corrected in place on their rulings of
2026-08-13:

1. **Requirements testable and unambiguous** — FR-015 originally said the six emptying cases "raise no
   notice" without saying why the cell is not an unresolved value. Since Rule 4 raises a notice for a
   value that *could not be determined*, the requirement was ambiguous about which it was.
   **Author's ruling: an empty cell equals 0.** There is no unknown state on the grid at all — each of
   the six cases confers zero, and no notice and no error of any kind is raised for one. FR-015 and the
   wip-spec's § "Resolution of the data to be placed" both now say so.

2. **Dependencies and assumptions identified** — FR-045 requires produce-before-destroy, which the
   textual sheet flow does not do today (`post_attendance_sheet` deletes the prior message before
   sending its successor). **Author's ruling: the image path inherits it.** The ordering is therefore
   owned by the textual sheet flow and corrected there, with the image path inheriting the corrected
   order rather than carrying a second implementation that could drift from it.

3. **Scope clearly bounded** — FR-055's obligation that a graphic never prevent the attendance rows
   being opened could be read as a demand to fix the existing flow, which opens rows only after the
   call posts successfully. An assumption bounds it to what the image path adds. The pre-existing
   hazard behind it is recorded below.

### The silent check-in failure, and what was done about it

`run_rsvp_notice` returns on a failed `channel.send` **before** `bulk_insert_attendance_rows`, so a
transient Discord failure leaves the round with no attendance rows at all. The penalty pass iterates
those rows, so nothing is penalised; combined with finding 1, the round's cells are then empty, which
now formally means every driver scored zero — **a failed post is recorded as flawless attendance**, and
nothing anywhere says the call was never made.

The fix first proposed was to enqueue the failed call for retry. On inspection that is not available:
`retry_service.enqueue` accepts `content: str` and `attempt_delivery` reposts that text in chunks, so a
check-in call put through the queue would arrive with no embed, no roster and no buttons — a message
the division cannot answer. The author's approval was conditioned on no detriment to normal behaviour,
and that would have been one.

What was applied instead, at no cost to the normal path:

- **FR-060** enqueues the textual **sheet** only, which is plain text and passes through the queue
  intact.
- **FR-061** keeps the check-in call out of the queue, with the reason stated so it is not re-proposed.
- **FR-062** reports a failed check-in post to the server's logging channel, naming season, division and
  round. Today the failure reaches the application log alone, where no league can see it. This turns a
  silent, permanently mis-recorded round into one the staff can act on.

Reordering the rows against the post was considered and rejected: opening them first means every driver
takes a no-RSVP penalty at the deadline for a call none of them saw, which is worse than the hazard it
would fix. It is recorded in Out of Scope.

`docs/wip-specs/image_module_specification.md` was corrected in the same window — its § "Generation and
posting" required the check-in call to be enqueued for retry, which the retry queue cannot honour.

### Deliberate divergences from the template

- The **Key Entities** section records that no entity is introduced or amended, rather than being
  omitted. The absence is a finding of this increment and is recorded so it is not re-derived, matching
  the practice of increments 038–040 and the constitution's "New Entities (v4.6.0)" section.
- Two normative-source callouts precede the user stories, as in 037–040. They exist so no rule is
  restated here in competition with the wip-spec.
