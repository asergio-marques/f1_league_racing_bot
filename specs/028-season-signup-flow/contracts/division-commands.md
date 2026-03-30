# Command Contracts: Season-Signup Flow Alignment

**Phase 1 Output** | Feature: `028-season-signup-flow` | Date: 2025-07-01

This document describes the Discord slash command interface introduced or modified by
this feature. One new command is added; four existing commands change their pre-conditions
or behaviour.

All commands follow the standard access pattern: `@admin_only` decorator enforces the
Tier-2 config role; `@channel_guard` restricts invocation to the configured bot channel.
All responses are ephemeral unless noted otherwise.

---

## New Commands

---

### `/division calendar-channel`

**Group**: `/division` (existing, `src/cogs/season_cog.py`)  
**Access**: `@admin_only` (Tier-2 trusted/config role)  
**Channel**: interaction channel only (enforced by `@channel_guard`)  
**Interaction**: single — no wizard

**Purpose**: Configure the Discord channel where the race calendar message will be posted
when the season is approved. This command is NOT gated on the signup module being enabled;
it is available whenever a season (any state) exists, consistent with other division
channel commands (`/division results-channel`, `/division standings-channel`, etc.).

**Parameters**:

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | String | Yes | Name of the division to configure. |
| `channel` | `discord.TextChannel` | Yes | The Discord text channel for calendar posts. |

**Pre-conditions checked (reject with ephemeral error if violated)**:
1. A season exists for this server (any state — SETUP or ACTIVE).
2. A division with the given `name` exists in that season.
3. Invoker holds the Tier-2 admin role.

**Success response** (ephemeral):
```
✅ Calendar channel set.
   Division : {division_name}
   Channel  : #{channel_name}
```

**Persisted state**: `divisions.calendar_channel_id` ← `channel.id`

**Errors returned**:

| Condition | Message |
|-----------|---------|
| No season exists | `⛔ No season found for this server.` |
| Division not found | `⛔ No division named "{name}" found in the current season.` |
| Insufficient role | Standard `@admin_only` message |

---

## Modified Commands

---

### `/division lineup-channel` — write target changed

**What changed**: The write target for `channel_id` moves from
`signup_division_config.lineup_channel_id` to `divisions.lineup_channel_id`.
The user-facing interface, parameters, and response format are unchanged.

**Before**: stored `lineup_channel_id` in `signup_division_config` via
`signup_module_service.upsert_division_config()`.

**After**: stores `lineup_channel_id` directly on the `divisions` row via a direct
`UPDATE divisions SET lineup_channel_id = ? WHERE id = ?`.

No change to pre-conditions, parameters, access level, or success/error messages.

---

### `/signup open` — season state gate removed

**What changed**: The guard that previously required an ACTIVE season before signups could
be opened has been removed entirely. Signups can now be opened at any time — regardless of
whether a season exists, is in SETUP, or is in ACTIVE state.

**Before**: rejected with "⛔ No active season found." if `get_active_season()` returned None.

**After**: No season check. The only pre-conditions for opening signups remain:
1. Signup module is enabled (`@module_guard("signup")`).
2. At least one time slot is configured.
3. Signups are not already open.

---

### `/driver assign` — season state requirement broadened

**What changed**: The command now accepts a SETUP season (in addition to ACTIVE). Role
grants are conditional on the season state.

**Before**: required an ACTIVE season; rejected with "⛔ No active season found." otherwise.

**After**: requires a season in SETUP **or** ACTIVE state; rejects with
"⛔ No season in SETUP or ACTIVE state found." if neither exists.

**Updated parameters / interface**: unchanged.

**Behavioural change (role timing)**:

| Season state at assignment | Discord role behaviour |
|:---:|---|
| `SETUP` | No roles granted at this time. Roles will be bulk-granted when the season is approved. |
| `ACTIVE` | Tier role and team role granted immediately (unchanged from previous behaviour). |

---

### `/driver unassign` — season state requirement broadened

**What changed**: Same pattern as `/driver assign` above.

**Before**: required an ACTIVE season.

**After**: requires a SETUP or ACTIVE season.

**Behavioural change (role timing)**:

| Season state at unassignment | Discord role behaviour |
|:---:|---|
| `SETUP` | No roles revoked (driver never held them). |
| `ACTIVE` | Tier role and team role revoked immediately (unchanged from previous behaviour). |

---

### `/season review` — lineup section added

**What changed**: The review output now includes a per-division driver lineup section and
flags UNASSIGNED drivers.

**New output section** (appended to existing review embed/message):

For each division in the season:
- Lists all ASSIGNED drivers, grouped by team.
- Shows driver Discord mention (`@username`) and driver type (Full-Time / Reserve) where set.

Server-level UNASSIGNED warning (if any drivers are in UNASSIGNED state for this season):
```
⚠️ {n} driver(s) UNASSIGNED — placement incomplete before approval.
```

No change to existing sections (modules, points config, tier/team roster, rounds).

---

### `/season approve` — bulk role grant + lineup/calendar posts added

**What changed**: After transitioning the season to ACTIVE and scheduling APScheduler jobs,
three additional operations now execute automatically:

1. **Bulk role grant**: Every driver currently in ASSIGNED state across all divisions of the
   season receives their tier role and team role via `_grant_roles`. Errors per-driver are
   logged but do not block approval from completing.

2. **Lineup post per division**: For each division with `lineup_channel_id` set, a lineup
   message is posted to that channel. The new message ID is stored in
   `divisions.lineup_message_id`. If no `lineup_channel_id` is set for a division, that
   division is skipped silently.

3. **Calendar post per division**: For each division with `calendar_channel_id` set, a
   calendar message is posted listing all rounds with track name and Discord dynamic
   timestamp (`<t:UNIX:F>`). If no `calendar_channel_id` is set, that division is skipped
   silently.

Channel errors (missing channel, forbidden access, rate limit) are caught, logged, and do
NOT block season approval (A-006).
