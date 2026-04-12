# Implementation Plan: Points Configuration XML Import

**Branch**: `034-points-config-bulk-import` | **Date**: 2026-04-12 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/034-points-config-bulk-import/spec.md`

## Summary

Introduce a `/results config xml-import` command that accepts an XML document (via a
Discord modal or an optional file attachment) representing an entire named points
configuration and applies it atomically. The XML schema is a `<config>` root containing
one or more `<session>` blocks. Parsing is handled by `lxml` with entity resolution and
network access disabled. All structural and monotonic validations are performed entirely
in memory before any database write occurs. Absent session types, positions, and
fastest-lap nodes in the XML leave existing database values unchanged. The command
provides a clear, itemised response on success and a full error report on failure.

## Technical Context

**Language/Version**: Python 3.13.2  
**Primary Dependencies**: discord.py ≥ 2.0, aiosqlite ≥ 0.19, lxml (new — XML parsing with entity resolution disabled)  
**Storage**: SQLite via aiosqlite; no schema migrations required  
**Testing**: pytest ≥ 7 with pytest-asyncio (asyncio_mode = auto)  
**Target Platform**: Linux/Windows server process (Discord bot; deployed on Raspberry Pi)  
**Project Type**: Discord bot (slash-command service)  
**Performance Goals**: Discord 3-second acknowledgement window; modal path defers before DB write; file path defers immediately on command invocation  
**Constraints**: Discord modal text field cap of 4 000 characters; file attachment limit of 100 KB enforced in code; Discord slash-command group nesting (one level deep)  
**Scale/Scope**: Single-server deployments; one active season per server; typically 1–4 session types and ≤ 20 positions per season

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I — Trusted Configuration Authority | `/results config xml-import` is guarded by the existing `@admin_only` and `@channel_guard` decorators, consistent with all other `/results config` subcommands. | ✅ PASS |
| II — Multi-Division Isolation | The points config store is server-scoped, not division-scoped. Import writes only to the named config for the invoking server. No cross-server or cross-division mutation. | ✅ PASS |
| III — Resilient Schedule Management | No interaction with scheduling logic. | ✅ N/A |
| IV — Three-Phase Weather Pipeline | No interaction with weather pipeline. | ✅ N/A |
| V — Observability & Change Audit Trail | Success imports produce an audit log entry via `output_router.post_log`, consistent with every other mutating command in `results_cog.py`. | ✅ PASS |
| VI — Incremental Scope Expansion | Command falls within the already-ratified "points configuration management" domain (Principle XII, Points Configuration Store). No new domains introduced. | ✅ PASS |
| VII — Output Channel Discipline | All responses are ephemeral to the interaction channel. The audit log entry is posted to the configured log channel via `output_router`. No ad-hoc channel writes. | ✅ PASS |
| XII — Race Results & Championship Integrity | Writes only to the server config store (`points_config_entries`, `points_config_fl`). Does not touch season snapshots or season points entries. Monotonic ordering validation is enforced on the imported payload per Principle XII's config store rule. Fastest-lap nodes on qualifying sessions are rejected. | ✅ PASS |

**Post-design re-check** (Phase 1 complete — 2026-04-12):

| Principle | Design decision to verify | Post-design result |
|-----------|--------------------------|-------------------|
| I | `@admin_only` + `@channel_guard` present on command handler; modal submission re-checks guild_id | ✅ PASS |
| V | `output_router.post_log` called on success path with user, command, config name, sessions updated | ✅ PASS |
| VII | All error and success replies are `ephemeral=True`; log channel via `output_router` | ✅ PASS |
| XII | `lxml` parser has `resolve_entities=False`; qualifying FL rejected; monotonic check enforced in-memory before any write; atomic transaction | ✅ PASS |

## Project Structure

### Documentation (this feature)

```text
specs/034-points-config-bulk-import/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   └── results.md       ← Phase 1 output (/results config xml-import contract)
└── tasks.md             ← Phase 2 output (/speckit.tasks — not created here)
```

### Source Code (repository root)

```text
src/
├── cogs/
│   └── results_cog.py           ← CHANGE: new XmlImportModal class + xml-import command
├── utils/
│   └── xml_import.py            ← NEW: XmlImportPayload, parse_xml_payload, validate_payload
└── services/
    └── points_config_service.py ← CHANGE: new xml_import_config async function

tests/
└── unit/
    └── test_xml_import.py       ← NEW: parser, validator, and service unit tests

requirements.txt                 ← CHANGE: add lxml
```

#### `src/utils/xml_import.py` — detailed breakdown

| Item | Type | Description |
|------|------|-------------|
| `XmlImportPayload` | `@dataclass` | In-memory representation: `positions: dict[SessionType, dict[int, int]]`, `fastest_laps: dict[SessionType, tuple[int, int \| None]]` |
| `_SESSION_TYPE_BY_LABEL` | module-level dict | Reverse lookup: lowercased label → `SessionType`, built once at import time |
| `parse_xml_payload` | function | Parses XML string with `lxml.etree.XMLParser(resolve_entities=False, no_network=True)`. Returns `(XmlImportPayload, warnings)` on success or raises `XmlImportError` listing all structural failures. |
| `validate_payload` | function | Monotonic ordering check. Returns list of error strings (empty = valid). |
| `XmlImportError` | exception | Carries a `list[str]` of user-facing error messages. |

#### `src/cogs/results_cog.py` — detailed breakdown

| Item | Type | Description |
|------|------|-------------|
| `XmlImportModal` | `discord.ui.Modal` | Single `discord.ui.TextInput` field for XML. `on_submit` calls `_run_xml_import`. |
| `_run_xml_import` | async helper | Shared logic for both modal and file paths: parse → validate → `xml_import_config` → respond + audit log. |
| `xml_import_config` command | `@config_group.command(name="xml-import")` | Parameters: `name: str`, `file: discord.Attachment \| None`. Opens modal if `file` is None; fetches file and calls `_run_xml_import` directly otherwise. Decorated with `@channel_guard`, `@admin_only`, and `_module_gate`. |

#### `src/services/points_config_service.py` — detailed breakdown

| Method | Change |
|--------|--------|
| `xml_import_config(db_path, server_id, config_name, payload)` | **NEW** — single atomic transaction that upserts all position and FL rows from the payload; reuses `_get_config_id` and existing upsert SQL patterns. |

## Complexity Tracking

No constitution violations. No complexity justification required.