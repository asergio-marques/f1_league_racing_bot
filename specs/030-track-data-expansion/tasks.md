# Tasks: Track Data Expansion (030)

**Branch**: `030-track-data-expansion`  
**Input**: [spec.md](spec.md) · [plan.md](plan.md) · [research.md](research.md) · [data-model.md](data-model.md)

> **Tests**: Existing test files are rewritten or amended where the old code they tested is
> deleted. No new test-first (TDD) tasks are added; the spec does not request a TDD approach.

---

## Phase 1: Setup

*Skipped — project structure already exists. No initialisation tasks required.*

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the storage layer that every user story depends on. Nothing in Phase 3+
can be implemented correctly until the migration SQL is written, the `Track` dataclass exists,
and the new `track_service` functions are defined.

**⚠️ CRITICAL**: All user story work is blocked until T001–T003 are complete.

- [X] T001 Create `src/db/migrations/029_track_data_expansion.sql`: `CREATE TABLE IF NOT EXISTS tracks (id INTEGER PRIMARY KEY NOT NULL, name TEXT NOT NULL UNIQUE, gp_name TEXT NOT NULL, location TEXT NOT NULL, country TEXT NOT NULL, mu REAL NOT NULL, sigma REAL NOT NULL)`; `CREATE TABLE IF NOT EXISTS track_records` and `lap_records` (schema per data-model.md, each with `UNIQUE (track_id, tier, session_type)`, FK `track_id REFERENCES tracks(id)`); `DROP TABLE IF EXISTS track_rpc_params`; `INSERT OR IGNORE INTO tracks` for all 28 circuits (values from spec seed table); `UPDATE rounds SET track_name = '<canonical>'` for all 27 old-short-name → canonical-circuit-name mappings (full list in research.md §2)
- [X] T002 [P] Replace `src/models/track.py`: delete `TRACK_IDS`, `TRACK_DEFAULTS`, `get_default_rpc_params()`, `get_effective_rpc_params()`; add `Track` dataclass with fields `id: int`, `name: str`, `gp_name: str`, `location: str`, `country: str`, `mu: float`, `sigma: float`; update module docstring to reflect DB-backed registry
- [X] T003 [P] Replace `src/services/track_service.py`: delete `get_track_override`, `set_track_override`, `reset_track_override`; add `async def get_all_tracks(db) -> list` that executes `SELECT id, name, gp_name, location, country, mu, sigma FROM tracks ORDER BY id` and returns `fetchall()`; add `async def get_track_by_name(db, name: str)` that executes `SELECT ... FROM tracks WHERE name = ?` and returns `fetchone()`

**Checkpoint**: Migration SQL is written; `Track` dataclass exists; `get_all_tracks`/`get_track_by_name` are testable. User story work may now begin.

---

## Phase 3: User Story 1 — Richer Track Registry & Weather Continuity (Priority: P1) 🎯 MVP

**Goal**: Phase 1 weather generation resolves `(mu, sigma)` from the `tracks` DB table. All 28
circuits appear in round-add/amend autocomplete with canonical names. Old `/track config`,
`/track reset`, `/track info` commands are gone. Existing unit and integration tests are
updated to cover the new service API.

**Independent Test**: Run `python -m pytest tests/ -v` after completing this phase. Confirm
`test_track_service.py` passes; confirm the migration smoke test in `test_database.py`
verifies 28 rows in `tracks` and absence of `track_rpc_params`.

- [X] T004 [US1] Amend `src/services/phase1_service.py`: remove `from models.track import get_effective_rpc_params` import; delete the `track_rpc_params` override-lookup block and the `get_effective_rpc_params(...)` try/except (~8 lines); replace with `SELECT mu, sigma FROM tracks WHERE name = ?` on `track_name`; if `fetchone()` returns `None`, log an error with the message "no track row found — check that migration 029 has run" and `return` (do not proceed with Phase 1)
- [X] T005 [P] [US1] Amend `src/cogs/season_cog.py` round track handling: remove `from models.track import TRACK_IDS, TRACK_DEFAULTS` import; in `round_add`, replace the 4-line `TRACK_IDS.get / TRACK_DEFAULTS` validation block with an `async with get_connection(self.bot.db_path)` query — try `SELECT name FROM tracks WHERE id = ?` (when input is numeric) then `SELECT name FROM tracks WHERE name = ?`; resolve to the returned `name` or return an unknown-track error; replace `round_add_track_autocomplete` to query `await track_service.get_all_tracks(db)` and build `Choice(name=f"{r['id']:02d} – {r['name']}", value=r['name'])` items; apply identical changes to `round_amend` track validation (both pending-config path and active-season path) and `round_amend_track_autocomplete`
- [X] T006 [P] [US1] Amend `src/cogs/track_cog.py`: remove `from models.track import TRACK_IDS` import; remove imports of `get_track_override`, `set_track_override`, `reset_track_override` from `track_service`; delete the `_autocomplete_track` method; delete the `track_config`, `track_reset`, and `track_info` command methods and any associated `@*.autocomplete` decorators; retain the `TrackCog` class and `track = app_commands.Group(...)` declaration
- [X] T007 [P] [US1] Rewrite `tests/unit/test_track_service.py`: remove all tests for the deleted CRUD functions; add `test_get_all_tracks_returns_rows` (seed 2 rows into an in-memory SQLite DB, assert `get_all_tracks` returns them); `test_get_all_tracks_ordered_by_id` (assert rows are sorted by `id`); `test_get_track_by_name_found` (assert correct `dict` returned for known `name`); `test_get_track_by_name_not_found` (assert `None` returned for unknown name)
- [X] T008 [US1] Amend `tests/integration/test_database.py`: add (or amend existing migration smoke test) to verify after migration 029 runs — `tracks` table has exactly 28 rows; `track_rpc_params` table does not exist; `track_records` and `lap_records` tables exist

