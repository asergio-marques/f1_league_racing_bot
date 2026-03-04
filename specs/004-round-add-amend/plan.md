# Implementation Plan: Round-Add Duplicate Guard & Round-Amend During Setup

**Branch**: `004-round-add-amend` | **Date**: 2025-03-04 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-round-add-amend/spec.md`

## Summary

Two targeted improvements to the season-setup flow:

1. **`/round-amend` on pending configs** — today `/round-amend` only works against an approved (ACTIVE) season in the database. Extending it to also target the not-yet-approved in-memory `PendingConfig` lets any `@admin_only` server admin correct a round's track, datetime, or format before the season is committed. No DB write occurs; no phase-invalidation runs.

2. **Duplicate round-number guard for `/round-add`** — today the bot silently appends a second round with the same number into the same division. Instead, on detecting a conflict the bot presents an ephemeral Discord button prompt offering four resolutions: **Insert Before** (shift existing ≥N up by 1), **Insert After** (shift existing >N up by 1), **Replace** (swap out the conflicting round), or **Cancel** (no change). The prompt times out after 60 seconds with no modification.

## Technical Context

**Language/Version**: Python 3.13.2 (targets 3.8+)
**Primary Dependencies**: discord.py 2.7.1 (`discord.ui.View`, `discord.ui.Button`), aiosqlite ≥ 0.19, APScheduler ≥ 3.10
**Storage**: SQLite via aiosqlite; no schema changes (both user stories operate on in-memory `PendingConfig` only)
**Testing**: pytest 9.0.2 + pytest-asyncio (`asyncio_mode = auto`)
**Target Platform**: Windows/Linux server running Python 3.8+
**Project Type**: Discord bot (slash commands)
**Performance Goals**: Command acknowledgement within 3 seconds (Discord hard limit)
**Constraints**: Discord interaction timeout 3 s for initial response; button-interaction timeout handled via `discord.ui.View(timeout=60)`
**Scale/Scope**: Single-server bot; changes affect the pending season configuration flow only

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I — Trusted Configuration Authority | ✅ PASS | Both commands remain behind `@admin_only` + `@channel_guard`. No access-tier changes. |
| II — Multi-Division Isolation | ✅ PASS | Duplicate-guard and pending amendment operate on a single division at a time; no cross-division reads or writes. |
| III — Resilient Schedule Management | ✅ PASS | Pending-config amendments are pre-approval corrections; no scheduled phase data exists yet for these rounds, so no invalidation is needed. |
| IV — Three-Phase Weather Pipeline | ✅ PASS | Phases are not computed during setup; no changes to phase services or scheduling horizons. |
| V — Observability & Change Audit Trail | ✅ PASS | Pending-config amendments are ephemeral (in-memory); the audit trail is written at approval time when data reaches the DB. No separate logging change needed. |
| VI — Simplicity & Focused Scope | ✅ PASS | No new commands; both changes improve correctness and UX in the existing setup flow. |
| VII — Output Channel Discipline | ✅ PASS | All new responses are ephemeral. No new channel writes. |

**Post-Phase 1 re-check**: No violations identified in design or implementation.

## Project Structure

### Documentation (this feature)

```text
specs/004-round-add-amend/
├── plan.md              ← this file
├── research.md          ← Phase 0
├── data-model.md        ← Phase 1
└── tasks.md             ← Phase 2 (/speckit.tasks)
```

### Source Code changes

```text
src/
├── cogs/
│   ├── season_cog.py        # /round-add: duplicate-round guard + DuplicateRoundView
│   └── amendment_cog.py     # /round-amend: pending-config lookup path (US1)

tests/
└── unit/
    ├── test_season_cog_duplicate.py   # 4 branches: Insert Before / Insert After / Replace / Cancel + timeout
    └── test_amendment_cog_pending.py  # pending-amend path: happy path, field validation, not-found errors
