# Implementation Plan: Command Streamlining & Quality of Life Improvements

**Branch**: `010-command-streamlining` | **Date**: 2026-03-05 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/010-command-streamlining/spec.md`

## Summary

Streamline season and round configuration commands by removing redundant parameters,
auto-deriving round numbers from scheduled datetimes, introducing division/round lifecycle
management commands (duplicate, delete, rename, cancel), and migrating all affected
commands to the `/domain action` subcommand-group convention. Additionally restricts
`/test-mode` commands to server administrators. README is updated as a utilization guide
throughout.

No new technologies are introduced. All work is additive changes to existing Python/
discord.py/aiosqlite patterns already established in the codebase.

## Technical Context

**Language/Version**: Python 3.13.2  
**Primary Dependencies**: discord.py ≥ 2.0, APScheduler ≥ 3.10, aiosqlite ≥ 0.19, SQLAlchemy, python-dotenv  
**Storage**: SQLite via aiosqlite; schema migrations auto-applied on startup  
**Testing**: pytest ≥ 7 with pytest-asyncio (asyncio_mode = auto)  
**Target Platform**: Linux/Windows server process (Discord bot)  
**Project Type**: Discord bot (slash-command service)  
**Performance Goals**: Discord 3-second acknowledgement window; all commands use deferred responses for DB work  
**Constraints**: Discord slash-command group nesting limit (one level deep); 25-parameter limit per command  
**Scale/Scope**: Single-server deployments; one active season per server at a time

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I — Trusted Configuration Authority | New commands (`/division duplicate`, `/division delete`, `/division rename`, `/round delete`) guarded by trusted-admin check. `/season cancel`, `/division cancel`, `/round cancel` use explicit confirmation. `/season cancel` and `/test-mode` gated at server-administrator level. | ✅ PASS |
| II — Multi-Division Isolation | Division duplication, deletion, rename, and cancellation all operate on a single named division; no cross-division mutations. Round operations are scoped to division + round identifier. | ✅ PASS |
| III — Resilient Schedule Management | Round auto-numbering and renumbering on insert/amend preserves round identity. Cancellation retains history; `/season cancel` deletes data identically to natural season end. | ✅ PASS |
| IV — Three-Phase Weather Pipeline | No changes to phase execution logic. Cancellation of a round or division during an active season does not retroactively alter phase results — already-posted forecasts remain visible. | ✅ PASS |
| V — Observability & Change Audit Trail | All new mutations (duplicate, delete, rename, cancel) must produce audit log entries. Confirmation responses show full updated state (round list / division list). | ✅ PASS — audit entries required in implementation |
| VI — Simplicity & Focused Scope | All new commands fall within the defined scope of season/division/schedule management. No new domains introduced. | ✅ PASS |
| VII — Output Channel Discipline | `/division cancel`, `/round cancel`, `/season cancel` post to division forecast channel(s). No posting to the interaction channel. | ✅ PASS |
| **v1.2.0 Bot Behavior Standards** | All new and migrated commands use `/domain action` subcommand-group form. Hyphenated top-level commands in affected domains are retired. Single-interaction preference maintained. | ✅ PASS |

**Post-design re-check** (Phase 1 complete — 2026-03-05):

| Principle | Design decision to verify | Post-design result |
|-----------|--------------------------|-------------------|
| I | `/season cancel` and `/test-mode` require `manage_guild`; new setup commands require trusted-admin role | ✅ PASS — contracts specify correct access levels per command |
| II | `duplicate_division`, `cancel_division`, `cancel_round` all scoped to a single division_id/round_id | ✅ PASS |
| III | `renumber_rounds` is atomic; `delete_season` follows FK-safe delete ordering from reset_service | ✅ PASS |
| IV | No changes to phase execution; `rounds.status = CANCELLED` must be honoured by scheduler skip (implementation note) | ✅ PASS — data-model confirms status field added |
| V | All new mutations require audit entries (cancel_division, cancel_round noted in service method table) | ✅ PASS — implementation must write audit entries |
| VI | No new scope domains introduced | ✅ PASS |
| VII | Cancel notices post to forecast channels only; no interaction-channel posting | ✅ PASS — contracts specify forecast channel target |
| v1.2.0 BBS | All contracts use `/season action`, `/division action`, `/round action` form | ✅ PASS |

## Project Structure

### Documentation (this feature)

```text
specs/010-command-streamlining/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/           ← Phase 1 output (slash-command signatures)
│   ├── season.md
│   ├── division.md
│   └── round.md
└── tasks.md             ← Phase 2 output (/speckit.tasks — not created here)
```

### Source Code (repository root)

```text
src/
├── cogs/
│   ├── season_cog.py          ← MAJOR CHANGE (see breakdown below)
│   ├── amendment_cog.py       ← CHANGE: subgroup migration + renumber on amend
│   ├── test_mode_cog.py       ← CHANGE: admin_only gate on all three subcommands
│   └── (no new cog files; new commands added to existing cogs by domain)
├── models/
│   ├── division.py            ← CHANGE: add status field (ACTIVE / CANCELLED)
│   └── round.py               ← CHANGE: add status field (ACTIVE / CANCELLED)
├── services/
│   └── season_service.py      ← CHANGE: new methods (see breakdown below)
└── db/
    └── migrations/
        └── 007_cancellation_status.sql   ← NEW: status columns on divisions + rounds

