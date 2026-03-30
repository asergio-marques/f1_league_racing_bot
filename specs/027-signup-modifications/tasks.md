# Tasks: Signup Module Modifications and Enhancements

**Input**: Design documents from `/specs/027-signup-modifications/`
**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | quickstart.md ✅

**Branch**: `027-signup-modifications`

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other [P] tasks in the same phase (different files, no unresolved deps)
- **[USn]**: Which user story this task belongs to
- File paths are repo-root-relative

---

## Phase 1: Setup

**Purpose**: Create the new utility module so downstream stories can import it.

- [ ] T001 [P] Create `src/utils/nationality_data.py` with `NATIONALITY_LOOKUP: dict[str, str]` — all sovereign widely-recognised states, both nationality adjective and country name as lowercase keys mapping to Title-Case canonical adjective; include common English aliases; include `"other" → "Other"`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Tasks that multiple user stories depend on — must complete before story phases.

**⚠️ CRITICAL**: T001 must complete before T002. T002–T003 unblock US1 and US2 respectively.

- [ ] T002 Add unit tests for `NATIONALITY_LOOKUP` in `tests/unit/test_nationality_data.py` — test: adjective accepted, country name accepted, "other" any-case accepted, 2-letter code absent, all values are Title-Case, ≥10 spot-checks across continents
- [ ] T003 Add unit tests for bulk-parse helper in `tests/unit/test_results_bulk.py` — test: valid pairs, position=0 invalid, negative points invalid, blank lines skipped, duplicate position last-wins, empty input returns `([], [])`, malformed line in errors

---

## Phase 3: User Story 1 — Nationality & Country Name Validation (Priority: P1) 🎯 MVP

**Goal**: Replace the 2-letter ISO code nationality wizard step with a full-name lookup against `NATIONALITY_LOOKUP`. Invalid inputs are rejected; canonical adjective is stored on success.

**Independent Test**: Open signups, reach nationality step, submit "German" → accepted stored as "German"; submit "Germany" → accepted stored as "German"; submit "DE" → rejected with clear error; submit "other" → accepted stored as "Other". See `quickstart.md` Story 1.

### Implementation for User Story 1

- [ ] T004 [US1] Rewrite `_validate_nationality()` in `src/services/wizard_service.py` — replace `re.fullmatch(r"[a-z]{2}", ...)` logic with `NATIONALITY_LOOKUP.get(raw.strip().lower())`; return canonical string or `None`; import `NATIONALITY_LOOKUP` from `src/utils/nationality_data.py`
- [ ] T005 [US1] Update rejection message in `_handle_nationality()` in `src/services/wizard_service.py` — replace "2-letter country code (e.g. `gb`, `us`)" text with "full nationality (e.g. `British`) or country name (e.g. `United Kingdom`), or type `other`"
- [ ] T006 [US1] Update step prompt text in `_prompt_for_state()` `COLLECTING_NATIONALITY` branch in `src/services/wizard_service.py` — replace "Enter your 2-letter country code (e.g. `gb`, `us`) or `other`" with "Enter your nationality (e.g. `British`) or country name (e.g. `United Kingdom`), or type `other`"
- [ ] T007 [US1] Extend `tests/unit/test_wizard_service.py` with nationality validation tests — test: full adjective accepted, country name accepted, "OTHER" (uppercase) accepted and stored as "Other", 2-letter code rejected (`None`), unknown string rejected, leading/trailing whitespace stripped before lookup

**Checkpoint**: Nationality wizard step fully functional with new validation. All `test_wizard_service.py` tests pass. `test_nationality_data.py` tests pass.

---

## Phase 4: User Story 2 — Server-Leave Logging (Priority: P2)

**Goal**: Post a log notification for all active driver states (wizard, Unassigned, Assigned) when a driver leaves the server. Fixes the pre-existing `raise NotImplementedError` stub in `handle_member_remove`.

**Independent Test**: Approve a test driver to Unassigned; remove from server; verify log-channel notification shows display name, Discord UID, and state "Unassigned". No DB state changes occur. See `quickstart.md` Story 2.

### Implementation for User Story 2

- [ ] T008 [US2] Fix `handle_member_remove()` in `src/services/wizard_service.py` — remove `raise NotImplementedError` at the end of the method; replace with `output_router.post_log()` call posting display name, Discord UID, and the wizard state label for the departing driver (after the existing cleanup block runs)
- [ ] T009 [US2] Expand `on_member_remove()` in `src/cogs/signup_cog.py` — after `await self.bot.wizard_service.handle_member_remove(...)`, query `driver_profiles` for `current_state` of the departing member; if state is `UNASSIGNED` or `ASSIGNED`, fetch display name from `signup_records` (or fall back to Discord username) and call `output_router.post_log()`; wrap entire new block in try/except to ensure no crash if query fails or log channel absent

**Checkpoint**: Leave events for all active driver states produce log notifications. No crashes if log channel absent. Wizard cleanup still fires for wizard-state drivers.

---

## Phase 5: User Story 3 — Signup Open Embed Includes Close Time (Priority: P3)

