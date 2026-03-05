# Data Model: Command Streamlining & QoL Improvements

## Schema Changes

### New migration: `007_cancellation_status.sql`

Two new `status` columns are added — one on `divisions`, one on `rounds`.

```sql
-- 007_cancellation_status.sql

ALTER TABLE divisions ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK(status IN ('ACTIVE', 'CANCELLED'));

ALTER TABLE rounds ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK(status IN ('ACTIVE', 'CANCELLED'));
```

#### `divisions.status`

| Value | Meaning |
|-------|---------|
| `ACTIVE` | Division is operational; phases run normally |
| `CANCELLED` | Division has been cancelled mid-season; scheduler skips all rounds in this division; no further phase output is produced |

#### `rounds.status`

| Value | Meaning |
|-------|---------|
| `ACTIVE` | Round is operational; phases run on schedule |
| `CANCELLED` | Round has been cancelled; scheduler skips this round; no further phase output is produced |

---

### Removed parameter: `seasons.start_date`

The `/season setup` command currently writes a `start_date` to `PendingConfig` which is
passed to `season_service.create_season(server_id, start_date)`. This parameter is
removed from the command interface.

`SeasonService.create_season` signature changes from:
```
create_season(server_id: int, start_date: date) → Season
```
to:
```
create_season(server_id: int) → Season
```

The `seasons` table already stores `start_date` as a column. The column is retained in
the database schema for backward compatibility with any existing rows, but the value is
no longer user-supplied — it defaults to the current date at season creation time
(handled inside `create_season`).

---

### Auto-derived `round_number`

`rounds.round_number` remains an `INTEGER NOT NULL` column. The value is no longer
user-supplied via the command. It is computed and written by the new
`SeasonService.renumber_rounds(division_id)` function, which is called after every
operation that changes the round set or a round's `scheduled_at` for a division.

**Ordering rule**: rounds within a division are numbered 1…N in ascending `scheduled_at`
order. Tied `scheduled_at` values within the same division are an error condition; the
spec requires a warning to the user (duplication path only).

---

## Entity Reference (updated)

### Season

| Field | Type | Change |
|-------|------|--------|
| id | INTEGER PK | — |
| server_id | INTEGER | — |
| status | TEXT | — (`SETUP` / `ACTIVE` / `COMPLETED`; no `CANCELLED`) |
| start_date | TEXT (ISO date) | No longer user-supplied; defaults to today at creation |

### Division

| Field | Type | Change |
|-------|------|--------|
| id | INTEGER PK | — |
| season_id | INTEGER FK | — |
| name | TEXT | — |
| role_id | INTEGER | — |
| forecast_channel_id | INTEGER | — |
| **status** | **TEXT** | **NEW** (`ACTIVE` / `CANCELLED`, default `ACTIVE`) |

### Round

| Field | Type | Change |
|-------|------|--------|
| id | INTEGER PK | — |
| division_id | INTEGER FK | — |
| round_number | INTEGER | No longer user-supplied; maintained by `renumber_rounds` |
| format | TEXT | — |
| track | TEXT (nullable) | — |
| scheduled_at | TEXT (ISO datetime) | — |
| phase1_done | INTEGER | — |
| phase2_done | INTEGER | — |
| phase3_done | INTEGER | — |
| **status** | **TEXT** | **NEW** (`ACTIVE` / `CANCELLED`, default `ACTIVE`) |

---

## New Service Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_season` | `(server_id: int) → Season` | Create season in SETUP; start_date = today |
| `renumber_rounds` | `(division_id: int) → None` | Rewrite all round_number values for a division in ascending scheduled_at order |
| `duplicate_division` | `(division_id: int, name: str, role_id: int, forecast_channel_id: int, day_offset: int, hour_offset: float) → Division` | Copy division + all rounds with shifted datetimes |
| `delete_division` | `(division_id: int) → None` | Cascade-delete division and all its rounds (SETUP only, enforced by caller) |
| `rename_division` | `(division_id: int, new_name: str) → None` | Update division name |
| `cancel_division` | `(division_id: int) → None` | Set status = CANCELLED; write audit entry |
| `delete_round` | `(round_id: int) → None` | Delete round; call renumber_rounds for its division |
| `cancel_round` | `(round_id: int) → None` | Set status = CANCELLED; write audit entry |
| `delete_season` | `(season_id: int) → None` | FK-safe cascade delete of entire season; same ordering as reset_service |

---

## Removed Code

| File | Item | Reason |
|------|------|--------|
| `src/cogs/season_cog.py` | `DuplicateRoundView` class | No longer needed; round numbers auto-derived |
| `src/cogs/season_cog.py` | `_rounds_insert_before`, `_rounds_insert_after`, `_rounds_replace` helpers | Replaced by `renumber_rounds` |
| `src/cogs/season_cog.py` | `PendingConfig.start_date` field | Parameter removed from `/season setup` |
| `src/cogs/season_cog.py` | `PendingConfig.divisions` pre-allocation loop | Number of divisions no longer declared upfront |