**Checkpoint**: Weather continuity is restored. All 28 tracks resolve correctly via DB. Old three `/track` commands are absent from the cog. Tests pass.

---

## Phase 4: User Story 2 — Division Tier Enforcement & Amendment (Priority: P2)

**Goal**: `/division amend` command is available to tier-2 admins to correct division name,
tier, or role during season setup. FR-012 (mandatory tier param on `/division add`) and
FR-013 (approval gate for gapless 1-indexed tiers) are already fully implemented and require
no code changes — verify in a smoke test or code review only.

**Independent Test**: Start a season setup with two divisions: `/division add name:Alpha role:@A tier:1` and `/division add name:Beta role:@B tier:2`. Run `/division amend name:Beta tier:3`. Attempt `/season approve` — expect blocked: tier 2 missing. Run `/division amend name:Beta tier:2`. Verify approval proceeds past the tier gate.

- [X] T009 [US2] Add `division_amend` command to `src/cogs/season_cog.py` in the `division` command group: `@division.command(name="amend", description="Amend a division's name, tier, or role during season setup.")` with `@app_commands.describe(name=..., new_name=..., tier=..., role=...)`, `@channel_guard`, `@admin_only`; signature `(name: str, new_name: str | None = None, tier: int | None = None, role: discord.Role | None = None)`; guard: if all three optional params are `None` return error "provide at least one of: `new_name`, `tier`, `role`"; guard: `season_id = await _get_setup_season_id(self.bot, interaction.guild_id)` — if `None` return error "only permitted during season setup"; locate division by `name`; if not found return error; if `new_name` conflicts with another division's name return duplicate-name error; build `old_value` JSON and apply changes with targeted `UPDATE divisions SET ... WHERE id = ?`; write `audit_entries` row (`change_type='DIVISION_AMENDED'`, `old_value`, `new_value` as JSON, UTC timestamp, `actor_id`, `actor_name`, `division_id`); reload pending config via `await self._reload_pending_from_db(cfg)` if `cfg` is in-memory; send ephemeral success response with updated division list via `format_division_list`; call `self.bot.output_router.post_log`

**Checkpoint**: `/division amend` is fully functional with audit trail. FR-012/FR-013 verified as pre-existing.

---

## Phase 5: User Story 3 — Track List Command (Priority: P3)

**Goal**: Tier-2 admins can run `/track list` to see all 28 circuits sorted by numeric ID,
each row showing ID, circuit name, and grand prix name. Response is ephemeral.

**Independent Test**: Run `/track list` as a tier-2 admin — expect a single ephemeral message
showing 28 entries in `ID | Circuit Name | Grand Prix Name` format sorted by numeric ID. Run
as a non-admin user — expect permission error.

- [X] T010 [US3] Add `track_list` command to `src/cogs/track_cog.py` in the `track` command group: `@track.command(name="list", description="List all available tracks.")` with `@channel_guard` and `@admin_only`; `await interaction.response.defer(ephemeral=True)`; `async with get_connection(self.bot.db_path) as db: rows = await track_service.get_all_tracks(db)`; format as a code block with one line per row: `f"{r['id']:02d} | {r['name']} | {r['gp_name']}"`; `await interaction.followup.send(f"**Tracks ({len(rows)})**\n```\n{table}\n```", ephemeral=True)`; add required imports: `from db.database import get_connection` and `import services.track_service as track_service`

**Checkpoint**: All three user stories complete. Full feature is operational.

---

## Phase N: Polish & Cross-Cutting Concerns

- [X] T011 Run full test suite `python -m pytest tests/ -v` from repo root and confirm all tests pass with no regressions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 2)**: No dependencies — can start immediately
- **US1 (Phase 3)**: Depends on T001 + T002 + T003 — BLOCKED until all three complete
- **US2 (Phase 4)**: Independent of Track work — can start any time (uses existing `divisions` and `audit_entries` tables)
- **US3 (Phase 5)**: Depends on T003 (`get_all_tracks`) and T006 (track group cleaned up in same file)
- **Polish (Phase N)**: Depends on all phases complete

### User Story Dependencies

| Story | Depends On | Can Start After |
|-------|-----------|-----------------|
| US1 | T001, T002, T003 | Phase 2 complete |
| US2 | Nothing new | Can start any time |
| US3 | T003, T006 | T003 + T006 complete |

### Within US1 — Parallel Opportunities

Once T001–T003 are complete, T004, T005, T006, T007 may all start simultaneously (they touch
different files: `phase1_service.py`, `season_cog.py`, `track_cog.py`, and the test file).
T008 (integration test) depends only on T001.

```
T001 ──┬──> T002 [P] ──┬──> T004 [US1]  (phase1_service.py)
       │               ├──> T005 [US1]  (season_cog.py rounds)
       │   T003 [P] ──┘├──> T006 [US1]  (track_cog.py cleanup)
       │               ├──> T007 [US1]  (test_track_service.py)
       │               └──> T010 [US3]  (track_cog.py /track list)
       └──────────────────> T008 [US1]  (test_database.py)

T009 [US2]  (season_cog.py /division amend)  — independent, start any time
```

### MVP Scope

**Minimum viable delivery = US1 only (T001–T008)**. This restores weather generation from the
DB, updates autocomplete, removes retired commands, and keeps the test suite green — all
without the two new commands. US2 and US3 can follow independently.
