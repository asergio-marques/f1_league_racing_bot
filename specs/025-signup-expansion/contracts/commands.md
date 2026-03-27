# Command Contracts: Signup Module Expansion (025-signup-expansion)

Slash-command interface definitions for all new and modified commands in this feature.
Each entry documents the command path, parameters, permission tier, ephemeral/public response, and the audit log action emitted.

---

## Modified Commands

### `/module enable signup`

**Change**: Remove `channel`, `base_role`, and `signed_up_role` parameters.

| Field | Value |
|---|---|
| Group | `/module enable` |
| Subcommand | `signup` |
| Parameters | *(none)* |
| Permission | Server administrator |
| Response | Ephemeral confirmation |
| Audit action | `SIGNUP_MODULE_ENABLED` |

**Pre-conditions**:
- Signup module must NOT already be enabled.

**Post-conditions**:
- `signup_module_enabled = 1` in `server_configs`.
- NO channel permission overwrites applied (deferred to `/signup channel`).
- A `signup_module_config` row is upserted with all channel/role fields NULL if not already present.

**Error cases**:
- Module already enabled → ephemeral error.

---

### `/signup open`

**Change**: Add optional `close_time` parameter.

| Field | Value |
|---|---|
| Group | `/signup` |
| Subcommand | `open` |
| Parameters | `track_ids` (str, optional) — space/comma-separated track IDs; `close_time` (str, optional) — future datetime string (e.g. `2026-04-01 20:00`) |
| Permission | Tier-2 admin |
| Response | Ephemeral confirmation + public signup-open post in general signup channel (mentions base role) |
| Audit action | `SIGNUP_WINDOW_OPENED` |

**Pre-conditions**:
- Signup module enabled.
- `signup_channel_id`, `base_role_id`, `signed_up_role_id` must all be set (guard against NULL).
- `signups_open = 0`.
- At least one availability slot configured.
- If `close_time` provided: must parse to a valid UTC future datetime.

**Post-conditions**:
- `signups_open = 1` persisted.
- `signup_button_message_id` persisted.
- If `close_time` provided: `close_at` persisted in ISO 8601 UTC; APScheduler job `signup_close_{server_id}` scheduled.
- Signup-open message in channel mentions `@base_role` (with `allowed_mentions`).

**Error cases**:
- Module not enabled → ephemeral error.
- Any of channel/base-role/complete-role not configured → ephemeral error naming missing item(s).
- Signups already open → ephemeral error.
- No time slots configured → ephemeral error.
- `close_time` is in the past → ephemeral error.
- `close_time` is unparseable → ephemeral error.

---

### `/signup close`

**Change**: Block command if `close_at` is set (auto-close timer active).

| Field | Value |
|---|---|
| Group | `/signup` |
| Subcommand | `close` |
| Parameters | *(none)* |
| Permission | Tier-2 admin |
| Response | Ephemeral + follow-up in signup channel (if in-progress drivers, confirmation view) |
| Audit action | `SIGNUP_WINDOW_CLOSED` / `SIGNUP_FORCE_CLOSE` |

**Pre-conditions** (new):
- `close_at` must be NULL. If non-null, return ephemeral error: "Signups will auto-close at `{close_at}`. Cancel the timer first if you need to close manually."

*(All other pre/post-conditions unchanged from existing implementation.)*

---

## New Commands

### `/signup channel`

| Field | Value |
|---|---|
| Group | `/signup` |
| Subcommand | `channel` |
| Parameters | `channel` (discord.TextChannel, required) |
| Permission | Server administrator |
| Response | Ephemeral confirmation |
| Audit action | `SIGNUP_CHANNEL_SET` |

**Pre-conditions**:
- Signup module enabled.
- `channel` must not be the server's interaction channel.
- Bot must have `manage_channels` permission on `channel`.

