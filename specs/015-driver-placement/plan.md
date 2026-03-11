# Implementation Plan: Driver Placement and Team Role Configuration

**Branch**: `015-driver-placement` | **Date**: 2026-03-11 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/015-driver-placement/spec.md`

## Summary

Introduce the driver placement workflow: a seeded unassigned listing, three new `/driver` sub-commands (`assign`, `unassign`, `sack`), and an admin-only `/team role set` command that maps a team name to a Discord role. On placement, the target driver receives their division's role and the team's configured Discord role; on unassign/sack the corresponding role(s) are revoked. Seeding is based on `total_lap_ms` written once at signup approval so the listing is instant and stable. All data is persisted in SQLite via raw aiosqlite SQL; a new migration (011) adds the `team_role_configs` table and nullable additive columns to `driver_season_assignments` and `signup_records`. All placement logic lives in a new `PlacementService`.

## Technical Context

**Language/Version**: Python 3.13.2  
**Primary Dependencies**: discord.py 2.7.1, aiosqlite 0.22.1, APScheduler 3.11.2, python-dotenv  
**Storage**: SQLite (raw SQL via aiosqlite; no ORM)  
**Testing**: pytest + pytest-asyncio  
**Target Platform**: Discord bot server process (Linux or Windows)  
**Project Type**: Discord bot (cog-based architecture)  
**Performance Goals**: N/A — interactive command responses; no throughput requirement  
**Constraints**: Discord rate limits on role mutations; `discord.HTTPException` must be handled gracefully (fail-soft on role grant/revoke without aborting DB transaction); migration must be idempotent  
**Scale/Scope**: Single Discord guild; ~10–100 drivers per season per division

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Applicability | Status |
|-----------|---------------|--------|
| I — Trusted Configuration Authority | `/driver assign/unassign/sack` and `/team role set` are tier-2 admin commands | ✅ PASS — commands gated behind trusted-role check |
| II — Multi-Division Isolation | Assignment queries are scoped per `server_id` / `division_id`; `team_role_configs` keyed on `server_id` | ✅ PASS |
| III — Resilient Schedule Management | No schedule mutations in this feature | N/A |
| IV — Three-Phase Weather Pipeline | No weather pipeline involvement | N/A |
| V — Observability & Change Audit Trail | Every assign/unassign/sack and role-config mutation must produce an audit log entry | ✅ PASS — `PlacementService` writes audit events for all mutations |
| VI — Incremental Scope Expansion | Domain 6 (driver assignment & placement) is ratified in-scope as of v2.2.0 | ✅ PASS |
| VII — Output Channel Discipline | No new channels introduced; all responses are ephemeral interaction replies | ✅ PASS |
| VIII — Driver Profile Integrity | `Unassigned → Assigned` (assign), `Assigned → Unassigned` (unassign), `Unassigned/Assigned → Not Signed Up` (sack) — all are permitted transitions in the state machine | ✅ PASS — transitions enforced via `DriverService.transition()` |
| IX — Team & Division Structural Integrity | Seat lookup enforced; Reserve team rules preserved; division isolation maintained | ✅ PASS |
| X — Modular Feature Architecture | Driver placement is a foundational feature (always active); no module guard required | ✅ PASS |
| XI — Signup Wizard Integrity | `total_lap_ms` computed and stored at approval in `wizard_service.py`; no wizard flow altered | ✅ PASS — additive touch only |
| XII — Race Results & Championship Integrity | Not in scope for this feature | N/A |

**Post-design re-check**: Phase 1 design introduces only additive schema changes (nullable columns + new table) and a new service. No principle violations. No entries required in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/015-driver-placement/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── slash-commands.md  # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks — not yet created)
```

### Source Code (repository root)

