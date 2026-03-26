# Contract: Penalty Review Wizard (inline, submission channel)

This document describes the interaction contract for the Post-Round Penalties state that replaces the removed `/round results penalize` command.

The penalty review is **not a slash command**. It is an in-channel interactive flow that activates automatically within the transient results submission channel after all sessions for a round have been submitted or cancelled.

*Access: Trusted admin only. All interactions from non-trusted users are silently rejected or acknowledged with a permissions error.*

---

## Entry Condition

Triggered automatically when the submission wizard processes its final session (submitted or cancelled). The submission channel is **not closed**. Instead:

1. Interim results tables are posted to the division's results channel for each non-cancelled session.
2. Interim standings are posted to the division's standings channel.
3. The bot posts a **Penalty Review prompt** in the submission channel.

---

## Penalty Review Prompt Message

The prompt is a persistent Discord message containing:

- A summary of all drivers from non-cancelled sessions, grouped by session, showing current position.
- A list of currently staged penalties (initially empty).
- Buttons:
  - **Add Penalty** (disabled if all sessions were CANCELLED)
  - **No Penalties / Confirm** — advances to the approval step with an empty list (if no penalties staged) or requests explicit confirmation to clear staged penalties before advancing.
  - **Approve** — visible only when at least one penalty is staged; advances to the approval step with the staged list intact.

---

## Add Penalty Flow

On pressing **Add Penalty**:

1. The bot presents a **session selection** prompt (buttons for each non-cancelled session).
2. The admin selects a session. The bot presents a **driver selection and value entry** modal or prompt.
3. The admin provides:
   - Driver `@mention` or Discord user ID.
   - Penalty value — one of:
     - A signed integer in seconds: `5`, `+5`, `-3`, `+5s`, `-3s`.
     - The literal string `DSQ`.

### Validation Rules

| Condition | Outcome |
|---|---|
| Driver not in this round's results for the selected session | Rejected with explanation |
| Value is `0` or `+0s` or `-0s` | Rejected: zero-second penalty has no effect |
| Signed time value for a qualifying session | Rejected: only `DSQ` accepted for qualifying |
| Valid DSQ for any session | Staged |
| Valid non-zero signed integer for a race session | Staged |

4. On successful staging, the bot updates the staged penalty list in the prompt message and returns to the session selection step (another penalty can be added).

---

## Remove Penalty

Each entry in the staged penalty list has a **Remove** button (or the list displays numbered entries and the admin runs a remove action). Pressing Remove:
- Removes only that specific entry.
- All other staged entries remain unchanged.
- The prompt message is updated to reflect the current list.

---

## No Penalties / Confirm Behavior

| Staged list state | Behavior |
|---|---|
| Empty | Advances directly to approval step with empty list |
| Non-empty | Bot requests explicit confirmation; if confirmed, list is cleared and approval step shown with empty list; if cancelled, returns to penalty entry state with list intact |

---

## Approval Step

The bot posts a **Review message** showing the final staged penalty list and two buttons:

- **Make Changes** — returns to penalty entry state with all staged penalties restored.
- **Approve** — applies all staged penalties and finalizes the round.

---

## Finalization (on Approve)

1. All staged penalties are applied to `driver_session_results` using `penalty_service.apply_penalties`.
2. Final positions and points for affected sessions are recomputed.
3. Final results tables are posted to the division's results channel.
4. The interim results posts and interim standings post are **deleted** from their channels.
5. Final standings are posted (replace interim standings message ID in DB).
6. `rounds.finalized` is set to `1`.
7. The submission channel is deleted.
8. An audit log entry is written.

---

## Removed Command: `/round results penalize`

**Status**: **REMOVED** in this feature branch.

This command is deregistered from the bot. It no longer appears in the Discord slash command menu. Existing league admins should use the inline penalty review flow within the submission channel.

Post-finalization corrections should use the existing `/results amend` flow (spec 019 User Story 6).
