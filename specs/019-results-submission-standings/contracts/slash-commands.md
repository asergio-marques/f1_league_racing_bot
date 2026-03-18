# Slash Command Contracts: Results & Standings Module

**Feature**: `019-results-submission-standings`  
**Date**: 2026-03-18  
**Convention**: `/domain action [required] (optional)` — per Bot Behavior Standards (v2.4.1)

All commands in this module require the module-enabled gate unless marked otherwise. Commands marked **[T2]** require tier-2 (trusted admin / season-config authority). Commands marked **[SA]** require server admin.

---

## `/results config` group

### `/results config add [name]` **[T2]**

Add a named configuration to the server points store.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | ✅ | Unique config name for this server, e.g. `"100%"` |

**Success**: Ephemeral confirmation — config created with all positions at 0.  
**Errors**: Config with that name already exists.

---

### `/results config remove [name]` **[T2]**

Remove a named configuration from the server store.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | ✅ | Config name to remove (autocomplete from server store) |

**Success**: Ephemeral confirmation with audit log entry.  
**Errors**: Config not found.

---

### `/results config session [name] [session] [position] [points]` **[T2]**

Set points for a finishing position in a session type within a named server config.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | ✅ | Config name (autocomplete from server store) |
| `session` | choice | ✅ | One of: `Sprint Qualifying`, `Sprint Race`, `Feature Qualifying`, `Feature Race` |
| `position` | integer | ✅ | Finishing position (1-indexed, min 1) |
| `points` | integer | ✅ | Points awarded (min 0) |

**Success**: Ephemeral confirmation.  
**Errors**: Config not found.

---

### `/results config fl [name] [session] [points]` **[T2]**

Set the fastest-lap bonus points for a race session type.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | ✅ | Config name (autocomplete) |
| `session` | choice | ✅ | `Sprint Race` or `Feature Race` only |
| `points` | integer | ✅ | Bonus points (min 0) |

**Success**: Ephemeral confirmation.  
**Errors**: Config not found; qualifying session type provided (rejected).

---

### `/results config fl-plimit [name] [session] [limit]` **[T2]**

Set the position eligibility limit for fastest-lap bonus.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | ✅ | Config name (autocomplete) |
| `session` | choice | ✅ | `Sprint Race` or `Feature Race` only |
| `limit` | integer | ✅ | Highest position eligible (e.g. `10` → positions 1–10 eligible) |

**Success**: Ephemeral confirmation.  
**Errors**: Config not found; qualifying session type provided (rejected).

---

### `/results config append [name]` **[T2]**

Attach a server-store config to the current season in SETUP.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | ✅ | Config name (autocomplete from server store) |

**Success**: Ephemeral confirmation.  
**Errors**: No season in SETUP; season is ACTIVE; config not found.

---

### `/results config detach [name]` **[T2]**

Detach a config from the current season in SETUP.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | ✅ | Config name (autocomplete from attached configs) |

**Success**: Ephemeral confirmation.  
**Errors**: No season in SETUP; season is ACTIVE; config not attached.

---

### `/results config view [name] (session)` **[T2]**

View the points configuration applied to the current season.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | ✅ | Config name (autocomplete from season store or attached configs) |
| `session` | choice | ❌ | Optional session filter; if omitted, all four session types shown |

**Success**: Ephemeral message with formatted points table. Trailing zero positions collapsed to `"xth+: 0"`.  
**Errors**: No active or SETUP season; config not found in season.

---

## `/results amend` group

### `/results amend toggle` **[SA]**

Enable or disable amendment mode for the current season.

*No parameters.*

**On enable**: Copies season points store to modification store; returns confirmation.  
**On disable**: Rejected if `modified_flag = 1` with a clear error. Otherwise disables and clears modification store.

---

### `/results amend revert` **[T2]**

Revert all uncommitted modification store changes back to the season points store.

*No parameters.* (Invalid if amendment mode is off.)

