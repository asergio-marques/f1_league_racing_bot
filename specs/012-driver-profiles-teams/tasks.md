# Tasks: Driver Profiles, Teams & Season Enhancements

**Feature Branch**: `012-driver-profiles-teams`  
**Input**: `specs/012-driver-profiles-teams/` — plan.md, spec.md, data-model.md, contracts/commands.md, research.md, quickstart.md  
**Generated**: 2026-03-06

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on in-flight tasks)
- **[Story]**: Which user story this task belongs to (US1–US6)
- All file paths are relative to the repository root

---

## Phase 1: Setup (New Modules & Migration)

**Purpose**: Create all new source files as stubs and the migration SQL. These must exist before
any implementation work begins.

- [X] T001 Create `src/db/migrations/008_driver_profiles_teams.sql` with all DDL: CREATE TABLE for `driver_profiles`, `driver_season_assignments`, `driver_history_entries`, `default_teams`, `team_instances`, `team_seats`; and ALTER TABLE for `server_configs` (+`previous_season_number`), `seasons` (+`season_number`), `divisions` (+`tier`)
- [X] T002 [P] Create `src/models/driver_profile.py` containing the `DriverState` string-enum (all 8 states) and `DriverProfile`, `DriverSeasonAssignment`, `DriverHistoryEntry` dataclasses matching data-model.md
- [X] T003 [P] Create `src/models/team.py` containing `DefaultTeam`, `TeamInstance`, `TeamSeat` dataclasses matching data-model.md
- [X] T004 [P] Create `src/cogs/driver_cog.py` as a stub: `DriverCog(commands.Cog)` class with a single `driver = app_commands.Group(name="driver", …)` attribute; no commands yet
- [X] T005 [P] Create `src/cogs/team_cog.py` as a stub: `TeamCog(commands.Cog)` class with a single `team = app_commands.Group(name="team", …)` attribute; no commands yet
- [X] T006 [P] Create `src/services/driver_service.py` as a stub: `DriverService` class with `__init__(self, db)` and empty method signatures for all methods referenced in this task list
- [X] T007 [P] Create `src/services/team_service.py` as a stub: `TeamService` class with `__init__(self, db)` and empty method signatures for all methods referenced in this task list
- [X] T008 Register `DriverCog` and `TeamCog` in `src/bot.py` alongside the existing cog registrations; instantiate with appropriate service dependencies

**Checkpoint**: All new files exist; `python -c "from src.cogs.driver_cog import DriverCog"` succeeds with no import errors.

---

## Phase 2: Foundational (Existing Model Updates)

**Purpose**: Extend the three existing model dataclasses with new fields. These changes are needed
by US6 service logic and by integration tests that load DB rows into models.

**⚠️ CRITICAL**: Must complete before US6 service work begins.

- [X] T009 [P] Add `tier: int = 0` field to the `Division` dataclass in `src/models/division.py` (append after existing fields; default 0 grandfathers pre-feature rows)
- [X] T010 [P] Add `season_number: int = 0` field to the `Season` dataclass in `src/models/season.py` (append after existing fields)
- [X] T011 [P] Add `previous_season_number: int = 0` field to the `ServerConfig` dataclass in `src/models/server_config.py` (append after existing fields)

**Checkpoint**: Existing unit tests still pass; no `TypeError` on model construction.

---

## Phase 3: User Story 1 — Driver Profile State Machine Foundation (Priority: P1) 🎯 MVP

**Goal**: A correct, fully-tested service layer for driver profile CRUD and all state transitions,
including auto-deletion of non-former-driver profiles on return to *Not Signed Up* and test-mode
bypass for direct state advancement.

**Independent Test**: With test mode enabled, use existing `/test-mode advance` flow (or direct
service calls) to exercise every allowed and disallowed transition listed in the spec; assert
correct resulting state, assert profile row deleted when `former_driver=False` and target is
`NOT_SIGNED_UP`, assert profile retained when `former_driver=True`.

- [X] T012 [US1] Implement `DriverService` DB helpers in `src/services/driver_service.py`: `get_profile(server_id, discord_user_id) -> DriverProfile | None` (SELECT), `_create_profile(server_id, discord_user_id, initial_state) -> DriverProfile` (INSERT), `_update_state(profile_id, new_state)` (UPDATE), `_clear_seat_references(profile_id)` (UPDATE team_seats SET driver_profile_id = NULL), `_delete_profile(profile_id)` (DELETE)
- [X] T013 [US1] Implement `ALLOWED_TRANSITIONS` constant dict and `DriverService.transition(server_id, discord_user_id, new_state, *, test_mode_active=False) -> DriverProfile` in `src/services/driver_service.py`: validate transition is in ALLOWED_TRANSITIONS[current_state], raise `ValueError` with descriptive message on invalid transition; if target is `NOT_SIGNED_UP` and `former_driver=False`, call `_clear_seat_references` then `_delete_profile`; otherwise persist the new state
- [X] T014 [US1] Add test-mode extended transitions in `DriverService.transition()` in `src/services/driver_service.py`: when `test_mode_active=True`, also permit `NOT_SIGNED_UP → UNASSIGNED` and `NOT_SIGNED_UP → ASSIGNED` (creating the profile row at the initial state if it does not exist); keep all standard transitions available regardless of test_mode

