# Feature Specification: Penalty Posting, Appeals, and Result Lifecycle

**Feature Branch**: `026-penalty-posting-appeals`  
**Created**: 2026-03-30  
**Status**: Draft  
**Input**: User description: "Expand results and standings module with penalty announcement posting, a post-penalty appeals stage, mandatory description and justification fields on penalty/appeal forms, result lifecycle labeling (provisional / post-race penalty / final), and a division verdicts-channel command."

## Context & Scope

This specification extends the inline post-submission penalty review flow defined in spec 023. The key behavioural changes relative to the current implementation are:

1. The round lifecycle gains a third result state — **Post-Race Penalty** — between the current "interim" (now called **Provisional**) and "final" (now called **Final**) states. A new intermediate **Appeals** stage sits between these two.
2. When the tier-2 admin approves the penalty review (existing `Approve` button in `ApprovalView`), the transient submission channel no longer closes immediately. Instead it transitions to an **Appeals Review** state, and the round is marked `POST_RACE_PENALTY`. The channel closes only when the appeals review is approved, at which point the round becomes `FINAL`.
3. Every results and standings post gains a standard heading (`Season {N} {Division Name} Round {X} — {Session Name}`) and a lifecycle label (`Provisional Results`, `Post-Race Penalty Results`, or `Final Results`).
4. The existing `AddPenaltyModal` (which already has two fields: driver and penalty value) is expanded with two new mandatory fields: **description** and **justification**. This same modal is reused for the appeals wizard.
5. When a penalty is staged and approved, an announcement is posted to the division's configured **verdicts channel** (fallback: results channel) using the populated description and justification fields.
6. `round results amend` (full re-entry amendment) is restricted to rounds in `FINAL` state.

**Terminology mapping to spec 023**:

| Spec 023 term | Spec 026 term |
|---|---|
| Interim results/standings post | Provisional Results post |
| Final results/standings post (after penalty approval) | Post-Race Penalty Results post |
| *(no equivalent — new)* | Final Results post (after appeals approval) |
| `rounds.finalized = 1` | Round `result_status = FINAL` |

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Result Lifecycle Labeling and Heading Format (Priority: P1)

A tier-2 admin submits session results for a round. The posted results and standings carry the label `Provisional Results` and the standard heading format. When the admin clicks **Approve** in the existing penalty review wizard, results are reposted as `Post-Race Penalty Results` and the submission channel transitions to the appeals review view rather than closing. When the admin clicks **Approve** in the appeals review view, results are reposted as `Final Results` and the channel closes.

**Why this priority**: The labels and headings are visible to all league members on every post, and the lifecycle state gates other features (`round results amend`). All other stories depend on this three-stage sequence existing.

**Independent Test**: Can be fully tested by submitting a round, approving a zero-penalty penalty review, and approving a zero-change appeals review. Verify three distinct results/standings reposts appear — each with the correct label and the standard heading — without configuring a verdicts channel.

**Acceptance Scenarios**:

1. **Given** session results are submitted for a round, **When** the initial results post is published, **Then** the heading reads `Season {N} {Division Name} Round {X} — {Session Name}` and the label reads `Provisional Results`.
2. **Given** the penalty review **Approve** button is clicked (with or without staged penalties), **When** the results are reposted, **Then** the label reads `Post-Race Penalty Results` and the heading format is unchanged.
3. **Given** the appeals review **Approve** button is clicked (with or without staged appeal corrections), **When** the results are reposted, **Then** the label reads `Final Results` and the heading format is unchanged.
4. **Given** the penalty review is approved, **When** the submission channel would previously have closed, **Then** the channel remains open and an appeals review prompt appears instead.
5. **Given** the appeals review is approved, **When** results are reposted as `Final Results`, **Then** the submission channel closes and the round is in `FINAL` state.
6. **Given** a round is in `FINAL` state, **When** a full re-entry amendment is applied via `round results amend`, **Then** the resulting post carries the `Final Results` label.
7. **Given** a round is in `PROVISIONAL` or `POST_RACE_PENALTY` state, **When** `round results amend` is attempted, **Then** the command is rejected with a clear error stating that amendments are only permitted on final results.

---

### User Story 2 — Division Verdicts Channel Configuration (Priority: P2)

A tier-2 admin runs `/division verdicts-channel` with a division name and a Discord channel. The bot stores that channel as the division's verdict announcement target. Subsequent penalty and appeal announcements for that division are posted there.

