# Research: Season-Signup Flow Alignment (`028-season-signup-flow`)

## Constitution Gaps and Required Amendments

### Finding 1 — Principle XI: Signup Close Timer Clause (CONSTITUTION VIOLATION)

**Current text** (Principle XI, "Signup close timer"):
> "When the timer fires, signups are closed automatically: all drivers in Pending Signup
> Completion, Pending Driver Correction, or Pending Admin Approval are transitioned to
> Not Signed Up…"

**Specification requirement** (FR-002, FR-003):
PENDING_ADMIN_APPROVAL and PENDING_DRIVER_CORRECTION drivers MUST NOT be transitioned to
NOT_SIGNED_UP when signups are force-closed (by timer or manual admin close). Only
PENDING_SIGNUP_COMPLETION drivers are cleared.

**Justification for amendment**:
- PENDING_ADMIN_APPROVAL drivers have fully completed the signup wizard and their record
  has been committed atomically (per Principle XI, "Signup data persistence"). Clearing
  them on close discards a valid, already-reviewed submission with no basis in the
  real-world intent of closing signups.
- PENDING_DRIVER_CORRECTION drivers have gone through a first-pass review and been
  directed to correct one parameter. Clearing them loses the corrected submission and
  forces the driver to restart from scratch.
- The real-world intent of "close signups" is to stop **new wizard initiations**, not to
  purge submissions that are awaiting or have passed admin review. The existing
  implementation conflates these two distinct operations.
- Narrowing the close-affected set to `{PENDING_SIGNUP_COMPLETION}` is strictly more
  conservative (fewer state changes) and matches the user's described real-world league
  operation.

**Decision**: PROCEED — amend Principle XI close timer clause before Phase 1 design.

---

### Finding 2 — Principle XI: Lineup Announcement Channel (CONSTITUTION VIOLATION)

**Current text** (Principle XI, "Lineup announcement channel"):
> "An optional per-division Discord channel may be configured **for the signup module**
> to post driver lineup notices."

**Specification requirement** (A-001, A-003, A-004, FR-015):
- `lineup_channel_id` moves from `signup_division_config` to the `divisions` table.
- `calendar_channel_id` (new) is added to the `divisions` table.
- `lineup_message_id` (new) is added to the `divisions` table for persistence.
- `/division calendar-channel` is NOT gated on the signup module being enabled.
- `/division lineup-channel` write target changes to `divisions` (not `signup_division_config`).

**Justification for amendment**:
- The calendar channel has no dependency on the signup module at all — it is a division-
  level announcement channel, exactly like `results_channel_id` and `standings_channel_id`
  which already live on the `divisions` table.
- Unifying both lineup and calendar channels on `divisions` is the architecturally coherent
  choice: all per-division output channels are on `divisions`, and `signup_division_config`
  is a narrow module-configuration table, not a channel registry.
- The `signup_division_config` table was the provisional home when lineup was the only
  division output channel introduced by the signup module. Now that it shares the same
  ownership pattern as other division channels, moving it to `divisions` is the correct
  normalisation.

**Decision**: PROCEED — amend Principle XI lineup announcement channel clause and extend
Data & State Management section to reflect `lineup_channel_id` and `lineup_message_id`
moving to the `Division` entity, and `calendar_channel_id` being added to `Division`.

---

## Codebase Research

### Finding 3 — `placement_service.assign_driver` / `unassign_driver` Signature Gap

**Current behaviour**: Both functions always call `_grant_roles` / `_revoke_roles` and never
take season state into account. The caller (`driver_cog.py`) has the season object available
but does not pass its state to the service.

**Required change**: Add a `season_state: str` parameter to both `assign_driver` and
`unassign_driver`. The caller already fetches the season (via `get_active_season`; after
this feature, `get_setup_or_active_season`), so it can pass `season.status` directly.

**Role matrix (A-005)**:
| Season state | Operation | Role action |
|:---:|:---:|:---:|
| SETUP | assign | Deferred — no role change |
| SETUP | unassign | No change — driver never held roles |
| ACTIVE | assign | Grant immediately |
| ACTIVE | unassign | Revoke immediately |

The signed-up (complete) role is unaffected — it was already granted at admin-approval time.

---

### Finding 4 — `_maybe_post_lineup` Current Behaviour vs. Required Redesign

**Current behaviour** (line ~857 of `placement_service.py`):
1. Reads `lineup_channel_id` from `signup_division_config` — **wrong table after migration**.
2. Only posts when ALL UNASSIGNED drivers across the **whole server** reach zero —
   **completely wrong trigger condition**.
3. Does NOT attempt to delete a previous message (no message ID tracking).
4. Does NOT store the new message ID anywhere.

**Required behaviour (renamed to `_refresh_lineup_post`):**
1. Read `lineup_channel_id` from `divisions.lineup_channel_id`.
2. Trigger on EVERY assignment change, not gated on UNASSIGNED count.
3. Read `lineup_message_id` from `divisions.lineup_message_id`; if set, attempt to delete
   the old message (catch and log `discord.NotFound` / `discord.Forbidden` gracefully —
   must not block the assignment operation).
4. If `lineup_channel_id` is set: post fresh lineup message; store new message ID to
   `divisions.lineup_message_id`.
5. If `lineup_channel_id` is not set: return silently (no action, no error).

---

### Finding 5 — `execute_forced_close` In-Progress States

**Location**: `module_cog.py`, `execute_forced_close` function, approximately line 32.

**Current behaviour**: transitions `{PENDING_SIGNUP_COMPLETION, PENDING_ADMIN_APPROVAL,
PENDING_DRIVER_CORRECTION}` to NOT_SIGNED_UP.