**Checkpoint**: User Story 1 is fully functional. All 16 acceptance scenarios from spec.md US1 can be exercised via direct DriverService calls. Profile row observed absent/present as expected.

---

## Phase 4: User Story 2 — Driver User ID Reassignment (Priority: P2)

**Goal**: Admin command to re-key an existing driver profile from one Discord User ID to another,
fully audited, with full profile data preserved.

**Independent Test**: Admin issues `/driver reassign old_user new_user`; look up profile by old
User ID → not found; look up by new User ID → full profile intact; audit log contains
`DRIVER_USER_ID_REASSIGN` entry.

- [X] T015 [US2] Implement `DriverService.reassign_user_id(server_id, old_user_id: str, new_user_id: str) -> DriverProfile` in `src/services/driver_service.py`: verify profile exists for `old_user_id` (else raise), verify no profile exists for `new_user_id` (else raise), UPDATE `driver_profiles.discord_user_id`, INSERT into `audit_entries` with `change_type="DRIVER_USER_ID_REASSIGN"`, `old_value=old_user_id`, `new_value=new_user_id`
- [X] T016 [US2] Implement `/driver reassign` subcommand in `src/cogs/driver_cog.py`: `old_user` param accepts `discord.Member` or raw snowflake string (handle both), `new_user` param is `discord.Member`; decorate with `@admin_only` and `@channel_guard`; call `DriverService.reassign_user_id`; return ephemeral success response with old/new User ID, current state, and former_driver flag; map service errors to ephemeral `⛔` messages per contracts/commands.md

**Checkpoint**: User Story 2 fully functional. Reassignment succeeds; old ID lookup returns nothing; new ID lookup returns intact profile; audit entry confirmed.

---

## Phase 5: User Story 3 — Test-Mode Former Driver Flag Override (Priority: P3)

**Goal**: Admin command (test-mode gated) to manually flip the `former_driver` flag on any driver
profile, enabling local testing of auto-deletion vs. retention logic.

**Independent Test**: Test mode on → set flag true → transition driver to NOT_SIGNED_UP → profile
retained. Set flag false → transition to NOT_SIGNED_UP → profile deleted. Attempt command with
test mode off → rejected.

- [X] T017 [US3] Implement `DriverService.set_former_driver(server_id, discord_user_id: str, value: bool) -> tuple[bool, bool]` in `src/services/driver_service.py` returning `(old_value, new_value)`: look up profile (raise if not found), UPDATE `driver_profiles.former_driver`, INSERT into `audit_entries` with `change_type="TEST_FORMER_DRIVER_FLAG_SET"`, `old_value=str(old)`, `new_value=str(value)`
- [X] T018 [US3] Implement `/test-mode set-former-driver` as a new subcommand of the existing `/test-mode` group in `src/cogs/test_mode_cog.py`: `user: discord.Member` and `value: bool` params; gate on `test_mode_active` (ephemeral error if off); decorate with `@admin_only`; call `DriverService.set_former_driver`; return ephemeral response showing old and new flag value per contracts/commands.md

**Checkpoint**: User Story 3 fully functional. Flag toggling confirmed; retention/deletion behavior verified; command rejected when test mode off.

---

## Phase 6: User Story 4 — Default Team Configuration Management (Priority: P4)

**Goal**: Admin commands to add, rename, and remove server-level default teams. Reserve is always
protected. New divisions seed their team instances from this list.

**Independent Test**: Add "Custom Team" to defaults; create a division; confirm "Custom Team"
instance present. Remove "Custom Team" from defaults; create another division; confirm absent.
Attempt to modify Reserve → rejected.