**Why this priority**: Without the verdicts channel, announcements fall back to the results channel — which works but is not the intended configuration for production use.

**Independent Test**: Can be fully tested by running the command, then approving one staged penalty, and verifying the announcement appears in the specified channel and not in the results channel.

**Acceptance Scenarios**:

1. **Given** no verdicts channel is configured for a division, **When** `/division verdicts-channel <division> <channel>` is run by a tier-2 admin, **Then** the bot confirms the channel is set and stores it.
2. **Given** a verdicts channel is already set, **When** the command is run with a different channel, **Then** the previous value is overwritten and the bot confirms the new channel.
3. **Given** no verdicts channel is configured, **When** a penalty or appeal announcement would be posted, **Then** the message falls back to the division's results channel.
4. **Given** a verdicts channel is configured, **When** a penalty or appeal announcement is posted, **Then** it goes to the verdicts channel, not the results channel.
5. **Given** an invalid or non-existent channel is supplied, **When** the command is run, **Then** the bot rejects it with a clear error.
6. **Given** the Results module is enabled and a verdicts channel is configured for a division, **When** a tier-2 admin runs `/season review`, **Then** the review output lists the verdicts channel for that division alongside the results and standings channels.
7. **Given** the Results module is enabled and a division has no verdicts channel configured, **When** a tier-2 admin runs `/season approve`, **Then** the approval is rejected with an error identifying the division and instructing the admin to run `/division verdicts-channel`.

---

### User Story 3 — Penalty Announcements (Priority: P3)

When the penalty review **Approve** button is clicked and there are staged penalties, the bot posts a formatted announcement to the division's configured verdicts channel (or fallback) for each applied penalty. The announcement includes the session context header, the affected driver mentioned by Discord tag, the penalty in descriptive language, and the description and justification the admin entered in the modal.

**Why this priority**: Announcements make stewards' decisions publicly visible; this is the primary new output of the feature.

**Independent Test**: Can be fully tested by staging a penalty with description and justification, clicking Approve, and verifying the announcement appears in the verdicts channel with all five required fields in the correct format.

**Acceptance Scenarios**:

1. **Given** a staged penalty of +5 seconds is approved, **When** the announcement is posted, **Then** it contains the header `Season {N} {Division Name} Round {X} — {Session Name}`, the driver's Discord mention, and the text `5 seconds removed`.
2. **Given** a staged penalty of −3 seconds is approved, **When** the announcement is posted, **Then** the penalty field reads `3 seconds added`.
3. **Given** a staged DSQ is approved, **When** the announcement is posted, **Then** the penalty field reads `Disqualified`.
4. **Given** a penalty is approved with a description and justification, **When** the announcement is posted, **Then** both fields appear verbatim in the announcement.
5. **Given** multiple penalties are staged for the same round, **When** all are approved together, **Then** each penalty produces its own separate announcement post in the verdicts channel.
6. **Given** the penalty review is approved with no staged penalties, **When** the round transitions to `POST_RACE_PENALTY`, **Then** no announcement is posted to the verdicts channel (there is nothing to announce).

---

### User Story 4 — Appeals Review Stage (Priority: P4)

After the penalty review is approved, the transient submission channel transitions to an **Appeals Review** state. An appeals review prompt appears automatically in the channel, mirroring the penalty review prompt. A tier-2 admin may stage appeal corrections (same driver, penalty value, description, justification fields as the penalty modal), review them, and approve. On approval the round becomes `FINAL`, `Final Results` are posted, and the channel closes.

**Why this priority**: The appeals stage is the mechanism that produces the definitive `FINAL` state required before `round results amend` is available, and it unlocks a second round of verdict announcements.

**Independent Test**: Can be fully tested by approving a penalty review (zero or more penalties), then approving the appeals review (with one staged correction), and verifying the channel closes, the round is in `FINAL` state, and the corrected `Final Results` post appears.

**Acceptance Scenarios**:

