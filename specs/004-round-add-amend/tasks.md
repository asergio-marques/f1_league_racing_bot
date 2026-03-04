# Tasks: 004 — Round-Add Duplicate Guard & Round-Amend During Setup

**Branch**: `004-round-add-amend`
**Created**: 2026-03-04
**Status**: Complete
**Source**: [plan.md](plan.md) · [spec.md](spec.md)

---

## Execution Rules

- Phases must be completed in order.
- Tasks marked **[P]** within a phase may be executed in parallel with other **[P]** tasks in the same phase, provided they touch different files.
- Tasks that touch the same file must be executed sequentially.
- Mark each task **[x]** as soon as it is done.

---

## Phase 1 — Foundational

**Purpose**: Shared helper required by US1 before `amendment_cog.py` can call into `season_cog.py`.

### T001 — Add `_get_pending_for_server()` helper to `SeasonCog`

- [x] T001 Add `_get_pending_for_server(server_id: int) -> PendingConfig | None` to `src/cogs/season_cog.py`
- **Action**: Add a method to `SeasonCog` that scans `self._pending.values()` and returns the first `PendingConfig` whose `server_id` matches the argument, or `None` if no match. Pattern is identical to the existing `clear_pending_for_server()` scan on line 344.
- **Why first**: `AmendmentCog.round_amend` (T002) must call this helper; it cannot be written until the method exists.

---

## Phase 2 — User Story 1: `/round-amend` on Pending Configs (Priority: P1)

**Goal**: Any `@admin_only` server admin can correct a round's track, datetime, or format in a pending (not-yet-approved) season config before it is committed to the database.

**Independent Test**: Run `/season-setup` → `/division-add` → `/round-add` to create a pending config. Without running `/season-approve`, invoke `/round-amend` with a corrected track. Confirm the change is reflected in `/season-review` and persists into the approved season.

### T002 — Update `round_amend` with pending-config path

- [x] T002 [US1] Update `round_amend()` in `src/cogs/amendment_cog.py` to check pending config before DB
- **Action**: At the top of `round_amend()` (before the existing active-season DB lookup), add:
  1. `season_cog = self.bot.get_cog("SeasonCog")`
  2. `pending_cfg = season_cog._get_pending_for_server(interaction.guild_id) if season_cog else None`
  3. If `pending_cfg is not None`, execute the pending amendment path:
     - Find `div` in `pending_cfg.divisions` where `div.name == division` → error ephemeral if not found.
     - Find `round_dict` in `div.rounds` where `round_dict["round_number"] == round_number` → error ephemeral if not found.
     - Apply only the fields the caller supplied: update `track_name` if `track` was provided; update `scheduled_at` if `scheduled_at` was provided; update `format` if `format` was provided.
     - If the new format is `MYSTERY`: set `round_dict["track_name"] = None`.
     - If the new format is non-MYSTERY and no `track` was supplied and `round_dict["track_name"]` is `None` or empty: reject with a descriptive error ephemeral.
     - **Do NOT** call phase-invalidation logic (no `invalidate_phases_for_round`). **Do NOT** write to the database.
     - Respond with an ephemeral success confirmation.
     - Return early — do not fall through to the DB path.
  4. If `pending_cfg is None`: continue to the existing active-season DB path unchanged.
- **Depends on**: T001

---

## Phase 3 — User Story 2: Duplicate Round-Number Guard in `/round-add` (Priority: P1)

**Goal**: When `/round-add` detects a conflicting `round_number` in the target division, present an interactive ephemeral 4-button prompt. The admin chooses Insert Before, Insert After, Replace, or Cancel. A 60-second timeout leaves the round list unchanged.

**Independent Test**: With a division containing round 3, call `/round-add round_number=3`. Verify the 4-button prompt appears. Run four separate invocations selecting each option and verify the resulting round list after each choice:
- Insert Before: rounds renumbered so new round is 3, old round 3 is now 4.
- Insert After: old round 3 stays at 3, new round added as round 4.
- Replace: only round 3 exists with the new data.
- Cancel: round list is identical to before the command.