tests/
└── unit/
    ├── test_season_cog_setup.py           ← NEW: parameterless setup, conflict guard
    ├── test_round_autonumber.py           ← NEW: insertion ordering, renumber on amend
    ├── test_division_duplicate.py         ← NEW: round copy + datetime offset
    ├── test_division_delete.py            ← NEW: cascade delete + feedback
    ├── test_division_rename.py            ← NEW: rename validation
    ├── test_round_delete.py               ← NEW: delete + renumber
    ├── test_cancellation.py               ← NEW: division/round/season cancel flows
    └── test_test_mode_access.py           ← NEW: admin-only gate verification

README.md                                  ← CHANGE: full slash-command section rewrite
```

#### `src/cogs/season_cog.py` — detailed breakdown

| Area | Change |
|------|--------|
| `SeasonCog` top-level commands → `season` group | Migrate `season_setup`, `season_review`, `season_approve`, `season_status` to subcommands of an `app_commands.Group(name="season")` |
| `division_add` → `division` group | Move `division_add` into a new `app_commands.Group(name="division")`; add `duplicate`, `delete`, `rename`, `cancel` as subcommands |
| `round_add` → `round` group | Move `round_add` into a new `app_commands.Group(name="round")`; add `delete`, `cancel` as subcommands; migrate `round_amend` from `amendment_cog.py` into this group |
| `/season setup` signature | Remove `start_date` and `num_divisions` parameters; remove division-slot pre-allocation; remove `PendingConfig.start_date`; pass `None` (or today) to `season_service.create_season` |
| `/round add` signature | Remove `round_number` parameter; auto-derive position from `scheduled_at` sorted against existing division rounds; replace `DuplicateRoundView` conflict dialog with automatic chronological insertion; respond with assigned round number and full round list |
| Post-modification feedback | All division mutations respond with full division list; all round mutations respond with full round list for that division |
| `/division duplicate` | New subcommand; copy all rounds with datetime offset (days: int, hours: float); reject if season not SETUP; warn on past datetimes or tied datetimes |
| `/division delete` | New subcommand; remove division + rounds; reject if season not SETUP |
| `/division rename` | New subcommand; rename only; reject if season not SETUP, name not found, or name collision |
| `/division cancel` | New subcommand; explicit confirmation; post to forecast channel (no role ping); mark division CANCELLED; reject if season not ACTIVE; reject if already CANCELLED |
| `/round delete` | New subcommand; remove round + renumber; reject if season not SETUP |
| `/round cancel` | New subcommand; explicit confirmation; post to forecast channel (no role ping); mark round CANCELLED; accept even if phases started; reject if season not ACTIVE; reject if already CANCELLED |
| `/season cancel` | New subcommand; server-administrator gate; explicit confirmation; post to each ACTIVE division forecast channel (no role pings); delete season + all data (identical to natural season end); reject if season not ACTIVE |

#### `src/services/season_service.py` — new/changed methods

| Method | Change |
|--------|--------|
| `create_season` | Remove `start_date` parameter (or make optional/default to today for backward compat with reset flow); remove division pre-allocation |
| `get_division_rounds` (existing) | Used by auto-numbering; no signature change needed |
| `renumber_rounds(division_id)` | **NEW** — rewrite all round numbers for a division in ascending `scheduled_at` order atomically |
| `duplicate_division(...)` | **NEW** — copy division row + all round rows with shifted datetimes |
| `delete_division(division_id)` | **NEW** — cascade delete division + rounds (setup only) |
| `rename_division(division_id, new_name)` | **NEW** — single-field update |
| `cancel_division(division_id)` | **NEW** — set `divisions.status = 'CANCELLED'` + write audit entry |
| `delete_round(round_id)` | **NEW** — delete single round then call `renumber_rounds` |
| `cancel_round(round_id)` | **NEW** — set `rounds.status = 'CANCELLED'` + write audit entry |
| `delete_season(season_id)` | **NEW** — delete all season data (same cascade as `reset_service` but scoped to one season_id); re-usable by `/season cancel` |

## Complexity Tracking

No constitution violations. No complexity justification required.
