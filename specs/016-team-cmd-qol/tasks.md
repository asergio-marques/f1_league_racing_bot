# Tasks: Team Command QoL Simplification

**Feature**: `016-team-cmd-qol`
**Plan**: [plan.md](plan.md) | **Spec**: [spec.md](spec.md)
**Status**: Ready to implement
**Last updated**: 2026-03-11

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: User story label — US1 `/team add`, US2 `/team remove`, US3 `/team rename`, US4 `/team list`
- Exact file paths are given for every task

---

## Phase 2: Foundational — Service Layer Additions (Plan Phase A)

**Purpose**: Add the four new service methods that every cog command depends on. All additions are backwards-compatible — no schema migration required.

**⚠️ CRITICAL**: No Phase 3–6 cog work can begin until T001–T006 are complete.

- [X] T001 Add `delete_team_role_config` async method (SELECT + DELETE + audit `TEAM_ROLE_CONFIG`) after the `set_team_role_config` block in `src/services/placement_service.py`
- [X] T002 [P] Add `get_teams_with_roles` async read method (LEFT JOIN `default_teams` × `team_role_configs`, ordered non-reserve A→Z then Reserve last) to `src/services/team_service.py` (FR-010, FR-011)
- [X] T003 Add `rename_team_role_config` async method (SELECT + UPDATE + audit `TEAM_ROLE_CONFIG`, silent no-op when absent) after `delete_team_role_config` in `src/services/placement_service.py` (FR-007)
- [X] T004 [P] Add `get_setup_season_team_names` async read method (returns `set[str]` of distinct non-reserve team names across all divisions of a given season) after `get_teams_with_roles` in `src/services/team_service.py` (FR-011)
- [X] T005 Create `tests/unit/test_placement_service_team_role.py` with 4 unit tests: `test_delete_team_role_config_existing` (row deleted + audit written), `test_delete_team_role_config_not_found` (no error, no audit), `test_rename_team_role_config_existing` (team_name updated + audit written), `test_rename_team_role_config_not_found` (no error, no audit) — use in-memory aiosqlite + migrations fixture matching existing unit test pattern
- [X] T006 [P] Create `tests/unit/test_team_service.py` with 6 unit tests: `test_get_teams_with_roles_no_roles`, `test_get_teams_with_roles_some_roles`, `test_get_teams_with_roles_empty`, `test_get_setup_season_team_names_basic`, `test_get_setup_season_team_names_excludes_reserve`, `test_get_setup_season_team_names_empty` — use in-memory aiosqlite + migrations fixture

**Checkpoint**: `pytest tests/unit/test_placement_service_team_role.py tests/unit/test_team_service.py` — all 10 tests green.

---

## Phase 3: User Story 1 — Add a Team to the Server (Priority: P1) 🎯 MVP

**Goal**: Admin runs `/team add name:"Red Bull" role:@RedBull` to create a team in the server list. If a SETUP season is active, the team is automatically inserted into every division.

**Independent Test**: Run `/team add name:"Alpine" role:@Alpine` with no active season — team appears in server list with the role. Run again with the same name — error returned. No other commands needed.

### Implementation

- [X] T007 [US1] Rewrite `src/cogs/team_cog.py`: remove `default_group`, `role_group`, `season_group`, and `_ConfirmView`; keep the `team = app_commands.Group(...)` definition; implement `/team add name: str role: discord.Role` (role required) with `@channel_guard @admin_only` and all response variants (role/no season · role+SETUP season showing div count · duplicate-name ⛔ error) (FR-001, FR-002, FR-003, FR-012)

### Tests

- [X] T008 [P] [US1] Create `tests/unit/test_team_cog.py` with 3 `/team add` cog tests using `AsyncMock` for `bot.team_service`, `bot.placement_service`, `bot.season_service`: role+no-season (`set_team_role_config` called, role mention in response), role+SETUP-season (`season_team_add` called, div count in response), duplicate-name (`add_default_team` raises `ValueError`, error response, no further service calls)

**Checkpoint**: `/team add` operational; `default_group`, `role_group`, `season_group`, `_ConfirmView` no longer present in `team_cog.py`.

---

## Phase 4: User Story 2 — Remove a Team from the Server (Priority: P1)

**Goal**: Admin runs `/team remove name:"Red Bull"` to delete a team and its role mapping. If a SETUP season is active, the team is removed from every division. No confirm/cancel prompt — direct response only (Plan Decision 5).

**Independent Test**: Add a team with `/team add`, then run `/team remove` with that name — team is gone, role mapping dropped. Run `/team remove` with an unknown name — not-found error returned.

### Implementation

- [X] T009 [US2] Add `/team remove name: str` command to `src/cogs/team_cog.py` after `/team add` with `@channel_guard @admin_only`; logic: get SETUP season first, then `remove_default_team` (raises on not-found), then `delete_team_role_config` (silent no-op), then `season_team_remove` if season present; response variants: no season · season+team present (div count) · season+team absent (div_count==0 note) · not-found ⛔ error (FR-004, FR-005, FR-006, FR-012)

### Tests

- [X] T010 [P] [US2] Add 4 `/team remove` cog tests to `tests/unit/test_team_cog.py`: no season (`season_team_remove` NOT called), SETUP season + team present (div count in response), SETUP season + team absent (`div_count=0`, "Not present" note), not-found error (`delete_team_role_config` NOT called)

**Checkpoint**: `/team remove` responds without any confirm/cancel UI; `season_remove` subcommand no longer exists.

---

## Phase 5: User Story 3 — Rename a Team in the Server (Priority: P2)

**Goal**: Admin runs `/team rename current_name:"Red Bull" new_name:"Oracle Red Bull"` to update a team's name in the server list, its role mapping key, and every occurrence in a SETUP-season's divisions.

