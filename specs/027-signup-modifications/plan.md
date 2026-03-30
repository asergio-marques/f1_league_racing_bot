# Implementation Plan: Signup Module Modifications and Enhancements

**Branch**: `027-signup-modifications` | **Date**: 2026-03-30 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/027-signup-modifications/spec.md`

## Summary

Six cohesive changes to the signup module and results commands:

1. **Nationality validation** — replace the 2-letter ISO code wizard step with a bundled
   static lookup (`nationality_data.py`) that accepts full nationality adjectives and
   country names; rejects all 2-letter codes.
2. **Server-leave logging** — extend `on_member_remove` and `handle_member_remove` to post
   a log notification for all active driver states (wizard, Unassigned, Assigned); fixes a
   pre-existing `raise NotImplementedError` stub in `wizard_service`.
3. **Signup open embed close time** — include the auto-close timestamp in the public
   announcement embed when set, instead of only in the ephemeral admin confirmation.
4. **Admin review waiting message** — append "Please wait for an admin to validate your
   signup." to `_format_review_panel()` output, after notes, before buttons.
5. **Bulk points configuration** — add `results bulk-config session` and
   `results bulk-amend session` commands that open a Discord modal for multi-line
   `<position>, <points>` bulk entry.
6. **Unassigned command restructure** — rename `/signup unassigned` → `/signup unassigned
   list`; add `/signup unassigned export` that returns a CSV file attachment.

No new DB schema. No new external dependencies. All CSV work uses stdlib `csv` +
`io.StringIO`/`io.BytesIO`. Bulk input uses Discord`s native `Modal` API.

## Technical Context

**Language/Version**: Python 3.13.2
**Primary Dependencies**: discord.py ≥ 2.0, aiosqlite ≥ 0.19, APScheduler ≥ 3.10
**Storage**: SQLite via aiosqlite; `get_connection(db_path)` async context manager
**Testing**: pytest with pytest-asyncio (`asyncio_mode = auto`); run from repo root as
`python -m pytest tests/ -v`
**Target Platform**: Linux (Raspberry Pi) deployment; Windows development environment
**Project Type**: Discord bot (slash-command service)
**Performance Goals**: Discord 3-second acknowledgement window; modal responses use
`send_modal()` as the direct interaction response (no `defer()` needed)
**Constraints**: No new external packages; CSV via stdlib only; Discord modal
`TextInput.paragraph` max 4000 chars (sufficient for 20 position entries)
**Scale/Scope**: Single-server tenancy; O(dozens–hundreds) concurrent drivers per server

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I — Trusted Configuration Authority | All new commands (`bulk-config session`, `bulk-amend session`, `unassigned export`) gated behind tier-2 admin checks (`@channel_guard` + `@admin_only`). Server-leave events are system-triggered and require no gate. | ✅ PASS |
| V — Observability & Change Audit Trail | Server-leave events produce a log-channel notification for all active driver states (FR-L001/FR-L002). Bulk points changes produce an audit log entry listing all applied position-points pairs (FR-B007). | ✅ PASS — audit entries required in implementation |
| VI — Incremental Scope Expansion | All changes are within item 5 (signup wizard and driver onboarding) and items 8/9 (results and standings). No new domains introduced. | ✅ PASS |
| VII — Output Channel Discipline | Leave notifications post to the configured calculation log channel only. No posting to interaction channels. CSV export is ephemeral. | ✅ PASS |
| VIII — Driver Profile & State Machine Integrity | UNASSIGNED/ASSIGNED drivers retain their profile and all assignments on server leave; only a log notification is posted. No state changes for those drivers. Wizard-state cleanup is unchanged. | ✅ PASS |
| X — Modular Feature Architecture | All signup commands check the signup module-enabled flag before executing. All results commands check the results module-enabled flag. New commands follow the same guard pattern as their siblings. | ✅ PASS |
| XI — Signup Wizard Integrity | Nationality validation change affects only the `COLLECTING_NATIONALITY` wizard step; the state machine flow, ordering, and all other steps are unchanged. `handle_member_remove` bug fix restores intended behaviour — it does not alter the approved transition sequence. | ✅ PASS |

**Post-design re-check** (Phase 1 complete — 2026-03-30):

| Principle | Design decision to verify | Post-design result |
|-----------|--------------------------|-------------------|
| I | bulk commands use same `@admin_only` guard as `config session` / `amend session`; `unassigned export` uses same guard as `unassigned list` | ✅ PASS |
| V | `handle_member_remove` log post uses `output_router.post_log()`; bulk config/amend on_submit writes audit entry | ✅ PASS |
| VIII | `get_unassigned_drivers_for_export` is a read-only query; `on_member_remove` expansion does NOT call any state-change method for UNASSIGNED/ASSIGNED | ✅ PASS |
| XI | `NATIONALITY_LOOKUP` lookup replaces the `re.fullmatch(r"[a-z]{2}", ...)` guard in `_validate_nationality`; no other wizard methods touched | ✅ PASS |