**Required change**: narrow the set to `{PENDING_SIGNUP_COMPLETION}` only.

APScheduler job cancellation (which runs per-transitioned driver) continues to apply only to
PENDING_SIGNUP_COMPLETION drivers, consistent with the inactivity-timeout behaviour for that
state.

---

### Finding 6 — `/signup open` Active Season Guard

**Location**: `signup_cog.py`, approximately line 1239 — guard rejects the command if
`get_active_season(server_id)` returns `None`.

**Required change**: remove this guard entirely. No season gate of any kind applies to
opening signups.

---

### Finding 7 — `/driver assign` and `/driver unassign` Season Lookup

**Current behaviour**: both use `get_active_season(server_id)` and reject if None.

**Required change**: switch to a new `get_setup_or_active_season(server_id)` helper (see
Finding 8) in both commands. Error message should reflect both states: "⛔ No season in
SETUP or ACTIVE state found."

`/driver sack` continues to use `get_active_season` (or a broader lookup) as per A-007.

---

### Finding 8 — New `get_setup_or_active_season` Helper

**Evidence**: `season_service.py` already has:
- `get_active_season(server_id)` — returns the ACTIVE season or None
- `get_setup_season(server_id)` — returns the SETUP season or None
- `has_active_or_setup_season(server_id)` — boolean, not a Season object

**Required addition**: `get_setup_or_active_season(server_id) -> Season | None` — returns
a SETUP or ACTIVE season (preferring ACTIVE if both existed, though that is architecturally
impossible as only one non-completed season can exist at a time). In practice this is:
`SELECT … WHERE status IN ('SETUP', 'ACTIVE') LIMIT 1`.

---

### Finding 9 — Migration Number and SQLite Constraints

**Confirmed migration sequence**: migrations directory contains `001` through `026`; the
next number is **027**.

**SQLite constraint**: SQLite (through at least version 3.35 / Python default builds)
does not support `ALTER TABLE … DROP COLUMN` in all deployment environments. The safe
approach is to ADD the three new columns to `divisions` AND to migrate the existing
`lineup_channel_id` data via an `UPDATE`, then recreate `signup_division_config` without
the `lineup_channel_id` column using the rename-create-insert-drop pattern. The existing
data in `signup_division_config.lineup_channel_id` must be preserved by copying to
`divisions.lineup_channel_id` before the recreation.

---

### Finding 10 — `/season review` Additional Data Reads

**Current behaviour** (`season_cog.py`, `/season review`): shows modules, points configs,
tier/team roster, and rounds. Does NOT show driver lineups or UNASSIGNED count.

**Required addition (FR-008, FR-009)**:
- For each division in the season: list all ASSIGNED drivers grouped by team.
- Flag any UNASSIGNED drivers (approved but not yet placed) at the server level with a
  visible warning.

**Data needed**: query `driver_assignments` (or equivalent join) for ASSIGNED drivers per
division; query `driver_profiles` for drivers with signup_status = 'UNASSIGNED' for this
season.

---

### Finding 11 — `_do_approve` Post-Approval Actions

**Current behaviour** (`season_cog.py`, `_do_approve`): validates gates, schedules APScheduler
jobs, calls `transition_to_active(season_id)`, posts a success message.

**Required additions (FR-006, FR-010, FR-011)**:
1. Bulk role grant: fetch all ASSIGNED drivers across all divisions of the season; call
   `_grant_roles(div_role_id, team_role_id)` per driver. Log errors per-driver; do not
   block approval on role-grant failures.
2. Linear process per division:
   a. Post lineup to `divisions.lineup_channel_id` (if set); store message ID to
      `divisions.lineup_message_id`.
   b. Post calendar to `divisions.calendar_channel_id` (if set).
   Catch `discord.HTTPException` (channel missing, forbidden, etc.); log and continue per
   A-006: posting failures MUST NOT block approval.

---

### Finding 12 — `/division lineup-channel` Write Target

**Current behaviour**: calls `signup_module_service.upsert_division_config(…, lineup_channel_id)`.

**Required change**: write directly to `divisions.lineup_channel_id` via a new
`division_service.set_lineup_channel(division_id, channel_id)` call (or an inline
UPDATE query). The `signup_division_config` table retains its other columns but the
`lineup_channel_id` column is dropped (via table recreation in migration 027).

---

### Finding 13 — Discord Dynamic Timestamp

Calendar post uses Discord dynamic timestamp format for scheduled datetimes (A-002):

```python
unix = int(dt.timestamp())
formatted = f"<t:{unix}:F>"  # Long date/time, local timezone per reader
```

`dt` in the existing schema is stored as a UTC ISO string; it must be parsed to `datetime`
before timestamp conversion.

---

## Summary of Architecture Decisions

| # | Decision | Rationale |
|---|---|---|
| D-1 | Add `season_state: str` param to `assign_driver` / `unassign_driver` | Caller already has season object; no service-level lookup needed |
| D-2 | Rename `_maybe_post_lineup` → `_refresh_lineup_post` | Signals the new "always refresh" semantics vs. old conditional logic |
| D-3 | Three new columns on `divisions` table (`lineup_channel_id`, `calendar_channel_id`, `lineup_message_id`) | Consistent with `results_channel_id`, `standings_channel_id` already on `divisions` |
| D-4 | Recreate `signup_division_config` without `lineup_channel_id` | SQLite cannot drop columns; data migrated to `divisions` in same migration |
| D-5 | New `get_setup_or_active_season()` in `season_service.py` | Minimal, targeted helper; reuses existing DB query pattern |
| D-6 | Posting failures at approval log-and-continue, not blocking | A-006: community-facing posts must not prevent season activation |
| D-7 | Next migration number: **027** | Verified from directory listing (026 is current highest) |
