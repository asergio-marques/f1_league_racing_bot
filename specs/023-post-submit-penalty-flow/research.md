# Research: Inline Post-Submission Penalty Review

**Branch**: `023-post-submit-penalty-flow`  
**Phase**: 0 — Pre-design research  
**Date**: 2026-03-26

---

## 1. Current Round State Machine

**Finding**: `rounds` has three boolean phase flags (`phase1_done`, `phase2_done`, `phase3_done`) but no round-level finalization state.  Result submission completion is inferred from DB state: `get_next_pending_phase` treats a round as needing results if the results module is enabled and no `ACTIVE` `session_results` row exists for it.

**Decision**: Add a `finalized` boolean column to `rounds` (migration 019) instead of introducing a `round_state` enum. Booleans are already the idiom used by the rest of this table; adding `finalized INTEGER NOT NULL DEFAULT 0` keeps the schema consistent and is the smallest possible change.

**Rationale**: Enum/text columns would require query changes in multiple services and would introduce forward-incompatible values. A boolean flag is sufficient because "finalized" is the only new terminal state needed; the previously implicit SUBMITTED state (all sessions present) becomes the new intermediate POST-ROUND-PENALTIES state handled in memory.

**Alternatives considered**:  
- `TEXT status` column with values `PENDING / SUBMITTED / FINALIZED`: rejected, the `status` column already exists on `rounds` for cancellation and would need to carry penalty-state too.
- In-memory-only tracking via `round_submission_channels.closed` and a new `penalty_active` flag: rejected, would not survive bot restarts cleanly (FR-014).

---

## 2. Signed Time Penalty Support in `penalty_service.py`

**Finding**: `validate_penalty_input` in `penalty_service.py` uses `_TIME_PENALTY_RE = re.compile(r"^\+?(\d+)s?$")` which captures only the absolute value and ignores a leading `-`. The function explicitly rejects `seconds <= 0` with an error. `_apply_time_penalty` calls `ms += penalty_seconds * 1000` — so it already supports negative values mechanically if passed in.

**Decision**: Modify `validate_penalty_input` to:
1. Accept a leading `-` in the regex: `r"^([+-]?\d+)s?$"`.
2. Replace the `<= 0` guard with `== 0`.
3. Pass the signed integer to `StagedPenalty.penalty_seconds`.

Modify `_apply_time_penalty` so that when `ms + penalty_seconds * 1000 < 0`, it clamps to `0` ms to avoid negative total times in the output string (edge case: a ridiculously large negative penalty). Document the clamp behavior.

**Rationale**: The arithmetic path already works for negative values; only the input parsing and guard need updating. This keeps the change minimal.

**Alternatives considered**:  
- Require input like `-5s` (with explicit minus sign mandatory): rejected, inconsistency with positive entries which don't require `+`.

---

## 3. Interim vs. Final Discord Message Tracking

**Finding**: `session_results.results_message_id` stores the Discord message ID of the posted results per session. `driver_standings_snapshots.standings_message_id` stores the most recent standings post per division-round pair (fetched via `_get_standings_message_id` in `results_post_service.py`). No column tracks whether these messages are "interim" or "final".

**Decision**: No new columns required. Use the existing `results_message_id` and `standings_message_id` throughout:
- Before FINALIZED: when posting interim results, populate these columns as normal.
- On finalization: delete the old Discord messages by fetching their IDs from the DB, post the final messages, then overwrite the IDs in the DB.

This approach reuses the existing edit-in-place / replace pattern already present in `results_post_service.post_round_standings` (which fetches `existing_msg_id` and edits or creates as appropriate).

**Rationale**: The existing service already handles the "post or edit" pattern; finalization is just a "delete then post fresh" variant.

**Alternatives considered**:  
- Add an `is_interim` boolean to `session_results`: rejected, adds column for a transient state rather than a value that needs to persist.

---

## 4. Penalty Wizard Entry Point: In-Channel vs. Standalone Command

**Finding**: The current `round_results_penalize` command is implemented as a slash command in `season_cog.py` (lines 1350–~1600). It uses `bot.wait_for('message', ...)` to collect input in the channel used for the interaction (the bot command channel), not a dedicated channel. This means it cannot be "inside" the submission channel.

