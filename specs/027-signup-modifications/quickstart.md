# Quickstart: Signup Module Modifications and Enhancements

**Feature**: `027-signup-modifications`
**Branch**: `027-signup-modifications`
**Date**: 2026-03-30

---

## Prerequisites

- Bot running with signup module enabled for the test server.
- Log channel configured (`/module config signup log-channel #channel`).
- At least one `AvailabilitySlot` configured.
- A named points config exists (e.g., "100%") for bulk-config tests.
- An active season is approved for bulk-amend tests (with amendment mode activated via
  `results amend toggle`).

---

## Story 1 — Nationality & Country Name Validation

### Test: valid nationality adjective
1. Open signups and begin the signup wizard as a test driver.
2. Reach the nationality step.
3. Type `British` → verify wizard advances, value stored as `"British"`.

### Test: valid country name
1. Repeat to nationality step.
2. Type `United Kingdom` → verify wizard advances, stored value is `"British"` (canonical
   adjective, not the country name).

### Test: valid "other"
1. Repeat to nationality step.
2. Type `other` (any casing) → wizard advances, stored as `"Other"`.

### Test: 2-letter code rejected
1. Repeat to nationality step.
2. Type `GB` → bot rejects; error message references full format; step re-prompts.

### Test: unrecognised string rejected
1. Repeat.
2. Type `Martian` → rejected; re-prompts.

### Test: nationality config toggled off
1. Disable nationality requirement (`/signup config nationality-required False`).
2. Complete a wizard run → nationality step should be skipped entirely.
3. Re-enable nationality requirement after test.

---

## Story 2 — Server-Leave Logging

### Test: Unassigned driver leaves
1. Approve a test driver to Unassigned state.
2. Remove that Discord member from the server.
3. Check the configured log channel → verify message including display name, Discord UID,
   and state "Unassigned" is posted. Verify the driver's profile still exists in the DB.

### Test: Assigned driver leaves
1. Assign a test driver to a team seat.
2. Remove that Discord member from the server.
3. Check log channel → message includes state "Assigned". Profile and team assignment
   retained in DB.

### Test: Wizard-state driver leaves (Pending Signup Completion)
1. Begin a wizard as a test driver but do not complete it.
2. Remove that Discord member.
3. Check log channel → message includes state "Pending Signup Completion". Wizard channel
   deleted immediately. No crash.

### Test: No log channel configured
1. Remove the log channel configuration.
2. Remove an Unassigned driver from the server.
3. Verify no crash occurs and no message is sent. Restore log channel after test.

---

## Story 3 — Signup Open Embed Includes Close Time

### Test: signups opened with close time
1. Run `/signup open close-in:2h` (or equivalent time parameter).
2. Observe the announcement embed in the general signup channel.
3. Verify the embed body contains a close-time line (e.g., `Auto-closes: 2026-04-10 20:00 UTC`).

### Test: signups opened without close time
1. Run `/signup open` with no close time.
2. Observe the announcement embed.
3. Verify no close-time line appears.

---

## Story 4 — Admin Review Waiting Message

### Test: waiting notice in review panel
1. Complete a full wizard run as a test driver to reach Pending Admin Approval.
2. Observe the admin review panel message in the driver's signup channel.
3. Verify the text "Please wait for an admin to validate your signup." appears after the
   Notes line, separated by one blank line, and before the Approve/Request Changes/Reject
   buttons.

### Test: waiting notice after correction cycle
1. From Pending Admin Approval, have an admin click "Request Changes" and flag a field.
2. Have the driver re-submit the corrected field.
3. Verify the review panel re-posted by the bot contains the same waiting notice.

---

## Story 5 — Bulk Points Configuration Editing

### Test: bulk-config session — happy path
1. Run `/results bulk-config session` with `name:100%` `session:Feature Race`.
2. A modal appears. Enter:
   ```
   1, 25
   2, 18
   3, 15
   4, 12
   5, 10
   ```
3. Submit → verify confirmation listing all 5 changes.
4. Run `/results config view name:100%` → verify positions 1–5 are stored correctly.

### Test: bulk-config session — mixed valid/invalid
1. Run bulk-config session modal with:
   ```
   1, 25
   0, 10
   2, -5
   3, 15
   ```
2. Submit → verify positions 1 and 3 are applied; lines 2 and 3 reported as invalid with
   clear explanation.

### Test: bulk-config session — config not found
1. Run `/results bulk-config session` with `name:nonexistent` → verify "config not found"
   error before any modal opens (or on submit if modal-gated).

### Test: bulk-amend session — happy path
1. Ensure amendment mode is active.
2. Run `/results bulk-amend session` with `session:Feature Race`.
3. Submit 3 valid position-points pairs.
4. Verify modification store updated; confirmation shown.

### Test: bulk-amend session — amendment mode off
1. Disable amendment mode.
2. Run `/results bulk-amend session` → verify clear error returned.

---

## Story 6 — Unassigned Command Restructure + CSV Export

### Test: /signup unassigned list
1. Ensure at least one Unassigned driver exists.
2. Run `/signup unassigned list` → verify same seeded list output as the old
   `/signup unassigned` command.

### Test: /signup unassigned export
1. Run `/signup unassigned export` → verify an ephemeral response is returned with a
   `.csv` file attachment named `unassigned_drivers.csv`.
2. Open the CSV. Verify:
   - Headers in order: `Seed`, `Display Name`, `Discord User ID`, `Driver Type`,
     `Lap Total`, one column per configured time slot, `Preferred Team 1`,
     `Preferred Team 2`, `Preferred Team 3`, `Platform`, `Platform ID`.
   - One data row per Unassigned driver.
   - Slot columns contain `X` for selected slots, empty for unselected.
   - Missing values (null lap time, null platform) appear as empty cells.

### Test: /signup unassigned export — no drivers
1. Ensure no Unassigned drivers exist.
2. Run `/signup unassigned export` → verify "no Unassigned drivers" message; no file
   attachment.

### Test: old command no longer works
1. Attempt `/signup unassigned` (bare, no subcommand) → verify Discord shows
   the subgroup options (`list`, `export`) rather than executing the old command.
