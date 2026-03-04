# Implementation Plan: Round-Add Duplicate Guard & Round-Amend During Setup

**Branch**: `004-round-add-amend` | **Date**: 2025-03-04 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-round-add-amend/spec.md`

## Summary

Two targeted improvements to the season-setup flow:

1. **`/round-amend` on pending configs** — today `/round-amend` only works against an approved (ACTIVE) season in the database. Extending it to also target the not-yet-approved in-memory `PendingConfig` lets any `@admin_only` server admin correct a round's track, datetime, or format before the season is committed. After the in-memory update the full pending config is immediately snapshotted to the DB (status=SETUP) for crash safety; no phase-invalidation runs.

2. **Duplicate round-number guard for `/round-add`** — today the bot silently appends a second round with the same number into the same division. Instead, on detecting a conflict the bot presents an ephemeral Discord button prompt offering four resolutions: **Insert Before** (shift existing ≥N up by 1), **Insert After** (shift existing >N up by 1), **Replace** (swap out the conflicting round), or **Cancel** (no change). The prompt times out after 60 seconds with no modification.

## Technical Context

**Language/Version**: Python 3.13.2 (targets 3.8+)
**Primary Dependencies**: discord.py 2.7.1 (`discord.ui.View`, `discord.ui.Button`), aiosqlite ≥ 0.19, APScheduler ≥ 3.10
**Storage**: SQLite via aiosqlite; no schema changes required — the `SETUP` season status already exists. After every pending-config mutation the full config is snapshotted to the DB as a `SETUP`-status season and restored into memory on startup.
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
│   ├── season_cog.py        # /round-add: duplicate guard + date ordering + DuplicateRoundView;
│   │                        # PendingConfig.season_id; _snapshot_pending(); recover_pending_setups();
│   │                        # _do_approve(): load from DB, schedule-before-transition
│   └── amendment_cog.py     # /round-amend: pending-config lookup path + DB snapshot (US1)
├── services/
│   ├── season_service.py    # has_active_or_completed_season(); save_pending_snapshot();
│   │                        # load_all_setup_seasons()
│   └── scheduler_service.py # _GLOBAL_SERVICE sentinel + _phase_job module-level callable
│                            #   (fixes APScheduler SQLAlchemyJobStore pickle error)
└── bot.py                   # _recover_pending_setups() + on_ready call

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

No database schema changes — the `SETUP` status value already exists in the `seasons` table. After every pending-config mutation the full config is written to the DB as a `SETUP`-status season via `save_pending_snapshot()`, and restored into memory on startup via `load_all_setup_seasons()`. `PendingConfig` gains a `season_id: int = 0` field populated after the first snapshot.

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

No external API/command-schema contracts change. The DB-facing interface (`/season-approve`) is updated internally: it loads divisions and rounds from the already-persisted SETUP season (via `cfg.season_id`), schedules all rounds with APScheduler first, then calls `transition_to_active`. A scheduling failure leaves the season in SETUP status (FR-016).

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
  │           no phase-invalidation
  │           save_pending_snapshot() → update pending_cfg.season_id   (FR-002, FR-014)
  │           respond ephemeral ✅
  └─ No → existing active-season DB path (unchanged)
```

### `/round-add` duplicate-guard flow

```
SeasonCog.round_add()
  ├─ cfg lookup: _pending.get(user_id) or _get_pending_for_server(guild_id)
  ├─ [existing validation: format, track, datetime, division lookup]
  ├─ date ordering check (FR-015):
  │     earlier_rounds = [r for r in div.rounds if r["round_number"] < round_number]
  │     later_rounds   = [r for r in div.rounds if r["round_number"] > round_number]
  │     if sched < max(earlier scheduled_at) → error naming offending round
  │     if sched > min(later  scheduled_at)  → error naming offending round
  ├─ conflict = next((r for r in div.rounds if r["round_number"] == round_number), None)
  ├─ conflict is None?
  │     Yes → append; _snapshot_pending(cfg); respond ✅
  └─ No  → build DuplicateRoundView(div, new_round_data, post_mutation_cb=_snapshot_cb)
            await interaction.response.send_message(embed, view=view, ephemeral=True)
            view.message = await interaction.original_response()
            (view handles all mutations asynchronously via button callbacks)

DuplicateRoundView callbacks:
  insert_before_cb → apply insert_before(); post_mutation_cb(); disable buttons; edit message ✅
  insert_after_cb  → apply insert_after();  post_mutation_cb(); disable buttons; edit message ✅
  replace_cb       → apply replace();       post_mutation_cb(); disable buttons; edit message ✅
  cancel_cb        → no change;             disable buttons; edit message ❌ cancelled
  on_timeout       → no change;             disable buttons; edit message ⏱ timed out

SeasonCog._do_approve()  (FR-016)
  ├─ cfg lookup: _pending.get(user_id) or _get_pending_for_server(guild_id)
  ├─ guard: cfg.season_id == 0 → error (setup incomplete)
  ├─ load divisions + rounds from DB using cfg.season_id
  ├─ create sessions for each round
  ├─ schedule_all_rounds(all_rounds)     ← FIRST (failure leaves season in SETUP)
  ├─ transition_to_active(cfg.season_id) ← only after scheduling succeeds
  └─ clear all _pending entries for this server; respond ✅
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
| V — Observability & Change Audit Trail | ✅ PASS | Pending-config mutations now write to DB immediately (SETUP status) providing a crash-recovery record; final audit trail still written at `transition_to_active`. |
| VI — Simplicity & Focused Scope | ✅ PASS | `DuplicateRoundView` is self-contained; persistence reuses existing SETUP season infrastructure. No new commands added. |
| VII — Output Channel Discipline | ✅ PASS | All responses ephemeral; no new channel output. |
