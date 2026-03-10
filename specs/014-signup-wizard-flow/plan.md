# Implementation Plan: Signup Wizard and Flow

**Branch**: `014-signup-wizard-flow` | **Date**: 2026-03-10 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/014-signup-wizard-flow/spec.md`

## Summary

Deliver the full signup wizard and onboarding pipeline for the F1 league Discord bot.
FR-001–FR-020 (module lifecycle, configuration, open/close) are partially implemented;
this plan completes them, then implements FR-021–FR-049 (wizard channel management,
sequential parameter collection, driver/wizard state machine, admin review panel,
correction request cycle, and inactivity timeouts) in full.

The approach extends the existing `SignupModuleService`, `SignupCog`, `DriverService`, and
`ModuleCog` with new models (`SignupRecord`, `SignupWizardRecord`, `WizardState`),
a new `WizardService`, an `on_message` listener, Discord `ui.View` panels for the signup
button, admin approve/reject/request-changes, withdrawal button, parameter picker, and
APScheduler jobs for 24-hour inactivity and channel-deletion timeouts.

## Technical Context

**Language/Version**: Python 3.13.2
**Primary Dependencies**: discord.py 2.7.1 (`app_commands`, `ui.View/Button`, `Intents`),
APScheduler 3.10+ (`AsyncIOScheduler`, `SQLAlchemyJobStore`, `DateTrigger`),
aiosqlite 0.19+
**Storage**: SQLite via `aiosqlite`; sequential numbered migration files
**Testing**: pytest + pytest-asyncio
**Target Platform**: Discord bot — Linux/Windows server process
**Project Type**: Discord bot (service daemon)
**Performance Goals**: Support tens-to-hundreds of concurrent driver wizard sessions per
server; each message handler path completes in < 1 s
**Constraints**: `message_content` intent MUST be enabled (bot.py change required);
SQLite WAL already handles concurrent writes; wizard state must survive bot restart
**Scale/Scope**: Typically 20–200 drivers per server; up to ~20 simultaneous wizard
sessions expected

## Constitution Check

| Principle | Requirement | Status for This Feature |
|-----------|-------------|------------------------|
| I — Trusted Configuration | Two access tiers: interaction-role (Tier-2) and Manage-Guild (admin) | ✅ Wizard buttons restricted to admin/Tier-2; `channel_guard` + `admin_only` on config commands; signup button/channel managed separately from the interaction channel |
| II — Multi-Division Isolation | All driver data keyed `(server_id, discord_user_id)` | ✅ No division coupling in signup wizard; signup records stored per-server |
| III — Resilient Schedule | Not applicable to signup | ✅ N/A |
| IV — Three-Phase Weather Pipeline | Not applicable to signup | ✅ N/A |
| V — Observability & Audit Trail | Every driver-state transition and config mutation must produce a timestamped audit entry | ✅ All 49 FRs produce audit entries; FR-005, FR-013, FR-038 explicitly mandate this |
| VI — Incremental Scope Expansion | Signup wizard (item 5) is formally in-scope as of constitution v2.2.0 | ✅ Ratified; driver assignment (item 6) explicitly deferred to next increment |
| VII — Output Channel Discipline | Two new channel categories: general signup channel and per-driver signup channels | ✅ Both categories explicitly documented in spec (FR-002, FR-021); registered via module enable command per Principle VII module-channel clause |
| VIII — Driver Profile Integrity | All transitions must follow the approved table; signup data clearing on Not Signed Up | ✅ All transitions initiated by this feature are in the v2.2.0 table; `AWAITING_CORRECTION_PARAMETER` state and transitions are already ratified; `ban_races_remaining` mechanism unaffected by wizard |
| IX — Team & Division Structural Integrity | Not materially affected by this feature | ✅ Preferred teams list read-only during wizard; no seat mutations |
| X — Modular Feature Architecture | All wizard commands gated behind `signup_module_enabled`; module enable/disable cleanly toggles the entire feature | ✅ FR-004 enforces module gate; FR-003 reverses all config and permissions on disable |
| XI — Signup Wizard Integrity | Signup channel IDs must be persisted throughout; wizard state must survive restart; page-read isolation per driver | ✅ `SignupWizardRecord.signup_channel_id` retained until channel pruned; restart-recovery procedure planned in bot.py |

## Project Structure

### Documentation (this feature)

```text
specs/014-signup-wizard-flow/
├── plan.md              ← This file
├── spec.md              ← Feature specification (committed)
├── research.md          ← Phase 0 output (to be written)
├── data-model.md        ← Phase 1 output (to be written)
├── quickstart.md        ← Phase 1 output (to be written)
├── contracts/           ← Phase 1 output (to be written)
├── checklists/
│   └── requirements.md  ← All items passing (committed)
└── tasks.md             ← Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
src/
├── bot.py                        MODIFY — enable message_content intent; attach wizard_service;
│                                           add on_member_remove handler; add wizard restart-
│                                           recovery on startup
├── models/
│   ├── driver_profile.py         MODIFY — add AWAITING_CORRECTION_PARAMETER to DriverState;
│   │                                       add ban_races_remaining: int = 0 to DriverProfile
│   ├── signup_module.py          MODIFY — add WizardState enum; add SignupRecord dataclass;
│   │                                       add SignupWizardRecord dataclass; add
│   │                                       ConfigSnapshot dataclass (wizard config copy)
│   └── ...                       (unchanged)
├── services/
│   ├── driver_service.py         MODIFY — add AWAITING_CORRECTION_PARAMETER transitions;
│   │                                       add signup-record clearing on NOT_SIGNED_UP
│   ├── signup_module_service.py  MODIFY — add SignupRecord CRUD; add SignupWizardRecord
│   │                                       CRUD; add stable slot_sequence_id retrieval;
│   │                                       add config snapshot capture
│   ├── wizard_service.py         NEW    — wizard state machine; parameter collection steps;
│   │                                       lap-time normalisation; nationality validation;
│   │                                       inactivity-timer arm/cancel; correction cycle
│   └── ...                       (unchanged)
├── cogs/
│   ├── signup_cog.py             MODIFY — add /signup open command; add /signup close
│   │                                       command; add SignupButtonView; add
│   │                                       WithdrawButtonView; add on_message listener
│   │                                       (wizard input dispatch)
│   ├── module_cog.py             MODIFY — extend _enable_signup to set channel permissions
│   │                                       (FR-002); extend _disable_signup to revert
│   │                                       permissions and cancel wizard jobs (FR-003)
│   ├── admin_review_cog.py       NEW    — AdminReviewView (Approve / Request Changes /
│   │                                       Reject); CorrectionParameterView (per-parameter
│   │                                       picker buttons); interaction guards for Tier-2
│   └── ...                       (unchanged)
└── db/
    └── migrations/
        └── 010_signup_wizard.sql NEW    — signup_records table; signup_wizard_records table;
                                           slot_sequence_id stable column on
                                           signup_availability_slots; selected_track_ids
                                           column on signup_module_config

