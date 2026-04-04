# Quickstart: Attendance Tracking — 033

**Branch**: `033-attendance-tracking`  
**Phase**: 1 — Design  
**Date**: 2026-04-03

This guide shows how to test the full attendance tracking pipeline end-to-end in a
development environment once the feature is implemented.

---

## Prerequisites

1. **Branch checked out**: `git checkout 033-attendance-tracking`
2. **Dependencies installed**: `pip install -r requirements.txt`
3. **Bot running locally** with a test Discord server configured.
4. **032-attendance-rsvp-checkin** work merged or available (RSVP status columns
   must already exist on `driver_round_attendance`).
5. **A season** with at least one division exists and is in `ACTIVE` state.
6. **Results & Standings module** is enabled for the division.
7. **Attendance module** is enabled for the division, both RSVP and attendance
   channels configured.
8. **Attendance penalty configs set** (e.g., no-rsvp-penalty = 2, no-attend-penalty = 3,
   no-show-penalty = 4; autoreserve = 5; autosack = 10).
9. **Two or more drivers** in `ASSIGNED` state for the division (at least one on a
   non-Reserve team, one on the Reserve team).
10. **A round** created and scheduled for the division.

---

## Step 1 — Record RSVP Responses

Before results are submitted, ensure RSVP states exist on `driver_round_attendance` rows.
These are populated by the 032 RSVP sub-increment:

| Driver | Expected RSVP State |
|--------|---------------------|
| Driver A | `NO_RSVP` |
| Driver B | `ACCEPTED` |
| Driver C | `ACCEPTED` |
| Driver D (Reserve team) | (no row; excluded) |

If the 032 RSVP system is not yet running, you can seed these directly in the DB
for testing:

```sql
INSERT INTO driver_round_attendance
  (round_id, division_id, driver_profile_id, rsvp_status, attended)
VALUES
  (<round_id>, <division_id>, <driver_A_profile_id>, 'NO_RSVP', NULL),
  (<round_id>, <division_id>, <driver_B_profile_id>, 'ACCEPTED', NULL),
  (<round_id>, <division_id>, <driver_C_profile_id>, 'ACCEPTED', NULL);
```

---

## Step 2 — Submit a Session Result

Use `/result submit` (or the equivalent command) to submit the Feature Race session
for the round. Include Driver B and Driver C in the results. Leave Driver A out
(they did not attend).

**Expected result after penalty approval (Step 4)**:

| Driver | Attended | points_awarded | Reason |
|--------|----------|----------------|--------|
| Driver A | `false` | `no_rsvp + no_attend = 5` | NO_RSVP + absent |
| Driver B | `true` | `0` | ACCEPTED + attended |
| Driver C | `false` (attending the race counts if in result rows) | see note | ACCEPTED + result present = attended |

> **Note**: Any driver whose `driver_profile_id` appears in any `DriverSessionResult`
> row for this round is marked `attended = true`, regardless of finish position.

---

## Step 3 — Stage an Attendance Pardon (Optional)

In the penalty wizard (after all sessions are submitted, before approval):

1. Observe that the "🏳️ Attendance Pardon" button appears in the penalty review prompt.
2. Click "🏳️ Attendance Pardon".
3. Fill in the modal:
   - **Driver Discord ID**: Driver A's Discord user ID
   - **Pardon Type**: `NO_RSVP`
   - **Justification**: "Family emergency — admin approved via DM"
4. Submit. Verify:
   - The wizard prompt now shows the staged pardon in the "Staged Attendance Pardons"
     subsection.
   - Attempting to submit a second `NO_RSVP` pardon for Driver A is rejected with an
     error ("duplicate pardon type").
   - Attempting to submit a `NO_SHOW` pardon for Driver A (who has `rsvp_status = NO_RSVP`)
     is rejected with an error.

---

## Step 4 — Approve the Penalty Review

