# Tasks: Season-Signup Flow Alignment

**Input**: Design documents from `specs/028-season-signup-flow/`
**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅

**Tests**: No test tasks — not requested in the feature specification.

---

## Phase 1: Setup

**Purpose**: Create the migration file that all user stories depend on.

- [X] T001 Create src/db/migrations/027_season_signup_flow.sql with full migration SQL per data-model.md (ADD lineup_channel_id, calendar_channel_id, lineup_message_id to divisions; UPDATE divisions from signup_division_config; recreate signup_division_config without lineup_channel_id)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Model and service-layer primitives that MUST be complete before any user story work can begin.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 [P] Add lineup_channel_id, calendar_channel_id, lineup_message_id fields to Division dataclass/model in src/models/division.py (all default None; update from_row to populate them)
- [X] T003 [P] Add get_setup_or_active_season(server_id) async method to SeasonService in src/services/season_service.py (SELECT … WHERE status IN ('SETUP', 'ACTIVE') LIMIT 1; returns Season | None)

**Checkpoint**: Foundation ready — user story phases can now begin.

---

## Phase 3: User Story 1 — Season-Independent Signup Window (Priority: P1) 🎯 MVP

**Goal**: Admins can open and close the signup window regardless of whether a season exists or its state. Forced close preserves PENDING_ADMIN_APPROVAL and PENDING_DRIVER_CORRECTION drivers.

**Independent Test**: Run `/signup open` with no season in the database — signups open without error. Complete driver signup → PENDING_ADMIN_APPROVAL. Run `/signup close` → driver remains in PENDING_ADMIN_APPROVAL, not NOT_SIGNED_UP.

- [X] T004 [US1] Remove the active-season guard from /signup open in src/cogs/signup_cog.py (delete the get_active_season() check and its associated error return at approximately line 1239; leave all other pre-conditions intact)
- [X] T005 [P] [US1] Narrow execute_forced_close in src/cogs/module_cog.py to only transition PENDING_SIGNUP_COMPLETION drivers to NOT_SIGNED_UP (remove PENDING_ADMIN_APPROVAL and PENDING_DRIVER_CORRECTION from the in_progress_states set; APScheduler job cancellation scope follows the same narrowing)

**Checkpoint**: User Story 1 fully functional — signups open with no season, forced close preserves approved drivers.

---

## Phase 4: User Story 2 — Driver Assignment Targets Setup Season (Priority: P1)

**Goal**: `/driver assign` and `/driver unassign` accept a SETUP season. Roles deferred (SETUP) or immediate (ACTIVE) per the four-case matrix in A-005.

**Independent Test**: Create SETUP season, approve a driver to UNASSIGNED, run `/driver assign` — driver is ASSIGNED, Discord roles unchanged. Run `/driver unassign` — driver is UNASSIGNED, Discord roles still unchanged.

- [X] T006 [US2] Add season_state: str parameter to assign_driver() in src/services/placement_service.py and make role grant conditional: if season_state == "ACTIVE" grant roles immediately; if "SETUP" skip role grant entirely
- [X] T007 [P] [US2] Add season_state: str parameter to unassign_driver() in src/services/placement_service.py and make role revoke conditional: if season_state == "ACTIVE" revoke roles immediately; if "SETUP" skip revocation entirely
- [X] T008 [US2] Update /driver assign command handler in src/cogs/driver_cog.py: replace get_active_season() with get_setup_or_active_season(); update error message to "⛔ No season in SETUP or ACTIVE state found."; pass season.status to assign_driver()
- [X] T009 [P] [US2] Update /driver unassign command handler in src/cogs/driver_cog.py: replace get_active_season() with get_setup_or_active_season(); update error message; pass season.status to unassign_driver()

**Checkpoint**: User Story 2 fully functional — driver assignment works against SETUP season; roles deferred; ACTIVE season still grants roles immediately.

---

## Phase 5: User Story 3 — Season Review Includes Driver Lineups (Priority: P2)