**Decision**: The new penalty state lives entirely within the submission channel, driven by `on_message` and Discord UI components (buttons + modals), not `wait_for`. The `result_submission_service.py` `run_result_submission_job` flow already ends by calling `close_submission_channel`; that call is deferred until post-penalty approval.

The `round_results_penalize` slash command in `season_cog.py` is removed (deregistered by deleting the decorated method from the cog).

**Rationale**: Keeping penalty collection in the same channel as submission makes the workflow contiguous for the admin and avoids a separate ephemeral interaction flow. Discord UI components (buttons for "Add Penalty", "No Penalties", "Remove", "Approve") provide clearer affordances than free-text `wait_for` prompts.

**Alternatives considered**:  
- Keep `wait_for` inside the submission channel: rejected, this pattern blocks the coroutine and is fragile across bot restarts (FR-014). Modal-based + button-driven UI is more robust.
- Separate modal/view re-entry after restart: modals are not re-post-able; the penalty state prompt must be a regular message with a persistent View so it survives restarts.

---

## 5. Test-Mode Advance Gate

**Finding**: `get_next_pending_phase` currently classifies a round as needing results (phase 4) if `results_module_enabled AND no ACTIVE session_result exists`. Once any session results exist it no longer returns that round. There is no concept of "results submitted but not finalized" in the current phase queue — once submission opens, the same guard in `test_mode_cog.py` (phase 4 path) blocks re-advancing by checking `is_submission_open`.

**Decision**: Extend `is_submission_open` (or add a parallel `is_round_finalized`) DB helper. In `test_mode_cog.py` advance command (phase 4 path), after the existing `is_submission_open` guard, add a second check: if all sessions for this round have been submitted (i.e., `session_results` rows exist) but `rounds.finalized = 0`, block advance with a message naming the pending division and round. This check is only applied in test mode (the command is already test-mode-gated).

`get_next_pending_phase` is also updated: after the "rounds with results" exclusion, additionally exclude rounds where `finalized = 1`, so finalized rounds do not re-surface as phase 4 entries.

**Rationale**: The change is isolated to `test_mode_service.py` and `test_mode_cog.py`. No live-mode paths are affected.

**Alternatives considered**:  
- A `phase_number = 5` for penalty review: rejected, the advance command is meant to advance to the next *event*, not to control the in-channel wizard. The finalization gate is a guard, not a schedulable event.

---

## 6. Bot-Restart Recovery (FR-014)

**Finding**: On restart, `run_result_submission_job` is not re-invoked automatically for open channels. The submission channel stays open (Discord does not delete it) but no bot handler is listening. The existing pattern for restart recovery relies on the scheduler re-queuing jobs it missed.

**Decision**: On `bot.on_ready`, scan `round_submission_channels` for rows where `closed = 0`. For each:
- If `rounds.finalized = 1`: mark `closed = 1` and delete the Discord channel (cleanup of an orphaned channel that was finalized before cleanup ran).
- If `session_results` exist for all applicable sessions of the round (i.e., the round is in penalty-pending state): re-post the penalty prompt into the existing channel and re-attach the penalty `View`.
- If not all sessions exist (submission mid-flight): re-post the current session prompt (already handled by the existing submission restart logic, if any, or added here).

The `View` for the penalty state must use **persistent views** (`timeout=None`, registered with `bot.add_view`) so Discord interactions from buttons work across restarts without the bot tracking the original coroutine.

**Rationale**: Persistent views are the canonical discord.py solution for buttons that survive restarts. This is the same approach as the existing submission `View` components where present.

**Alternatives considered**:  
- Store penalty wizard state (staged penalties) in the DB: partial state is acceptable as a recovery aid, but staged (unapproved) penalties are transient and need not persist — the admin simply re-enters them after restart. The spec only requires that the channel remains open and finalizable, not that staged-but-unapproved data is preserved.

---

## 7. Removal of `round results penalize` Command

**Decision**: Remove the `round_results_penalize` method (and its sub-group registration) from `season_cog.py` entirely. Discord slash commands are deregistered automatically when the cog method is absent. No tombstone response is added in the bot itself; the Discord UI simply won't show the command.

**Rationale**: Keeping a tombstone increases maintenance surface. The spec requirement (US5, scenario 1) is satisfied by absence from the command registry.

**Alternatives considered**:  
- Redirect the old command with a deprecation message: rejected, the spec says the command should be absent or show a removal notice, and the simpler "absent" path is preferred.