**Goal**: Include the auto-close timestamp in the public signup announcement embed when set, so drivers can see when the signup window ends.

**Independent Test**: Open signups with a close time; observe the announcement embed — close time line present (YYYY-MM-DD HH:MM UTC format). Open without close time — no close time line appears. See `quickstart.md` Story 3.

### Implementation for User Story 3

- [ ] T010 [US3] Add close-time line to signup open embed in `src/cogs/signup_cog.py` — in the embed-building block where `info_embed` is constructed, check if `close_at_iso` is non-null; if so, parse it and append a line `Auto-closes: <YYYY-MM-DD HH:MM> UTC` to the embed description before the embed is sent to the signup channel; the existing ephemeral admin confirmation is unchanged

**Checkpoint**: Signup announcement embed includes close time when set; omits it when not set.

---

## Phase 6: User Story 4 — Admin Review Panel Waiting Message (Priority: P4)

**Goal**: Append "Please wait for an admin to validate your signup." to the admin review panel after the Notes line, with a blank line separator, appearing before the action buttons.

**Independent Test**: Complete a wizard run; observe the review panel — waiting text appears after Notes, separated by one blank line, before the Approve/Request Changes/Reject buttons. Correction cycle re-post also contains the notice. See `quickstart.md` Story 4.

### Implementation for User Story 4

- [ ] T011 [US4] Append waiting message to `_format_review_panel()` in `src/services/wizard_service.py` — after the `f"**Notes:** {record.notes or 'None'}\n"` terminating line, append `"\nPlease wait for an admin to validate your signup.\n"` (the `\n` before is the blank line gap; the text appears on its own line before the buttons are rendered by the caller)

**Checkpoint**: Admin review panel contains waiting notice in correct position on both initial post and correction re-post.

---

## Phase 7: User Story 5 — Bulk Points Configuration Editing (Priority: P5)

**Goal**: Add `results bulk-config session` and `results bulk-amend session` commands that open a Discord modal for multi-line `<position>, <points>` bulk entry.

**Independent Test**: Run `results bulk-config session` with 5 valid pairs — all 5 stored; run with mixed valid/invalid — valid apply, invalid reported; run `results bulk-amend session` with amendment mode off — clear error returned. See `quickstart.md` Story 5.

### Implementation for User Story 5

- [ ] T012 [US5] Add `_parse_bulk_lines(text: str) -> tuple[list[tuple[int, int]], list[str]]` helper function in `src/cogs/results_cog.py` — parse multi-line `<position>, <points>` text; skip blank lines; reject position < 1 or points < 0; last value wins on duplicate position (note duplication in errors); return `(valid_pairs, error_messages)`
- [ ] T013 [P] [US5] Add `BulkConfigSessionModal` class in `src/cogs/results_cog.py` — `discord.ui.Modal` subclass; one `TextInput(style=TextStyle.paragraph, label="position, points — one per line")`; holds `config_name` and `session_type` set at construction; `on_submit`: call `_parse_bulk_lines`; if `ConfigNotFoundError` raised on first `set_session_points` call, send ephemeral error and return; apply all valid pairs via `points_config_service.set_session_points()` in loop; send ephemeral followup listing applied changes and any errors; write audit log entry
- [ ] T014 [P] [US5] Add `BulkAmendSessionModal` class in `src/cogs/results_cog.py` — same modal pattern; `on_submit`: call `_parse_bulk_lines`; guard `AmendmentNotActiveError` at the top of `on_submit`; apply valid pairs via `amendment_service.modify_session_points()` in loop; send ephemeral followup with summary; write audit log entry
- [ ] T015 [US5] Add `bulk_config_session` subcommand to `config_group` in `src/cogs/results_cog.py` — parameters: `name: str`, `session: SessionTypeChoice`; handler calls `await interaction.response.send_modal(BulkConfigSessionModal(name, session, self.bot.db_path, guild_id))`
- [ ] T016 [US5] Add `bulk_amend_session` subcommand to `amend_group` in `src/cogs/results_cog.py` — parameters: `session: SessionTypeChoice`; handler calls `await interaction.response.send_modal(BulkAmendSessionModal(session, self.bot.db_path, guild_id))`

**Checkpoint**: Both bulk commands open a modal; valid entries apply; invalid lines reported; amendment-mode guard works; audit log entries written.

---

## Phase 8: User Story 6 — Unassigned Command Restructure + CSV Export (Priority: P6)

**Goal**: Rename `/signup unassigned` → `/signup unassigned list`; add `/signup unassigned export` that returns a CSV file attachment with full driver data for all Unassigned drivers.

**Independent Test**: `/signup unassigned list` returns seeded list identical to old command; `/signup unassigned export` returns CSV with correct headers and slot columns; no Unassigned drivers → "no drivers" message with no attachment. See `quickstart.md` Story 6.

### Implementation for User Story 6

