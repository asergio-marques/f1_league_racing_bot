# Research: Attendance RSVP Check-in & Reserve Distribution

**Feature Branch**: `032-attendance-rsvp-checkin`  
**Date**: 2026-04-03

## 1. Persistent Discord UI Views (RSVP Buttons)

**Decision**: Use `discord.ui.View` with `timeout=None` (persistent view) and
deterministic `custom_id` strings per round+division. Register via `bot.add_view()`
in `bot.py` on startup.

**Rationale**: The RSVP embed persists between bot restarts. A persistent `View`
(timeout=None + static `custom_id`) guarantees that button presses are dispatched
to the handler even after a restart because discord.py reconnects to the existing
message. This is already the established pattern in this codebase: `AdminReviewView`,
`SignupButtonView`, `PenaltyReviewView` all use `timeout=None` and are registered via
`bot.add_view()` in the `setup_hook`. The RSVP view requires round-scoped `custom_id`
values (`rsvp_accept_r{round_id}`, `rsvp_tentative_r{round_id}`, `rsvp_decline_r{round_id}`)
so each button maps unambiguously to its round.

**Alternative considered**: Per-message `View` with dynamic timeout set to match the
RSVP deadline. Rejected because the view would not survive a bot restart (it is not
stored in the APScheduler jobstore or any DB table) and the deadline/locking logic
should be evaluated at interaction time from DB state, not embedded in the view
timeout.

**Implementation pattern**: The `RsvpView` class lives in `attendance_cog.py`. On
startup, `bot.py` loops over all active RSVP embed message IDs (from the new
`rsvp_embed_messages` DB table) and calls `bot.add_view(RsvpView(), message_id=msg_id)`
for each one — mirroring how `appeals_view` is re-registered in the existing
`_recover_missed_phases` / startup block.

---

## 2. Job ID Conventions for Attendance Scheduler Jobs

**Decision**: Three new APScheduler job ID prefixes:

| Job | ID pattern | Trigger time |
|-----|-----------|------|
| RSVP notice embed post | `rsvp_notice_r{round_id}` | T − `rsvp_notice_days` days |
| Last-notice ping | `rsvp_last_notice_r{round_id}` | T − `rsvp_last_notice_hours` hours |
| RSVP deadline / distribution | `rsvp_deadline_r{round_id}` | T − `rsvp_deadline_hours` hours |

**Rationale**: All existing job IDs follow the `{prefix}_r{round_id}` pattern
(`phase1_r`, `mystery_r`, `results_r`, `cleanup_r`). Adopting the same convention
keeps `cancel_round` and `get_pending_advance_jobs` extendable with minimal changes.
Using the round_id (not division_id) is consistent because each round belongs to
exactly one division — there is no per-division fan-out needed.

**Alternative considered**: Per-division job IDs using `{prefix}_r{round_id}_d{div_id}`.
Rejected because each round is already division-scoped through the `rounds` table; the
extra `_d` suffix would complicate cancellation logic without benefit.

---

## 3. Module-Level APScheduler Callables

**Decision**: Add three new module-level async functions in `scheduler_service.py`,
following the identical pattern used for `_phase_job`, `_mystery_notice_job`, and
`_result_submission_job_wrapper`:

```python
async def _rsvp_notice_job(round_id: int) -> None: ...
async def _rsvp_last_notice_job(round_id: int) -> None: ...
async def _rsvp_deadline_job(round_id: int) -> None: ...
```

Each delegates via `_GLOBAL_SERVICE._rsvp_*_callback`.

**Rationale**: SQLAlchemyJobStore requires top-level picklable callables: closures
and methods are not picklable. The codebase has consistently used this pattern for
every new job type since Phase 1. Deviating would break job persistence across
restarts.

---

## 4. Scheduling Attendance Jobs on Season Approval

**Decision**: Extend `_do_approve` in `season_cog.py` (after the existing weather/
results scheduling block) to call a new `schedule_attendance_round(rnd, cfg)` method
when the attendance module is enabled. This method is guarded by an attendance-enabled
check — it is a no-op when attendance is off, matching how result submission jobs are
guarded by `results_enabled`.