**Independent Test**: Add a team, rename it — old name gone, new name present, role mapping preserved. Try renaming to an already-taken name — conflict error returned.

### Implementation

- [X] T011 [US3] Add `/team rename current_name: str new_name: str` command to `src/cogs/team_cog.py` after `/team remove` with `@channel_guard @admin_only`; logic: get SETUP season, then `rename_default_team` (raises on not-found or new name conflict), then `rename_team_role_config` (silent no-op if absent), then `season_team_rename` if season present; response variants: no season · with SETUP season (div count) · current name not found ⛔ · new name already taken ⛔ (FR-007, FR-008, FR-009, FR-012)

### Tests

- [X] T012 [P] [US3] Add 4 `/team rename` cog tests to `tests/unit/test_team_cog.py`: no season (`season_team_rename` NOT called), SETUP season (all three service calls made, div count in response), current name not found (error response, `rename_team_role_config` NOT called), new name conflict (error response from `rename_default_team`)

**Checkpoint**: `/team rename` propagates across server list + SETUP season in one command; `/team default rename` and `/team season rename` no longer exist.

---

## Phase 6: User Story 4 — List all Teams with their Roles (Priority: P2)

**Goal**: Admin runs `/team list` to see all server teams with their mapped roles. When a SETUP season is active and its team set diverges from the server list, both lists plus a discrepancy warning are shown.

**Independent Test**: Add two teams with roles, run `/team list` — both names and roles displayed. Run with no teams — empty-state message returned.

### Implementation

- [X] T013 [US4] Add `/team list` command (no parameters) to `src/cogs/team_cog.py` after `/team rename` with `@channel_guard @admin_only`; logic: `get_teams_with_roles` → empty-state guard → `get_setup_season` → build server-list string (`{name} → <@&{role_id}>` or `no role`) → if no season: respond with server list only → if season: `get_setup_season_team_names` → compare sets → if equal: unified header `Server team list (Season N will use this list):` → if divergent: two-section response with ⚠️ warning; split response if >2000 chars (FR-010, FR-011, FR-012)

### Tests

- [X] T014 [P] [US4] Add 4 `/team list` cog tests to `tests/unit/test_team_cog.py`: no teams (empty-state message), teams+no season (server list only), SETUP season+sets match (unified header, no ⚠️), SETUP season+sets diverge (two-section output with ⚠️ warning)

**Checkpoint**: All four `/team` commands are present and working; all three old subcommand groups (`/team default *`, `/team role *`, `/team season *`) have been removed (FR-013, SC-001).

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, documentation, and compliance check.

- [X] T015 Run full unit test suite `pytest tests/unit/` and confirm no regressions; fix any breakage introduced by the cog rewrite
- [X] T016 [P] Update the **Team Commands** section in `README.md` (lines ~361–425): remove the nine old subcommand entries (`/team default add`, `/team default rename`, `/team default remove`, `/team role set`, `/team role list`, `/team season add`, `/team season rename`, `/team season remove`); add four new entries (`/team add`, `/team remove`, `/team rename`, `/team list`) with parameter tables and descriptions matching the implemented behaviour (SC-001, FR-013)
- [X] T017 Verify FR-012 compliance: confirm all four commands in `src/cogs/team_cog.py` carry both `@channel_guard` and `@admin_only` decorators; confirm no command accidentally responds to a non-interaction-channel invocation

---

## Dependencies

```
T001 ──► T003 (same file: placement_service.py)
T002 ──► T004 (same file: team_service.py)

T001 + T003 ──► T005  (test placement service methods)
T002 + T004 ──► T006  (test team service methods)

T001 + T002 + T003 + T004                    ──► T007 (cog can import new methods)
T007 (cog skeleton + /team add complete)      ──► T009 (/team remove added to cog)
T009 (/team remove complete)                  ──► T011 (/team rename added to cog)
T011 (/team rename complete)                  ──► T013 (/team list added to cog)

T008 (test file created)  ──► T010 ──► T012 ──► T014  (sequential, same test file)

T013 ──► T015 (full test run after all commands implemented)
T015 ──► T016 (documentation)
T015 ──► T017 (compliance check)
```

**Parallel opportunities**:
- T001 (placement_service) and T002 (team_service) and T006 (test_team_service) can all start simultaneously
- T005 and T006 are parallel (different files)
- README update (T016) is fully independent of all implementation tasks and can be written at any time

---

## Implementation Strategy

**MVP scope** (P1 stories): Complete Phases 2–4 (T001–T010) — delivers `/team add` and `/team remove` plus their service layer and unit tests. Covers the two most-used admin workflows.

**Full feature**: Add Phases 5–7 (T011–T017) for `/team rename`, `/team list`, documentation, and final verification.

**No migration needed**: Existing `default_teams` and `team_role_configs` rows are immediately compatible — zero data-prep steps before deploying.

---

## Task Summary

| Phase | Tasks | Covers |
|-------|-------|--------|
| Phase 2 — Foundational | T001–T006 | 4 service methods + 10 unit tests |
| Phase 3 — US1 `/team add` | T007–T008 | FR-001, FR-002, FR-003, FR-012 |
| Phase 4 — US2 `/team remove` | T009–T010 | FR-004, FR-005, FR-006, FR-012 |
| Phase 5 — US3 `/team rename` | T011–T012 | FR-007, FR-008, FR-009, FR-012 |
| Phase 6 — US4 `/team list` | T013–T014 | FR-010, FR-011, FR-012 |
| Phase 7 — Polish | T015–T017 | Full test run, README, FR-013 compliance |
| **Total** | **17 tasks** | All 13 FRs covered |