- [ ] T017 [US6] Add `get_unassigned_drivers_for_export()` in `src/services/placement_service.py` — async method accepting `(server_id: int, slots: list[AvailabilitySlot])`; SQL same as `get_unassigned_drivers_seeded` plus `sr.platform_id` in SELECT; Python post-process: seed, display_name, discord_user_id, driver_type, total_lap_fmt, slot-presence dict `{slot_sequence_id: bool}`, preferred_team_1/_2/_3 split from JSON, platform, platform_id (empty string for nulls)
- [ ] T018 [US6] Add unit tests for export row building in `tests/unit/test_placement_export.py` — test: slot expansion X present, slot expansion empty, platform_id included, null values → empty string, preferred_teams split into three columns, correct seed ordering
- [ ] T019 [US6] Restructure unassigned command in `src/cogs/signup_cog.py` — remove the `@signup.command(name="unassigned")` decorated function; add `unassigned_group = app_commands.Group(name="unassigned", description="...", parent=signup)`; add `list` subcommand with identical body to the old `signup_unassigned`
- [ ] T020 [US6] Add `export` subcommand to `unassigned_group` in `src/cogs/signup_cog.py` — fetch `AvailabilitySlot`s for server; call `placement_service.get_unassigned_drivers_for_export(server_id, slots)`; if empty → ephemeral "no Unassigned drivers" message; otherwise: build header row (Seed, Display Name, Discord User ID, Driver Type, Lap Total, one col per slot ordered by `slot_sequence_id`, Preferred Team 1, Preferred Team 2, Preferred Team 3, Platform, Platform ID); write rows via `csv.writer(io.StringIO())`; encode `io.BytesIO(output.getvalue().encode("utf-8-sig"))`; return `discord.File(buf, filename="unassigned_drivers.csv")` as ephemeral attachment

**Checkpoint**: `/signup unassigned list` works identically to old command. `/signup unassigned export` produces correct CSV. No Unassigned drivers → informative message.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T021 [P] Run full test suite (`python -m pytest tests/ -v`) and confirm all new and existing tests pass
- [ ] T022 [P] Run quickstart.md manual validation scenarios for all 6 stories against the running bot

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately.
- **Phase 2 (Foundational)**: Depends on T001 (nationality module must exist before tests can import it). T002 and T003 are parallel.
- **Phases 3–8 (User Stories)**: All depend on Phase 1 complete. Each story is otherwise independent.
- **Phase 9 (Polish)**: Depends on all desired story phases complete.

### User Story Dependencies

| Story | Phase | Depends on | Independently testable? |
|-------|-------|-----------|------------------------|
| US1 — Nationality Validation | 3 | T001 (nationality_data.py must exist) | ✅ Yes |
| US2 — Server-Leave Logging | 4 | None beyond Phase 1 | ✅ Yes |
| US3 — Signup Embed Close Time | 5 | None | ✅ Yes |
| US4 — Waiting Message | 6 | None | ✅ Yes |
| US5 — Bulk Points Commands | 7 | None | ✅ Yes |
| US6 — Unassigned Restructure | 8 | None | ✅ Yes |

All user story phases are fully independent of each other. They touch different files or
non-overlapping sections of the same files.

### Within Each User Story

- Service-layer changes (wizard_service, placement_service) before cog-layer changes that depend on them
- T017 (placement_service export method) must complete before T020 (cog calls it)
- T012 (`_parse_bulk_lines`) must complete before T013/T014 (modals call it)
- T013/T014 (modal classes) must complete before T015/T016 (commands instantiate them)

---

## Parallel Opportunities

### Phase 1
```
T001  # nationality_data.py — prerequisite for everything; do first
```

### Phase 2 (after T001)
```
T002  # test_nationality_data.py
T003  # test_results_bulk.py
# Both can run simultaneously — different test files
```

### User Stories (after Phase 1 done — all independent)
```
Phase 3 (US1): T004 → T005 → T006 → T007
Phase 4 (US2): T008 → T009
Phase 5 (US3): T010
Phase 6 (US4): T011
Phase 7 (US5): T012 → [T013 ‖ T014] → [T015 ‖ T016]
Phase 8 (US6): T017 → T018 (parallel) → T019 → T020
```

T013 and T014 (modal classes) can be built in parallel.
T015 and T016 (commands) can be built in parallel once modals exist.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: T001 — create `nationality_data.py`
2. Complete Phase 2: T002 — nationality data tests
3. Complete Phase 3: T004 → T005 → T006 → T007
4. **STOP and VALIDATE**: Wizard nationality step works end-to-end
5. Proceed to remaining stories in priority order (US2 → US3 → US4 → US5 → US6)

### Incremental Delivery Order (by risk/complexity)

| Order | Story | Complexity | Risk |
|-------|-------|-----------|------|
| 1 | US1 — Nationality validation | Medium (new module + 3 wizard edits) | Low |
| 2 | US4 — Waiting message | Trivial (one string append) | None |
| 3 | US3 — Embed close time | Trivial (one embed line) | None |
| 4 | US2 — Server-leave logging | Low (bug fix + 2 new queries) | Low |
| 5 | US6 — Unassigned restructure | Medium (subgroup + CSV export) | Low |
| 6 | US5 — Bulk points commands | Medium (2 modals + parse logic) | Low |