**Goal**: `/season review` output includes a per-division lineup section (ASSIGNED drivers by team) and flags any UNASSIGNED drivers with a warning.

**Independent Test**: Assign two drivers to a division, leave one UNASSIGNED, run `/season review` — output lists the two assigned drivers under their teams and shows a warning for the UNASSIGNED driver.

- [X] T010 [US3] Extend /season review command in src/cogs/season_cog.py: for each division in the season add a section listing ASSIGNED drivers grouped by team (driver Discord mention + driver type where available); add a server-level UNASSIGNED warning if any drivers are in UNASSIGNED state for this season (e.g. "⚠️ N driver(s) UNASSIGNED — placement incomplete")

**Checkpoint**: User Story 3 fully functional — `/season review` shows complete driver lineup state before approval.

---

## Phase 6: User Story 4 — Season Approval Publishes Lineups and Calendars (Priority: P2)

**Goal**: Season approval triggers: (1) bulk Discord role grant for all ASSIGNED drivers, (2) lineup post per division to lineup_channel_id, (3) calendar post per division to calendar_channel_id. New `/division calendar-channel` command. `/division lineup-channel` write target moves to divisions table.

**Independent Test**: Configure lineup and calendar channels for all divisions. Run `/season approve`. Confirm: all ASSIGNED drivers receive tier + team roles; a lineup message appears in #lineup; a calendar message with round timestamps appears in #calendar.

- [X] T011 [US4] Add /division calendar-channel subcommand to the /division group in src/cogs/season_cog.py (params: name: str, channel: discord.TextChannel; pre-cond: season exists; stores calendar_channel_id on the divisions row; success confirmation; follows same pattern as /division lineup-channel)
- [X] T012 [US4] Update /division lineup-channel command handler in src/cogs/season_cog.py: replace call to signup_module_service.upsert_division_config() with a direct UPDATE divisions SET lineup_channel_id = ? WHERE id = ?
- [X] T013 [P] [US4] Update src/services/signup_module_service.py: remove lineup_channel_id parameter from upsert_division_config() and remove any read of lineup_channel_id from get_division_config(); update all call sites in the file accordingly
- [X] T014 [P] [US4] Rewrite _maybe_post_lineup as _refresh_lineup_post(guild, division_id) in src/services/placement_service.py: read lineup_channel_id and lineup_message_id from divisions table; if no lineup_channel_id return silently; attempt to delete old message (catch discord.NotFound/Forbidden gracefully); build fresh lineup embed grouped by team; post to channel; persist new message ID to divisions.lineup_message_id; remove deprecated _maybe_post_lineup method
- [X] T015 [US4] Extend _do_approve in src/cogs/season_cog.py: after season transitions to ACTIVE, fetch all ASSIGNED drivers across all divisions; call _grant_roles(div_role_id, team_role_id) per driver; log per-driver errors but do not block approval on failure
- [X] T016 [US4] Extend _do_approve in src/cogs/season_cog.py: after bulk role grant (T015), iterate each division; if lineup_channel_id is set call _refresh_lineup_post for that division; catch discord.HTTPException per division (log and continue per A-006)
- [X] T017 [US4] Extend _do_approve in src/cogs/season_cog.py: after lineup posts (T016), iterate each division; if calendar_channel_id is set build a calendar message listing rounds chronologically (round number, track name or "Mystery", scheduled datetime as f"<t:{int(dt.timestamp())}:F>"); post to calendar channel; catch discord.HTTPException per division (log and continue)

**Checkpoint**: User Story 4 fully functional — season approval bulk-grants roles and auto-posts lineups and calendars.

---

## Phase 7: User Story 5 — Live Lineup Updates After Assignment Changes (Priority: P3)

**Goal**: Every call to `/driver assign` or `/driver unassign` automatically refreshes the lineup message in the affected division's lineup channel (delete old, post new).