Click "✅ Approve" in the penalty wizard. Under the existing flow, after approval the
bot sets `result_status = 'POST_RACE_PENALTY'` and posts the AppealsReviewView.

**New behaviour triggered by this feature**:

1. **Attendance recording**: `record_attendance_from_results` runs. Check DB:
   ```sql
   SELECT driver_profile_id, attended
   FROM driver_round_attendance
   WHERE round_id = <round_id>;
   ```
   Driver A: `attended = 0`. Driver B: `attended = 1`. Driver C: `attended = 1`.

2. **Point distribution**: `distribute_attendance_points` runs, respecting the staged
   NO_RSVP pardon for Driver A. Check DB:
   ```sql
   SELECT driver_profile_id, points_awarded, total_points_after
   FROM driver_round_attendance
   WHERE round_id = <round_id>;
   ```
   Driver A: `points_awarded = 3` (NO_RSVP waived, only `no_attend_penalty=3`).  
   Driver B: `points_awarded = 0`.  
   Driver C: `points_awarded = 4` (`no_show_penalty = 4`, ACCEPTED + absent).

3. **Attendance sheet**: A message appears in the division's attendance channel. Verify:
   - Drivers listed in descending order of `total_points_after`.
   - Each line formatted as `@mention — X attendance points`.
   - Footer includes autoreserve and autosack threshold lines (if configured).

4. **Santions** (if thresholds are crossed): Any driver whose `total_points_after`
   meets or exceeds `autoreserve_threshold` (5) or `autosack_threshold` (10) triggers
   the appropriate seat mutation. For this first round, totals should be below thresholds.

---

## Step 5 — Trigger Autoreserve (Threshold Test)

To test autoreserve:

1. Reduce `autoreserve_threshold` to `1` using `/attendance config autoreserve 1`.
2. Finalize another round (or amend the current round) such that Driver A accumulates
   ≥ 1 attendance point.
3. After penalty approval, verify:
   - Driver A's team seat in the division is changed to the Reserve team.
   - An audit log entry is posted to the log channel.
   - The attendance sheet is updated to reflect the post-sanction state.
   - Repeating for a driver already in Reserve: no seat mutation occurs (FR-026).

---

## Step 6 — Trigger Autosack (Threshold Test)

1. Reduce `autosack_threshold` to `1` using `/attendance config autosack 1`.
2. After Step 5 or a subsequent round finalization, Driver A's `total_points_after`
   meets the threshold.
3. Verify:
   - Driver A is unassigned from all team seats across **all** divisions.
   - Driver A's driver role is revoked.
   - One audit log entry per affected division is posted.
   - Autoreserve is NOT applied (autosack supersedes; FR-025).

---

## Step 7 — Amendment Recalculation

1. Enable amendment mode: `/amendment enable` (or equivalent command).
2. Add Driver A to the round results (e.g., modify the session result to include them).
3. Approve the amendment.
4. Verify:
   - Driver A's `attended` flag is flipped to `true`.
   - Driver A's `points_awarded` is recalculated: now only `no_rsvp_penalty = 2`
     minus the staged pardon (NO_RSVP was waived) = 0, since they're now recorded
     as having attended.
   - `total_points_after` is updated accordingly.
   - The attendance sheet in the attendance channel is replaced (old message deleted,
     new message posted).
   - Sanctions are re-evaluated with updated totals.

---

## Step 8 — Verify Bot Restart Persistence

1. Restart the bot.
2. Resume a penalty wizard that was open before the restart.
3. Verify that staged pardons survive the restart and are still visible in the wizard
   prompt (note: staged pardons are in-memory and do NOT survive across restarts;
   this is acceptable per the wizard's existing ephemeral state design — the appeals
   prompt and penalty review simply reconstruct from DB state on restart).

---

## Automated Test Run

```bash
# From repo root
python -m pytest tests/unit/test_attendance_tracking.py -v
```

All 15 unit tests should pass.

To run the full test suite:

```bash
python -m pytest tests/ -v
```