1. **Given** the penalty review **Approve** button is clicked, **When** the submission channel transitions, **Then** an appeals review prompt appears in the same channel with the same controls as the penalty review prompt (Add, No Changes / Confirm, Approve).
2. **Given** the appeals review prompt is active, **When** a tier-2 admin stages an appeal correction using the same modal as the penalty wizard, **Then** the correction is staged and the list updates.
3. **Given** the appeals review **Approve** button is clicked with staged corrections, **When** finalization runs, **Then** the corrections are applied to the driver results, standings are recomputed, and `Final Results` are posted.
4. **Given** the appeals review **Approve** button is clicked with no staged corrections, **When** finalization runs, **Then** `Final Results` identical to the `Post-Race Penalty Results` are posted and the round is marked `FINAL`.
5. **Given** finalization from the appeals review completes, **When** the channel is closed, **Then** the round is in `FINAL` state and `round results amend` is now available for that round.
6. **Given** a round is still in `PROVISIONAL` state (penalty review not yet approved), **When** any attempt is made to interact with an appeals review, **Then** no appeals review prompt exists in the channel; the channel is still showing the penalty review prompt.

---

### User Story 5 — Mandatory Description and Justification Fields on the Penalty/Appeal Modal (Priority: P5)

The existing `AddPenaltyModal` (currently: driver field + penalty value field) is expanded with two new mandatory `TextInput` fields: **description** (what the ruling is) and **justification** (the reasoning). Both fields are required; the modal cannot be submitted without them. This same expanded modal is used in both the penalty review wizard and the appeals review wizard.

**Why this priority**: The description and justification fields are required for verdict announcements to be complete and for the audit trail to capture why each decision was made.

**Independent Test**: Can be fully tested by attempting to submit the modal without each field in turn and verifying the Discord-level required-field validation blocks submission. Then completing the modal and verifying the stored record contains the correct values.

**Acceptance Scenarios**:

1. **Given** the penalty modal is open, **When** the admin leaves the description field empty and submits, **Then** the Discord modal's required-field validation blocks submission.
2. **Given** the penalty modal is open, **When** the admin leaves the justification field empty and submits, **Then** the Discord modal's required-field validation blocks submission.
3. **Given** all four fields are filled in (driver, penalty value, description, justification) and the modal is submitted, **When** the penalty is staged, **Then** the staged entry carries the description and justification verbatim.
4. **Given** the appeals review modal is open (same modal, same four fields), **When** the admin fills in all fields and submits, **Then** the staged appeal correction carries the description and justification verbatim.
5. **Given** a penalty or appeal correction is approved, **When** the audit log entry is written, **Then** it includes the description and justification for each applied entry.

---

### Edge Cases

- What happens when a round has only cancelled sessions? No penalty or appeals stage is entered; the round skips directly to `FINAL` with `Final Results` posts (matched to the existing CANCELLED session handling in spec 023).
- What if the bot restarts while the submission channel is in the appeals review state? The channel persists; on bot restart the appeals review prompt is re-posted (same recovery guarantee as spec 023 penalty restart recovery). Staged-but-unapproved appeal corrections are transient and not preserved, matching the same policy as staged penalties.
- What if zero penalties were staged but the admin still clicks Approve in the penalty review? The round transitions to `POST_RACE_PENALTY`, a `Post-Race Penalty Results` post is made (identical to the `Provisional Results` post), and the appeals review prompt appears. This is valid.
- What if zero appeal corrections are staged? The admin clicks Approve in the appeals review; the round transitions to `FINAL` with `Final Results` identical to `Post-Race Penalty Results`. Valid.
- What if a driver receives two separate penalties in the same penalty review session? Each penalty entry in the staged list produces its own individual announcement when approved.
- What if the verdicts channel is deleted or inaccessible at the time an announcement is attempted? The bot catches the channel error, logs it, and falls back to the results channel. If both are inaccessible, the error is logged and the announcement is skipped without blocking finalization.
- What about existing finalized rounds (from before this feature)? Rounds with `finalized = 1` in the legacy schema are treated as `FINAL`; they already completed the only review stage that existed at the time. No re-review is required.
- What is the `result_status` field scoped to — round or session? The lifecycle state is tracked at the **round** level (one state per round), since the penalty wizard and appeals wizard both operate across all sessions of a round simultaneously. The `result_status` from the constitution's `SessionResult` entity is effectively uniform for all sessions of the same round because they are all advanced together by the wizard approval.

---

## Requirements *(mandatory)*

### Functional Requirements

**Result Lifecycle & Labeling**