## Project Structure

### Documentation (this feature)

```text
specs/027-signup-modifications/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
└── tasks.md             ← Phase 2 output (/speckit.tasks — not created here)
```

*(No `contracts/` directory: this feature adds subcommands to existing command groups
rather than defining new public API surfaces that warrant separate contract documents.)*

### Source Code (repository root)

```text
src/
├── utils/
│   └── nationality_data.py            ← NEW: NATIONALITY_LOOKUP static dict
├── services/
│   ├── wizard_service.py              ← MODIFY (see breakdown below)
│   └── placement_service.py           ← MODIFY: new get_unassigned_drivers_for_export()
└── cogs/
    ├── signup_cog.py                  ← MODIFY (see breakdown below)
    └── results_cog.py                 ← MODIFY: bulk-config + bulk-amend commands

tests/
└── unit/
    ├── test_nationality_data.py        ← NEW: lookup coverage + edge cases
    ├── test_wizard_service.py          ← EXTEND: nationality validation tests
    ├── test_placement_export.py        ← NEW: CSV row building + slot expansion
    └── test_results_bulk.py            ← NEW: modal parse logic (valid, invalid, dupe)
```

**Structure Decision**: Single-project layout. All changes are additive to existing layers
(utils, services, cogs). No new service files are needed; `nationality_data.py` is a
utility module, not a service.

---

### `src/utils/nationality_data.py` — new module

| Content | Detail |
|---------|--------|
| `NATIONALITY_LOOKUP: dict[str, str]` | ~250-entry static dict; lowercase keys (nationality adjective + country name aliases) → Title-Case canonical nationality adjective; `"other"` → `"Other"` |

No class, no functions — a single module-level constant imported by `wizard_service`.

---

### `src/services/wizard_service.py` — detail breakdown

| Area | Change |
|------|--------|
| `_validate_nationality(raw)` | Replace `re.fullmatch(r"[a-z]{2}", ...)` logic with `NATIONALITY_LOOKUP.get(raw.strip().lower())`; return canonical string or `None` |
| `_handle_nationality()` rejection branch | Update rejection message text: remove "2-letter country code (e.g. `gb`, `us`)" reference; replace with "full nationality (e.g. `British`) or country name (e.g. `United Kingdom`), or type `other`" |
| `_prompt_for_state()` — COLLECTING_NATIONALITY case | Update prompt text from "Enter your 2-letter country code (e.g. `gb`, `us`) or `other`" to "Enter your nationality (e.g. `British`) or country name (e.g. `United Kingdom`), or type `other`" |
| `_format_review_panel()` | After the `f"**Notes:** {record.notes or 'None'}\n"` line, append `"\n**Please wait for an admin to validate your signup.**\n"` (one blank line gap before the text, newline after) |
| `handle_member_remove()` | (a) Remove `raise NotImplementedError` at the end of the method; (b) after the wizard cleanup block completes, call `output_router.post_log()` with driver display name/UID and wizard state label |

---

### `src/cogs/signup_cog.py` — detail breakdown

| Area | Change |
|------|--------|
| `on_member_remove()` | After `await self.bot.wizard_service.handle_member_remove(...)` returns, query `driver_profiles` for the departing member's `current_state`. If state is `UNASSIGNED` or `ASSIGNED`, call `output_router.post_log()` with display name (from `signup_records` join), Discord UID, and state label. Wrap in try/except to ensure no crash if query fails or log channel is absent. |
| Signup open embed (`_open_signups` or equivalent) | In the embed-building block: when `close_at_iso` is non-null after opening signups, add a line `Auto-closes: <YYYY-MM-DD HH:MM> UTC` to the `info_embed` description before posting it to the channel. The existing ephemeral admin confirmation remains unchanged. |
| `signup_unassigned` bare command (line ~1458) | Remove the `@signup.command(name="unassigned")` decorated function entirely. |
| `unassigned_group` subgroup | Add `unassigned_group = app_commands.Group(name="unassigned", description="...", parent=signup)`. Register two subcommands: `list` (identical body to the old `signup_unassigned`) and `export` (CSV generation). |
| `export` subcommand body | Fetch `AvailabilitySlot`s for the server; call `placement_service.get_unassigned_drivers_for_export(server_id, slots)`; if empty → ephemeral "no Unassigned drivers" message; otherwise build CSV via `csv.writer` + `io.StringIO`, encode to `io.BytesIO(...encode("utf-8-sig"))`, return `discord.File(buf, filename="unassigned_drivers.csv")` as ephemeral attachment. |

---

### `src/cogs/results_cog.py` — detail breakdown

