# Contract: Division Channel Commands

**Feature**: 018-results-standings — module registration and channel setup  
**Introduced commands**: `/division weather-channel`, `/division results-channel`, `/division standings-channel`

These three commands share a common structure and access model. They are defined in `SeasonCog` under the existing `/division` app-command group.

---

## `/division weather-channel`

Assign (or reassign) the weather forecast channel for a named division.

| Field | Value |
|-------|-------|
| **Command path** | `/division weather-channel` |
| **Access** | `@channel_guard` — interaction role required (Tier-2 / config authority) |
| **Response** | Ephemeral confirmation or ephemeral error |

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | `str` | Yes | Name of the division to update (case-insensitive match) |
| `channel` | `discord.TextChannel` | Yes | The Discord text channel to designate as the weather forecast channel |

### Behaviour

1. Resolve the current or SETUP season for the server.
2. Find the division by name (case-insensitive). Error if not found.
3. Upsert `divisions.forecast_channel_id = channel.id` for the matched division.
4. Write audit log entry: `DIVISION_CHANNEL_SET`, actor, division name, old value → new value.
5. Respond ephemerally: `✅ Weather forecast channel for **{name}** set to {channel.mention}.`

### Error responses

| Condition | Message |
|-----------|---------|
| No season exists (no divisions to update) | `❌ No season found. Set up a season before assigning channels.` |
| Division name not found | `❌ Division **{name}** not found in the current season.` |
| Same channel already set | `ℹ️ Weather forecast channel for **{name}** is already set to {channel.mention}.` |

---

## `/division results-channel`

Assign (or reassign) the race results posting channel for a named division.

| Field | Value |
|-------|-------|
| **Command path** | `/division results-channel` |
| **Access** | `@channel_guard` — interaction role required (Tier-2 / config authority) |
| **Response** | Ephemeral confirmation or ephemeral error |

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | `str` | Yes | Name of the division to update (case-insensitive match) |
| `channel` | `discord.TextChannel` | Yes | The Discord text channel where race results will be posted |

### Behaviour

1. Resolve the current or SETUP season for the server.
2. Find the division by name (case-insensitive). Error if not found.
3. Upsert `division_results_config.results_channel_id = channel.id` for the matched division.
4. Write audit log entry: `DIVISION_CHANNEL_SET`, actor, division name, channel type, old value → new value.
5. Respond ephemerally: `✅ Results channel for **{name}** set to {channel.mention}.`

### Error responses

| Condition | Message |
|-----------|---------|
| No season exists | `❌ No season found. Set up a season before assigning channels.` |
| Division name not found | `❌ Division **{name}** not found in the current season.` |
| Same channel already set | `ℹ️ Results channel for **{name}** is already set to {channel.mention}.` |

---

## `/division standings-channel`

Assign (or reassign) the standings posting channel for a named division.

| Field | Value |
|-------|-------|
| **Command path** | `/division standings-channel` |
| **Access** | `@channel_guard` — interaction role required (Tier-2 / config authority) |
| **Response** | Ephemeral confirmation or ephemeral error |

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | `str` | Yes | Name of the division to update (case-insensitive match) |
| `channel` | `discord.TextChannel` | Yes | The Discord text channel where standings will be posted |

### Behaviour

1. Resolve the current or SETUP season for the server.
2. Find the division by name (case-insensitive). Error if not found.
3. Upsert `division_results_config.standings_channel_id = channel.id` for the matched division.
4. Write audit log entry: `DIVISION_CHANNEL_SET`, actor, division name, channel type, old value → new value.
5. Respond ephemerally: `✅ Standings channel for **{name}** set to {channel.mention}.`

### Error responses

| Condition | Message |
|-----------|---------|
| No season exists | `❌ No season found. Set up a season before assigning channels.` |
| Division name not found | `❌ Division **{name}** not found in the current season.` |
| Same channel already set | `ℹ️ Standings channel for **{name}** is already set to {channel.mention}.` |

---

## Common notes

- All three commands use the same division lookup: search all divisions of the server's most recent season (any status — SETUP, ACTIVE, or COMPLETED). This permits channel reassignment during an active season per the spec assumption.
- Changing one channel type MUST NOT affect the other two. Each command issues a targeted upsert.
- Audit entries record: `server_id`, `actor_id`, `actor_name`, `division_id`, `change_type = 'DIVISION_CHANNEL_SET'`, `old_value = JSON{channel_type, channel_id}`, `new_value = JSON{channel_type, channel_id}`.

---

## Modified command: `/module enable` — new "results" choice

The existing `/module enable` and `/module disable` commands gain a third choice.

| Choice name | Choice value |
|-------------|-------------|
| `weather` | `"weather"` |
| `signup` | `"signup"` |
| `results` *(new)* | `"results"` *(new)* |

### `/module enable results` behaviour

1. If R&S module already enabled → `⚠️ Results & Standings module is already enabled.`
2. If any season is in ACTIVE state → `❌ Results & Standings module cannot be enabled while a season is active.`
3. Atomically set `results_module_config.module_enabled = 1` + write audit entry.
4. Post log channel notice. Respond ephemerally: `✅ Results & Standings module enabled.`

### `/module disable results` behaviour

1. If R&S module already disabled → `⚠️ Results & Standings module is already disabled.`
2. Atomically set `results_module_config.module_enabled = 0` + write audit entry.
3. Post log channel notice. Respond ephemerally: `✅ Results & Standings module disabled.`

**Note**: Disable has no active-season guard (FR-004). Configuration data (division channels) is preserved on disable (Principle X rule 3).
