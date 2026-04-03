# Data Model: Attendance RSVP Check-in & Reserve Distribution

**Feature Branch**: `032-attendance-rsvp-checkin`  
**Date**: 2026-04-03

## New Entities

### DriverRoundAttendance

Stores per-driver RSVP state for each round in each division. One row per
(round, division, driver). Created in bulk when the RSVP embed is posted (FR-009).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `round_id` | INTEGER | NOT NULL, FK → rounds(id) ON DELETE CASCADE | |
| `division_id` | INTEGER | NOT NULL, FK → divisions(id) | |
| `driver_profile_id` | INTEGER | NOT NULL, FK → driver_profiles(id) | |
| `rsvp_status` | TEXT | NOT NULL DEFAULT 'NO_RSVP' | NO_RSVP / ACCEPTED / TENTATIVE / DECLINED |
| `accepted_at` | TEXT | NULL | ISO 8601 UTC; set on first Accept; reset on re-Accept after non-Accept |
| `assigned_team_id` | INTEGER | NULL | FK → team_instances(id); set by distribution |
| `is_standby` | INTEGER | NOT NULL DEFAULT 0 | 1 = classified as standby at deadline |
| `attended` | INTEGER | NULL | 0/1; populated in future increment when results submitted |
| UNIQUE | | (round_id, division_id, driver_profile_id) | one row per driver per round |

**Index**: `(round_id, division_id)` — primary query pattern for embed building and distribution.

---

### rsvp_embed_messages

Stores the Discord message ID of the posted RSVP embed per (round, division).
Used to edit the embed in-place on button interactions and to re-register persistent views on restart.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `round_id` | INTEGER | NOT NULL, FK → rounds(id) ON DELETE CASCADE | |
| `division_id` | INTEGER | NOT NULL, FK → divisions(id) | |
| `message_id` | TEXT | NOT NULL | Discord message snowflake ID |
| `channel_id` | TEXT | NOT NULL | Discord channel snowflake ID; stored for fetch without querying attendance config |
| `posted_at` | TEXT | NOT NULL | ISO 8601 UTC |
| UNIQUE | | (round_id, division_id) | one embed per round per division |

**Pattern**: Mirrors `forecast_messages` (used for weather phase outputs). Allows
re-registration of `RsvpView` on bot restart by iterating rows for rounds whose
`scheduled_at` is in the future (or whose deadline has not yet passed).

---

## Amended Entities

### AttendanceConfig (existing — no schema change)

All fields needed for this increment already exist: `rsvp_notice_days`,
`rsvp_last_notice_hours`, `rsvp_deadline_hours`. No migration required.

### AttendanceDivisionConfig (existing — no schema change)

`rsvp_channel_id` and `attendance_channel_id` already present. No migration required.

---

## New DB Migration

**File**: `src/db/migrations/031_attendance_rsvp.sql`

```sql
-- Migration 031: Attendance RSVP check-in tables
-- Creates driver_round_attendance and rsvp_embed_messages tables.

CREATE TABLE IF NOT EXISTS driver_round_attendance (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id           INTEGER NOT NULL
                           REFERENCES rounds(id)
                           ON DELETE CASCADE,
    division_id        INTEGER NOT NULL
                           REFERENCES divisions(id),
    driver_profile_id  INTEGER NOT NULL
                           REFERENCES driver_profiles(id),
    rsvp_status        TEXT    NOT NULL DEFAULT 'NO_RSVP',
    accepted_at        TEXT,
    assigned_team_id   INTEGER REFERENCES team_instances(id),
    is_standby         INTEGER NOT NULL DEFAULT 0,
    attended           INTEGER,
    UNIQUE (round_id, division_id, driver_profile_id)
);

CREATE INDEX IF NOT EXISTS idx_dra_round_division
    ON driver_round_attendance (round_id, division_id);

CREATE TABLE IF NOT EXISTS rsvp_embed_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id    INTEGER NOT NULL
                    REFERENCES rounds(id)
                    ON DELETE CASCADE,
    division_id INTEGER NOT NULL
                    REFERENCES divisions(id),
    message_id  TEXT    NOT NULL,
    channel_id  TEXT    NOT NULL,
    posted_at   TEXT    NOT NULL,
    UNIQUE (round_id, division_id)
);
```

---

## New Dataclasses

### DriverRoundAttendance (src/models/attendance.py — extend existing file)

```python
@dataclass
class DriverRoundAttendance:
    id: int
    round_id: int
    division_id: int
    driver_profile_id: int
    rsvp_status: str        # NO_RSVP | ACCEPTED | TENTATIVE | DECLINED
    accepted_at: str | None
    assigned_team_id: int | None
    is_standby: bool
    attended: bool | None   # None until results submitted (future increment)
```

### RsvpEmbedMessage (src/models/attendance.py — extend existing file)

```python
@dataclass
class RsvpEmbedMessage:
    id: int
    round_id: int
    division_id: int
    message_id: str
    channel_id: str
    posted_at: str
```

---

## Entity Relationships

```
rounds (1) ──< driver_round_attendance >── (1) driver_profiles
rounds (1) ──< rsvp_embed_messages >── divisions (1)
rounds (1) ──> divisions (1) ──> seasons
driver_round_attendance.assigned_team_id ──> team_instances
team_instances.is_reserve ──> 0 (full-time) | 1 (Reserve team)
driver_season_assignments ──> team_seats ──> team_instances (determines is_reserve)
team_standings_snapshots (most recent round_id for division) ──> standing_position
```

---

## Data Flow Summary

1. **Season approval** → scheduling jobs created for `rsvp_notice_r{round_id}`,
   `rsvp_last_notice_r{round_id}` (if `rsvp_last_notice_hours > 0`),
   `rsvp_deadline_r{round_id}`.
2. **Notice job fires** → query all full-time + reserve drivers in division via
   `driver_season_assignments → team_seats → team_instances` join. Insert
   `driver_round_attendance` rows (status = NO_RSVP). Build embed. Post to RSVP
   channel. Store `message_id` + `channel_id` in `rsvp_embed_messages`.
3. **Driver presses button** → validate driver membership + locking rules. Update
   `rsvp_status` (and `accepted_at` if newly ACCEPTED). Rebuild embed string.
   Fetch message via `rsvp_embed_messages` channel_id + message_id. Edit message.
4. **Last-notice job fires** → query `driver_round_attendance` WHERE `rsvp_status =
   'NO_RSVP'` AND driver is full-time (joined via `driver_season_assignments`). Post
   mention string to RSVP channel. No DB writes.
5. **Deadline job fires** → lock embed (disable buttons by editing the message with
   a new View(disabled=True) or removing buttons). Run distribution algorithm. Write
   `assigned_team_id` and `is_standby` to `driver_round_attendance`. Post assignment
   message to RSVP channel.