| Area | Change |
|------|--------|
| `config_group` | Add `bulk_config_session` subcommand. Parameters: `name: str` (config name), `session: SessionTypeChoice`. Handler calls `await interaction.response.send_modal(BulkConfigSessionModal(name, session, ...))`. |
| `amend_group` | Add `bulk_amend_session` subcommand. Parameters: `session: SessionTypeChoice`. Handler calls `await interaction.response.send_modal(BulkAmendSessionModal(session, ...))`. |
| `BulkConfigSessionModal` (inner class or module-level) | `discord.ui.Modal`; one `TextInput(style=TextStyle.paragraph, label="position, points — one per line")`; `on_submit`: parse lines → call `points_config_service.set_session_points()` per valid line in one async block (no explicit transaction needed since each call commits individually per existing service pattern); collect errors; send ephemeral followup with summary + audit log entry. |
| `BulkAmendSessionModal` (inner class or module-level) | Same mechanical pattern; `on_submit` calls `amendment_service.modify_session_points()` per valid line; guards `AmendmentNotActiveError` at the start of `on_submit` (analogous to gating in `amend_session`). |
| Parse logic (shared) | `_parse_bulk_lines(text: str) -> tuple[list[tuple[int,int]], list[str]]` — returns `(valid_pairs, error_messages)`. Rules: skip blank lines; `int(pos) >= 1`; `int(pts) >= 0`; duplicate positions: last value wins, note duplication in errors list. |

---

### `src/services/placement_service.py` — detail breakdown

| Area | Change |
|------|--------|
| `get_unassigned_drivers_for_export(server_id, slots)` | New async method. SQL: same as `get_unassigned_drivers_seeded` plus `sr.platform_id` in SELECT. Python post-processing: same seed/display_name logic; add `platform_id` to each row dict; compute `slots` dict (slot_sequence_id → bool) from `availability_slot_ids` JSON against the passed `slots` list; split `preferred_teams` into `_1/_2/_3` fields. |

---

## Test Plan

### `tests/unit/test_nationality_data.py` (NEW)

| Test | Assertion |
|------|-----------|
| `test_nationality_adjective_accepted` | `NATIONALITY_LOOKUP["british"] == "British"` |
| `test_country_name_accepted` | `NATIONALITY_LOOKUP["united kingdom"] == "British"` |
| `test_other_accepted` | `NATIONALITY_LOOKUP["other"] == "Other"` |
| `test_two_letter_code_absent` | `"gb" not in NATIONALITY_LOOKUP` (or maps to nothing useful) |
| `test_all_values_are_title_case` | All values in `NATIONALITY_LOOKUP` match `Title Case` pattern |
| `test_coverage_spot_check` | Spot-check ≥10 distinct nationalities spanning continents |

### `tests/unit/test_wizard_service.py` (EXTEND existing)

| Test | Assertion |
|------|-----------|
| `test_validate_nationality_full_adjective` | `_validate_nationality("German") == "German"` |
| `test_validate_nationality_country_name` | `_validate_nationality("Germany") == "German"` |
| `test_validate_nationality_other` | `_validate_nationality("OTHER") == "Other"` |
| `test_validate_nationality_two_letter_rejected` | `_validate_nationality("DE") is None` |
| `test_validate_nationality_unknown_rejected` | `_validate_nationality("Martian") is None` |
| `test_validate_nationality_strips_whitespace` | `_validate_nationality("  french  ") == "French"` |

### `tests/unit/test_placement_export.py` (NEW)

| Test | Assertion |
|------|-----------|
| `test_export_row_slot_expansion_x` | Driver with slot 2 selected → `slots[2] == True` |
| `test_export_row_slot_expansion_empty` | Driver without slot 3 → `slots[3] == False` |
| `test_export_row_platform_id_included` | `row["platform_id"] == expected_value` |
| `test_export_row_missing_values_empty_string` | Null platform → `row["platform"] == ""` |
| `test_export_preferred_teams_split` | 2 teams → `_1` and `_2` set, `_3` is `""` |
| `test_csv_headers_match_spec` | Generated CSV header row matches ordered spec columns |

### `tests/unit/test_results_bulk.py` (NEW)

| Test | Assertion |
|------|-----------|
| `test_parse_valid_lines` | `_parse_bulk_lines("1, 25\n2, 18")` → `([(1,25),(2,18)], [])` |
| `test_parse_invalid_position_zero` | `"0, 10"` → appears in errors, not in valid pairs |
| `test_parse_invalid_negative_points` | `"1, -5"` → appears in errors |
| `test_parse_blank_lines_skipped` | Lines with only whitespace produce no output either way |
| `test_parse_duplicate_position_last_wins` | `"1, 25\n1, 10"` → valid `(1, 10)`, duplication noted in errors |
| `test_parse_entirely_empty_input` | `""` → `([], [])` — no changes, caller handles message |
| `test_parse_malformed_line` | `"abc"` → in errors |

## Complexity Tracking

> No constitution violations. All work is within ratified scope; no deviations to justify.