tests/
├── unit/
│   ├── test_wizard_service.py    NEW    — state machine transitions; parameter validation;
│   │                                       lap-time normalisation; inactivity scheduling
│   ├── test_lap_time.py          NEW    — lap-time format parsing, normalisation, edge cases
│   ├── test_driver_state_machine.py
│   │                             EXTEND — add AWAITING_CORRECTION_PARAMETER paths
│   └── ...                       (unchanged)
└── integration/                  (new integration tests as needed per task specification)
```

**Structure Decision**: Single-project structure (`src/` monolith) matching the existing
layout. The `admin_review_cog.py` is introduced as a new file rather than appended to
`signup_cog.py` to keep each cog's responsibility below ~300 lines and separate the
Discord UI layer (`View`/`Button` classes) from the slash-command layer.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `message_content = True` intent required | Wizard collects free-text driver answers inside a private channel; bot cannot read those messages without this privileged intent | The alternative — modal dialogs for every step — hits Discord's 5-component-per-modal limit and cannot accommodate image attachments for lap time proof |
| `slot_sequence_id` stable column on `signup_availability_slots` | FR-010 requires IDs that do not renumber on removal; current implementation computes IDs as rank-on-read | Rank-on-read is cheaper but incoherent with the spec's stable-ID guarantee and breaks historical availability records in SignupRecord |

## Gap Analysis: Already Implemented vs. Needs Building

### Already Implemented (FR-001–FR-020 partial)

| Requirement | Existing Location | Completeness |
|-------------|-------------------|--------------|
| FR-001: `/module enable signup` command exists | `src/cogs/module_cog.py — _enable_signup` | ~80% — missing strict permission lockdown on general signup channel (FR-002) |
| FR-003: `/module disable signup` exists + forced-close helper | `module_cog.py — execute_forced_close`, `_disable_signup` | ~70% — missing permission revert and wizard job cancellation |
| FR-004: `interaction_check` gate in `SignupCog` | `signup_cog.py — interaction_check` | ✅ Complete |
| FR-006: `/signup config nationality` toggle | `signup_cog.py` | ✅ Complete |
| FR-007: `/signup config time-type` toggle | `signup_cog.py` | ✅ Complete |
| FR-008: `/signup config time-image` toggle | `signup_cog.py` | ✅ Complete |
| FR-009: `/signup config time-slot add` | `signup_cog.py — time_slot_add` | ~90% — missing `slot_sequence_id` stable assignment |
| FR-011: Duplicate day+time rejection | `signup_module_service.py — add_slot` | ✅ Complete |
| FR-012: `/signup config time-slot remove` | `signup_cog.py` | ✅ Complete |
| _parse_time utility | `signup_cog.py` | ✅ (24h + 12h AM/PM → HH:MM) |

### Needs Building or Completing

| Requirement Group | What Is Missing |
|-------------------|-----------------|
| FR-002: Channel permission lock on enable | `_enable_signup` must set overwrites: base-role → button-only; Tier-2 + admin → full write; all others → deny view |
| FR-003 completion | `_disable_signup` must cancel all APScheduler wizard jobs for server and revert permission overrides |
| FR-005, FR-013: Audit entries for module + config events | Audit writes missing from some config command paths |
| FR-010: Stable slot IDs | Add `slot_sequence_id INT` (assigned MAX+1 per server on insert) to `signup_availability_slots`; update `AvailabilitySlot` dataclass; update `add_slot` and `get_slots` |
| FR-014–FR-017: `/signup open` command | New slash command; guard (no slots → block); persist selected_track_ids; post signup button message |
| FR-018–FR-020: `/signup close` command + in-progress confirmation | New slash command; `ConfirmCloseView`; forced-close flow for in-progress drivers |
| FR-021–FR-028: Wizard channel lifecycle | Channel create/delete/hold; `on_member_remove` handler; re-engagement deletion and re-create |
| FR-029–FR-033: Sequential parameter collection | `WizardService` state machine; `on_message` dispatcher; config snapshot; 9-step collection with per-step validation |
| FR-034–FR-038: Driver + wizard state transitions | `AWAITING_CORRECTION_PARAMETER` in enum + transitions; signup-data clearing on NOT_SIGNED_UP; `ban_races_remaining` field |
| FR-039–FR-041: Admin review panel | `AdminReviewView`; Approve/Reject/Request Changes buttons; signed-up role grant on approve |
| FR-042–FR-046: Correction request cycle | `CorrectionParameterView`; `AWAITING_CORRECTION_PARAMETER` → `PENDING_DRIVER_CORRECTION` transitions; 5-minute APScheduler or `asyncio.create_task` timeout |
| FR-047–FR-048: 24h inactivity timeouts | APScheduler `DateTrigger` jobs; restart-recovery |
| FR-026, SC-003: 24h channel deletion hold | APScheduler `DateTrigger` job; restart-recovery |
| SC-005: Restart recovery | `bot.py` startup scan of active wizard records; re-arm inactivity jobs; re-register channel message listeners |
| New DB tables | `010_signup_wizard.sql`: `signup_records`, `signup_wizard_records`, stable `slot_sequence_id` column |
| `message_content` intent | `bot.py` line ~27: `intents.message_content = True` |

## Phase-by-Phase Work Plan

### Phase 0 — Research *(complete)*

All source files read; stack confirmed; gap analysis complete (captured above and in
`research.md` to be committed). No blockers.

### Phase 1 — Design Artifacts

Produce the following design outputs before any implementation task begins:

1. **`data-model.md`** — ER diagram (text) and DDL for `signup_records`,
   `signup_wizard_records`; updated `signup_availability_slots` DDL with
   `slot_sequence_id`; updated `signup_module_config` DDL with `selected_track_ids`.
   Include field types, constraints, and indexes.

2. **`quickstart.md`** — Step-by-step "hello world" for a server operator: enable module →
   configure slots → open signups → driver completes wizard → admin approves → confirm
   state. Used as a smoke-test guide for manual QA.

3. **`contracts/wizard_service.md`** — Public interface of `WizardService`: method
   signatures, pre/post-conditions, raised exceptions, and invariants. Agreed before
   implementation begins so that cog code calling the service is stable.

4. **`contracts/admin_review_cog.md`** — Button callback signatures, access-check contract
   (who may press which button), and the protocol for handling race conditions
   (A-004: first action wins).

5. **Re-run Constitution Check** after data model is finalised. Confirm Principle XI
   compliance (wizard channel ID retention until pruned) and Principle VIII compliance
   (all new transitions in the approved table).

### Phase 2 — Task Generation

Run `/speckit.tasks` after Phase 1 design artifacts are committed to produce `tasks.md`.
Tasks must be sequenced by the dependency order below.

**Dependency order for implementation**:

1. Migration 010 (schema) — blocks everything
2. Model updates (`DriverState`, `DriverProfile`, `signup_module.py` additions) — blocks services
3. `driver_service.py` updates (new transitions) — blocks wizard commands
4. `signup_module_service.py` updates (stable slot IDs, `SignupRecord`/`SignupWizardRecord`
   CRUD, config snapshot) — blocks wizard service
5. `wizard_service.py` (NEW) — blocks cog wizard commands and admin review
6. `bot.py` changes (`message_content`, startup recovery, service attach) — blocks on_message
7. `module_cog.py` updates (permission management) — blocks FR-002/FR-003 acceptance tests
8. `signup_cog.py` updates (`/signup open`, `/signup close`, `SignupButtonView`,
   `WithdrawButtonView`, `on_message`) — blocks driver wizard flow
9. `admin_review_cog.py` (NEW) — blocks admin panel acceptance tests
10. Tests (unit + integration) — last, verifying all above

### Phase 3 — Implementation

Implement tasks from `tasks.md` in dependency order. Key implementation notes:

**`010_signup_wizard.sql`**
```sql
-- New stable ID column on existing availability slots table
ALTER TABLE signup_availability_slots ADD COLUMN slot_sequence_id INTEGER;
-- One-time backfill (assign chronological rank to existing rows)
-- signup_records and signup_wizard_records tables (see data-model.md for full DDL)
```

**`WizardState` enum** (new, in `signup_module.py`)
```
UNENGAGED
COLLECTING_NATIONALITY
COLLECTING_PLATFORM
COLLECTING_PLATFORM_ID
COLLECTING_AVAILABILITY
COLLECTING_DRIVER_TYPE
COLLECTING_PREFERRED_TEAMS
COLLECTING_PREFERRED_TEAMMATE
COLLECTING_LAP_TIME          # parameterised by track index
COLLECTING_NOTES
```

**Lap-time normalisation rules** (FR-031 step 8)
- Accept `M:ss.mss` (canonical) and `M:ss:mss` (colon-separated ms)
- Colon-separated ms → dot-separated ms
- Millisecond portion < 3 digits → zero-pad right
- Millisecond portion > 3 digits → round to 3 digits (standard half-up)
- Strip leading/trailing whitespace

**Nationality validation** (FR-031 step 1)
- Normalise to lowercase
- Accept any two-letter ASCII code (matches Discord regional indicator set A–Z)
- Accept the literal string `"other"` (case-insensitive)
- Reject everything else with a re-prompt

**Race condition guard for review panel buttons** (A-004)
- Inside each button callback, load driver state in a DB transaction, confirm still
  `PENDING_ADMIN_APPROVAL`, then act; if state has changed, respond ephemerally with
  "This signup has already been actioned."

**5-minute Awaiting Correction Parameter timeout**
- Prefer `asyncio.create_task` over APScheduler for precision (5 min is short; APScheduler
  misfire grace is 300 s which exactly equals 5 min — unsafe on slow restarts).
- Store the task reference in `WizardService` keyed by `(server_id, discord_user_id)`;
  cancel it when a parameter is selected.

**APScheduler jobs for 24-hour timeouts** (FR-047, FR-048, FR-026)
- Use the existing `_GLOBAL_SERVICE` + module-level callable pattern in
  `scheduler_service.py`.
- Job IDs: `wizard_inactivity_{server_id}_{discord_user_id}` and
  `wizard_channel_delete_{server_id}_{discord_user_id}`.
- On bot restart, `bot.py` startup queries `signup_wizard_records` for rows where
  `wizard_state NOT IN ('UNENGAGED')` and re-arms any jobs whose fire time is in the
  future (by comparing `last_activity_at + 24h` against `datetime.now(UTC)`).

**`on_message` dispatch in `signup_cog.py`**
```python
@commands.Cog.listener()
async def on_message(self, message: discord.Message) -> None:
    if message.author.bot:
        return
    record = await self.bot.wizard_service.get_active_wizard_for_channel(
        message.guild.id, message.channel.id
    )
    if record is None or record.discord_user_id != message.author.id:
        return  # not the active driver's channel, or wrong user
    await self.bot.wizard_service.handle_message(record, message)