**Success**: Modification store overwritten by season store; `modified_flag` cleared to 0.  
**Errors**: Amendment mode not active.

---

### `/results amend session [name] [session] [position] [points]` **[T2]**

Set points in the modification store (same signature as `/results config session`).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | ✅ | Config name (autocomplete from modification store) |
| `session` | choice | ✅ | Session type |
| `position` | integer | ✅ | Finishing position |
| `points` | integer | ✅ | Points |

**On success**: Sets `modified_flag = 1`.  
**Errors**: Amendment mode not active; config not in modification store.

---

### `/results amend fl [name] [session] [points]` **[T2]**

Set fastest-lap bonus in modification store.

Same parameters as `/results config fl`. Sets `modified_flag = 1` on success.

---

### `/results amend fl-plimit [name] [session] [limit]` **[T2]**

Set fastest-lap position limit in modification store.

Same parameters as `/results config fl-plimit`. Sets `modified_flag = 1` on success.

---

### `/results amend review` **[SA]**

Display modification store contents and present Approve / Reject buttons.

*No parameters.* (Invalid if amendment mode is off.)

**On Approve**: Season points store overwritten by modification store; all results and standings recomputed and reposted for all divisions; `modified_flag` cleared; modification store purged; amendment mode disabled.  
**On Reject**: No changes; modification store and amendment mode remain active.

---

## `/results reserves` group

### `/results reserves toggle [division]` **[T2]**

Toggle reserve driver visibility in the public standings for a division.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | string | ✅ | Division name (autocomplete) |

**Success**: Ephemeral confirmation of new toggle state.  
**Errors**: Division not found; module not enabled.

---

## `/round results` group

### `/round results penalize [division] [round]` **[T2]**

Launch the penalty/disqualification wizard for a completed round.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | string | ✅ | Division name (autocomplete) |
| `round` | integer | ✅ | Round number |

**Wizard flow** (message-based in interaction channel):
1. **Start** — buttons for each session; Cancel button always shown; Review button visible if penalties are staged.
2. **Insert user ID** — bot requests a Discord user ID; Back button returns to Start.
3. **Insert penalty** — bot requests: time penalty (integer seconds), DSQ button, or Back/Cancel.
   - QUALifying sessions: time penalty rejected; only DSQ accepted.
   - Race sessions: both time penalty and DSQ accepted.
4. **Review** — lists staged penalties; Approve / Make Changes / Cancel buttons.
   - Make Changes → returns to Start with all staged penalties preserved.
   - Approve → applies corrections, recalculates positions and standings from this round, reposts affected channels.
   - Cancel → wizard exits; no changes applied.

**Errors**: Division not found; round not completed; module not enabled.

---

### `/round results amend [division] [round] (session)` **[T2]**

Re-submit the results for one or all sessions of a completed round.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | string | ✅ | Division name (autocomplete) |
| `round` | integer | ✅ | Round number |
| `session` | choice | ❌ | If omitted, bot presents session buttons to choose one |

**Flow**: Bot requests re-entry in the same format as original submission. Validates, supersedes previous session results, recomputes standings from that round onwards, reposts affected channels. Produces audit log entry.

**Errors**: Division not found; round not completed; session not found for that round; module not enabled.

---

## Submission Channel (Not a slash command — automated)

The transient results submission channel is created at the round's scheduled start time by the APScheduler `results_r{round_id}` job, not via a command. It follows this contract:

**Channel name**: `results-sub-{division-slug}-r{round_number}`  
**Category**: Same category as the division's configured results channel  
**Visibility**: Server admins + tier-2 admin role only  
**Lifetime**: Open until all sessions submitted or cancelled; then deleted  
**Per-session prompt format**:
```
📋 Round {N} — {Session Type}
Submit results in the following format, one driver per line:
[Format description per session type]
Or enter: CANCELLED
```
**Config selection prompt** (after valid session input):
```
Choose a points configuration for {Session Type}:
[Button: Config Name 1] [Button: Config Name 2] ...
```
