# F1 League Racing Bot

A Discord bot for F1 league racing servers that includes the following functionality:
  - Management of a driver signup procedure;
  - Assignment of drivers across teams and divisions;
  - Submission and management of results and standings for multiple divisions;
  - Automated, three-phase weather generation pipeline for every race round.

Made using GitHub Copilot Spec Kit and Claude as an experiment.

---

## Prerequisites

- Python 3.8 or higher (3.12+ recommended)
- A Discord Bot Token ([Discord Developer Portal](https://discord.com/developers/applications))

---

## Setup

### 1. Clone & install dependencies

```bash
git clone <repository-url>
cd f1_league_weather_randomizer_bot
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```
BOT_TOKEN=your_discord_bot_token_here
DB_PATH=bot.db
```

### 3. Run the bot

```bash
python src/bot.py
```

On first run the bot will create `bot.db` and apply all schema migrations automatically.

---

## Required Permissions

When inviting the bot, grant it the following OAuth2 bot permissions. All are used in normal operation.

### Bot Permissions (OAuth2 scopes: `bot`, `applications.commands`)

| Permission | Why it's needed |
|---|---|
| **View Channels** | Required before any channel operation — the bot must be able to see forecast channels, log channels, and signup wizard channels before it can read or write them |
| **Send Messages** | Posts weather forecasts to division channels, signup wizard messages to private channels, and audit logs to the log channel |
| **Send Messages in Threads** | Required if any configured channels are threads |
| **Embed Links** | Posts the signup module info embed (the button drivers click to start a signup) |
| **Manage Channels** | Creates private signup wizard channels; applies and removes channel permission overwrites for the signup module and per-driver wizard channels |
| **Manage Messages** | Deletes the old forecast message when a newer phase supersedes it (`forecast_cleanup_service`) |
| **Manage Roles** | Grants the signed-up role on signup approval; grants/revokes division and team roles on driver placement, unassignment, and sacking |
| **Mention @everyone, @here, and All Roles** | Pings the division role in weather forecast messages (phase 1–3) and round amendment notices. Required when division roles are not set to "Allow anyone to @mention this role" (the typical default for private league roles) |

### Privileged Gateway Intents

These must be enabled in the **Discord Developer Portal → Bot → Privileged Gateway Intents** for the bot to function:

| Intent | Why it's needed |
|---|---|
| **Server Members Intent** | Resolves `guild.get_member()` / `fetch_member()` for role management; handles `on_member_remove` to auto-withdraw in-progress signups |
| **Message Content Intent** | Reads message content in the signup wizard's `on_message` handler (drivers submit answers by typing in their private channel) |

> **Note:** Without the Server Members Intent the bot cannot grant or revoke roles. Without the Message Content Intent the signup wizard will not receive driver responses.

---

## First-time Server Setup

After inviting the bot, a **server administrator** (Manage Server permission) must run:

```
/bot-init interaction_role:@YourRole interaction_channel:#commands log_channel:#bot-logs
```

This registers:
- **Interaction role** -- who can use bot commands
- **Interaction channel** -- the only channel where commands are accepted
- **Log channel** -- where computation audit logs are posted

---

## Prefix Commands (Bot Owner)

These are traditional `!` prefix commands restricted to the **bot owner** (the account that owns the Discord application). They are hidden from the slash command menu.

### `!sync` — Sync slash command tree

Clears any guild-scoped command overrides and pushes the latest global slash command schema. Use this immediately after deploying changes to avoid waiting up to an hour for Discord's default propagation delay.

**What it does:**
1. Clears any guild-specific command overrides for the current server
2. Syncs the cleared guild state (removes leftover duplicates)
3. Pushes the full global command tree to Discord
4. Confirms with the count of synced commands (message auto-deletes after 15 seconds)

> **Note:** The bot requires **Manage Messages** in the channel you run this from to delete your `!sync` invocation. If it lacks that permission the command still runs successfully — the original message just won't be removed.

---

## Slash Commands

### `/bot-init` — One-time server setup
*Access: Server administrator (Manage Server permission)*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `interaction_role` | Role | ✅ | The Discord role permitted to use bot commands |
| `interaction_channel` | Channel | ✅ | The only channel where bot commands are accepted |
| `log_channel` | Channel | ✅ | Channel where computation audit logs are posted |
| `force` | Boolean | — | Set `True` to overwrite an existing configuration (default: `False`) |

---

### `/clean-bot` — Delete bot messages in this channel
*Access: Trusted admin*

No parameters. Scans the last 500 messages in the interaction channel and deletes every message sent by the bot. Useful for tidying up after `/season review` or other multi-message commands. Responds ephemerally with a count of deleted messages.

> **Note:** Requires the bot to have **Manage Messages** in the channel (already a required bot permission).

---

### `/bot-reset` — Reset server data
*Access: Server administrator (Manage Server permission) · Can be run from any channel*

Removes all season data for this server. Use `full:True` to also wipe the bot configuration (equivalent to a factory reset).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `confirm` | String | ✅ | Must be exactly `CONFIRM` (case-sensitive) to authorise deletion |
| `full` | Boolean | — | Also deletes bot configuration — you must re-run `/bot-init` afterwards (default: `False`) |

**Partial reset** (`full:False`, the default): deletes all seasons, divisions, rounds, sessions, phase results, and audit entries.  Bot configuration (channel, role) is preserved; the bot remains usable immediately.

**Full reset** (`full:True`): additionally deletes the bot configuration row.  Run `/bot-init` to re-configure the bot for this server.

---

### Season Setup Workflow

Season configuration is a multi-step flow: run `/season setup`, add divisions with `/division add`, add rounds with `/round add`, then review with `/season review` and approve with `/season approve`.

#### `/season setup` — Start season configuration
*Access: Trusted admin*

No parameters. Creates a pending season tied to today's date and enables the `/division` and `/round` setup commands.

#### `/division add` — Add a division
*Access: Trusted admin · Requires active `/season setup` session*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name (used to reference it in subsequent commands) |
| `role` | Role | ✅ | Discord role mentioned when referencing this division |
| `forecast_channel` | Channel | — | Channel where weather forecast messages are posted. Required when the weather module is enabled; must be omitted when disabled. |
| `tier` | Integer | — | Tier number for this division (1 = top tier; must be sequential and unique within the season). Default: `1` |

#### `/division duplicate` — Copy a division with a datetime offset
*Access: Trusted admin · Setup only*

Clones all rounds from an existing division into a new one, shifting every scheduled_at by the given offset. Useful for multi-division season setups with staggered schedules.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_name` | String | ✅ | Name of the division to copy from |
| `new_name` | String | ✅ | Name for the new division |
| `role` | Role | ✅ | Discord role for the new division |
| `forecast_channel` | Channel | — | Forecast channel for the new division. Required when the weather module is enabled; must be omitted when disabled. |
| `tier` | Integer | — | Tier number for the new division (must be unique within the season). Default: `1` |
| `day_offset` | Integer | — | Days to shift all round datetimes (can be negative). Default: `0` |
| `hour_offset` | Float | — | Hours to shift all round datetimes (can be negative; decimals OK). Default: `0.0` |

#### `/division delete` — Remove a division from setup
*Access: Trusted admin · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the division to delete |

Permanently removes the division and all its rounds from the pending setup.

#### `/division rename` — Rename a division
*Access: Trusted admin · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `current_name` | String | ✅ | Current name of the division |
| `new_name` | String | ✅ | New name for the division |

#### `/round add` — Add a round to a division
*Access: Trusted admin · Requires active `/season setup` session*

Round numbers are **auto-assigned** by sorting all rounds in the division by `scheduled_at`; there is no manual `round_number` parameter.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Exact name of the division this round belongs to |
| `format` | String | ✅ | Race format: `NORMAL`, `SPRINT`, `MYSTERY`, or `ENDURANCE` |
| `scheduled_at` | String | ✅ | Race date and time in ISO format: `YYYY-MM-DDTHH:MM:SS` (UTC) |
| `track` | String | — | Track ID or name — use the autocomplete dropdown (e.g. `27` or `United Kingdom`). Omit for Mystery rounds. |

#### `/round delete` — Remove a round from setup
*Access: Trusted admin · Setup only*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division containing this round |
| `round_number` | Integer | ✅ | Round number to delete |

Deletes the round and renumbers remaining rounds by date.

#### `/season review` — Review pending configuration
*Access: Trusted admin*

No parameters. Displays the pending season configuration with **Approve** and **Go Back to Edit** buttons.

#### `/season approve` — Commit the configuration
*Access: Trusted admin*

No parameters. Saves all pending divisions and rounds to the database and arms the weather scheduler. Equivalent to pressing Approve in `/season review`.

---

### Active Season Commands

#### `/season status` — Active season summary
*Access: Interaction role*

No parameters. Shows active season overview: divisions, next scheduled round per division, and its track and datetime.

#### `/season cancel` — Delete the active season
*Access: Trusted admin*

> ⚠️ **Destructive — irreversible.** All season data, rounds, and results are permanently deleted.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `confirm` | String | ✅ | Type exactly `CONFIRM` to proceed |

Posts a cancellation notice to each active division's forecast channel before deleting.

#### `/season complete` — Mark the active season as complete
*Access: Trusted admin*

No parameters. Triggers the season-end flow manually. The bot will refuse if any non-cancelled round is not yet finalized, and will list the outstanding rounds. Once all rounds are finalized it executes the season-end sequence (standings archival, server reset) in the same way that `/season cancel` would not — but without deleting any data prematurely.

> **Note:** Season completion is no longer automatic. A league manager must run this command once every round in every division has been finalized.

#### `/round amend` — Amend a round in the active season
*Access: Trusted admin*

At least one optional field must be provided. Amending `scheduled_at` automatically re-sorts and renumbers all rounds in the division.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division containing the round |
| `round_number` | Integer | ✅ | The round number to amend |
| `track` | String | — | New track — use the autocomplete dropdown (e.g. `05` or `Bahrain`). Amending invalidates prior weather phases. |
| `scheduled_at` | String | — | New race datetime in ISO format `YYYY-MM-DDTHH:MM:SS` (UTC). Amending re-triggers the scheduler and renumbers rounds. |
| `format` | String | — | New format: `NORMAL`, `SPRINT`, `MYSTERY`, or `ENDURANCE`. Amending invalidates prior weather phases. |

#### `/round cancel` — Cancel a round in the active season
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division containing this round |
| `round_number` | Integer | ✅ | The round number to cancel |
| `confirm` | String | ✅ | Type exactly `CONFIRM` to proceed |

Cancels scheduled jobs for the round, sets its status to `CANCELLED`, and posts a notice to the division's forecast channel.

#### `/division cancel` — Cancel a division in the active season
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the division to cancel |
| `confirm` | String | ✅ | Type exactly `CONFIRM` to proceed |

Cancels all scheduled rounds in the division (jobs + status flags) and posts a notice to the forecast channel.

#### `/division weather-channel` — Set the weather forecast channel for a division
*Access: Trusted admin · Weather module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where weather forecast messages are posted |

#### `/division results-channel` — Set the results posting channel for a division
*Access: Trusted admin · Results & Standings module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where session results are posted |

#### `/division standings-channel` — Set the standings posting channel for a division
*Access: Trusted admin · Results & Standings module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Division name |
| `channel` | Channel | ✅ | Channel where standings tables are posted |

---

### Test Mode Commands

Test mode allows triggering weather phases on demand without waiting for the real scheduled times. Useful for verifying the bot setup before a live season.

#### `/test-mode toggle` — Enable or disable test mode
*Access: Interaction role*

No parameters. Flips test mode on/off; state persists across bot restarts.

#### `/test-mode advance` — Execute the next pending phase
*Access: Interaction role · Requires test mode active*

No parameters. Immediately runs the next pending weather phase in the queue (ordered by round date, then division). Bypasses all scheduled time checks — rounds can be advanced at any time regardless of their configured date.

#### `/test-mode review` — View phase completion status
*Access: Interaction role · Requires test mode active*

No parameters. Displays a summary of all rounds for the active season, showing which phases (✅/⏳) have been completed per round and division.

#### `/test-mode set-former-driver` — Override the former_driver flag
*Access: Trusted admin · Requires test mode active*

Manually sets the `former_driver` flag on a driver profile. Only available when test mode is enabled.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user` | Member | ✅ | The driver whose flag is being updated |
| `value` | Boolean | ✅ | The new value for the `former_driver` flag (`True` / `False`) |

---

### Module Commands

Modules extend the bot beyond weather generation. Three modules are available: **weather**, **signup**, and **results**.

#### `/module enable` — Enable a bot module
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `module_name` | Choice | ✅ | Module to enable: `weather`, `signup`, or `results` |
| `channel` | Channel | — | *(signup only)* Channel designated for signup interactions |
| `base_role` | Role | — | *(signup only)* Role granted to members eligible to sign up |
| `signed_up_role` | Role | — | *(signup only)* Role granted on successful signup completion |

#### `/module disable` — Disable a bot module
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `module_name` | Choice | ✅ | Module to disable: `weather`, `signup`, or `results` |

---

### Driver Commands

#### `/driver reassign` — Re-key a driver profile to a new Discord account
*Access: Trusted admin*

Transfers an existing driver profile from one Discord account to another. Provide either `old_user` (mention) or `old_user_id` (raw snowflake) for users who have left the server.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `new_user` | Member | ✅ | Target Discord account. Must not already have a driver profile. |
| `old_user` | Member | — | Mention of the existing Discord user whose profile is to be transferred |
| `old_user_id` | String | — | Raw Discord snowflake ID, for users who have left the server |

#### `/driver assign` — Assign a driver to a team and division
*Access: Trusted admin*

Places an Unassigned driver into a specific team seat within a division for the active season. Also grants the division role and the team role (if configured via `/team add`).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user` | Member | ✅ | The driver to assign |
| `division` | String | ✅ | Division tier number or name (e.g. `1` or `Pro`) |
| `team` | String | ✅ | Exact team name as it appears in the division |

#### `/driver unassign` — Remove a driver from a division
*Access: Trusted admin*

Removes a driver's placement from one division. Revokes the division role and (if no other team-role seat remains) the team role. If this was their only assignment the driver reverts to Unassigned.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user` | Member | ✅ | The driver to unassign |
| `division` | String | ✅ | Division tier number or name |

#### `/driver sack` — Sack a driver
*Access: Trusted admin*

Revokes all placement roles, removes all season assignments, and transitions the driver back to Not Signed Up. For former drivers the profile row is retained; for others it is deleted.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user` | Member | ✅ | The driver to sack |

---

### Team Commands

#### `/team add` — Add a team to the server list
*Access: Trusted admin*

Adds the team to the server's default team list. If a Discord role is provided it is saved as the team's role mapping (granted/revoked on driver placement). If a SETUP season is active the team is also seeded into every division with 2 seats.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the new team (max 50 chars) |
| `role` | Role | — | Discord role to grant drivers placed into this team |

#### `/team remove` — Remove a team from the server list
*Access: Trusted admin*

Removes the team from the server's default list and clears its role mapping. If a SETUP season is active the team is also removed from every division in that season.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Exact name of the team to remove |

#### `/team rename` — Rename a team
*Access: Trusted admin*

Renames the team in the server's default list and updates its role mapping key. If a SETUP season is active the name is also updated across every division in that season.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `current_name` | String | ✅ | Exact current name of the team |
| `new_name` | String | ✅ | Replacement name (max 50 chars) |

#### `/team list` — List all teams and their role mappings
*Access: Trusted admin*

Displays all teams on the server's default list alongside their configured Discord roles. If a SETUP season is active and its team list differs from the server default, the divergence is shown with a warning.

#### `/team lineup` — Show team lineups for the active season
*Access: Trusted admin*

Displays the placed drivers for each team seat in the active season. If a division name or tier number is provided only that division is shown; otherwise all divisions are listed. Requires an active season.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | String | — | Division name or tier number; omit to show all divisions |
| `public` | Boolean | — | Post the lineup visibly in the channel; defaults to ephemeral (only visible to you) |

#### `/team reserve-role` — Set or clear the Reserve team's Discord role
*Access: Trusted admin*

Sets the Discord role granted to (and revoked from) drivers placed in the Reserve team. Omit the `role` parameter to clear any existing mapping.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `role` | Role | — | Discord role for Reserve drivers; omit to clear |

---

### Signup Module Commands

All commands below require the signup module to be enabled (`/module enable signup`). Most commands also require being invoked from the configured interaction channel.

#### `/signup config channel` — Set the signup channel
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `channel` | Channel | ✅ | Channel for signup interactions |

#### `/signup config roles` — Set the signup roles
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `base_role` | Role | ✅ | Role granted to members eligible to sign up |
| `signed_up_role` | Role | ✅ | Role granted on successful signup completion |

#### `/signup config view` — View current signup configuration
*Access: Trusted admin*

No parameters. Displays the current signup module configuration as an embed.

#### `/signup nationality` — Toggle nationality requirement
*Access: Trusted admin*

No parameters. Toggles whether drivers must provide their nationality during signup.

#### `/signup time-type` — Toggle the time type setting
*Access: Trusted admin*

No parameters. Cycles the lap time type between Time Trial and Short Qualification.

#### `/signup time-image` — Toggle time image requirement
*Access: Trusted admin*

No parameters. Toggles whether drivers must attach a screenshot of their lap time.

#### `/signup time-slot add` — Add an availability time slot
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `day` | Choice | ✅ | Day of the week (Monday–Sunday) |
| `time` | String | ✅ | Time in `HH:MM` 24 h or 12 h format (e.g. `14:30` or `2:30pm`) |

#### `/signup time-slot remove` — Remove an availability time slot
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `slot_id` | Integer | ✅ | Stable sequence ID shown in `/signup time-slot list` |

#### `/signup time-slot list` — List all configured availability time slots
*Access: Trusted admin*

No parameters.

#### `/signup open` — Open the signup window
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `track_ids` | String | — | Space- or comma-separated track IDs for required lap times (e.g. `01 03 12`). Omit to require no specific tracks. |

#### `/signup close` — Close the signup window
*Access: Trusted admin*

No parameters. If drivers are currently in progress you will be prompted to confirm transitioning them to Not Signed Up.

#### `/signup unassigned` — List all Unassigned drivers seeded by lap time
*Access: Trusted admin*

No parameters. Displays all drivers in the Unassigned state, ordered by total lap time ascending (fastest first). Drivers with no lap time on record appear last.

---

### Results Module Commands

All commands below require the results module to be enabled (`/module enable results`). Most commands also require the `results` module gate, and some also require Server Admin access.

#### Points Config Management

##### `/results config add` — Create a named points configuration
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Unique config name (e.g. `100%`) |

All positions default to 0 points after creation.

##### `/results config remove` — Delete a named points configuration
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name to remove |

##### `/results config session` — Set points for a finishing position
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Session type: `Feature Qualifying`, `Feature Race`, `Sprint Qualifying`, or `Sprint Race` |
| `position` | Integer | ✅ | Finishing position (1-indexed) |
| `points` | Integer | ✅ | Points awarded |

##### `/results config fl` — Set the fastest-lap bonus
*Access: Trusted admin*

Only applicable to race session types (`Feature Race`, `Sprint Race`).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Race session type |
| `points` | Integer | ✅ | Bonus points for fastest lap |

##### `/results config fl-plimit` — Set the fastest-lap position eligibility limit
*Access: Trusted admin*

Only applicable to race session types. For example `limit:10` means only drivers finishing in positions 1–10 are eligible.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Race session type |
| `limit` | Integer | ✅ | Highest eligible position |

##### `/results config append` — Attach a config to the current season
*Access: Trusted admin*

Only allowed when the season is in **SETUP** status.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name to attach |

##### `/results config detach` — Detach a config from the current season
*Access: Trusted admin*

Only allowed when the season is in **SETUP** status.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name to detach |

##### `/results config view` — View a points config
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | — | Optional: filter output to a specific session type |

Displays position-to-points mappings and fastest-lap settings. Works for both server-level configs (SETUP) and season-attached configs (ACTIVE).

##### `/results config xml-import` — Import a full points configuration from XML
*Access: Trusted admin · Results module required*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of an existing config to update |
| `file` | Attachment | — | `.xml` file to import; if omitted a modal is opened instead |

Bulk-upserts position points and fastest-lap bonuses for one or more session types in a single operation. Existing rows not mentioned in the XML are **left untouched** (partial imports are safe). The entire import is applied atomically — any validation failure leaves the database unchanged.

**Input methods:**
- **Modal** (no `file` argument) — paste XML directly into the modal text field (up to 4 000 characters).
- **File attachment** — attach an `.xml` file (up to 100 KB, UTF-8 encoded) to bypass the modal character limit.

**XML schema:**

```xml
<config>
  <session>
    <type>Feature Race</type>            <!-- required; see valid values below -->
    <position id="1">25</position>       <!-- id ≥ 1, points ≥ 0; multiple allowed -->
    <position id="2">18</position>
    <fastest-lap limit="10">2</fastest-lap> <!-- race sessions only; limit attr optional -->
  </session>
  <!-- additional <session> blocks as needed -->
</config>
```

**Valid `<type>` values:** `Feature Race`, `Feature Qualifying`, `Sprint Race`, `Sprint Qualifying` (case-insensitive).

**Validation rules:**
- Unknown session types, negative points, position `id` < 1, or fastest-lap on a qualifying session are rejected outright.
- Position points within each session block must be monotonically non-increasing (ties are not permitted between two positive values).
- Duplicate `id` attributes within one session block: last value wins, a warning is shown.
- A session block containing no `<position>` elements and no `<fastest-lap>` element is silently skipped.

---

#### Round Results Commands

##### Submission format — Race session

Each line of a race submission block represents one driver. The number of fields depends on the context:

**Event submission (6 fields):**
```
{pos}, {driver}, {team role}, {total time / gap}, {fastest lap}, {time penalties}
```

| Field | Description |
|-------|-------------|
| `pos` | Finishing position (integer) |
| `driver` | Discord member mention (e.g. `<@123456789>`) |
| `team role` | Discord role mention (e.g. `<@&987654321>`) |
| `total time / gap` | `H:MM:SS.mmm` for P1; `+M:SS.mmm` or `+SS.mmm` delta for others; `+N Lap(s)` for lapped drivers; `DNF`, `DNS`, or `DSQ` for non-classified entries |
| `fastest lap` | Lap time string (e.g. `1:24.000`) or `N/A` |
| `time penalties` | `N/A`, or an in-game time penalty in `M:SS.mmm` or `SS.mmm` format (e.g. `0:05.000`) |

**Results amend (8 fields):**
```
{pos}, {driver}, {team role}, {total time / gap}, {fastest lap}, {ingame penalties}, {postrace penalty}, {appeal penalty}
```

All fields above apply, plus:

| Field | Description |
|-------|-------------|
| `ingame penalties` | `N/A`, or an in-game time penalty in `M:SS.mmm` or `SS.mmm` format (e.g. `0:05.000`) |
| `postrace penalty` | `N/A`; a penalty in seconds (e.g. `5.000`); or `DSQ` |
| `appeal penalty` | `N/A`; a penalty in seconds (e.g. `5.000`); or `DSQ` |

**Race ordering rules:**
- Rows must be ordered: classified entries (lead-lap or lapped times) → `DNF` → `DNS` → `DSQ`. Any violation is rejected.
- Setting both `postrace penalty` **and** `appeal penalty` to `DSQ` on the same row is invalid (amend only).
- A driver whose either penalty field is `DSQ` has their outcome recorded as `DSQ` regardless of the `total time` value.

**Example (event submission):**
```
1, @Driver,  @TeamRole, 1:23:45.678, 1:24.000, N/A
2, @Other,   @TeamRole, +5.321,      1:24.000, N/A
3, @Driver3, @TeamRole, +12.450,     1:25.100, N/A
```

**Example (results amend):**
```
1, @Driver,  @TeamRole, 1:23:45.678, 1:24.000, N/A,       N/A,   N/A
2, @Other,   @TeamRole, +5.321,      1:24.000, 0:05.000,  N/A,   N/A
3, @Driver3, @TeamRole, +12.450,     1:25.100, N/A,       5.000, N/A
4, @Driver4, @TeamRole, DNF,         N/A,      N/A,       DSQ,   N/A
```

---

##### Submission format — Qualifying session

Each line of a qualifying submission block represents one driver. The number of fields depends on the context:

**Event submission (6 fields):**
```
{pos}, {driver}, {team role}, {tyre}, {best lap}, {gap}
```

| Field | Description |
|-------|-------------|
| `pos` | Qualifying position (integer) |
| `driver` | Discord member mention (e.g. `<@123456789>`) |
| `team role` | Discord role mention (e.g. `<@&987654321>`) |
| `tyre` | Tyre compound used on the fastest lap (e.g. `Soft`) |
| `best lap` | Lap time string (e.g. `1:20.456`); or `DNF`, `DNS`, `DSQ` for non-classified entries |
| `gap` | `N/A` for P1; delta time (e.g. `+0.456`) for all other classified entries |

**Results amend (8 fields):**
```
{pos}, {driver}, {team role}, {tyre}, {best lap}, {gap}, {postrace penalty}, {appeal penalty}
```

All fields above apply, plus:

| Field | Description |
|-------|-------------|
| `postrace penalty` | `N/A` or `DSQ` — disqualification applied after the session |
| `appeal penalty` | `N/A` or `DSQ` — disqualification upheld on appeal |

**Ordering rules (both formats):**
- Rows must be ordered: classified entries (valid lap time) → `DNF` → `DNS` → `DSQ`. Any violation is rejected.
- Setting both `postrace penalty` **and** `appeal penalty` to `DSQ` on the same row is invalid (amend only).
- A driver whose either penalty field is `DSQ` has their outcome recorded as `DSQ` regardless of the `best lap` value (amend only).

**Example (event submission):**
```
1, @Driver,  @TeamRole, Soft,   1:20.456, N/A
2, @Other,   @TeamRole, Medium, 1:20.789, +0.333
3, @Driver3, @TeamRole, Soft,   DNF,      N/A
4, @Driver4, @TeamRole, Hard,   DNS,      N/A
```

**Example (results amend):**
```
1, @Driver,  @TeamRole, Soft,   1:20.456, N/A,    N/A, N/A
2, @Other,   @TeamRole, Medium, 1:20.789, +0.333, N/A, N/A
3, @Driver3, @TeamRole, Soft,   DNF,      N/A,    N/A, N/A
4, @Driver4, @TeamRole, Hard,   DNS,      N/A,    N/A, N/A
5, @Driver5, @TeamRole, Soft,   1:19.000, N/A,    DSQ, N/A
```

---

##### Post-submission penalty review — Apply post-race penalties or disqualifications

After all sessions of a round are submitted, the submission channel enters **penalty review state** instead of closing immediately. The bot posts a penalty review prompt with the following buttons:

- **➕ Add Penalty** — opens a modal to enter a driver mention and penalty value (e.g. `+5s`, `-3s`, `DSQ`). Positive and negative time penalties are supported for race sessions; only DSQ is accepted for qualifying sessions. A zero-second penalty is rejected. Negative penalties are also rejected if they would produce a negative total race time.
- **🗑 Clear All** — prompts for confirmation, then clears the entire staged list.
- **✅ Approve** — disabled until at least one or zero penalties have been staged; moves to the approval step (see below).
- **Remove [driver] [penalty]** — a per-entry button appears for each staged penalty, allowing individual removals.

Once **Approve** is pressed, the bot posts an **approval message** to the submission channel with:
- **✏️ Make Changes** — returns to the penalty review prompt.
- **✅ Approve** — applies all staged penalties, recomputes positions and points for all affected sessions, deletes and reposts the interim results and standings, cascades standing recalculations to subsequent rounds, then closes the submission channel. The round is marked **finalized**.

**Notes:**
- Any message posted in the submission channel while it is in penalty review state is automatically deleted with an explanatory reply.
- Penalties can be positive (`+5s`, `5s`, `5`) or negative (`-3s`, `-3`) for race sessions.
- A DSQ on the fastest-lap holder forfeits the bonus; no other driver receives it.
- A round that is finalized blocks `/test-mode advance` until approved.

##### Fastest-lap tie-breaking — FL override header

The fastest-lap bonus is awarded to the driver with the lowest Fastest Lap time in the submitted block. When two or more drivers share the exact same time, add an optional **FL override header** as the very first line of the race submission:

```
FL: @Driver
1, @Driver, @TeamRole, 1:23:45.678, 1:24.000, N/A
2, @Other,  @TeamRole, +5.321,       1:24.000, N/A
...
```

Rules:
- The header format is `FL: <@user_id>` (standard Discord member mention).
- The named driver must appear in the submitted results — if not, the submission is rejected.
- The override replaces automatic time-comparison entirely for that submission.
- Omitting the header restores normal behaviour: the lowest lap time wins; ties fall to the driver listed highest (lowest finishing position).
- The header is ignored for qualifying submissions.
- On bot restart, open penalty review channels are automatically restored.

##### `/round results amend` — Re-submit results for a completed session
*Access: Trusted admin · Results module required*

Opens a temporary, private **amend channel** (named `amend-S{N}-{slug}-R{N}`) in the same category as the bot commands channel. Paste the corrected results in that channel; the bot validates and applies them, recalculates standings, then deletes the channel automatically. The optional `FL: @Driver` fastest-lap override header (see above) is supported here as well. A **❌ Cancel Amendment** button is posted in the channel to abort at any time. If `session` is omitted you will be prompted to choose one before the channel is created.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division_name` | String | ✅ | Name of the division |
| `round_number` | Integer | ✅ | Round number to amend |
| `session` | Choice | — | Session to amend. If omitted the bot will prompt for one |

---

#### Mid-Season Points Amendment

##### `/results amend toggle` — Enable or disable amendment mode
*Access: Server admin*

No parameters. Toggles amendment mode for the active season. When amendment mode is active, changes made via `/results amend session`, `/results amend fl`, and `/results amend fl-plimit` are staged in a modification store and do not affect live standings until approved with `/results amend review`.

Disabling amendment mode while there are uncommitted changes is blocked — use `/results amend revert` to discard them first.

##### `/results amend revert` — Discard modification store changes
*Access: Trusted admin*

No parameters. Resets the modification store to match the current season points and clears the modified flag.

##### `/results amend session` — Stage a points change in the modification store
*Access: Trusted admin*

Requires amendment mode to be active.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Session type |
| `position` | Integer | ✅ | Finishing position |
| `points` | Integer | ✅ | New points value |

##### `/results amend fl` — Stage a fastest-lap bonus change
*Access: Trusted admin*

Requires amendment mode to be active. Race session types only.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Race session type |
| `points` | Integer | ✅ | New FL bonus value |

##### `/results amend fl-plimit` — Stage a fastest-lap position limit change
*Access: Trusted admin*

Requires amendment mode to be active. Race session types only.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Config name |
| `session` | Choice | ✅ | Race session type |
| `limit` | Integer | ✅ | New position limit |

##### `/results amend review` — Review and approve modification store changes
*Access: Server admin*

No parameters. Displays a diff of the staged changes against the current season points. Approve to atomically overwrite season points and recalculate all standings. Reject to leave the modification store unchanged.

---

#### Reserve Driver Visibility
##### `/results standings sync` — Force a full standings repost for a division
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|--------------|
| `division` | String | ✅ | Division name |

Deletes every existing standings Discord message for the division and reposts fresh standings for each round that has results, in round order. Useful after manual data corrections or if standings messages were accidentally deleted.

---

##### `/results rounds sync` — Force a full results repost for a division
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|--------------|
| `division` | String | ✅ | Division name |

Deletes every existing session results Discord message for the division and reposts fresh results for each session of each round, in round order. Useful after manual data corrections or if results messages were accidentally deleted.

---
##### `/results reserves toggle` — Toggle reserve driver visibility in standings
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | String | ✅ | Division name |

Toggles whether reserve drivers appear in the publicly posted standings for the specified division.

---

### Attendance Module

All commands below require the attendance module to be enabled (`/module enable attendance`).

#### `/attendance config autoreserve` — Set the auto-reserve threshold
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `points` | Integer | ✅ | Cumulative attendance-penalty threshold that triggers auto-reserve. Use `0` to disable. |

When a driver's cumulative attendance-penalty total reaches this value they are automatically unassigned from their current full-time seat and moved to the reserve team of their division.

> **Limitation:** Cannot be set to a non-zero value while auto-sack is active. Disable auto-sack first (`/attendance config autosack 0`). The two features are mutually exclusive.

---

#### `/attendance config autosack` — Set the auto-sack threshold
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `points` | Integer | ✅ | Cumulative attendance-penalty threshold that triggers auto-sack. Use `0` to disable. |

When a driver's cumulative attendance-penalty total reaches this value they are automatically removed from all driving seats across all divisions and lose their driver role.

> **Limitation:** Cannot be set to a non-zero value while auto-reserve is active. Disable auto-reserve first (`/attendance config autoreserve 0`). The two features are mutually exclusive.

---

#### `/attendance config show` — View the current attendance configuration
*Access: Trusted admin*

No parameters. Displays the full attendance configuration for this server as an ephemeral message, including:

- **Timing** — RSVP notice days, last-reminder hours, and RSVP deadline hours
- **Penalties** — No-RSVP penalty, No-RSVP-absent extra penalty, and RSVP'd-but-absent penalty
- **Auto-actions** — Auto-reserve threshold and auto-sack threshold (both shown as `disabled` when set to `0`)

---

### Track Distribution Parameters

#### `/track config` — Set per-track Beta distribution parameters
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|--------------|
| `track` | String | ✅ | Track ID or name (autocomplete supported) |
| `mu` | Float | ✅ | Mean rain probability (0.0 – 1.0 exclusive, e.g. `0.30` for 30%) |
| `sigma` | Float | ✅ | Dispersion / standard deviation (must be > 0) |

Changes take effect for all future Phase 1 draws. Existing results are not retroactively recalculated.

#### `/track reset` — Revert to packaged default
*Access: Trusted admin*

| Parameter | Type | Required | Description |
|-----------|------|----------|--------------|
| `track` | String | ✅ | Track ID or name to reset |

Removes the server override; the bot reverts to its packaged default values for that track.

#### `/track info` — Inspect effective parameters
*Access: Interaction role*

| Parameter | Type | Required | Description |
|-----------|------|----------|--------------|
| `track` | String | ✅ | Track ID or name |

Shows the effective μ and σ, whether they come from a server override or the bot's packaged default, and (for overrides) who set them and when.

---

### Track ID Reference

Use these IDs in `/round add` and `/round amend` — autocomplete will show the full list as you type.

| ID | Track | ID | Track | ID | Track |
|----|-------|----|-------|----|-------|
| 01 | Abu Dhabi | 10 | China | 19 | Monza |
| 02 | Australia | 11 | Hungary | 20 | Netherlands |
| 03 | Austria | 12 | Imola | 21 | Portugal |
| 04 | Azerbaijan | 13 | Japan | 22 | Qatar |
| 05 | Bahrain | 14 | Las Vegas | 23 | Saudi Arabia |
| 06 | Barcelona | 15 | Madrid | 24 | Singapore |
| 07 | Belgium | 16 | Mexico | 25 | Texas |
| 08 | Brazil | 17 | Miami | 26 | Turkey |
| 09 | Canada | 18 | Monaco | 27 | United Kingdom |

---

## Track Distribution Parameters

Phase 1 draws the rain probability coefficient (`Rpc`) from a **Beta distribution** parameterised by two values per track:

| Symbol | Name | Meaning |
|--------|------|---------|
| **μ** (`mu`) | Mean rain probability | Expected average Rpc for this circuit |
| **σ** (`sigma`) | Dispersion | Controls how wide / unpredictable the distribution is |

The Beta distribution is natively bounded to [0, 1], so no clamping is needed under normal parameters.

### How σ affects the shape

Raising σ **widens** the distribution and pushes probability mass towards both extremes:

- **Small σ** (e.g. Bahrain: μ = 5%, σ = 2%): draws cluster tightly around the mean. Rare to see anything above ~10%; the track feels reliably dry.
- **Larger σ** (e.g. Belgium: μ = 30%, σ = 8%): draws spread across a wider band. You might see 5% or 55% in the same season — genuine unpredictability.

**Concrete tail probabilities (approximate)**:

| Track | μ | σ | P(Rpc ≥ 10%) | P(Rpc ≥ 25%) |
|-------|---|---|--------------|---------------|
| Bahrain | 5% | 2% | ~2% | < 0.1% |
| Bahrain | 5% | 5% | ~14% | ~3% |
| Belgium | 30% | 8% | ~97% | ~50% |

Raising Bahrain's σ from 2% to 5% increases the chance of a surprise wet event (≥ 10%) from ~2% to ~14%. Belgium at σ = 8% is almost always substantially wet, but occasionally surprises with a dry day.

### The J-shape / humped-bell transition

The Beta distribution changes shape depending on the derived parameters α = μν and β = (1 − μ)ν, where ν = μ(1 − μ)/σ² − 1.

- **When α < 1** (typical for low-μ, wider-σ tracks): the distribution is **J-shaped** — mode at 0, with a long right tail. Most draws are near 0, but genuine spikes into moderate territory are possible. This is exactly the desired behaviour for arid circuits like Bahrain or Qatar.
- **When α > 1 and β > 1** (typical for mid-μ tracks with moderate σ): the distribution is **bell-shaped (humped)** — centred around the mean with symmetric spread. United Kingdom (μ = 30%, σ = 5%) behaves like this.

### Feasibility constraint

σ must satisfy `σ < √(μ × (1 − μ))`. If this is violated, the Beta parameters become non-positive and sampling will fail — Phase 1 will block with an error to the log channel. Use `/track info` after setting parameters to verify.

### Packaged defaults

All 27 circuits ship with pre-tuned defaults. Use `/track info <track>` to inspect them or `/track config` to override them for your server.

---

## Weather Pipeline

Three phases fire automatically per round (non-Mystery formats only):

| Phase | Horizon | Output |
|-------|---------|--------|
| Phase 1 | T-5 days | Rain probability coefficient (Rpc) |
| Phase 2 | T-2 days | Rain/mixed/sunny slot per session |
| Phase 3 | T-2 hours | Slot-by-slot weather labels per session |

All forecast messages go to each division forecast channel.
Computation logs go to the server log channel.

---

## Running Tests

```bash
pytest
```

---

## Architecture

```
src/
  bot.py               Entry point
  models/              Dataclasses and enums
  db/                  Database connection + migrations
  services/            Business logic (season, phases, scheduler, amendments)
  cogs/                Discord slash commands
  utils/               Math formulas, message builders, channel guard, output router
tests/
  unit/                Pure-function tests (math_utils)
  integration/         Database migration and query tests
```