- [X] T019 [US4] Implement `TeamService` default-team DB helpers in `src/services/team_service.py`: `get_default_teams(server_id) -> list[DefaultTeam]`, `add_default_team(server_id, name, max_seats) -> DefaultTeam` (check name uniqueness, not Reserve name), `rename_default_team(server_id, current_name, new_name)` (check is_reserve guard, check new_name uniqueness), `remove_default_team(server_id, name)` (check is_reserve guard); all mutating methods reject Reserve rows
- [X] T020 [US4] Implement `TeamService.seed_division_teams(division_id: int, server_id: int)` in `src/services/team_service.py`: SELECT all `default_teams` for server; for each non-Reserve row INSERT a `team_instances` row with `max_seats` from default, then INSERT 2 `team_seats` rows (seat_number 1 and 2, `driver_profile_id=NULL`); for the Reserve row INSERT a `team_instances` row with `max_seats=-1` (no seats pre-created)
- [X] T021 [US4] Add default-team seeding to the `/bot-init` command handler in `src/cogs/init_cog.py`: after existing server-config creation, if no `default_teams` rows exist for this server, INSERT the 10 standard F1 constructor team names (with `max_seats=2`) plus the Reserve team (with `max_seats=-1`, `is_reserve=1`)
- [X] T022 [US4] Implement `/team default add`, `/team default rename`, `/team default remove` subcommand group in `src/cogs/team_cog.py`: each action decorated with `@admin_only` and `@channel_guard`; `remove` shows a confirm/cancel prompt before executing; all responses ephemeral; map TeamService errors to `⛔` messages per contracts/commands.md

**Checkpoint**: User Story 4 fully functional. Default CRUD confirmed; Reserve protection confirmed; new division seeding confirmed via quickstart.md step 4.

---

## Phase 7: User Story 5 — Season Team Configuration Management (Priority: P5)

**Goal**: Admin commands to add, rename, and remove a named team across all divisions of the
current SETUP-phase season simultaneously. Atomically applied; Reserve always protected.

**Independent Test**: Season in SETUP with 3 divisions → add "Extra Team" → all 3 divisions gain
it. Attempt same while ACTIVE → rejected. Remove Reserve → rejected.

- [X] T023 [US5] Implement `TeamService.season_team_add(server_id, season_id, name, max_seats)`, `season_team_rename(server_id, season_id, current_name, new_name)`, `season_team_remove(server_id, season_id, name)` in `src/services/team_service.py`: each method first asserts the season is in SETUP status (raise if not); changes applied to ALL `team_instances` rows for divisions belonging to that season in a single transaction; Reserve rows excluded; on `remove`, also DELETE associated `team_seats` rows
- [X] T024 [US5] Implement `/team season add`, `/team season rename`, `/team season remove` subcommand group in `src/cogs/team_cog.py`: each action decorated with `@admin_only` and `@channel_guard`; `remove` shows a confirm/cancel prompt; all responses ephemeral with division count in success message; map TeamService errors (non-SETUP, Reserve, name conflict) to `⛔` messages per contracts/commands.md

**Checkpoint**: User Story 5 fully functional. Season-level team mutations reflected in all divisions; ACTIVE-season rejection confirmed; Reserve protection confirmed.

---

## Phase 8: User Story 6 — Season Counter, Division Tier & Roster Review (Priority: P6)

**Goal**: Seasons carry a sequential display number. Divisions require a tier on creation;
approval is gated on gapless sequential tiers. Season review output includes team rosters.

**Independent Test**: Fresh server → create season → displays "Season 1". Cancel → create new
season → displays "Season 2". Create two divisions with tiers 1 and 3 → approve → blocked with
missing-tier diagnostic. Fix to 1 and 2 → approve succeeds. Review shows team rosters per
division.

- [X] T025 [US6] Update `SeasonService.create_season()` in `src/services/season_service.py` to read `server_config.previous_season_number`, set `season.season_number = previous_season_number + 1` at INSERT time, and include `season_number` in the returned `Season` object
- [X] T026 [US6] Update `SeasonService.cancel_season()` in `src/services/season_service.py` and `SeasonEndService` completion path in `src/services/season_end_service.py` to each increment `server_configs.previous_season_number` by 1 after persisting the terminal status (CANCELLED or COMPLETED)
- [X] T027 [US6] Add required `tier: int` parameter to `SeasonService.add_division()` and `SeasonService.duplicate_division()` in `src/services/season_service.py`: validate `tier >= 1`; validate no existing division in the season already has this tier (raise with descriptive `ValueError`); persist `tier` to the `divisions` row; call `TeamService.seed_division_teams(new_division_id, server_id)` after successful INSERT
- [X] T028 [US6] Update `SeasonService.approve_season()` in `src/services/season_service.py` to validate tier sequential integrity: collect all division tiers for the season, sort, compare to `range(1, len+1)`; if any tier is missing, raise `ValueError` with the full diagnostic listing missing tiers and current tiers (message matches contracts/commands.md format)
- [X] T029 [US6] Update `/division add` and `/division duplicate` in `src/cogs/season_cog.py` to include a required `tier: int` parameter; pass tier to the service methods; propagate tier-conflict errors as ephemeral `⛔` messages
- [X] T030 [US6] Update `/season setup` response in `src/cogs/season_cog.py` to display `season_number` from the returned Season object in the confirmation message
- [X] T031 [P] [US6] Update season review output builder in `src/utils/message_builder.py` to append `(Tier {tier})` to each division header and render a team roster block per division: one line per team instance showing `team_name`, each seat number with driver mention or `unassigned`, Reserve row showing `(no seats pre-assigned)`