```text
src/
├── cogs/
│   ├── driver_cog.py        # MODIFIED — add assign, unassign, sack subcommands
│   ├── signup_cog.py        # MODIFIED — add unassigned listing subcommand
│   └── team_cog.py          # MODIFIED — add role set subcommand
├── db/
│   └── migrations/
│       └── 011_driver_placement.sql   # NEW — team_role_configs table + additive columns
├── models/
│   ├── driver_profile.py    # MODIFIED — add team_seat_id: int | None to DriverSeasonAssignment
│   ├── signup_module.py     # MODIFIED — add total_lap_ms: int | None to SignupRecord
│   └── team.py              # MODIFIED — add TeamRoleConfig dataclass
└── services/
    ├── placement_service.py # NEW — all placement + role-revocation logic
    └── wizard_service.py    # MODIFIED — compute & store total_lap_ms at signup approval

tests/
├── unit/
│   └── services/
│       ├── test_placement_service.py  # NEW
│       └── test_wizard_service.py     # MODIFIED — cover total_lap_ms path
└── integration/
    └── test_placement_flows.py        # NEW — assign/unassign/sack end-to-end
```

**Structure Decision**: Single project (Option 1). The feature touches three existing cogs, three existing models, one existing service, and adds one new service and one migration. No new top-level project or subpackage is required.

## Phase 0: Research

**Output**: [research.md](research.md) — all unknowns resolved.

| ID | Question | Decision |
|----|----------|----------|
| R-001 | Discord role grant/revoke API | `guild.fetch_member()` + `member.add_roles()` / `member.remove_roles()`; `discord.HTTPException` caught and logged (fail-soft) |
| R-002 | When to compute `total_lap_ms` | Once at signup approval; stored on `signup_records.total_lap_ms`; NULL = no times submitted |
| R-003 | `team_seat_id` migration strategy | Additive nullable column on `driver_season_assignments` via migration 011 |
| R-004 | Team-role config storage | New table `team_role_configs(server_id, team_name, role_id)`; INSERT OR REPLACE upsert |
| R-005 | Service boundary | New `placement_service.py`; no logic in cogs; avoids bloating `driver_service.py` |
| R-006 | Command routing | `/team role set` → TeamCog; `/signup unassigned` → SignupCog; `/driver assign/unassign/sack` → DriverCog |
| R-007 | Division role source | `divisions.mention_role_id` is the division's Discord role (already persisted); no new column needed |

## Phase 1: Design

### Data Model

**Output**: [data-model.md](data-model.md)

Key schema changes (migration `011_driver_placement.sql`):

- `signup_records`: ADD COLUMN `total_lap_ms INTEGER` (nullable)
- `driver_season_assignments`: ADD COLUMN `team_seat_id INTEGER REFERENCES team_seats(id)` (nullable)
- NEW TABLE `team_role_configs(id, server_id, team_name, role_id, updated_at)` with `UNIQUE(server_id, team_name)`

New Python types:
- `TeamRoleConfig` dataclass → `src/models/team.py`
- `DriverSeasonAssignment.team_seat_id: int | None` (field added)
- `SignupRecord.total_lap_ms: int | None = None` (field added)

### Interface Contracts

**Output**: [contracts/slash-commands.md](contracts/slash-commands.md)

| Command | Access | Summary |
|---------|--------|---------|
| `/team role set <team_name> <role>` | Tier-2 admin | Upserts a team→Discord role mapping; blocked during ACTIVE season |
| `/signup unassigned [page]` | Tier-2 admin | Lists approved drivers with no seat, seeded by `total_lap_ms` ASC |
| `/driver assign <user> <division> <team> <seat_number>` | Tier-2 admin | Places driver in seat; grants division + team roles |
| `/driver unassign <user>` | Tier-2 admin | Removes driver from seat; revokes team role; retains division role |
| `/driver sack <user>` | Tier-2 admin | Transitions driver to Not Signed Up; revokes all placement roles |

### Quickstart

**Output**: [quickstart.md](quickstart.md) — local setup, migration verification, command smoke-test guide.