### T003 — Add module-level mutation helpers

- [x] T003 [P] [US2] Add `_rounds_insert_before`, `_rounds_insert_after`, `_rounds_replace` to `src/cogs/season_cog.py`
- **Action**: Add three module-level functions (not methods — pure, no I/O) immediately before the `SeasonCog` class definition:

  ```python
  def _rounds_insert_before(rounds: list[dict], conflict_num: int, new_round: dict) -> None:
      """Shift all rounds with round_number >= conflict_num up by 1, then insert new_round at conflict_num."""
      for r in rounds:
          if r["round_number"] >= conflict_num:
              r["round_number"] += 1
      rounds.append(new_round)
      rounds.sort(key=lambda r: r["round_number"])

  def _rounds_insert_after(rounds: list[dict], conflict_num: int, new_round: dict) -> None:
      """Shift all rounds with round_number > conflict_num up by 1, then insert new_round at conflict_num + 1."""
      for r in rounds:
          if r["round_number"] > conflict_num:
              r["round_number"] += 1
      new_round = {**new_round, "round_number": conflict_num + 1}
      rounds.append(new_round)
      rounds.sort(key=lambda r: r["round_number"])

  def _rounds_replace(rounds: list[dict], conflict_num: int, new_round: dict) -> None:
      """Remove the existing round at conflict_num and insert new_round in its place."""
      for i, r in enumerate(rounds):
          if r["round_number"] == conflict_num:
              rounds[i] = new_round
              return
  ```

- **Depends on**: T001 (same file edit sequence)

### T004 — Add `DuplicateRoundView` class

- [x] T004 [US2] Add `DuplicateRoundView(discord.ui.View)` class to `src/cogs/season_cog.py`
- **Action**: Add the class after the mutation helpers (before `SeasonCog`). It receives `div: PendingDivision` and `new_round: dict` in `__init__`. Store `self.message: discord.Message | None = None` for editing on timeout. Implement:
  - `@discord.ui.button(label="Insert Before", style=discord.ButtonStyle.primary)` → call `_rounds_insert_before(div.rounds, conflict_num, new_round)`, disable all buttons, edit message ✅.
  - `@discord.ui.button(label="Insert After", style=discord.ButtonStyle.secondary)` → call `_rounds_insert_after(...)`, disable all buttons, edit message ✅.
  - `@discord.ui.button(label="Replace", style=discord.ButtonStyle.danger)` → call `_rounds_replace(...)`, disable all buttons, edit message ✅.
  - `@discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)` → no mutation, disable all buttons, edit message ❌ cancelled.
  - `async def on_timeout(self)` → no mutation, disable all buttons, edit `self.message` with timeout message.
  - Helper `_disable_all(self)`: iterates `self.children` setting `item.disabled = True`.
  - `conflict_num` = `new_round["round_number"]` (store on self in `__init__`).
- **Depends on**: T003

### T005 — Add duplicate guard to `round_add()`

- [x] T005 [US2] Update `round_add()` in `src/cogs/season_cog.py` to detect conflict and present `DuplicateRoundView`
- **Action**: After all existing validation (format, track, datetime, division lookup) and before the final `div.rounds.append(new_round)`, insert:
  ```python
  conflict = next((r for r in div.rounds if r["round_number"] == round_number), None)
  if conflict is not None:
      view = DuplicateRoundView(div, new_round_dict)
      await interaction.response.send_message(
          embed=...,  # describe the conflict: existing round fields vs new round fields
          view=view,
          ephemeral=True,
      )
      view.message = await interaction.original_response()
      return
  ```
  The no-conflict path is unchanged — append and respond as before.
- **Depends on**: T004

---

## Phase 4 — Tests

**Note**: Both test files are independent; create them in parallel.

### T006 — Unit tests: pending-config amendment (US1)