**Checkpoint**: User Story 6 fully functional. Season numbers increment correctly; tier gap rejection fires with diagnostic; roster output visible in /season review.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation and any loose integration gaps identified during implementation.

- [ ] T032 [P] Run all quickstart.md verification steps in order (US1 through US6) and resolve any gaps; confirm each user story's independent test criterion is met and the bot responds as documented in contracts/commands.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 completion — must be done before Phase 8 (US6 service changes read tier/season_number fields)
- **Phase 3 (US1)**: Depends on Phase 1 (models + service stub) — no dependency on Phase 2
- **Phase 4 (US2)**: Depends on Phase 3 (DriverService DB helpers must exist first)
- **Phase 5 (US3)**: Depends on Phase 3 (DriverService.get_profile must exist); independent of Phase 4
- **Phase 6 (US4)**: Depends on Phase 1 (TeamService stub + team models); independent of Phases 3–5
- **Phase 7 (US5)**: Depends on Phase 6 (TeamService season methods extend the service built in US4)
- **Phase 8 (US6)**: Depends on Phase 2 (model fields), Phase 6 T027 (seed_division_teams must exist to call from add_division)
- **Phase 9 (Polish)**: Depends on all prior phases

### User Story Dependencies

| Story | Depends On | Rationale |
|-------|-----------|-----------|
| US1 (P1) | Phase 1 | Needs DriverProfile model and DriverService stub |
| US2 (P2) | US1 | reassign_user_id calls get_profile internally |
| US3 (P3) | US1 | set_former_driver calls get_profile internally |
| US4 (P4) | Phase 1 | Needs DefaultTeam/TeamInstance models and TeamService stub |
| US5 (P5) | US4 | Season methods depend on division seeding structure built in US4 |
| US6 (P6) | Phase 2, US4 T027 (seed call) | Needs tier/season_number model fields; add_division calls seed_division_teams |

### Parallel Opportunities per Phase

**Phase 1** — T002, T003, T004, T005, T006, T007 all work on different files; run in parallel after T001:
```
T001 (migration SQL)
  └─► T002 T003 T004 T005 T006 T007  [parallel]
        └─► T008
```

**Phase 2** — T009, T010, T011 all on different files; fully parallel:
```
T009  T010  T011  [parallel, start after Phase 1]
```

**Within US4** — T019 and T020 both touch `team_service.py` so are sequential; T021 is an independent file:
```
T019 → T020 → T027 (US6, wires seed call)
T021            [parallel with T019/T020]
T022 (depends on T019)
```

**Within US6** — T025, T026, T027, T028, T029 all touch `season_service.py` or `season_cog.py`; run sequentially. T031 (`message_builder.py`) is independent:
```
T025 → T026 → T027 → T028  [sequential, season_service.py]
T029 → T030                  [sequential, season_cog.py]
T031                          [parallel with above]
```

---

## Implementation Strategy

**MVP Scope** — deliver US1 first, then US2/US3 in either order, then US4/US5 as a pair, then US6:

1. **Increment 1** (Phases 1–3): Migration + models + DriverService state machine → state machine is live and testable in test-mode
2. **Increment 2** (Phase 4, US2): `/driver reassign` → account migration admin command available
3. **Increment 3** (Phase 5, US3): `/test-mode set-former-driver` → flag testing available
4. **Increment 4** (Phases 6–7, US4+US5): Default + season team management → team config commands available
5. **Increment 5** (Phases 2+8, US6): Season counter, division tiers, roster review → season lifecycle enforcement live

Each increment is independently deployable; no increment introduces regressions to prior increments.

---

## Task Summary

| Phase | User Story | Tasks | Count |
|-------|-----------|-------|-------|
| Phase 1: Setup | — | T001–T008 | 8 |
| Phase 2: Foundational | — | T009–T011 | 3 |
| Phase 3 | US1 (P1) State Machine | T012–T014 | 3 |
| Phase 4 | US2 (P2) ID Reassignment | T015–T016 | 2 |
| Phase 5 | US3 (P3) Former Driver Flag | T017–T018 | 2 |
| Phase 6 | US4 (P4) Default Teams | T019–T022 | 4 |
| Phase 7 | US5 (P5) Season Teams | T023–T024 | 2 |
| Phase 8 | US6 (P6) Season/Tier/Review | T025–T031 | 7 |
| Phase 9: Polish | — | T032 | 1 |
| **Total** | | | **32** |