- **FR-001**: Each round MUST carry a `result_status` of `PROVISIONAL`, `POST_RACE_PENALTY`, or `FINAL`. New rounds MUST be created with status `PROVISIONAL` (replacing the existing `finalized` boolean, where `finalized = 1` maps to `FINAL`).
- **FR-002**: Every results post MUST include a heading in the format `Season {N} {Division Name} Round {X} — {Session Name}` followed immediately by the result-type label.
- **FR-003**: Every standings post MUST include the same heading and label format, using the label corresponding to the round's current `result_status`.
- **FR-004**: Result-type labels MUST read exactly: `Provisional Results`, `Post-Race Penalty Results`, and `Final Results` for the three states respectively.
- **FR-005**: When the penalty review `Approve` button is clicked, the round `result_status` MUST be set to `POST_RACE_PENALTY` and results and standings MUST be reposted with the `Post-Race Penalty Results` label. The submission channel MUST remain open.
- **FR-006**: When the appeals review `Approve` button is clicked, the round `result_status` MUST be set to `FINAL` and results and standings MUST be reposted with the `Final Results` label. The submission channel MUST then close.
- **FR-007**: `round results amend` MUST be rejected with a clear error if the target round's `result_status` is not `FINAL`.
- **FR-008**: Results produced by `round results amend` MUST always carry the `Final Results` label.
- **FR-009**: Approving the penalty review with an empty staged list (zero penalties) MUST still advance the round to `POST_RACE_PENALTY` and open the appeals stage.
- **FR-010**: Approving the appeals review with an empty staged list (zero corrections) MUST still advance the round to `FINAL`.

**Division Verdicts Channel**

- **FR-011**: A new `/division verdicts-channel <division> <channel>` command MUST be implemented for tier-2 admins. It sets `penalty_channel_id` on the division's `DivisionResultsConfig` record.
- **FR-012**: The command MUST validate that the supplied channel exists and is accessible by the bot before storing it.
- **FR-013**: If no `penalty_channel_id` is configured for a division, the bot MUST fall back to `results_channel_id` for all verdict announcements.
- **FR-025**: When the Results module is enabled, `/season review` MUST display the configured verdicts channel (or `*(not configured)*`) for each division, in the same per-division block as the results and standings channels.
- **FR-026**: When the Results module is enabled, `/season approve` MUST be rejected if any division does not have a `penalty_channel_id` configured. The error message MUST identify each unconfigured division and instruct the admin to run `/division verdicts-channel <division> <channel>`.

**Penalty & Appeal Announcement Format**

- **FR-014**: When the penalty review is approved with one or more staged penalties, the bot MUST post one announcement per penalty to the verdicts channel (or fallback). Each announcement MUST contain exactly:
  1. Header: `Season {N} {Division Name} Round {X} — {Session Name}`
  2. Driver: Discord mention of the penalised driver
  3. Penalty: descriptive translation of the magnitude (FR-015)
  4. Description: verbatim from the modal
  5. Justification: verbatim from the modal
- **FR-015**: Penalty magnitude MUST be translated: positive time (`+Ns`) → `{N} seconds removed`; negative time (`−Ns`) → `{N} seconds added`; DSQ → `Disqualified`.
- **FR-016**: When the appeals review is approved with one or more staged corrections, the bot MUST post one announcement per correction in the same format as FR-014, using the correction's stored description and justification.
- **FR-017**: Approving with an empty staged list (either penalty or appeals review) MUST produce no announcement posts.

**Modal Fields**

- **FR-018**: The existing `AddPenaltyModal` MUST be extended with two new `TextInput` fields, both marked as **required** at the Discord modal level:
  - `description` — free text, label "Penalty description", max 200 characters.
  - `justification` — free text, label "Justification", max 200 characters.
- **FR-019**: The same expanded modal MUST be used in both the penalty review wizard and the appeals review wizard.
- **FR-020**: Description and justification values MUST be included in the staged entry, stored in the `PenaltyRecord` / `AppealRecord`, and included in the audit log entry.

**Appeals Review Stage**

- **FR-021**: When the penalty review `Approve` button is clicked, the submission channel MUST transition to the appeals review state. An appeals review prompt MUST be posted automatically in the same channel. The prompt MUST mirror the penalty review prompt: it lists all drivers across non-cancelled sessions, shows a staged corrections list (initially empty), and provides Add, No Changes / Confirm, and Approve controls.
- **FR-022**: The Make Changes / staged-list recovery mechanism from the penalty review MUST equally apply to the appeals review.
- **FR-023**: On approving the appeals review, standings for the affected round and all subsequent rounds in the division MUST be recomputed and reposted atomically.
- **FR-024**: After bot restart while the channel is in appeals review state, the appeals review prompt MUST be re-posted (matching the recovery guarantee in spec 023 FR-014). Staged-but-unapproved corrections need not be preserved.