- [x] T006 [P] [US1] Create `tests/unit/test_amendment_cog_pending.py`
- **Action**: Create new file. Use `AsyncMock` / `MagicMock` for `interaction`, `SeasonCog`, and `SeasonService`. Cover:
  1. Happy path — track change: pending config found, div found, round found; track updated in-memory, ephemeral success returned, no DB call, no phase-invalidation call.
  2. Happy path — `scheduled_at` change: same as (1) but updating the date field.
  3. Format → MYSTERY: update format to MYSTERY, verify `track_name` cleared to `None`, no error.
  4. Format ← MYSTERY, no track supplied, stored track is empty: verify rejection error returned.
  5. Format ← MYSTERY, no track supplied, stored track already has a value: verify amendment succeeds and existing track is preserved.
  6. Division not found in pending config: verify descriptive error returned.
  7. Round not found in pending config (div exists, round missing): verify descriptive error returned.
  8. No pending config, no active season in DB: verify "no season" error returned (existing behaviour covered by existing tests — a smoke-test pass is sufficient).
  9. Active season in DB (no pending config): verify the DB path is called and returns its existing response unchanged.
- **Depends on**: T002

### T007 — Unit tests: duplicate round guard (US2)

- [x] T007 [P] [US2] Create `tests/unit/test_season_cog_duplicate.py`
- **Action**: Create new file. Use isolated `PendingDivision` objects (no Discord mocks needed for pure helper tests). Cover:
  - **Mutation helpers** (no mocks required):
    1. `_rounds_insert_before`: rounds `[1,2,3,4]`, conflict=3 → result `[1,2,3,4,5]` with new round as 3.
    2. `_rounds_insert_after`: rounds `[1,2,3,4]`, conflict=3 → result `[1,2,3,4,5]` with new round as 4.
    3. `_rounds_replace`: rounds `[1,2,3,4]`, conflict=3 → result `[1,2,3,4]`, round 3 replaced.
    4. Cascading shift: `_rounds_insert_before` with consecutive rounds `[3,4,5]`, conflict=3 → all three incremented.
  - **`DuplicateRoundView` integration** (with `AsyncMock` for interaction):
    5. Insert Before button: `div.rounds` updated, buttons disabled, success message edited.
    6. Insert After button: `div.rounds` updated, buttons disabled, success message edited.
    7. Replace button: `div.rounds` updated, buttons disabled, success message edited.
    8. Cancel button: `div.rounds` unchanged, buttons disabled, cancel message edited.
    9. `on_timeout`: `div.rounds` unchanged, buttons disabled, timeout message edited on `view.message`.
  - **`round_add` integration**:
    10. No conflict → no prompt, immediate success (ensures existing path not broken by T005).
    11. Conflict detected → `DuplicateRoundView` sent as ephemeral, `return` before append.
- **Depends on**: T003, T004, T005

---

## Phase 5 — Validation

### T008 — Run full test suite

- [x] T008 Run `python -m pytest tests/ -v` — all tests must pass (baseline: 69 + new tests)
- **Depends on**: T001–T007

---

## Dependency Graph

```
T001
├── T002 [US1]  (amendment_cog.py — can run in parallel with T003)
└── T003 [US2]  (season_cog.py — sequential with T001 same file)
    └── T004 [US2]  (season_cog.py)
        └── T005 [US2]  (season_cog.py)
            ├── T006 [P] [US1]  (new file)
            └── T007 [P] [US2]  (new file)
                └── T008
```

**Parallel opportunities**:
- T002 and T003 touch different files → run in parallel after T001.
- T006 and T007 create different new files → run in parallel after T005.

---

## Summary

| Phase | Tasks | User Story | Status |
|-------|-------|-----------|--------|
| 1 — Foundational | T001 | — | ☐ |
| 2 — US1 pending-amend | T002 | US1 (P1) | ☐ |
| 3 — US2 duplicate guard | T003, T004, T005 | US2 (P1) | ☐ |
| 4 — Tests | T006, T007 | US1 + US2 | ☐ |
| 5 — Validation | T008 | — | ☐ |

**Total tasks**: 8
**Parallelisable**: T002‖T003 (phase 3), T006‖T007 (phase 4)
**MVP scope**: T001 + T002 (US1 fully deliverable before US2 work begins)