```

**Structure Decision**: Single-project layout (`src/` + `tests/`), consistent with features 001–003.

## Complexity Tracking

> No Constitution violations — table omitted.

---

## Phase 0: Research

### R-001 — discord.ui.View + Button interaction pattern

**Question**: What is the correct discord.py pattern for a 4-button ephemeral response with a 60-second timeout that mutates in-memory state and then edits the original message?

**Findings**:
- `discord.ui.View(timeout=60)` accepts an `on_timeout` coroutine; setting `view.message` after `send_message` allows editing on expiry.
- `interaction.response.send_message(view=view, ephemeral=True)` sends the prompt; `await interaction.response.defer()` inside each button callback followed by `await interaction.edit_original_response(...)` handles the post-mutation update.
- The `View` must store references to the mutable round list and the new-round data so button callbacks can apply the chosen resolution without re-parsing the interaction.
- Buttons must be disabled after any resolution (including timeout) to prevent double-submission.

**Decision**: Implement `DuplicateRoundView(discord.ui.View)` inside `season_cog.py` with four `discord.ui.Button` callbacks (`insert_before_cb`, `insert_after_cb`, `replace_cb`, `cancel_cb`) plus `on_timeout`.

**Alternatives considered**: Modal with a text field (poor UX — requires typing a choice); select menu (acceptable but buttons are more scannable for 4 fixed options).

---

### R-002 — Pending-config lookup by guild_id for `/round-amend`

**Question**: `_pending` is keyed by `user_id`. How do we find the pending config for a server when the requesting admin may not be the one who started the setup?

**Findings**:
- The existing `clear_pending_for_server(server_id)` helper in `SeasonCog` already iterates `self._pending.values()` scanning for `cfg.server_id == server_id`.
- The same scan pattern can be factored into a helper `_get_pending_for_server(server_id) -> PendingConfig | None` that returns the first matching config.
- Since only one pending config per server is allowed (enforced by an existing guard), first-match is unambiguous.

**Decision**: Add `SeasonCog._get_pending_for_server(server_id)` and call it from `AmendmentCog.round_amend` before the existing active-season DB lookup.

---

## Phase 1: Design & Contracts

### Data model

No database schema changes. Both user stories operate entirely on the in-memory `PendingConfig.divisions[*].rounds` list.

**Round dict schema** (unchanged):

```python
{
    "round_number": int,
    "format":       RoundFormat,
    "track_name":   str | None,
    "scheduled_at": datetime,
}
```

**Mutation helpers** (pure functions, no I/O):

```python
def insert_before(rounds: list[dict], conflict_num: int, new_round: dict) -> list[dict]:
    """Increment round_number of all rounds >= conflict_num, then insert new_round."""

def insert_after(rounds: list[dict], conflict_num: int, new_round: dict) -> list[dict]:
    """Increment round_number of all rounds > conflict_num, then insert new_round at conflict_num + 1."""

def replace(rounds: list[dict], conflict_num: int, new_round: dict) -> list[dict]:
    """Remove round at conflict_num and insert new_round in its place."""
```

These helpers live in `season_cog.py` (module-level functions).

### Contracts

No external contracts — both user stories affect only in-memory state. The DB-facing interface (`/season-approve`) is unchanged.

### `/round-amend` pending-path flow

```
AmendmentCog.round_amend()
  ├─ season_cog = bot.get_cog("SeasonCog")
  ├─ pending_cfg = season_cog._get_pending_for_server(guild_id) if season_cog else None
  ├─ pending_cfg is not None?
  │     Yes → pending amendment path:
  │           find div by name in pending_cfg.divisions       → error if not found
  │           find round dict by round_number in div.rounds   → error if not found
  │           validate + apply field changes in-memory
  │           if format → MYSTERY: clear track_name
  │           if format ← MYSTERY with no track provided and existing track empty: reject
  │           no phase-invalidation; no DB write
  │           respond ephemeral ✅
  └─ No → existing active-season DB path (unchanged)
```

### `/round-add` duplicate-guard flow

```
SeasonCog.round_add()
  ├─ [existing validation: format, track, datetime, division lookup]
  ├─ conflict = next((r for r in div.rounds if r["round_number"] == round_number), None)
  ├─ conflict is None?
  │     Yes → append and respond ✅  (unchanged path)
  └─ No  → build DuplicateRoundView(div, new_round_data)
            await interaction.response.send_message(embed, view=view, ephemeral=True)
            view.message = await interaction.original_response()
            (view handles all mutations asynchronously via button callbacks)

DuplicateRoundView callbacks:
  insert_before_cb → apply insert_before(); disable buttons; edit message ✅
  insert_after_cb  → apply insert_after();  disable buttons; edit message ✅
  replace_cb       → apply replace();       disable buttons; edit message ✅
  cancel_cb        → no change;             disable buttons; edit message ❌ cancelled
  on_timeout       → no change;             disable buttons; edit message ⏱ timed out
```

### Agent context update

```
.specify/memory/copilot-context.md  ← updated with discord.ui.View pattern (R-001)
```

## Constitution Check (post-design)

| Principle | Status | Notes |
|-----------|--------|-------|
| I — Trusted Configuration Authority | ✅ PASS | `_get_pending_for_server` is called only from within the `@admin_only` path. |
| II — Multi-Division Isolation | ✅ PASS | All mutations scoped to a single division object; no cross-division reads. |
| III — Resilient Schedule Management | ✅ PASS | Pending amendments are corrections before any schedule is committed; no invalidation needed. |
| IV — Three-Phase Weather Pipeline | ✅ PASS | No phase services involved. |
| V — Observability & Change Audit Trail | ✅ PASS | Audit trail written at `/season-approve` time, unchanged. |
| VI — Simplicity & Focused Scope | ✅ PASS | `DuplicateRoundView` is self-contained; no new commands added. |
| VII — Output Channel Discipline | ✅ PASS | All responses ephemeral; no new channel output. |
