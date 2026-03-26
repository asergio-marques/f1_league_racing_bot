# Implementation Plan: Results & Standings — Inline Post-Submission Penalty Review

**Branch**: `023-post-submit-penalty-flow` | **Date**: 2026-03-26 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `specs/023-post-submit-penalty-flow/spec.md`

---

## Summary

Replace the standalone `round results penalize` slash command with an inline penalty review state embedded in the results submission wizard. After all sessions in a round are submitted or cancelled, the transient submission channel stays open and enters a Post-Round Penalties state where trusted admins may stage signed time penalties (positive or negative) and DSQs. Only after the penalty list is approved does the round reach FINALIZED state, the final results and standings posts replace the interim posts, and the submission channel closes. In test mode, the advance command is blocked until the current round is finalized.

---

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: discord.py (`discord.ext.commands`, `discord.ui`), APScheduler, aiosqlite  
**Storage**: SQLite via `db/database.py` — aiosqlite async wrapper  
**Testing**: pytest + pytest-asyncio; existing unit and integration test suites under `tests/`  
**Target Platform**: Linux server / any host running the Discord bot process  
**Project Type**: Discord bot (event-driven single-process service)  
**Performance Goals**: No throughput requirements; interactions must respond within Discord's 3-second deferred interaction window  
**Constraints**: Discord persistent views required for button handlers that survive bot restarts (discord.py `timeout=None` + `bot.add_view`)  
**Scale/Scope**: Single-server Discord bot; one penalty wizard active per round per division at a time

---

## Constitution Check

*All principles checked against constitution v2.4.1.*

| Principle | Status | Notes |
|---|---|---|
| I — Minimal footprint | ✅ PASS | One new DB column; no new tables |
| II — Explicit access control | ✅ PASS | Penalty UI rejects non-trusted-admin interactions |
| III — Audit trail | ✅ PASS | Existing `audit_entries` table used for PENALTY_APPLIED and ROUND_FINALIZED entries |
| IV — Reversibility gates | ✅ PASS | "Make Changes" step allows staged penalty correction before committing |
| V — Module gating | ✅ PASS | Results module enabled check preserved in all entry points |
| VI — Season state coherence | ✅ PASS | Penalty state only reachable from within an ACTIVE season with submitted sessions |
| VII — Channel categories | ✅ PASS | No new channel categories; submission channel lifecycle extended, not changed |
| VIII — Test mode isolation | ✅ PASS | FINALIZED gate applies only when test_mode_active; live mode unaffected |
| IX — Wizard idempotency | ✅ PASS | Re-posting penalty prompt on restart does not duplicate data; staged list is in-memory only |
| X — Modular architecture | ✅ PASS | Results module; no cross-module writes |
| XI — Data integrity | ✅ PASS | `rounds.finalized` default 0; existing rounds unaffected by migration |
| XII — Race results integrity | ✅ PASS | Signed time penalties extend existing penalty mechanics; DSQ semantics unchanged |

**Gate result**: No violations. No complexity tracking required.

---

## Project Structure

### Documentation (this feature)

```text
specs/023-post-submit-penalty-flow/
├── plan.md                              # This file
├── spec.md                              # Feature specification
├── research.md                          # Phase 0 output
├── data-model.md                        # Phase 1 output
├── quickstart.md                        # Phase 1 output
├── contracts/
│   └── penalty-review-wizard.md         # Phase 1 output
├── checklists/
│   └── requirements.md
└── tasks.md                             # Phase 2 output (speckit.tasks)
```

### Source Code (affected files)

```text
src/
├── db/
│   └── migrations/
│       └── 019_round_finalized.sql      # NEW — adds rounds.finalized column
├── models/
│   └── round.py                         # MODIFY — add finalized: bool = False field
├── services/
│   ├── penalty_service.py               # MODIFY — signed penalty support; new PenaltyWizardState class
│   ├── result_submission_service.py     # MODIFY — defer close_submission_channel; enter penalty state
│   ├── test_mode_service.py             # MODIFY — exclude finalized rounds from phase queue
│   └── results_post_service.py          # MODIFY — delete_and_repost_final helper
├── cogs/
│   ├── season_cog.py                    # MODIFY — remove round_results_penalize command
│   └── test_mode_cog.py                 # MODIFY — add FINALIZED gate in advance command
└── bot.py                               # MODIFY — add persistent PenaltyReviewView on_ready recovery

tests/
├── unit/
│   ├── test_penalty_service.py          # MODIFY — add signed penalty tests
│   └── test_result_submission_service.py # MODIFY — add penalty state transition tests
└── integration/
    └── test_penalty_flow.py             # NEW — end-to-end penalty review flow tests
```