**Rationale**: Season approval is the single synchronisation point for all scheduled
jobs in this codebase. All three types (weather, results, attendance) follow: check
if module enabled → compute trigger times → add_job with replace_existing=True. The
guard is `bot.module_service.is_attendance_enabled(server_id)`, consistent with the
pattern already used for the Gate 4 approval check.

**cancel_round must be extended**: `cancel_round(round_id)` in `SchedulerService`
currently removes `phase1_r`, `phase2_r`, `phase3_r`, `mystery_r`, `cleanup_r`,
`results_r`. It must also remove `rsvp_notice_r`, `rsvp_last_notice_r`, and
`rsvp_deadline_r`. This is a one-line addition per ID.

---

## 5. Reserve Driver Identification

**Decision**: Full-time vs. reserve classification is determined via
`team_instances.is_reserve` (INTEGER, 0 = full-time, 1 = Reserve team). Queried
through the join chain: `driver_season_assignments → team_seats → team_instances`.

**Rationale**: This is already the authoritative discriminator used in
`standings_service.py` (filter `ti.is_reserve = 0` for driver/team standings) and
`results_cog.py`. No new field is required.

---

## 6. Constructors' Championship Position for Distribution Tie-Breaking

**Decision**: Query `team_standings_snapshots` for the most recent `round_id` in the
division before the current round, ordered by `standing_position ASC`. Team identity
is matched by `team_role_id`. If no snapshot rows exist (first round of the season),
fall back to alphabetical ordering of the team name.

**Rationale**: `team_standings_snapshots` already has `standing_position` per
`(round_id, division_id, team_role_id)`. The most recent snapshot is the one with
the highest `round_id` (rounds complete sequentially within a season). The fallback
is alphabetical to remain deterministic with no external configuration needed.

---

## 7. RSVP Embed Message ID Persistence

**Decision**: Add a new table `rsvp_embed_messages` to store the Discord message ID
of the posted RSVP embed, keyed by `(round_id, division_id)`.

**Rationale**: The embed must be edited in-place on every button press and after
distribution. The message ID is not predictable and cannot be derived from other data.
The pattern is established: `forecast_messages` table already does exactly this for
weather phase messages. This table also supports re-registration of the persistent
view on bot restart.

**Migration number**: `031_attendance_rsvp.sql` (next sequential).

---

## 8. DriverRoundAttendance Table

**Decision**: New table storing per-driver RSVP state for each round. Columns:
`id`, `round_id`, `division_id`, `driver_profile_id`, `rsvp_status`
(NO_RSVP/ACCEPTED/TENTATIVE/DECLINED), `accepted_at` (ISO 8601 UTC, null until
first accepted, reset on re-accept), `assigned_team_id` (null until distribution),
`is_standby` (INTEGER 0/1), `attended` (null — future increment).

**Rationale**: Required by constitution Principle XIII and spec FR-009. Centralises
all RSVP state in a single queryable table rather than embedding it in the embed
message content. `accepted_at` supports the timestamp-based reserve distribution
ordering (FR-022). `attended` is null-allowed from the start since it is populated
by a future increment.

---

## 9. test_mode_service Integration

**No changes required for this increment.**

**Rationale**: `get_pending_advance_jobs` in `test_mode_service.py` uses
`_PHASE_PREFIX_MAP` to recognise advance-queue jobs. The `rsvp_*` jobs should NOT
appear in the test-mode advance queue — they are attendance booking jobs, not
race-phase progression events. Test mode for the RSVP timers will be handled in a
separate future increment (or tested via unit/integration tests injecting mock times
directly). This keeps the advance command's responsibility narrowly scoped.

---

## 10. Locking Logic Implementation

**Decision**: Locking is evaluated at interaction time (inside the button callback),
not enforced by View timeout. The button handler queries:
1. Whether the interacting user is a full-time or reserve driver in this division.
2. Whether the RSVP deadline has passed (compare UTC now against
   `round.scheduled_at − rsvp_deadline_hours`).
3. If reserve and not currently ACCEPTED: compare UTC now against
   `round.scheduled_at` (round start time as the lock horizon).

**Rationale**: Evaluating locking at interaction time is the only reliable approach
because View timeout does not survive bot restarts and Discord can deliver button
events after restart. The DB is always the authoritative source of timing truth.
This pattern is used throughout the codebase (e.g., the penalty wizard checks
`season.status` in each interaction rather than relying on view state).
