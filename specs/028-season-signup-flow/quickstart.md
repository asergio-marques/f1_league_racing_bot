# Quickstart: Season-Signup Flow Alignment (`028-season-signup-flow`)

This guide walks through the end-to-end admin flow after this feature is implemented. Use
it to verify the full integration manually on a local bot instance.

---

## Prerequisites

- Bot running locally with the feature branch applied.
- A Discord test server with admin access.
- Relevant slash commands synced (restart or `/sync` the bot after code changes).
- At least two Discord test accounts: one for the admin, one for a driver.

---

## Step 1 — Open Signups With No Season

Confirm signups can be opened even when no season record exists.

```
/signup open
```

**Expected**: Success confirmation. No season-state error. The signup button appears in the
configured signup channel.

**Previous behaviour (now removed)**: "⛔ No active season found." if no ACTIVE season exists.

---

## Step 2 — Complete a Driver Signup

Using the driver test account:

1. Press the signup button in the signup channel.
2. Complete all wizard steps.
3. Confirm the driver reaches **Pending Admin Approval** state.

Using the admin account:

```
/driver pending-review   (or equivalent review command)
```

Accept/approve the driver. Confirm the driver's state transitions to **UNASSIGNED**.

---

## Step 3 — Create a SETUP Season and Division

```
/season setup name:"Test S1" year:2025
/division add name:"Div 1" role:@Div1Role forecast_channel:#forecast
```

Confirm a SETUP season exists, and a division named "Div 1" is created within it.

---

## Step 4 — Configure Lineup and Calendar Channels

```
/division lineup-channel name:"Div 1" channel:#lineup
/division calendar-channel name:"Div 1" channel:#calendar
```

**Expected**:
- Confirmation for both commands.
- `/division lineup-channel` now writes to `divisions.lineup_channel_id` (not
  `signup_division_config`).
- `/division calendar-channel` stores `calendar_channel_id` on the division row.
- No "signup module not enabled" error for either command.

---

## Step 5 — Assign Driver During SETUP (No Role Grant)

```
/driver assign driver:@TestDriver division:"Div 1" team:"Team A"
```

**Expected**:
- Driver moves from UNASSIGNED → ASSIGNED.
- **No Discord role changes** — the driver does not receive the tier role or team role yet.
- The lineup message appears in **#lineup** (live lineup update behaviour applies in SETUP
  too — a fresh lineup post is sent since `lineup_channel_id` is now configured).

---

## Step 6 — Unassign and Re-assign During SETUP

```
/driver unassign driver:@TestDriver
```

**Expected**:
- Driver returns to UNASSIGNED.
- No Discord role changes.
- Lineup message in **#lineup** is deleted and a new one (showing no assigned drivers) is posted.

```
/driver assign driver:@TestDriver division:"Div 1" team:"Team A"
```

Lineup updates again to show the re-assignment.

---

## Step 7 — Run `/season review` and Check Lineup Section

```
/season review
```

**Expected output includes**:
- Existing sections (modules, points config, rounds).
- New section per division listing assigned drivers by team.
- Warning for any UNASSIGNED driver: "⚠️ 0 drivers assigned — lineup incomplete" (or
  similar) if any remain; or showing the assigned driver under their team.

---

## Step 8 — Force-Close Signups and Verify State Preservation

Reopen signups briefly:

```
/signup open
```

As the driver, begin but do not complete a second signup (reach PENDING_SIGNUP_COMPLETION).
Approve a second test driver to reach PENDING_ADMIN_APPROVAL.

Force-close signups:

```
/signup close --confirm   (or equivalent)
```

**Expected**:
- Driver in PENDING_SIGNUP_COMPLETION → transitions to NOT_SIGNED_UP (existing behaviour).
- Driver in PENDING_ADMIN_APPROVAL → **remains in PENDING_ADMIN_APPROVAL** (new behaviour).

---

## Step 9 — Approve the Season

```
/season approve
```

**Expected (all happen in the same approval response cycle)**:
1. Season transitions from SETUP → ACTIVE.
2. **Bulk role grant**: the ASSIGNED driver (`@TestDriver`) receives the tier role
   (`@Div1Role`) and the team role for "Team A" — confirmed by checking the Discord member
   roles in the test server.
3. **Lineup post**: the current lineup message in **#lineup** is deleted and a fresh
   one is posted (the same data, reflecting the post-ACTIVE state).
4. **Calendar post**: a calendar message appears in **#calendar** listing rounds with
   Discord dynamic timestamps (`<t:UNIX:F>` format), one entry per round.

---

## Step 10 — Verify Live Lineup Update After Approval (ACTIVE Season)

With the season now ACTIVE:

```
/driver assign driver:@TestDriver division:"Div 1" team:"Team B"
```

**Expected**:
- Driver's team changes to "Team B".
- **Immediate role change**: Team A role revoked, Team B role granted (ACTIVE season).
- Lineup message in **#lineup** is deleted and a new one showing "Team B" is posted.

---

## Step 11 — Verify Edge Cases

**Missing lineup channel (skip silently)**:
- Remove `lineup_channel_id` from a second division (or don't configure it).
- Run `/season approve` — confirm approval still succeeds and no error is thrown.

**Deleted lineup message (graceful)**:
- Manually delete the lineup message posted in **#lineup**.
- Run `/driver assign` to trigger a lineup refresh.
- Confirm a new message is posted without error (no "message not found" crash).

**Deleted lineup channel (graceful)**:
- Simulate by configuring a non-existent channel ID directly in the DB (or by deleting the
  Discord channel after configuration).
- Run an assignment change.
- Confirm no crash; error is logged internally.

---

## Quick Command Reference

| Command | Purpose |
|---------|---------|
| `/signup open` | Open signup window (no season required) |
| `/signup close` | Force-close signups |
| `/driver assign` | Assign UNASSIGNED driver (SETUP or ACTIVE season) |
| `/driver unassign` | Remove a driver's placement (SETUP or ACTIVE season) |
| `/division lineup-channel name:X channel:#Y` | Set lineup channel on the division |
| `/division calendar-channel name:X channel:#Y` | NEW: Set calendar channel on the division |
| `/season review` | Show full season summary including driver lineups |
| `/season approve` | Approve → bulk role grant + lineup/calendar posts |
