# Slash Command Contracts: Driver Placement and Team Role Configuration

**Feature**: `015-driver-placement` | **Phase**: 1

These are the public-facing Discord slash command contracts introduced by this feature. They describe the command surface as experienced by users — parameters, access tiers, response types, and error conditions.

---

## `/team role set`

**Group**: `/team` (existing `TeamCog`)  
**Subcommand**: `role set`  
**Access**: Server administrator only  
**Response**: Ephemeral confirmation

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `team` | string (autocomplete from server default teams + Reserve) | Yes | The team to configure |
| `role` | Discord Role | Yes | The Discord server role to associate with this team |

### Behaviour

- If no mapping exists for `team`: creates a new `team_role_configs` row.
- If a mapping already exists: overwrites it.
- Blocked with a clear ephemeral error if any season is currently in ACTIVE status.
- Produces an audit log entry on success.

### Responses

| Condition | Response |
|-----------|----------|
| Success (new) | Ephemeral: "✅ Team **{team}** is now associated with role **{role.name}**." |
| Success (overwrite) | Ephemeral: "✅ Team **{team}** role updated from **{old_role}** to **{role.name}**." |
| Season is ACTIVE | Ephemeral error: "Team role configuration is not permitted while a season is active. Wait until the season completes or is cancelled." |
| Team name not found | Ephemeral error: "Team **{team}** does not exist in this server's default team configuration." |

---

## `/signup unassigned`

**Group**: `/signup` (existing `SignupCog`)  
**Subcommand**: `unassigned`  
**Access**: Tier-2 (trusted) role  
**Response**: Ephemeral, visible only to invoker

### Parameters

None.

### Behaviour

- Signup module must be enabled; returns a clear error if not.
- Queries all `DriverProfile` rows with `current_state = UNASSIGNED` for this server.
- Joins with `signup_records` to retrieve display fields and `total_lap_ms`.
- Orders by: `total_lap_ms ASC NULLS LAST`, then by earliest transition-to-Unassigned timestamp.
- Returns formatted text, one driver per entry (see format below).

### Entry Format (per driver)

```
#<N> — <display_name> (<discord_user_id>)
  Platform       : <platform>
  Availability   : <slot_id>: <Day HH:MM>, ...
  Type           : Full-Time | Reserve
  Preferred Teams: 1. <team1>, 2. <team2>, 3. <team3>   (or N/A)
  Teammate Pref  : <name>   (or N/A)
  Total Lap Time : <M:ss.mmm>   (or — if none)
  Notes          : <notes>   (or —)
```

### Responses

| Condition | Response |
|-----------|----------|
| Drivers present | Ephemeral multi-line listing, paginated if > 10 entries |
| No unassigned drivers | Ephemeral: "No drivers are currently in the Unassigned state." |
| Signup module not enabled | Ephemeral error: "The signup module is not enabled on this server." |
| Invoker lacks tier-2 role | Ephemeral error per Principle I |

---

## `/driver assign`

**Group**: `/driver` (existing `DriverCog`)  
**Subcommand**: `assign`  
**Access**: Tier-2 (trusted) role  
**Response**: Ephemeral confirmation

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user` | Discord User | Yes | The driver to assign |
| `division` | string (autocomplete: tier number or division name) | Yes | Target division |
| `team` | string (autocomplete from teams in target division) | Yes | Target team |

### Behaviour

1. Resolves `division` to a `divisions.id` (accepts tier integer or name string).
2. Looks up the driver's `DriverProfile`. State must be Unassigned or Assigned.
3. Checks no existing `driver_season_assignments` row exists for (driver, season, division).
4. For non-Reserve teams: checks `team_seats` for an available seat (where `driver_profile_id IS NULL`).
5. Atomically: occupies the seat, creates a `driver_season_assignments` row, transitions driver to Assigned (if was Unassigned).
6. Grants `divisions.mention_role_id` and `team_role_configs.role_id` (if configured) to the Discord member. Role failures are logged but do not abort.

### Responses

| Condition | Response |
|-----------|----------|
| Success (was Unassigned) | Ephemeral: "✅ **{display_name}** assigned to **{team}** in **{division}** and moved to Assigned." |
| Success (was Assigned) | Ephemeral: "✅ **{display_name}** assigned to **{team}** in **{division}**." |
| Driver not found / wrong state | Ephemeral error describing required state |
| Already in division | Ephemeral error: "**{display_name}** is already assigned to a team in **{division}**." |
| Team full | Ephemeral error: "**{team}** in **{division}** has no available seats." |
| Division not found | Ephemeral error: "Division **{input}** not found." |

---

## `/driver unassign`

**Group**: `/driver` (existing `DriverCog`)  
**Subcommand**: `unassign`  
**Access**: Tier-2 (trusted) role  
**Response**: Ephemeral confirmation

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user` | Discord User | Yes | The driver to unassign |
| `division` | string (autocomplete: tier number or division name) | Yes | Division to remove from |

### Behaviour

1. Validates driver state is Assigned.
2. Looks up the `driver_season_assignments` row for (driver, season, division).
3. Atomically: frees the seat (`driver_profile_id = NULL`), deletes the assignment row.
4. Checks if the driver has any remaining assignments; if none, transitions to Unassigned.
5. Revokes `divisions.mention_role_id`. Revokes `team_role_configs.role_id` only if the driver holds no other seat in any team mapped to that role.

### Responses

| Condition | Response |
|-----------|----------|
| Success (no remaining assignments) | Ephemeral: "✅ **{display_name}** removed from **{division}** and returned to Unassigned." |
| Success (assignments remain) | Ephemeral: "✅ **{display_name}** removed from **{division}**." |
| Driver not Assigned | Ephemeral error: "**{display_name}** is not in Assigned state." |
| Driver not in division | Ephemeral error: "**{display_name}** is not assigned to any team in **{division}**." |
| Division not found | Ephemeral error: "Division **{input}** not found." |

---

## `/driver sack`

**Group**: `/driver` (existing `DriverCog`)  
**Subcommand**: `sack`  
**Access**: Tier-2 (trusted) role  
**Response**: Ephemeral confirmation with confirm/cancel prompt (state-mutating and irreversible)

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user` | Discord User | Yes | The driver to sack |

### Behaviour

1. Validates driver state is Unassigned or Assigned.
2. Presents an ephemeral confirm/cancel prompt listing the driver's assignments.
3. On confirmation: atomically frees all seats, deletes all `driver_season_assignments` rows for current season, transitions driver to Not Signed Up.
4. Calls the reusable `revoke_all_placement_roles` function to strip all division and team roles.
5. Applies Not Signed Up transition rules: if `former_driver = true`, retains profile and nulls SignupRecord fields; if `former_driver = false`, deletes profile.

### Responses

| Condition | Response |
|-----------|----------|
| Confirm prompt (before action) | Ephemeral: "⚠️ Sack **{display_name}**? This will remove them from all divisions: {list}. This cannot be undone." + Confirm / Cancel buttons |
| Success | Ephemeral: "✅ **{display_name}** has been sacked and transitioned to Not Signed Up." |
| Driver not in valid state | Ephemeral error: "**{display_name}** must be in Unassigned or Assigned state to be sacked." |
| Driver not found | Ephemeral error: "No driver profile found for the specified user." |

---

## Error Behaviour (all commands)

- All responses are ephemeral (visible only to the invoking user).
- All commands are rejected silently out-of-channel per Principle I.
- All commands require the interaction role per Principle I; access-tier enforcement applies as above.
- All commands acknowledge within 3 seconds; DB-heavy operations use `interaction.response.defer(ephemeral=True)` followed by `interaction.followup.send(...)`.
