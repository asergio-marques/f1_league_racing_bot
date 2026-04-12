# Data Model: Points Configuration XML Import

## Schema Changes

**None.** This feature introduces no new database tables, columns, or migrations.
All existing tables (`points_config_store`, `points_config_entries`, `points_config_fl`)
are written to using existing service functions (`set_session_points`, `set_fl_bonus`,
`set_fl_position_limit`).

---

## New In-Memory Types

Two new Python types are introduced in the service/utility layer. Neither is persisted.

### `XmlImportPayload`

Represents the fully-parsed, pre-validation payload extracted from a submitted XML
document. Only exists in memory during a single import operation.

| Field | Type | Description |
|-------|------|-------------|
| `positions` | `dict[SessionType, dict[int, int]]` | Maps session type → (position id → points). Only session types present in the XML are included. |
| `fastest_laps` | `dict[SessionType, tuple[int, int \| None]]` | Maps session type → (fl_points, fl_position_limit \| None). Only sessions whose XML block contained a `<fastest-lap>` node are included. |

#### Merge semantics

- A `SessionType` key absent from `positions` → that session's position rows are **not touched**.
- A `SessionType` key absent from `fastest_laps` → that session's FL row is **not touched**.
- A position absent from `positions[session_type]` → that position row is **not touched**.
- `fl_position_limit = None` in `fastest_laps` → the `<fastest-lap>` node had no `limit` attribute; the existing `fl_position_limit` value in the database is preserved (via the existing `set_fl_bonus` preserve-logic).

---

## Entities — Reference (unchanged)

These entities already exist; shown for orientation.

### `points_config_store`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto |
| `server_id` | INTEGER | FK to server scope |
| `config_name` | TEXT | Unique per server |

### `points_config_entries`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto |
| `config_id` | INTEGER | FK → `points_config_store.id` |
| `session_type` | TEXT | One of four `SessionType` values |
| `position` | INTEGER | 1-indexed finishing position |
| `points` | INTEGER | Points awarded (≥ 0) |

Unique constraint on `(config_id, session_type, position)`.
Import uses `INSERT … ON CONFLICT DO UPDATE SET points = excluded.points` (existing behaviour).

### `points_config_fl`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto |
| `config_id` | INTEGER | FK → `points_config_store.id` |
| `session_type` | TEXT | Race session types only |
| `fl_points` | INTEGER | Fastest-lap bonus (≥ 0) |
| `fl_position_limit` | INTEGER \| NULL | Position eligibility cap (≥ 1 or NULL = no limit) |

Unique constraint on `(config_id, session_type)`.
Import uses existing `set_fl_bonus` / `set_fl_position_limit` helpers which preserve the
other field when only one is being set.

---

## New Service Function

### `xml_import_config` (in `points_config_service.py`)

```
async def xml_import_config(
    db_path: str,
    server_id: int,
    config_name: str,
    payload: XmlImportPayload,
) -> None
```

**Preconditions**: `payload` has already passed all structural and monotonic validation.
Raises `ConfigNotFoundError` if the named config does not exist.

**Behaviour**:
1. Opens a single DB connection.
2. Resolves `config_id` via `_get_config_id`.
3. For each `(session_type, positions)` in `payload.positions`, upserts each `(position, points)` row.
4. For each `(session_type, (fl_pts, fl_limit))` in `payload.fastest_laps`, calls the
   appropriate upsert with field-preservation semantics (`set_fl_bonus` / `set_fl_position_limit`
   pattern — if limit is `None`, only `fl_points` is updated; if both are present, both are set).
5. Commits atomically. Any exception causes automatic rollback (context manager).

---

## New Parser/Validator Module

### `src/utils/xml_import.py` (NEW)

A pure-function module with no discord or DB imports. Contains:

| Function | Signature | Description |
|----------|-----------|-------------|
| `parse_xml_payload` | `(xml_text: str) → XmlImportPayload \| list[str]` | Parses the XML string. Returns `XmlImportPayload` on success or a list of error strings on failure. |
| `validate_payload` | `(payload: XmlImportPayload) → list[str]` | Runs monotonic ordering checks. Returns empty list if valid. |

`parse_xml_payload` handles: lxml parse errors, unknown session types, missing `<type>`
elements, malformed `id`/`limit` attributes, negative/zero values, duplicate position
warnings (collected but non-fatal), and the `<fastest-lap>` on qualifying rejection.

Both functions are synchronous and stateless — safe to unit-test without any async
infrastructure.