**Independent Test**: Approve season (lineup posted). Run `/driver assign` to change a driver's team. Confirm the old lineup message is gone and a new one reflecting the change is in #lineup.

- [X] T018 [US5] Wire _refresh_lineup_post call in assign_driver() in src/services/placement_service.py: after the assignment is committed, call await self._refresh_lineup_post(guild, division_id) (replacing the old _maybe_post_lineup call); handle no-guild gracefully
- [X] T019 [P] [US5] Wire _refresh_lineup_post call in unassign_driver() in src/services/placement_service.py: after the unassignment is committed, call await self._refresh_lineup_post(guild, division_id) (replacing the old _maybe_post_lineup call); handle no-guild gracefully

**Checkpoint**: User Story 5 fully functional — lineup message auto-refreshes on every assignment change, in SETUP or ACTIVE, pre- or post-approval.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T020 [P] Verify bot startup: start the bot locally and confirm migration 027 applies cleanly (no SQL errors), all slash commands register without ImportError or AttributeError, and no existing tests regress (python -m pytest tests/ -v from repo root)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1. Blocks all user story phases.
- **Phase 3 — US1 (P1)**: Depends on Phase 2. No dependency on other stories.
- **Phase 4 — US2 (P1)**: Depends on Phase 2. No dependency on US1.
- **Phase 5 — US3 (P2)**: Depends on Phase 2. No dependency on US1/US2 (reads division data already present).
- **Phase 6 — US4 (P2)**: Depends on Phase 2 (Division model). T016 depends on T014 (_refresh_lineup_post). T015/T016/T017 must be applied sequentially (all extend _do_approve).
- **Phase 7 — US5 (P3)**: Depends on T014 (_refresh_lineup_post must exist before being wired).
- **Phase 8 (Polish)**: Depends on all desired user stories complete.

### User Story Dependencies

| Story | Can start after | Dependencies on other stories |
|-------|----------------|-------------------------------|
| US1 (P1) | Phase 2 | None |
| US2 (P1) | Phase 2 | None |
| US3 (P2) | Phase 2 | None |
| US4 (P2) | Phase 2 | T014 must precede T016 (same story) |
| US5 (P3) | T014 (US4) | Requires _refresh_lineup_post from US4 |

### Within-Phase Parallel Opportunities

| Phase | Task pair | Why parallel |
|-------|-----------|--------------|
| Phase 2 | T002 ‖ T003 | Different files: division.py vs season_service.py |
| Phase 3 | T004 ‖ T005 | Different files: signup_cog.py vs module_cog.py |
| Phase 4 | T006 ‖ T007 | Different functions in placement_service.py |
| Phase 4 | T008 ‖ T009 | Different functions in driver_cog.py |
| Phase 6 | T013 ‖ T014 ‖ T011/T012 | T013: signup_module_service.py; T014: placement_service.py; T011/T012: season_cog.py |
| Phase 7 | T018 ‖ T019 | Different functions in placement_service.py |

### MVP Scope

Suggested MVP (deployable increment): **Phase 1 + Phase 2 + Phase 3 (US1) + Phase 4 (US2)**
- Unblocks the real-world league flow: open signups without a season, collect and approve drivers, assign to a SETUP season without premature role grants.
- US3–US5 can follow as a second deployment.

---

## Implementation Strategy

1. Complete Phases 1 and 2 first (migration + model + service helper) — these are true blockers.
2. US1 and US2 are both P1 and independent; implement together for MVP deployment.
3. US3 is a pure read-path extension (/season review); low risk, implement after US2.
4. US4 is the most complex phase (7 tasks, three _do_approve extensions); implement T011–T014 first (commands + _refresh_lineup_post), then T015 → T016 → T017 strictly in order.
5. US5 is a 2-task wire-up that depends only on T014; implement after T014 is complete.
6. The constitution amendments (Principle XI close-timer scope + lineup channel ownership) are already applied to `.specify/memory/constitution.md` as part of the plan phase.