---

## Implementation Phases

### Phase 1 — Database & Model (foundation)

**Goal**: Land the schema change and update the `Round` model. All existing tests must still pass.

**Tasks**:

1. **Write migration `019_round_finalized.sql`**
   ```sql
   -- Migration 019: Add finalized flag to rounds
   ALTER TABLE rounds ADD COLUMN finalized INTEGER NOT NULL DEFAULT 0;
   ```
   Register in the migrations runner (`db/__init__.py` or equivalent migration list).

2. **Update `Round` dataclass** (`src/models/round.py`)
   - Add `finalized: bool = False` field.
   - Update all DB `SELECT` queries in `season_service.py` (and any other service that constructs `Round` from a row) to include the `finalized` column.

3. **Update `season_service.get_division_rounds`** (and any other query that reads rounds) to map `finalized` from the DB row.

**Acceptance**: `pytest` green; existing round-related tests unaffected.

---

### Phase 2 — Signed Penalty Support

**Goal**: Allow `penalty_service.validate_penalty_input` to accept and process signed integers.

**Tasks**:

4. **Update `_TIME_PENALTY_RE`** in `penalty_service.py`
   ```python
   _TIME_PENALTY_RE = re.compile(r"^([+-]?\d+)s?$", re.IGNORECASE)
   ```

5. **Update `validate_penalty_input`**
   - Remove the `seconds <= 0` guard. Replace with `seconds == 0` guard (reject zero only).
   - Pass the signed integer (`int(m.group(1))`) directly to `StagedPenalty.penalty_seconds`.

6. **Update `_apply_time_penalty`**
   - After computing `ms += penalty_seconds * 1000`, clamp to `max(0, ms)` to prevent negative total times.
   - Document the clamp behavior in the docstring.

7. **Update `apply_penalties` to handle negative `post_race_time_penalties` accumulation**
   - The existing line `update["post_race_time_penalties"] = existing_pen + penalty_s` already works for signed values; verify no cast issue.

8. **Unit tests**: add cases for `-3`, `-5s`, `0`, `+0s` to `test_penalty_service.py`.

**Acceptance**: All new unit tests pass; existing penalty tests unaffected.

---

### Phase 3 — Penalty Wizard View (UI)

**Goal**: Build the persistent Discord UI components for the penalty review state.

**Tasks**:

9. **Create `PenaltyReviewState`** (class in `penalty_service.py` or a new `penalty_wizard.py`)

   ```python
   @dataclass
   class PenaltyReviewState:
       round_id: int
       division_id: int
       session_types_present: list[SessionType]   # non-cancelled sessions
       staged: list[StagedPenalty]                # mutable; in-memory only
       db_path: str
       bot: Any
   ```

10. **Create `PenaltyReviewView`** (`discord.ui.View`, `timeout=None`)

    Buttons:
    - **Add Penalty** — disabled when `session_types_present` is empty; opens `AddPenaltyModal`.
    - **No Penalties / Confirm** — calls `_handle_no_penalties(interaction, state)`.
    - **Approve** — visible only when `state.staged` is non-empty; calls `_handle_approve(interaction, state)`.

    The View holds a reference to `PenaltyReviewState`. On each button interaction, validate the actor is a trusted admin (check guild role); reject otherwise.

11. **Create `AddPenaltyModal`** (`discord.ui.Modal`)

    Fields:
    - Session (select/button step before modal, or a `TextInput` if session count is small)  
    - Driver (TextInput: Discord @mention or user ID)
    - Penalty value (TextInput: signed integer or `DSQ`)

    On submit: call `validate_penalty_input`; if valid, append to `state.staged`; edit the prompt message to show updated list; if invalid, send ephemeral error.

12. **`_handle_no_penalties`**: if `state.staged` is empty → proceed to approval step. If non-empty → send a confirmation prompt; on confirm clear `state.staged` and proceed to approval step.

13. **`_handle_approve`**: proceed to approval step — post a Review message showing `state.staged` with Make Changes and final Approve buttons.

14. **`ApprovalView`** (`discord.ui.View`, `timeout=None`):
    - **Make Changes** → re-post (or edit) the penalty prompt with `state.staged` intact; re-attach `PenaltyReviewView`.
    - **Approve** → call `finalize_round(interaction, state)`.

