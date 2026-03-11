# Tasks: Driver Placement and Team Role Configuration

**Feature**: `015-driver-placement` | **Branch**: `015-driver-placement`  
**Input**: `specs/015-driver-placement/` — spec.md, plan.md, research.md, data-model.md, contracts/slash-commands.md, quickstart.md  
**Total tasks**: 19 | **Phases**: 8

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no incomplete dependencies)
- **[US#]**: Which user story this task belongs to
- All paths are repository-root relative

---

## Phase 1: Setup

**Purpose**: Create the SQL migration file — the single new file required before any code changes can be tested.

- [X] T001 Create `src/db/migrations/011_driver_placement.sql` per data-model.md: ALTER signup_records ADD total_lap_ms INTEGER; ALTER driver_season_assignments ADD team_seat_id INTEGER REFERENCES team_seats(id); CREATE TABLE IF NOT EXISTS team_role_configs(id, server_id, team_name, role_id, updated_at) UNIQUE(server_id, team_name)

**Checkpoint**: Running the bot now applies migration 011 automatically on startup; verify with `sqlite3 bot.db ".tables"`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Model updates and the PlacementService foundation. All user story phases depend on these.

**⚠️ CRITICAL**: No user story work begins until this phase is complete.

- [X] T002 [P] Add `total_lap_ms: int | None = None` field to `SignupRecord` dataclass in `src/models/signup_module.py`
- [X] T003 [P] Add `team_seat_id: int | None` field to `DriverSeasonAssignment` dataclass in `src/models/driver_profile.py`
- [X] T004 [P] Add `TeamRoleConfig` dataclass (id, server_id, team_name, role_id, updated_at) to `src/models/team.py`
- [X] T005 Create `src/services/placement_service.py` with DB helper layer: `get_team_role_config(server_id, team_name)`, `set_team_role_config(server_id, team_name, role_id)`, `get_all_team_role_configs(server_id)`, and Discord role helpers `_grant_roles(member, *role_ids)` / `_revoke_roles(member, *role_ids)` (fail-soft: catch `discord.HTTPException` and log)

**Checkpoint**: All model imports resolve; `placement_service.py` is importable with no errors; migration 011 verified applied

---

## Phase 3: User Story 1 — Configure Team–Role Associations (Priority: P1) 🎯 MVP

**Goal**: A server admin can map any team to a Discord role via `/team role set`. The mapping persists across seasons and is blocked while any season is ACTIVE.

**Independent Test**: Run `/team role set Ferrari @FerrariRole` with no active season → verify `team_role_configs` row written. Run again with a different role → verify overwrite. Set an active season and retry → expect blocked error.

- [X] T006 [US1] Add `role_group` sub-group (`/team role`) to `TeamCog` in `src/cogs/team_cog.py` with `role set` command: accept `team` (str, autocomplete) and `role` (discord.Role); check no ACTIVE season (query `seasons` table); call `PlacementService.set_team_role_config`; read prior mapping for overwrite vs. new success message; write audit log entry; all responses ephemeral

**Checkpoint**: `/team role set <team> <role>` registers, persists, and is blocked when season is ACTIVE

---

## Phase 4: User Story 2 — Seeded Unassigned Driver List (Priority: P2)

**Goal**: A tier-2 admin can view all Unassigned drivers ordered by ascending `total_lap_ms` (timed first, NULLs last; tiebreaker: earliest approval timestamp).

**Independent Test**: Approve three signups with different lap totals; run `/signup unassigned`; verify output order matches ascending `total_lap_ms`. Approve one with no tracks; verify it appears after all timed drivers. Invoke with no unassigned drivers → verify empty-state message.

- [X] T007 [P] [US2] Compute and store `total_lap_ms` at signup approval in `src/services/wizard_service.py`: in the approval code path (transition to Unassigned), parse `signup_records.lap_times_json`, convert each time string to milliseconds, sum across all tracks, write to `signup_records.total_lap_ms` (NULL if no times); same DB transaction as the state change
- [X] T008 [P] [US2] Add `get_unassigned_drivers_seeded(server_id) -> list[dict]` to `src/services/placement_service.py`: JOIN `driver_profiles`, `signup_records`; WHERE `current_state = 'Unassigned'` AND `server_id = ?`; ORDER BY `total_lap_ms ASC NULLS LAST`, `signup_approved_at ASC`; return all fields required by the listing format in contracts/slash-commands.md
- [X] T009 [US2] Add `/signup unassigned` subcommand to `SignupCog` in `src/cogs/signup_cog.py`: check signup module enabled; tier-2 gate; defer ephemeral; call `PlacementService.get_unassigned_drivers_seeded`; format each entry per contract (`#N — display_name (uid) / Platform / Availability / Type / Preferred Teams / Teammate Pref / Total Lap Time / Notes`); paginate at 10 entries; return empty-state message if list empty

**Checkpoint**: `/signup unassigned` returns drivers in correct seed order; NULL-time drivers appear last; ephemeral to invoker only

---

## Phase 5: User Story 3 — Assign Driver to Division–Team Seat (Priority: P3)

**Goal**: A tier-2 admin places an Unassigned or Assigned driver into a team seat within a division; division role and configured team role are granted.

**Independent Test**: Assign an Unassigned driver to Ferrari in Division 1 with a configured Ferrari role → driver state is Assigned; seat occupied; division role and Ferrari role granted. Attempt duplicate division assignment → blocked. Attempt on full team → blocked.

- [X] T010 [US3] Add `assign_driver(server_id, driver_profile_id, division_id, team_name, season_id, acting_user_id)` to `src/services/placement_service.py`: (1) validate driver state is Unassigned or Assigned; (2) check no existing `driver_season_assignments` row for (driver, season, division); (3) find available `team_seats` for non-Reserve teams; (4) atomically: SET `team_seats.driver_profile_id`, INSERT `driver_season_assignments` with `team_seat_id`, transition driver Unassigned→Assigned if needed; (5) fetch `divisions.mention_role_id` and `team_role_configs.role_id`; call `_grant_roles`; (6) write audit log entry; return summary dict
- [X] T011 [US3] Add `/driver assign` subcommand to `DriverCog` in `src/cogs/driver_cog.py`: accept `user` (discord.Member), `division` (str, autocomplete: tier or name), `team` (str, autocomplete); defer ephemeral; resolve division by tier int or name string; call `PlacementService.assign_driver`; format success/error response per contracts/slash-commands.md

**Checkpoint**: Full assign cycle works end-to-end from Discord; roles appear on member; seat is marked occupied in DB

---

## Phase 6: User Story 4 — Unassign Driver from a Division (Priority: P4)

**Goal**: A tier-2 admin removes a driver's assignment from one division; the division role is revoked and the team role is conditionally revoked (only if no other seat maps to that role).

**Independent Test**: Assign driver to Division 1 Ferrari only; unassign from Division 1 → driver returns to Unassigned; Division 1 role revoked; Ferrari role revoked; seat freed. Assign to two divisions; unassign from one → driver remains Assigned; only that division's roles revoked.

- [X] T012 [US4] Add `unassign_driver(server_id, driver_profile_id, division_id, season_id, acting_user_id)` to `src/services/placement_service.py`: (1) validate driver state is Assigned; (2) find `driver_season_assignments` row for (driver, season, division); (3) atomically: CLEAR `team_seats.driver_profile_id`, DELETE assignment row; (4) revoke `divisions.mention_role_id`; (5) check remaining assignments for same team role — revoke `team_role_configs.role_id` only if driver holds no other seat mapped to that role; (6) if no assignments remain, transition driver to Unassigned; (7) write audit log entry
- [X] T013 [US4] Add `/driver unassign` subcommand to `DriverCog` in `src/cogs/driver_cog.py`: accept `user` (discord.Member), `division` (str, autocomplete); defer ephemeral; resolve division; call `PlacementService.unassign_driver`; format success/error response per contracts/slash-commands.md

**Checkpoint**: Unassign cycle clears seat, revokes the correct roles, and returns driver to Unassigned when no assignments remain

---

## Phase 7: User Story 5 — Sack Driver (Priority: P5)

**Goal**: A tier-2 admin removes a driver from the league entirely — clearing all assignments, revoking all roles, and transitioning to Not Signed Up — with a confirm/cancel prompt before execution.

**Independent Test**: Assign driver to two divisions; sack → all seats freed; all division and team roles revoked; driver state is Not Signed Up; audit log entry present. Sack an `former_driver = true` driver → profile retained, SignupRecord fields nulled. Sack `former_driver = false` → profile deleted.

- [X] T014 [US5] Add `revoke_all_placement_roles(server_id, driver_profile_id, season_id, member)` reusable function to `src/services/placement_service.py`: query all `driver_season_assignments` for (driver, season); build set of division role IDs from `divisions.mention_role_id`; build set of team role IDs from `team_role_configs` for each assigned team; call `_revoke_roles(member, *all_role_ids)` (FR-029 reusable contract)
- [X] T015 [US5] Add `sack_driver(server_id, driver_profile_id, season_id, acting_user_id)` to `src/services/placement_service.py`: (1) validate driver state Unassigned or Assigned; (2) collect all assignment rows; (3) call `revoke_all_placement_roles`; (4) atomically: CLEAR all occupied `team_seats`, DELETE all `driver_season_assignments` rows; (5) apply Not Signed Up transition: if `former_driver = true` retain profile and NULL SignupRecord fields; if `former_driver = false` DELETE profile atomically; (6) write audit log entry
- [X] T016 [US5] Add `/driver sack` subcommand to `DriverCog` in `src/cogs/driver_cog.py`: accept `user` (discord.Member); defer ephemeral; validate state; build list of current division assignments; send ephemeral confirm/cancel `discord.ui.View` ("⚠️ Sack {display_name}? This will remove them from: {list}..."); on confirm call `PlacementService.sack_driver`; format success/error per contracts/slash-commands.md

**Checkpoint**: Full sack cycle works end-to-end; confirm prompt appears; all roles stripped in one command; both `former_driver` paths verified

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Audit completeness check, interaction pattern review, and smoke test validation.

- [X] T017 Audit log review — scan `src/services/placement_service.py` and confirm every mutation method (`set_team_role_config`, `assign_driver`, `unassign_driver`, `sack_driver`) writes an audit entry covering actor, entity, previous value, new value (Principle V / FR-004, FR-020, FR-025, FR-031)
- [X] T018 Interaction deferral review — confirm `await interaction.response.defer(ephemeral=True)` is used before any DB call in all 5 new command handlers across `src/cogs/driver_cog.py`, `src/cogs/signup_cog.py`, `src/cogs/team_cog.py`; confirm follow-ups use `interaction.followup.send(...)`
- [X] T019 Smoke test — start bot locally, confirm migration 011 applies cleanly, sync command tree, and manually exercise all 5 commands per `specs/015-driver-placement/quickstart.md` test scenarios

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — **BLOCKS all user story phases**
- **Phase 3 (US1)**: Depends on Phase 2 — no dependency on US2–US5
- **Phase 4 (US2)**: Depends on Phase 2 — no dependency on US1, US3–US5
- **Phase 5 (US3)**: Depends on Phase 2 — no dependency on US1/US2 (but US1 must be done to test role grants end-to-end)
- **Phase 6 (US4)**: Depends on Phase 2 — no dependency on US1/US2; US3 needed for manual testing
- **Phase 7 (US5)**: Depends on Phase 2 + T014 before T015 (revoke_all before sack_driver); US3 needed for manual testing
- **Phase 8 (Polish)**: Depends on all user story phases complete

### User Story Dependencies

| Story | Depends On | Can Test Independently |
|-------|-----------|----------------------|
| US1 (P1) | Phase 2 | ✅ Yes — no other story required |
| US2 (P2) | Phase 2 | ✅ Yes — read-only; requires approved signups in DB |
| US3 (P3) | Phase 2 | ✅ Yes — US1 needed only to verify team role grant |
| US4 (P4) | Phase 2 | ✅ Yes — US3 needed to create assignments to unassign |
| US5 (P5) | Phase 2 + T014 | ✅ Yes — US3 needed to create assignments to sack |

### Within Each User Story

- Service method (`T010`, `T012`, `T015`) before cog command (`T011`, `T013`, `T016`)
- T014 (`revoke_all_placement_roles`) before T015 (`sack_driver`)
- T007 and T008 in Phase 4 are independent (different files) — can run in parallel

---

## Parallel Opportunities

### Phase 2 — Foundational

```
# All three model updates can run simultaneously (different files):
T002  Add total_lap_ms to SignupRecord in src/models/signup_module.py
T003  Add team_seat_id to DriverSeasonAssignment in src/models/driver_profile.py
T004  Add TeamRoleConfig dataclass to src/models/team.py

# Then (depends on T004 for TeamRoleConfig import):
T005  Create src/services/placement_service.py scaffold
```

### Phase 4 — User Story 2

```
# Wizard service change and DB query can run simultaneously:
T007  Add total_lap_ms computation to src/services/wizard_service.py
T008  Add get_unassigned_drivers_seeded to src/services/placement_service.py

# Then (depends on T008):
T009  Add /signup unassigned to src/cogs/signup_cog.py
```

---

## Implementation Strategy

### MVP (User Story 1 Only — Team–Role Config)

1. Complete Phase 1 (migration)
2. Complete Phase 2 (models + service scaffold)
3. Complete Phase 3 (US1 — `/team role set`)
4. **STOP and VALIDATE**: team role mapping persists; blocked on ACTIVE season
5. Proceed to US2 when US1 is verified

### Incremental Delivery

1. Phase 1 + Phase 2 → Foundation ready
2. Phase 3 (US1) → Team roles configurable — demo/validate
3. Phase 4 (US2) → Seeded listing visible — demo/validate
4. Phase 5 (US3) → Drivers assignable with role grants — demo/validate
5. Phase 6 (US4) → Drivers unassignable — demo/validate
6. Phase 7 (US5) → Full sack flow — demo/validate
7. Phase 8 → Polish and smoke test

Each phase adds a complete, independently testable capability without breaking the previous phase.