### Key Entities

- **Round** (amended): `finalized` (BOOLEAN) replaced by `result_status` (ENUM: `PROVISIONAL` / `POST_RACE_PENALTY` / `FINAL`, default `PROVISIONAL`). Migration: `finalized = 1` → `FINAL`; `finalized = 0` → `PROVISIONAL`.
- **PenaltyRecord** (new, linked to `DriverSessionResult`): stores `penalty_type`, `time_seconds` (nullable), `description` (TEXT NOT NULL), `justification` (TEXT NOT NULL), `applied_by`, `applied_at`, and `announcement_channel_id`. Replaces the loose `post_race_time_penalties` / `post_stewarding_total_time` fields on `DriverSessionResult` for new records.
- **AppealRecord** (new, linked to `PenaltyRecord` or standalone): stores `status` (PENDING / UPHELD / OVERTURNED), `description` (TEXT NOT NULL), `justification` (TEXT NOT NULL), `submitted_by`, `submitted_at`, `reviewed_by`, `reviewed_at`, `review_reason`. One per penalty maximum.
- **DivisionResultsConfig** (amended): gains `penalty_channel_id` (TEXT, nullable), populated via `/division verdicts-channel`.

## Assumptions

- All sessions within a round advance through the lifecycle states simultaneously (via the wizard operating at the round level). There is no per-session independent lifecycle tracking within a single round.
- A round where all sessions are `CANCELLED` is implicitly `FINAL` immediately after the last session is marked CANCELLED; no penalty or appeals prompt is shown.
- Existing rounds with `finalized = 1` (created before this feature) are treated as `FINAL` with no migration action required beyond the column rename/type change. They do not need to go through the appeals stage retroactively.
- Negative time corrections (time added back to benefit a driver) follow the same signed-integer mechanics already implemented in spec 023; the only change is that the value is now stored in a `PenaltyRecord` row rather than a loose column.
- The appeals review wizard uses the exact same `AddPenaltyModal` class as the penalty wizard (after the two new fields are added). There is no separate modal class for appeals.
- Verdict announcements are posted one per penalty/correction at the time of approval (not at the time of staging).
- When the verdicts channel is deleted or inaccessible, the bot falls back to the results channel. If both are unavailable, the announcement is skipped without blocking finalization.
- `round results amend` restriction to `FINAL` state applies to the existing full re-entry amendment flow; it does not apply to the penalty/appeals wizard flows themselves (those are the mechanism that drives rounds toward `FINAL`).

## Dependencies

- Spec 023 (Inline Post-Submission Penalty Review) — this feature modifies the `ApprovalView.approve_btn` behaviour and the `finalize_round` function in `result_submission_service.py`, and the `PenaltyReviewState` dataclass.
- `DivisionResultsConfig` and `results_channel_id` — prerequisite for the announcement fallback.
- `rounds.finalized` column — replaced by `rounds.result_status`; migration required.
- Tier-2 admin permission guard (`_require_lm` in `penalty_wizard.py`) — reused unchanged for the appeals wizard.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every results or standings post produced by the bot for a round carries a lifecycle label (`Provisional Results`, `Post-Race Penalty Results`, or `Final Results`) and the standard session heading — zero unlabeled posts permitted.
- **SC-002**: Every approved penalty or appeal correction produces one announcement per entry in the verdicts channel (or fallback) with all five required fields present.
- **SC-003**: The Discord modal's built-in required-field validation prevents submission of the penalty/appeal modal without both the description and justification fields — this is enforced at the Discord client level with no additional server-side step needed.
- **SC-004**: `round results amend` is rejected for every round not in `FINAL` state and accepted for every round in `FINAL` state — no false positives or false negatives in any test scenario.
- **SC-005**: The division verdicts channel can be configured and updated by a tier-2 admin in a single command interaction.
- **SC-006**: The full round lifecycle (initial submission → penalty review approved → appeals review approved) produces exactly three results and standings reposts per session, each with the correct label, using only the existing wizard channel without any additional commands.
- **SC-007**: `/season approve` is blocked for any server where the Results module is enabled and at least one division has no verdicts channel configured — and it succeeds once all divisions have a verdicts channel configured.
