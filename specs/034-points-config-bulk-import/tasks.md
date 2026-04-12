# Tasks: Points Configuration XML Import

**Input**: Design documents from `specs/034-points-config-bulk-import/`
**Branch**: `034-points-config-bulk-import`
**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Data Model**: [data-model.md](data-model.md) | **Contract**: [contracts/results.md](contracts/results.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelisable (different files, no incomplete dependencies)
- **[Story]**: User story label (US1–US4); absent in Setup/Foundational/Polish phases

---

## Phase 1: Setup

**Purpose**: Add the new dependency and register the new utility module.

- [ ] T001 Add `lxml` to `requirements.txt`
- [ ] T002 Create empty `src/utils/xml_import.py` with module docstring and `__all__`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core parsing, validation, and service logic that all user story phases depend on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T003 Define `XmlImportPayload` dataclass and `XmlImportError` exception in `src/utils/xml_import.py`
- [ ] T004 [P] Build `_SESSION_TYPE_BY_LABEL` reverse-lookup dict (case-insensitive label → `SessionType`) in `src/utils/xml_import.py`
- [ ] T005 Implement `parse_xml_payload(xml_text: str) -> tuple[XmlImportPayload, list[str]]` in `src/utils/xml_import.py` — lxml parse with `resolve_entities=False` and `no_network=True`; extracts `<session>` blocks; validates structure (type lookup, position id ≥ 1, points ≥ 0, FL points ≥ 0, FL limit ≥ 1 when present, FL rejected on qualifying); last-wins on duplicate position ids with warning collection; raises `XmlImportError` on hard failures
- [ ] T006 Implement `validate_payload(payload: XmlImportPayload) -> list[str]` in `src/utils/xml_import.py` — monotonic non-increasing check per session block (skip zero-points entries per existing rule); returns list of error strings
- [ ] T007 Implement `xml_import_config(db_path, server_id, config_name, payload)` async function in `src/services/points_config_service.py` — single DB connection, resolves config_id via `_get_config_id`, upserts all position rows and FL rows from payload in one atomic transaction; raises `ConfigNotFoundError` on unknown config
- [ ] T008 [P] Write unit tests for `parse_xml_payload` and `validate_payload` in `tests/unit/test_xml_import.py` — covers: valid full import, partial session, duplicate position id (last-wins + warning), unknown session type rejection, FL on qualifying rejection, negative/zero position id rejection, negative points rejection, invalid FL limit rejection, malformed XML, monotonic violation detection, empty payload

**Checkpoint**: Foundation ready — `src/utils/xml_import.py` + `xml_import_config` + unit tests all passing.

---

## Phase 3: User Story 1 — Happy-Path XML Import via Modal (Priority: P1) 🎯 MVP

**Goal**: Allow a trusted admin to run `/results config xml-import name:<config>` (no attachment), paste XML into a modal, and have the config updated in the database with a success summary.

**Independent Test**: `/results config xml-import name:"100%"` with valid XML → modal opens → submit → config updated → view confirms values. (Quickstart steps 1 and 2.)

- [ ] T009 [US1] Implement `XmlImportModal` class in `src/cogs/results_cog.py` — `discord.ui.Modal` titled "XML Points Config Import" with a single `discord.ui.TextInput` field (`label="XML payload"`, `style=long`, `max_length=4000`); stores `config_name`, `db_path`, `guild_id`; `on_submit` defers ephemerally then calls `_run_xml_import`
- [ ] T010 [US1] Implement `_run_xml_import(interaction, xml_text, config_name, db_path, guild_id)` async helper in `src/cogs/results_cog.py` — calls `parse_xml_payload`, reports `XmlImportError` ephemerally; calls `validate_payload`, reports failures ephemerally; calls `xml_import_config`, handles `ConfigNotFoundError` and generic DB exceptions ephemerally; on success sends ephemeral summary listing each session updated and FL changes, including any duplicate-id warnings; calls `output_router.post_log` with user info and session summary
- [ ] T011 [US1] Add `xml_import_config` slash command to `config_group` in `src/cogs/results_cog.py` — `@config_group.command(name="xml-import")`, `@app_commands.describe`, `@channel_guard`, `@admin_only`; parameters: `name: str`, `file: discord.Attachment | None = None`; when `file is None`: calls `_module_gate` then sends `XmlImportModal`; when `file is not None`: defers ephemerally, calls `_module_gate`, reads file bytes, size-checks (> 100 KB → ephemeral error), empty-check, decodes UTF-8, calls `_run_xml_import`

**Checkpoint**: US1 complete — modal path fully functional end-to-end.

---

## Phase 4: User Story 2 — XML Import via File Attachment (Priority: P2)

**Goal**: Allow a trusted admin to supply an `.xml` file attachment as an alternative to the modal, bypassing the 4 000-character limit.

**Independent Test**: `/results config xml-import name:"100%" file:import.xml` → no modal → config updated → success reply. (Quickstart step 3.)

- [ ] T012 [US2] Extend the `xml_import_config` command handler in `src/cogs/results_cog.py` to handle the `file is not None` branch: defer ephemerally → `_module_gate` check → `await file.read()` → size check (> 100 KB → ephemeral error) → empty check → UTF-8 decode → `_run_xml_import`

> **Note**: T012 completes the file branch already scaffolded in T011. If T011 was implemented with a stub `pass` for the file branch, T012 fills it in.

**Checkpoint**: US1 + US2 complete — both input paths functional.

---

## Phase 5: User Story 3 — Validation Failure and Rollback (Priority: P2)

**Goal**: Guarantee that any invalid XML (parse errors, unknown session types, value range errors, monotonic violations) is rejected with a clear error and leaves the database unchanged.

**Independent Test**: Submit XML with P1=10, P2=20 (monotonic violation) → ephemeral error identifies the violation → `/results config view` confirms no change. (Quickstart steps 4–6.)

- [ ] T013 [US3] Verify atomicity of `xml_import_config` by ensuring it uses a single `async with get_connection(db_path) as db` context with `await db.commit()` only on success — any exception before commit leaves the DB unchanged (already enforced by aiosqlite context manager rollback); confirm this in service implementation from T007
- [ ] T014 [US3] Write unit tests for all validation-failure paths in `tests/unit/test_xml_import.py` (if not already covered in T008): parse error → `XmlImportError` raised; unknown session type → `XmlImportError`; monotonic violation → `validate_payload` returns non-empty errors; structural errors → `XmlImportError` with full error list

> **Note**: T013 is a verification/review task. If T007 already handles it correctly, it closes immediately. T014 extends T008's coverage with explicit rollback-path assertions.

**Checkpoint**: US1 + US2 + US3 complete — all error paths reject cleanly.

---

## Phase 6: User Story 4 — Partial-Session XML (Priority: P3)

**Goal**: Confirm that XML containing only a subset of session types, or session blocks with no `<position>` or `<fastest-lap>` children, leaves all other database rows unchanged.

**Independent Test**: Import XML with only a Feature Race block → Sprint Qualifying, Sprint Race, Feature Qualifying entries are unmodified. (Quickstart step 2.)

- [ ] T015 [US4] Verify partial-session semantics in `parse_xml_payload` — session types absent from XML must not appear in `XmlImportPayload.positions` or `XmlImportPayload.fastest_laps`; session blocks with no `<position>` children result in an empty positions dict for that session type (not added to payload); confirm in unit tests in `tests/unit/test_xml_import.py`
- [ ] T016 [US4] Verify that `xml_import_config` only upserts rows for session types and positions present in the payload — untouched rows are never written; add a targeted unit/integration assertion confirming DB rows for absent sessions are unchanged after import

**Checkpoint**: All 4 user stories complete.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final wiring, edge cases confirmed, and requirements traceability.

- [ ] T017 [P] Confirm `lxml` is importable in the bot's Python environment (`python -c "import lxml.etree"`) and add a comment in `src/utils/xml_import.py` noting the required install (`pip install lxml` / `apt install python3-lxml`)
- [ ] T018 [P] Confirm UTF-8 decode error on file attachment is handled gracefully in the file path of the command handler — add a `try/except UnicodeDecodeError` wrapping the decode step in `src/cogs/results_cog.py`; return ephemeral "File could not be decoded as UTF-8" error
- [ ] T019 Run the full test suite (`python -m pytest tests/ -v`) and confirm all tests pass

---

## Dependencies

```
T001 → T002 → T003 → T004, T005
T003, T004 → T005 → T006
T005, T006 → T007
T007 → T009 → T010 → T011 → T012
T008 (parallel with T009–T012 once T005/T006 done)
T013–T016 require T010, T011 to be in place
T017, T018 (polish — can run any time after T011)
T019 requires all prior tasks complete
```

## Parallel Execution Opportunities

### Phase 2 (Foundational)
- T004 (`_SESSION_TYPE_BY_LABEL` dict) can be written in parallel with T003 after T002
- T008 (unit tests) can be written in parallel with T009–T011 once T005 and T006 are done

### Phase 3–6 (Stories)
- T013 (atomicity verification) is a review task — can be done in parallel with T011/T012
- T015 (partial-session verification) shares the test file with T008/T014; parallelisable if editing different test functions

## Implementation Strategy

**MVP scope**: Phases 1–3 (T001–T011) deliver the complete modal import path (US1) with full validation and audit logging. This is a shippable increment.

**Full scope**: Phases 4–6 (T012–T016) add the file attachment path (US2) and explicit verification of validation/rollback (US3) and partial-session semantics (US4). These are low-risk since T010 (`_run_xml_import`) and T007 (`xml_import_config`) already implement the correct behaviour — these phases are primarily verification and wiring.

## Validation Summary

| User Story | Tasks | Independent Test |
|------------|-------|-----------------|
| US1 — Modal import | T009, T010, T011 | Quickstart steps 1–2 |
| US2 — File attachment | T012 | Quickstart step 3 |
| US3 — Validation & rollback | T013, T014 | Quickstart steps 4–6 |
| US4 — Partial-session | T015, T016 | Quickstart step 2 (partial XML) |

**Total tasks**: 19  
**Parallelisable tasks**: T004, T008, T013, T015, T017, T018 (6)  
**New files**: `src/utils/xml_import.py`, `tests/unit/test_xml_import.py`  
**Modified files**: `src/services/points_config_service.py`, `src/cogs/results_cog.py`, `requirements.txt`  
**Schema migrations**: None