**Post-conditions**:
- `signup_channel_id` updated in `signup_module_config`.
- Discord permission overwrites applied to `channel`:
  - `@everyone` → `view_channel=False`
  - `base_role` (if set) → `view_channel=True`, `send_messages=False`, `use_application_commands=True`
  - bot member → `view_channel=True`, `send_messages=True`
  - interaction role (if set) → `view_channel=True`, `send_messages=True`
- If a previous channel was set, its bot-applied permission overwrites are reverted before applying to the new channel.

**Error cases**:
- Module not enabled → ephemeral error.
- `channel` is the interaction channel → ephemeral error.
- Bot lacks `manage_channels` on channel → ephemeral error.

---

### `/signup base-role`

| Field | Value |
|---|---|
| Group | `/signup` |
| Subcommand | `base-role` |
| Parameters | `role` (discord.Role, required) |
| Permission | Server administrator |
| Response | Ephemeral confirmation |
| Audit action | `SIGNUP_BASE_ROLE_SET` |

**Pre-conditions**:
- Signup module enabled.

**Post-conditions**:
- `base_role_id` updated in `signup_module_config`.
- If `signup_channel_id` is already set, re-applies permission overwrites for the base role on the channel (adds view_channel overwrite for new role; removes overwrite for previous role if changed).

**Error cases**:
- Module not enabled → ephemeral error.

---

### `/signup complete-role`

| Field | Value |
|---|---|
| Group | `/signup` |
| Subcommand | `complete-role` |
| Parameters | `role` (discord.Role, required) |
| Permission | Server administrator |
| Response | Ephemeral confirmation |
| Audit action | `SIGNUP_COMPLETE_ROLE_SET` |

**Pre-conditions**:
- Signup module enabled.

**Post-conditions**:
- `signed_up_role_id` updated in `signup_module_config`.

**Error cases**:
- Module not enabled → ephemeral error.

---

### `/division lineup-channel`

| Field | Value |
|---|---|
| Group | `/division` |
| Subcommand | `lineup-channel` |
| Parameters | `division` (str, required) — division name or tier number; `channel` (discord.TextChannel, required) |
| Permission | Tier-2 admin |
| Response | Ephemeral confirmation |
| Audit action | `SIGNUP_LINEUP_CHANNEL_SET` |

**Pre-conditions**:
- Signup module enabled.
- Active or SETUP season must exist (division must be resolvable).

**Post-conditions**:
- `SignupDivisionConfig` row upserted: `(server_id, division_id, lineup_channel_id = channel.id)`.

**Error cases**:
- Module not enabled → ephemeral error.
- Division not found → ephemeral error.
- No season exists → ephemeral error.

---

## Automated Actions (system-actor events)

### Signup Auto-Close Timer (`signup_close_{server_id}`)

**Trigger**: APScheduler `DateTrigger` fires at `close_at` UTC timestamp.

**Effect**:
1. Call `execute_forced_close(server_id, bot, audit_action="SIGNUP_AUTO_CLOSE")`.
2. All `PENDING_SIGNUP_COMPLETION`, `PENDING_DRIVER_CORRECTION`, `PENDING_ADMIN_APPROVAL`, `AWAITING_CORRECTION_PARAMETER` drivers → `NOT_SIGNED_UP`.
3. `UNASSIGNED` and `ASSIGNED` drivers unchanged.
4. Signup button message deleted; "signups are closed" notice posted in signup channel.
5. `close_at` cleared from `signup_module_config`.

**Audit entry**: `change_type = "SIGNUP_AUTO_CLOSE"`, actor = "system", timestamp = fire time.

---

### Lineup Post (internal, triggered post-assign/unassign/sack)

**Trigger**: `placement_service` calls `_maybe_post_lineup(server_id, division_id, guild)` after any assignment mutation.

**Condition**: Zero UNASSIGNED drivers server-wide AND at least one ASSIGNED driver in `division_id` AND `lineup_channel_id` is set for `division_id`.

**Effect**: Posts formatted lineup embed to `lineup_channel_id` listing all teams with their assigned drivers.

**Audit entry**: `change_type = "SIGNUP_LINEUP_POSTED"`, actor = "system", division noted.