**Acceptance**: Views instantiated without error; button callbacks dispatch correctly in unit test mocks.

---

### Phase 4 — Finalization Service Logic

**Goal**: Implement `finalize_round` — applies penalties, posts final messages, deletes interim messages, marks round finalized.

**Tasks**:

15. **Add `delete_and_repost_final_results`** to `results_post_service.py`

    ```python
    async def delete_and_repost_final_results(
        db_path: str,
        round_id: int,
        division_id: int,
        guild: discord.Guild,
    ) -> None:
        """Delete interim results posts for each session and post final versions."""
    ```
    - For each `session_results` row for this round: fetch `results_message_id`; delete the Discord message if it exists; post the final formatted table; update `results_message_id`.
    - Then call the existing standings repost/replace: delete the current `standings_message_id` message; post fresh standings; update `standings_message_id`.

16. **Add `finalize_round`** (service function, called from `ApprovalView`):

    ```python
    async def finalize_round(
        interaction: discord.Interaction,
        state: PenaltyReviewState,
    ) -> None:
    ```
    Steps:
    1. Call `penalty_service.apply_penalties(db_path, round_id, division_id, staged, applied_by, bot)` — already handles DB writes and cascade.
    2. Call `delete_and_repost_final_results(db_path, round_id, division_id, guild)`.
    3. `UPDATE rounds SET finalized = 1 WHERE id = ?`.
    4. Audit log: `ROUND_FINALIZED` entry with actor, division, round, penalty list (JSON).
    5. Call `close_submission_channel(channel_id, round_id, guild, db_path)`.
    6. Send ephemeral confirmation to the interaction.

17. **Update `apply_penalties` audit log entry** (if the existing entry says `PENALTY_APPLIED`): emit the audit entry regardless of empty/non-empty staged list so every finalization is recorded.

**Acceptance**: `finalize_round` with an empty staged list produces no DB changes to `driver_session_results`; `rounds.finalized` is `1`; channel is deleted.

---

### Phase 5 — Submission Service Integration

**Goal**: Wire the penalty wizard entry into `result_submission_service.run_result_submission_job`.

**Tasks**:

18. **In `run_result_submission_job`**: remove (or defer) the call to `close_submission_channel`. After all sessions are processed:
    - Post interim results and interim standings (existing behavior).
    - Determine `session_types_present` (non-cancelled sessions with ACTIVE results).
    - Instantiate `PenaltyReviewState`.
    - Instantiate `PenaltyReviewView(state)`.
    - Post the penalty prompt message to the submission channel with the view.
    - Register the view: `bot.add_view(view)`.
    - **Do not close the channel.**

19. **Ensure the submission channel `on_message` handler** rejects any plain text messages while the round is in the penalty state (check `rounds.finalized == 0 AND session_results count == expected_count` — i.e., all sessions submitted — return a "round is in penalty review" message).

**Acceptance**: After last session submission, channel remains open, penalty prompt appears, no `close_submission_channel` is called yet.

---

### Phase 6 — Bot Restart Recovery

**Goal**: On `bot.on_ready`, recover open submission channels whose rounds are in penalty-pending state.

**Tasks**:

20. **Add recovery logic** in `bot.py` `on_ready` (or a dedicated `_recover_submission_channels` coroutine):

    ```python
    async def _recover_submission_channels(self) -> None:
        """Re-attach penalty wizard views to open submission channels on restart."""
    ```
    - Query `round_submission_channels WHERE closed = 0`.
    - For each: load `rounds.finalized` and check if all expected `session_results` rows exist.
      - If `finalized = 1`: orphaned cleanup — mark `closed = 1`, attempt channel delete.
      - If all sessions submitted (`finalized = 0`, all session results present): re-post penalty prompt + re-attach `PenaltyReviewView`.
      - If mid-submission: existing recovery path (if any) handles this; otherwise: re-post last session prompt.

21. **Register `PenaltyReviewView` as a persistent view** globally at bot startup:
    ```python
    bot.add_view(PenaltyReviewView(state=None))  # stub for custom_id registration
    ```
    Use `custom_id` constants on each button so discord.py can re-route interactions after restart.

**Acceptance**: After simulated bot restart (in integration test), an open submission channel in penalty-pending state receives the prompt again and the Approve button functions.

---

### Phase 7 — Test Mode Advance Gate

**Goal**: Block `test-mode advance` when the current round has sessions submitted but `finalized = 0`.

**Tasks**:

22. **Add `is_round_finalized(db_path, round_id) -> bool`** helper in `result_submission_service.py` (or `test_mode_service.py`):
    ```python
    async def is_round_finalized(db_path: str, round_id: int) -> bool:
        """Return True if rounds.finalized = 1 for the given round."""
    ```

23. **Update `get_next_pending_phase`** in `test_mode_service.py`:
    - In the fallback loop (results module enabled path), add `finalized = 0` to the query for rounds with results:
      ```sql
      AND r.finalized = 0
      ```
    - This ensures finalized rounds are not re-surfaced as phase 4 entries.

24. **Update `test_mode_cog.advance`** (phase 4 path):
    - After the existing `is_submission_open` guard, add:
      ```python
      # Guard: block advance if round has results but is not yet finalized
      has_results = round_id in rounds_with_results
      if has_results and not await is_round_finalized(bot.db_path, entry["round_id"]):
          await interaction.followup.send(
              f"⏸️ **{entry['division_name']}** — Round {entry['round_number']} "
              "is in Post-Round Penalties state and must be finalized before advancing. "
              "Complete the penalty review in the submission channel.",
              ephemeral=True,
          )
          return
      ```

**Acceptance**: In test mode, `advance` is blocked with the correct message when penalty state is pending; unblocked after finalization.

---

### Phase 8 — Remove `round results penalize` Command

**Goal**: Deregister the old standalone penalize command.

**Tasks**:

25. **Remove `round_results_penalize`** method and its `results_group` sub-command registration from `season_cog.py` (lines ~1348–~1600).
    - Remove the `@round_results_group.command(name="penalize", ...)` decorator block.
    - Remove the `_run_wizard` nested function and all associated View classes (`_SessionView`, `_UserIdView`, `_ReviewView`) that were local to that command.

**Acceptance**: Bot starts without error; `/round results penalize` does not appear in the slash command registry.

---

### Phase 9 — Tests

**Goal**: Full test coverage for the new flow.

**Tasks**:

26. **`tests/unit/test_penalty_service.py`** — extend existing file:
    - `test_validate_negative_time_penalty` — `-3s` → `StagedPenalty(penalty_seconds=-3)`
    - `test_validate_zero_penalty_rejected` — `0` → error string
    - `test_apply_negative_penalty_reorders` — driver with lower adjusted time moves up
    - `test_dsq_fastest_lap_not_redistributed` — DSQ driver had fastest lap; no other driver gains bonus
    - `test_time_clamp_at_zero` — very large negative penalty clamps to 0 ms

27. **`tests/unit/test_result_submission_service.py`** — extend:
    - `test_submission_channel_not_closed_after_final_session` — after last session, `close_submission_channel` not called
    - `test_penalty_state_entered_after_final_session` — penalty prompt message sent

28. **`tests/integration/test_penalty_flow.py`** — new file:
    - `test_full_flow_no_penalties` — submit round, approve empty list, round finalized, interim posts deleted
    - `test_full_flow_with_time_penalty` — submit round, stage +5s on P1 driver, approve, verify P1 driver drops position
    - `test_full_flow_with_negative_penalty` — stage -3s on last-place driver, approve, verify position improvement
    - `test_full_flow_with_dsq` — stage DSQ, approve, verify driver at bottom, 0 points
    - `test_test_mode_advance_blocked_before_finalize` — advance blocked; approve; advance succeeds
    - `test_restart_recovery` — simulate restart with open submission channel in penalty state; prompt re-posted; approve works

**Acceptance**: All new and existing tests pass; coverage for `penalty_service.py` ≥ existing baseline.

---

## Cross-Cutting Concerns

### Audit Log Entries

Two new `change_type` values in `audit_entries`:

| `change_type` | When written | `new_value` JSON shape |
|---|---|---|
| `ROUND_FINALIZED` | On finalization approval | `{"round_id": N, "penalties": [...], "actor_id": N}` |
| `PENALTY_APPLIED` | Per `apply_penalties` call (already exists; keep as-is) | existing schema |

### Message Deletion Safety

`delete_and_repost_final_results` catches `discord.NotFound` and `discord.Forbidden` on message deletion (message manually deleted by an admin) and logs a warning without failing the finalization.

### Empty Round (All Sessions CANCELLED)

If all sessions are CANCELLED, `session_types_present` is empty. `PenaltyReviewView` disables the Add Penalty button; only No Penalties / Confirm is available. Approval with empty list proceeds normally and sets `finalized = 1`.

---

## Complexity Tracking

No constitution violations. No complexity tracking required.
