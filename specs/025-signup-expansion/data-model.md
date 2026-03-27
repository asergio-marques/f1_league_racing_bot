# Data Model: Signup Module Expansion (025-signup-expansion)

## Entities Modified

### SignupModuleConfig  *(amended — `src/models/signup_module.py`)*

Existing entity. One new field added.

| Field | Type | Default | Notes |
|---|---|---|---|
| server_id | int | — | PK |
| signup_channel_id | int | — | (existing) |
| base_role_id | int | — | (existing) |
| signed_up_role_id | int | — | (existing) |
| signups_open | bool | False | (existing) |
| signup_button_message_id | int \| None | None | (existing) |
| selected_tracks | list[str] | [] | (existing) |
| signup_closed_message_id | int \| None | None | (existing) |
| **close_at** | **str \| None** | **None** | **NEW — ISO 8601 UTC; set when signups opened with timer; cleared on close** |

**Storage**: `signup_module_config` table. DB migration adds `close_at TEXT` nullable column.

**Invariants**:
- `close_at` is non-null only while `signups_open = 1` AND a close timer is active.
- `close_at` is always a future timestamp at the time it is persisted. It MUST be cleared (set to NULL) when signups are closed by any path (manual close, auto-close, module disable).
- While `close_at` is non-null, the `/signup close` command MUST be blocked.

---

### SignupModuleConfig — nullable fields during decoupled enable

After removing inline parameters from `/module enable signup`, the three configuration fields become independently settable and may be absent:

| Field | Was always set? | After change |
|---|---|---|
| signup_channel_id | Yes (required on enable) | Nullable — must be set via `/signup channel` |
| base_role_id | Yes (required on enable) | Nullable — must be set via `/signup base-role` |
| signed_up_role_id | Yes (required on enable) | Nullable — must be set via `/signup complete-role` |

**DB migration**: ALTER all three columns to remove `NOT NULL` constraint and default to NULL. Any future query using these fields must guard against NULL.

**Season approval gate**: If signup module is enabled AND any of the three fields is NULL, season approval MUST be blocked with a per-field diagnostic.

---

## New Entities

### SignupDivisionConfig  *(new — `src/models/signup_module.py`)*

Per-division configuration owned by the signup module. Holds the optional lineup announcement channel.

| Field | Type | Default | Notes |
|---|---|---|---|
| id | int | autoincrement | Internal PK |
| server_id | int | — | FK → server_configs(server_id) ON DELETE CASCADE |
| division_id | int | — | FK → divisions(id) ON DELETE CASCADE |
| lineup_channel_id | int \| None | None | Discord channel ID; NULL = no lineup posting |

**Uniqueness**: `(server_id, division_id)` — at most one config record per division per server.

**Lifecycle**:
- Created lazily on first `/division lineup-channel` invocation for that division.
- If absent for a division, no lineup post is made and no error is raised.
- Deleted via cascade when the parent Division or server_config is removed.
- NOT deleted when the signup module is disabled (cleared per Principle X rule 3 — only live/scheduled artifacts are removed, config records are retained for re-enable). However spec FR-020 overrides: the command MUST be blocked when the module is disabled, and the channel value is inert while the module is off.

**Invariants**:
- `lineup_channel_id` is the only meaningful field; no other per-division signup config currently exists.
- If `lineup_channel_id` is set and the channel is deleted from Discord, the DB record is NOT automatically cleared; the bot must handle the resulting `NotFound` gracefully, log the error, and continue.

---

## DB Migration: `024_signup_expansion.sql`

```sql
-- 1. Add close_at to signup_module_config
ALTER TABLE signup_module_config
    ADD COLUMN close_at TEXT;

-- 2. Make channel and role fields nullable (SQLite does not support ALTER COLUMN;
--    must recreate the table)
-- NOTE: full table recreation required; done as a defensive migration.
-- See migration file for full RENAME → CREATE NEW → INSERT → DROP OLD sequence.

-- 3. Create signup_division_config table
CREATE TABLE IF NOT EXISTS signup_division_config (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id       INTEGER NOT NULL,
    division_id     INTEGER NOT NULL,
    lineup_channel_id INTEGER,
    UNIQUE(server_id, division_id),
    FOREIGN KEY (server_id)   REFERENCES server_configs(server_id) ON DELETE CASCADE,
    FOREIGN KEY (division_id) REFERENCES divisions(id) ON DELETE CASCADE
);
```

---

## State Transitions Affected

### Close timer → driver state

When the `signup_close_timer_{server_id}` APScheduler job fires:

```
PENDING_SIGNUP_COMPLETION  →  NOT_SIGNED_UP  (wizard channel frozen, deleted after 24h hold)
PENDING_DRIVER_CORRECTION  →  NOT_SIGNED_UP  (wizard channel frozen, deleted after 24h hold)
PENDING_ADMIN_APPROVAL     →  NOT_SIGNED_UP  (wizard channel frozen, deleted after 24h hold)
AWAITING_CORRECTION_PARAM  →  NOT_SIGNED_UP  (transient state; same treatment)
UNASSIGNED                 →  (unchanged)
ASSIGNED                   →  (unchanged)
```

This is identical to the `execute_forced_close()` path already used for manual forced-close.

### Assignment → lineup check

After every successful `placement_service` call for assign / unassign / sack in a division:
1. Query: are there any `UNASSIGNED` drivers who have **ever** been placed in or are waiting for that division?
2. Query: does the division have at least one `ASSIGNED` driver?
3. If both are satisfied and a `lineup_channel_id` is configured: post lineup.

Specifically: check `COUNT(*) FROM driver_profiles WHERE current_state = 'UNASSIGNED' AND server_id = ?` combined with a join to determine if they are "relevant" to the given division. The simplest correct approach: after assign/unassign/sack, check if any driver in the server is still in UNASSIGNED state AND has a `season_assignment` row targeting this division (or: check all UNASSIGNED drivers server-wide since any of them might eventually be assigned to any division).

**Decision (from R-004)**: After an assign/unassign/sack in division D, query whether any driver_profile in UNASSIGNED state has a `season_assignment` row FOR division D, or has no assignment row but is UNASSIGNED universally. Since "unassigned means not yet placed anywhere", the correct check is simpler: post the lineup when there are ZERO drivers in UNASSIGNED state server-wide AND at least one driver is assigned to division D. This is consistent with the spec's intent ("once there are no unassigned drivers").

---

## Validation Rules

| Rule | Location | Enforcement |
|---|---|---|
| `close_at` must be in the future | `/signup open` (cog) | Reject with descriptive error |
| `close_at` must be cleared on close | `execute_forced_close()` / `set_window_closed()` | Clear in same DB transaction as window close |
| `/signup close` blocked when `close_at` is set | `/signup close` (cog) | Return ephemeral error with scheduled time |
| All 3 config fields required for season approval | Season approval service | Per-field diagnostic if any null while module enabled |
| Lineup post only when `lineup_channel_id` is set | `placement_service` / post-assign hook | Guard before posting |
| `/division lineup-channel` blocked when module disabled | `/division lineup-channel` (cog) | `interaction_check()` or inline guard |
