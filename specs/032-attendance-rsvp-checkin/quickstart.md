# Quickstart: Attendance RSVP Check-in & Reserve Distribution

**Feature Branch**: `032-attendance-rsvp-checkin`  
**Date**: 2026-04-03

## Prerequisites

- Attendance module enabled (`/module enable attendance`)
- Season in `SETUP` state with at least one division
- RSVP channel configured per division (`/division rsvp-channel`)
- Attendance channel configured per division (`/division attendance-channel`)
- Full-time drivers assigned to teams in the division
- (Optional) Reserve drivers assigned to the Reserve team

---

## Triggering the RSVP Flow (Development / Test)

### 1. Run the migration

On bot startup the migration runner applies `031_attendance_rsvp.sql` automatically.
Verify with:

```sql
SELECT name FROM sqlite_master WHERE type='table' AND name IN (
    'driver_round_attendance', 'rsvp_embed_messages'
);
```

### 2. Schedule and inspect jobs

Approve a season. If attendance is enabled, three new scheduler jobs are created per
non-Mystery round:

```
rsvp_notice_r{round_id}       — fires at round_start − rsvp_notice_days days
rsvp_last_notice_r{round_id}  — fires at round_start − rsvp_last_notice_hours hours
                                  (only if rsvp_last_notice_hours > 0)
rsvp_deadline_r{round_id}     — fires at round_start − rsvp_deadline_hours hours
```

Confirm in the APScheduler SQLite jobstore:

```python
for job in bot.scheduler_service._scheduler.get_jobs():
    if job.id.startswith("rsvp_"):
        print(job.id, job.next_run_time)
```

### 3. Manually fire the notice job (development shortcut)

```python
from services.rsvp_service import run_rsvp_notice
await run_rsvp_notice(round_id=<ID>, bot=bot)
```

This posts the RSVP embed to the configured channel, creates `driver_round_attendance`
rows, and stores the message ID in `rsvp_embed_messages`.

### 4. Verify embed content

The embed in the RSVP channel should show:
- Title: `Season N Round N — <track name>`
- Fields: datetime timestamp, location, event type
- Per-team roster with `()` status indicators
- Three buttons: ✅ Accept (green), ❓ Tentative (grey), ❌ Decline (red)

### 5. Test button interactions

Have a registered driver press each button. Verify:
- Embed updates the driver's status indicator in-place
- `driver_round_attendance.rsvp_status` row updated in DB
- Non-members receive an ephemeral error

Verify locking by simulating past-deadline by temporarily setting `rsvp_deadline_hours`
to a high value and checking server time vs round time.

### 6. Manually fire the last-notice job

```python
from services.rsvp_service import run_rsvp_last_notice
await run_rsvp_last_notice(round_id=<ID>, bot=bot)
```

The RSVP channel should receive a message mentioning only full-time drivers whose
`rsvp_status` is `NO_RSVP`. Reserve drivers must not appear.

### 7. Manually fire the deadline job

```python
from services.rsvp_service import run_rsvp_deadline
await run_rsvp_deadline(round_id=<ID>, bot=bot)
```

Verify:
- Embed buttons disabled (or message edited without buttons)
- `driver_round_attendance` rows updated with `assigned_team_id` / `is_standby`
- Distribution announcement posted in RSVP channel mentioning assigned and standby
  reserves

---

## Running Tests

```bash
python -m pytest tests/ -v -k attendance
```

New test files for this increment:
- `tests/unit/test_rsvp_service.py` — notice, last-notice, deadline, distribution logic
- `tests/unit/test_rsvp_embed_builder.py` — embed content and roster formatting

All 20 existing attendance service tests must continue to pass.

---

## Key Files

| File | Purpose |
|------|---------|
| `src/db/migrations/031_attendance_rsvp.sql` | New tables: driver_round_attendance, rsvp_embed_messages |
| `src/models/attendance.py` | Add DriverRoundAttendance, RsvpEmbedMessage dataclasses |
| `src/services/rsvp_service.py` | NEW — notice dispatch, last-notice, deadline + distribution |
| `src/services/scheduler_service.py` | Add 3 module-level job callables + schedule/cancel methods |
| `src/cogs/attendance_cog.py` | Add RsvpView (persistent buttons) + extend with RSVP cog logic |
| `src/cogs/season_cog.py` | Extend `_do_approve` to schedule attendance jobs; extend `cancel_round` |
| `src/bot.py` | Register RsvpView, register 3 attendance callbacks, re-arm embed views on restart |
| `tests/unit/test_rsvp_service.py` | Unit tests for distribution algorithm + service methods |
| `tests/unit/test_rsvp_embed_builder.py` | Unit tests for embed content builder |