```

**Channel permission overrides** (FR-002)
- On module enable: set `signup_channel` overwrites:
  - `base_role` → `PermissionOverwrite(view_channel=True, send_messages=False, use_application_commands=True)`
  - `@everyone` → `PermissionOverwrite(view_channel=False)` (unless server default already hides it)
  - `tier2_role` + `interaction.guild.me` → `PermissionOverwrite(view_channel=True, send_messages=True)`
- On module disable: `channel.set_permissions(target, overwrite=None)` for each overwrite
  previously applied; iterate the stored role IDs from config before deleting them.

### Phase 4 — Testing and Validation

Minimum test coverage required before marking feature complete:

| Test File | Coverage Target |
|-----------|----------------|
| `tests/unit/test_wizard_service.py` | All `WizardState` transitions; invalid inputs rejected; lap-time normalisation; nationality validation; config snapshot isolation; inactivity timer scheduling/cancellation |
| `tests/unit/test_lap_time.py` | All format variants; zero-padding; rounding; colon-vs-dot ms; strip whitespace; reject invalid format |
| `tests/unit/test_driver_state_machine.py` (extend) | `AWAITING_CORRECTION_PARAMETER` transitions; correction timeout auto-return; wizard data clearing on NOT_SIGNED_UP |
| `tests/unit/test_signup_module_service.py` (extend) | Stable `slot_sequence_id` assignment; removed slots do not renumber; `SignupRecord` CRUD; `SignupWizardRecord` CRUD; config snapshot |
| Manual QA via quickstart.md | Full happy-path wizard end-to-end; rejection path; correction path; inactivity timeout |

## Known Incoherencies / Design Decisions

| Item | Status | Resolution |
|------|--------|------------|
| `slot_id` computed-on-read (current) vs. stable ID required by FR-010 | **RESOLVED** in this plan | Add `slot_sequence_id INT` column assigned `MAX(slot_sequence_id) + 1` per server on insert. Existing rows backfilled with chronological rank. `AvailabilitySlot.slot_id` field renamed to `slot_sequence_id` in the dataclass. |
| `AWAITING_CORRECTION_PARAMETER` missing from `DriverState` enum | **RESOLVED** in this plan | Add to enum; add transitions to `ALLOWED_TRANSITIONS` in `driver_service.py`; matches constitution v2.2.0 Principle VIII table exactly. |
| `ban_races_remaining` missing from `DriverProfile` | **RESOLVED** in this plan | Add `ban_races_remaining: int = 0` field; used by future ban-management feature. No new DB column needed now — add DDL in migration 010. |
| `message_content` intent disabled | **RESOLVED** in this plan | Change `intents.message_content = False` → `True` in `bot.py`. This is a privileged intent requiring Discord Developer Portal approval for verified bots; acceptable for a private league bot. |
| Forced-close in `module_cog.execute_forced_close` does not cancel wizard APScheduler jobs | **RESOLVED** in this plan | Extend `execute_forced_close` to also cancel `wizard_inactivity_*` and `wizard_channel_delete_*` jobs for all affected drivers in the server. |
