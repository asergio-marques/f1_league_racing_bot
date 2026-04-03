# Data Model: Attendance Module — Initial Setup & Configuration

**Feature**: 031-attendance-module  
**Date**: 2026-04-03

## Entities

---

### `AttendanceConfig`

Per-server configuration for the Attendance module. One row per server. Created atomically
(with defaults) on first enable or re-enable; `module_enabled` set to 0 on disable but row
is retained so re-enable can `INSERT OR REPLACE` cleanly.

| Column | Type | Constraints | Default | Description |
|--------|------|-------------|---------|-------------|
| `server_id` | INTEGER | PRIMARY KEY, FK → `server_configs(server_id)` ON DELETE CASCADE | — | Discord guild ID |
| `module_enabled` | INTEGER | NOT NULL | 0 | 0 = disabled, 1 = enabled |
| `rsvp_notice_days` | INTEGER | NOT NULL | 5 | Days before a round at which the RSVP embed is posted |
| `rsvp_last_notice_hours` | INTEGER | NOT NULL | 24 | Hours before a round for the last-notice ping; 0 = disabled (bypasses `> deadline` check) |
| `rsvp_deadline_hours` | INTEGER | NOT NULL | 2 | Hours before a round when RSVP locks; 0 = lock at round start |
| `no_rsvp_penalty` | INTEGER | NOT NULL | 1 | Attendance points awarded for a no-RSVP infraction |
| `no_attend_penalty` | INTEGER | NOT NULL | 1 | Attendance points awarded for a no-attend infraction |
| `no_show_penalty` | INTEGER | NOT NULL | 1 | Attendance points awarded for a no-show infraction |
| `autoreserve_threshold` | INTEGER | NULL | NULL | Points threshold for autoreserve sanction; NULL = disabled |
| `autosack_threshold` | INTEGER | NULL | NULL | Points threshold for autosack sanction; NULL = disabled |

**Validation invariant** (enforced in application layer, not DB):

> `rsvp_notice_days × 24 > rsvp_last_notice_hours` (always); and when `rsvp_last_notice_hours > 0`: `rsvp_last_notice_hours > rsvp_deadline_hours`.
> A value of 0 for `rsvp_last_notice_hours` is a valid sentinel (last-notice ping disabled); the second comparison is skipped.

---

### `AttendanceDivisionConfig`

Per-division channel assignments for the Attendance module. One row per division. Created
lazily on first channel-set command for the division. All rows for a server are deleted
atomically when the module is disabled.

| Column | Type | Constraints | Default | Description |
|--------|------|-------------|---------|-------------|
| `division_id` | INTEGER | PRIMARY KEY, FK → `divisions(id)` ON DELETE CASCADE | — | Division ID |
| `server_id` | INTEGER | NOT NULL | — | Guild ID (denormalised; NOT a FK — avoids cascade conflict) |
| `rsvp_channel_id` | TEXT | NULL | NULL | Discord channel ID for RSVP embed posting |
| `attendance_channel_id` | TEXT | NULL | NULL | Discord channel ID for attendance sheet posting |

**Note on `server_id`**: stored as a plain integer (not a FK) to allow efficient
`DELETE FROM attendance_division_config WHERE server_id = ?` on module disable without
requiring a join through `divisions`.

---

## Migration SQL

**File**: `src/db/migrations/030_attendance_module.sql`

```sql
-- Migration 030: Attendance module — server config and per-division channel config
-- Adds: attendance_config, attendance_division_config

CREATE TABLE IF NOT EXISTS attendance_config (
    server_id                INTEGER PRIMARY KEY
                                 REFERENCES server_configs(server_id)
                                 ON DELETE CASCADE,
    module_enabled           INTEGER NOT NULL DEFAULT 0,
    rsvp_notice_days         INTEGER NOT NULL DEFAULT 5,
    rsvp_last_notice_hours   INTEGER NOT NULL DEFAULT 24,
    rsvp_deadline_hours      INTEGER NOT NULL DEFAULT 2,
    no_rsvp_penalty          INTEGER NOT NULL DEFAULT 1,
    no_attend_penalty        INTEGER NOT NULL DEFAULT 1,
    no_show_penalty          INTEGER NOT NULL DEFAULT 1,
    autoreserve_threshold    INTEGER,
    autosack_threshold       INTEGER
);

CREATE TABLE IF NOT EXISTS attendance_division_config (
    division_id               INTEGER PRIMARY KEY
                                  REFERENCES divisions(id)
                                  ON DELETE CASCADE,
    server_id                 INTEGER NOT NULL,
    rsvp_channel_id           TEXT,
    attendance_channel_id     TEXT
);
```

---

## State Transitions

### Module Enable

```
Pre-conditions: R&S module enabled, no ACTIVE season.

1. INSERT OR REPLACE INTO attendance_config
       (server_id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours,
        rsvp_deadline_hours, no_rsvp_penalty, no_attend_penalty, no_show_penalty,
        autoreserve_threshold, autosack_threshold)
   VALUES (?, 1, 5, 24, 2, 1, 1, 1, NULL, NULL)
2. INSERT INTO audit_entries (change_type = 'ATTENDANCE_MODULE_ENABLED')
3. post_log confirmation
```

### Module Disable (manual via `/module disable attendance`)

```
1. UPDATE attendance_config SET module_enabled = 0 WHERE server_id = ?
2. DELETE FROM attendance_division_config WHERE server_id = ?
3. (cancel scheduler jobs — no-op this increment)
4. INSERT INTO audit_entries (change_type = 'ATTENDANCE_MODULE_DISABLED')
5. post_log notice
```

### Cascading Disable (triggered by `/module disable results`)

```
Same steps as manual disable but:
- audit change_type = 'ATTENDANCE_MODULE_CASCADE_DISABLED'
- post_log note indicates cascade reason
```

### Re-enable after prior disable

```
INSERT OR REPLACE attendance_config with all defaults, module_enabled = 1
(fresh start — prior division configs already deleted on disable per Principle X rule 6)
```

---

## Relationships

```
server_configs (1)
    └──(1) attendance_config          (FK server_id)

divisions (1)
    └──(0..1) attendance_division_config  (FK division_id, CASCADE DELETE)
```

---

## Deferred Entities (future increments)

The following entities are defined in the constitution (v2.10.0) but are **not** created in
this increment. Their migrations will appear in later feature branches.

| Entity | Description | Increment |
|--------|-------------|-----------|
| `DriverRoundAttendance` | Per-driver per-round RSVP status + attendance status | RSVP automation |
| `AttendancePardon` | Attendance pardon records from penalty wizard | Pardon workflow |
